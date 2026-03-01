"""ComfyUI REST API + WebSocket 통신 클라이언트.

LTX-2 워크플로우를 ComfyUI 서버에 제출하고 결과를 수신한다.

의존성:
- httpx (비동기 HTTP, 이미 requirements.txt에 있음)
- websockets (pip install websockets 필요)

코딩 규칙:
- ai_worker.tts 모듈을 import하지 않는다.
- logging.getLogger(__name__) 사용.
- pathlib.Path 필수.
- config/settings.py의 COMFYUI_URL 사용.
"""

import json
import logging
import random
import uuid
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """ComfyUI API 클라이언트 (LTX-2)."""

    def __init__(self, base_url: str, output_dir: Path | None = None):
        """
        Args:
            base_url: ComfyUI 서버 URL (예: "http://comfyui:8188")
            output_dir: ai_worker에서 접근 가능한 ComfyUI 출력 디렉터리.
                        ComfyUI 내부 /comfyui/output → 공유 볼륨으로 마운트된 경로.
        """
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._workflow_dir = Path(__file__).resolve().parent / "workflows"
        self._output_dir = output_dir or Path("media/tmp/videos")

    # -- 공개 API --

    async def health_check(self) -> bool:
        """ComfyUI 서버 상태 확인.

        Returns:
            True: 서버 정상 응답
            False: 서버 다운 또는 응답 지연
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/system_stats", timeout=5.0,
                )
                return resp.status_code == 200
        except Exception as e:
            logger.warning("[comfy] health_check 실패: %s", e)
            return False

    async def generate_t2v(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1280,
        height: int = 720,
        num_frames: int = 97,
        fps: int = 24,
        steps: int = 20,
        cfg: float = 3.5,
        seed: int = -1,
        use_distilled: bool = False,
        timeout: int = 300,
    ) -> Path:
        """Text-to-Video 생성.

        Args:
            prompt: 영어 비디오 프롬프트
            negative_prompt: 네거티브 프롬프트
            width, height: 출력 해상도 (1280×720 권장)
            num_frames: 프레임 수 (97 = 1+8*12 ~4초@24fps)
            fps: 프레임 레이트 (24 권장)
            steps: 샘플링 스텝 수 (풀 20, Distilled 8)
            cfg: CFG 스케일 (풀 3.5, Distilled 1.0)
            seed: 랜덤 시드 (-1이면 자동)
            use_distilled: True면 Distilled 워크플로우 사용
            timeout: 최대 대기 시간 (초)

        Returns:
            생성된 mp4 파일의 절대 경로

        Raises:
            RuntimeError: 생성 실패 (OOM, 타임아웃 등)
            ConnectionError: ComfyUI 서버 연결 불가
        """
        if use_distilled:
            workflow = self._load_workflow("t2v_ltx2_distilled.json")
        else:
            workflow = self._load_workflow("t2v_ltx2.json")

        workflow = self._patch_workflow(workflow, {
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "length": num_frames,
            "frames_number": num_frames,
            "frame_rate": fps,
            "steps": steps,
            "cfg": cfg,
            "noise_seed": seed if seed >= 0 else random.randint(0, 2**32 - 1),
        })
        return await self._queue_and_wait(workflow, timeout=timeout)

    async def generate_t2v_with_upscale(
        self,
        prompt: str,
        negative_prompt: str = "",
        num_frames: int = 97,
        fps: int = 24,
        seed: int = -1,
        timeout: int = 600,
    ) -> Path:
        """2-Stage 업스케일 T2V 생성 (640×360 → 1280×720).

        테스트 비교용. 운영 기본값 아님.

        Args:
            prompt: 영어 비디오 프롬프트
            negative_prompt: 네거티브 프롬프트
            num_frames: 프레임 수
            fps: 프레임 레이트
            seed: 랜덤 시드 (-1이면 자동)
            timeout: 최대 대기 시간 (초, 2-Stage이므로 더 길게)

        Returns:
            생성된 mp4 파일의 절대 경로
        """
        workflow = self._load_workflow("t2v_ltx2_upscale.json")
        actual_seed = seed if seed >= 0 else random.randint(0, 2**32 - 1)
        workflow = self._patch_workflow(workflow, {
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            "length": num_frames,
            "frames_number": num_frames,
            "frame_rate": fps,
            "noise_seed": actual_seed,
        })
        return await self._queue_and_wait(workflow, timeout=timeout)

    async def generate_i2v(
        self,
        prompt: str,
        init_image_path: Path,
        negative_prompt: str = "",
        width: int = 1280,
        height: int = 720,
        num_frames: int = 97,
        fps: int = 24,
        strength: float = 0.75,
        steps: int = 20,
        cfg: float = 3.5,
        seed: int = -1,
        use_distilled: bool = False,
        timeout: int = 300,
    ) -> Path:
        """Image-to-Video 생성.

        LTXVImgToVideoInplace 노드를 사용하여 초기 이미지를 기반으로 비디오를 생성한다.
        입력 이미지는 OOM 방지를 위해 target 해상도로 리사이즈된다.

        Args:
            prompt: 영어 비디오 프롬프트
            init_image_path: 초기 프레임 이미지 파일 경로
            strength: LTXVImgToVideoInplace strength (0.0~1.0)
            use_distilled: True면 Distilled 워크플로우 사용
            (나머지 파라미터는 generate_t2v와 동일)

        Returns:
            생성된 mp4 파일의 절대 경로
        """
        # 이미지 리사이즈 (OOM 방지 + 해상도 통일)
        resized_path = self._resize_image(init_image_path, width, height)
        try:
            image_name = await self._upload_image(resized_path)

            if use_distilled:
                workflow = self._load_workflow("i2v_ltx2_distilled.json")
            else:
                workflow = self._load_workflow("i2v_ltx2.json")
            workflow = self._patch_workflow(workflow, {
                "positive_prompt": prompt,
                "negative_prompt": negative_prompt,
                "init_image": image_name,
                "width": width,
                "height": height,
                "length": num_frames,
                "frames_number": num_frames,
                "frame_rate": fps,
                "strength": strength,
                "steps": steps,
                "cfg": cfg,
                "noise_seed": seed if seed >= 0 else random.randint(0, 2**32 - 1),
            })
            return await self._queue_and_wait(workflow, timeout=timeout)
        finally:
            # HIGH-6: 리사이즈된 임시 이미지 정리
            if resized_path != init_image_path and resized_path.exists():
                resized_path.unlink()
                logger.debug("[comfy] 리사이즈 임시 파일 삭제: %s", resized_path.name)

    def _resize_image(self, image_path: Path, width: int, height: int) -> Path:
        """이미지를 target 해상도로 리사이즈한다.

        원본 이미지가 target보다 큰 경우에만 리사이즈하며,
        비율을 유지하면서 target 크기에 맞춘다.
        """
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        if img.width <= width and img.height <= height:
            return image_path

        img.thumbnail((width, height), Image.LANCZOS)
        resized_path = image_path.parent / f"resized_{image_path.name}"
        img.save(resized_path, quality=95)
        logger.info(
            "[comfy] 이미지 리사이즈: %dx%d → %dx%d (%s)",
            img.width, img.height, width, height, resized_path.name,
        )
        return resized_path

    # -- 내부 메서드 --

    def _load_workflow(self, filename: str) -> dict:
        """workflows/ 디렉터리에서 워크플로우 JSON 로드."""
        path = self._workflow_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"워크플로우 파일 없음: {path}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # 값 매칭에서 허용되는 placeholder 화이트리스트.
    # 이 목록에 없는 문자열은 placeholder로 취급하지 않는다.
    _PLACEHOLDER_WHITELIST = frozenset({
        "positive_prompt", "negative_prompt", "init_image",
    })

    def _patch_workflow(self, workflow: dict, params: dict) -> dict:
        """워크플로우 JSON의 노드 입력값을 params로 교체.

        두 가지 매칭 방식을 지원:
        1. 키 매칭: inputs의 키가 params의 키와 일치하면 값 교체
        2. 값 매칭 (화이트리스트): inputs의 값이 허용된 placeholder이고
           params에 해당 키가 있으면 교체
        """
        applied: set[str] = set()
        for node_id, node_data in workflow.items():
            if not isinstance(node_data, dict) or "inputs" not in node_data:
                continue
            inputs = node_data["inputs"]
            for key, value in list(inputs.items()):
                # 1. 키 매칭
                if key in params:
                    inputs[key] = params[key]
                    applied.add(key)
                # 2. 값 매칭 (화이트리스트 placeholder만 허용)
                elif (
                    isinstance(value, str)
                    and value in self._PLACEHOLDER_WHITELIST
                    and value in params
                ):
                    inputs[key] = params[value]
                    applied.add(value)

        # 적용되지 않은 파라미터 경고 (디버깅용)
        unapplied = set(params.keys()) - applied
        if unapplied:
            logger.debug("[comfy] 워크플로우에 적용되지 않은 파라미터: %s", unapplied)
        return workflow

    async def _upload_image(self, image_path: Path) -> str:
        """ComfyUI 서버에 이미지를 업로드하고 서버 내 파일명을 반환.

        POST /upload/image (multipart/form-data)
        """
        if not image_path.exists():
            raise FileNotFoundError(f"이미지 파일 없음: {image_path}")

        async with httpx.AsyncClient() as client:
            with open(image_path, "rb") as f:
                resp = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (image_path.name, f, "image/png")},
                    data={"overwrite": "true"},
                    timeout=30.0,
                )
            resp.raise_for_status()
            data = resp.json()
            return data["name"]

    async def _poll_until_done(self, prompt_id: str, interval: float = 3.0) -> None:
        """GET /history/{prompt_id}를 폴링하여 완료를 대기한다.

        WebSocket 연결이 끊어진 경우의 폴백 메커니즘.
        history에 prompt_id가 나타나고 completed=True이면 완료로 판단한다.
        ComfyUI가 재시작되면 history가 유실되므로 queue 상태도 확인한다.
        """
        import asyncio

        logger.info("[comfy] polling 시작: prompt_id=%s", prompt_id)
        consecutive_errors = 0
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self.base_url}/history/{prompt_id}",
                        timeout=10.0,
                    )
                    consecutive_errors = 0
                    if resp.status_code == 200:
                        history = resp.json()
                        if prompt_id in history:
                            status = history[prompt_id].get("status", {})
                            if status.get("completed", False):
                                logger.info("[comfy] polling 완료 확인")
                                return
                            if status.get("status_str") == "error":
                                msgs = status.get("messages", [])
                                raise RuntimeError(
                                    f"ComfyUI 실행 에러 (poll): {msgs}"
                                )
            except (httpx.HTTPError, httpx.ConnectError, OSError):
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    raise RuntimeError(
                        "ComfyUI 서버 응답 없음 — 크래시 가능성 (polling 중단)"
                    )
                logger.debug("[comfy] polling 연결 실패 (%d회)", consecutive_errors)
            await asyncio.sleep(interval)

    async def _queue_and_wait(self, workflow: dict, timeout: int = 300) -> Path:
        """워크플로우를 큐에 제출하고 완료까지 WebSocket으로 대기.

        절차:
        1. POST /prompt — prompt_id 획득
        2. WebSocket ws://{host}/ws?clientId={client_id} 연결
        3. "executing" 메시지 수신 시 진행 상황 로깅
        4. "executed" 이벤트에서 출력 파일 경로 추출
        5. GET /history/{prompt_id}에서 output 파일 경로 확인
        6. 파일이 공유 볼륨에 있으면 해당 경로 반환

        타임아웃 시 RuntimeError("ComfyUI generation timeout") 발생.
        """
        import asyncio

        # 1. 큐 제출
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": self.client_id},
                timeout=30.0,
            )
            if resp.status_code != 200:
                logger.error("[comfy] 워크플로우 제출 실패 (%d): %s", resp.status_code, resp.text[:2000])
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]
            logger.info("[comfy] 워크플로우 제출: prompt_id=%s", prompt_id)

        # 2. WebSocket 대기 (연결 끊김 시 polling 폴백)
        import websockets

        ws_host = self.base_url.replace("http://", "").replace("https://", "")
        ws_url = f"ws://{ws_host}/ws?clientId={self.client_id}"

        try:
            async with asyncio.timeout(timeout):
                try:
                    async with websockets.connect(
                        ws_url, ping_interval=20, ping_timeout=60,
                    ) as ws:
                        while True:
                            msg = json.loads(await ws.recv())
                            msg_type = msg.get("type")

                            if msg_type == "executing":
                                node = msg.get("data", {}).get("node")
                                if node is None:
                                    break
                                logger.debug("[comfy] 실행 중: node=%s", node)

                            elif msg_type == "execution_error":
                                error_data = msg.get("data", {})
                                raise RuntimeError(
                                    f"ComfyUI 실행 에러: {error_data.get('exception_message', 'unknown')}"
                                )

                            elif msg_type == "progress":
                                data = msg.get("data", {})
                                logger.debug(
                                    "[comfy] 진행률: %d/%d",
                                    data.get("value", 0), data.get("max", 1),
                                )
                except (websockets.exceptions.ConnectionClosed, OSError) as ws_err:
                    logger.warning(
                        "[comfy] WebSocket 끊김 — polling 폴백: %s", ws_err,
                    )
                    await self._poll_until_done(prompt_id)
        except asyncio.TimeoutError:
            raise RuntimeError(f"ComfyUI generation timeout ({timeout}초)")

        # 3. 결과 파일 경로 조회
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/history/{prompt_id}",
                timeout=10.0,
            )
            resp.raise_for_status()
            history = resp.json()

        outputs = history.get(prompt_id, {}).get("outputs", {})
        for node_id, node_output in outputs.items():
            if "gifs" in node_output:
                for gif_info in node_output["gifs"]:
                    filename = gif_info["filename"]
                    subfolder = gif_info.get("subfolder", "")
                    output_path = self._output_dir / subfolder / filename if subfolder else self._output_dir / filename
                    result = await self._wait_for_file(output_path)
                    logger.info("[comfy] 출력 파일: %s", result)
                    return result
            if "images" in node_output:
                for img_info in node_output["images"]:
                    filename = img_info["filename"]
                    if filename.endswith((".mp4", ".webp", ".gif")):
                        output_path = self._output_dir / filename
                        result = await self._wait_for_file(output_path)
                        return result

        raise RuntimeError(f"ComfyUI 출력 파일을 찾을 수 없음: prompt_id={prompt_id}")

    async def _wait_for_file(self, path: Path, timeout: float = 30.0) -> Path:
        """파일이 실제로 존재하고 크기가 안정될 때까지 대기.

        Docker 볼륨 마운트에서 파일시스템 동기화 지연이 발생할 수 있으므로,
        파일 존재 + 크기 안정(2회 연속 동일)을 확인한다.
        """
        import asyncio

        start = asyncio.get_event_loop().time()
        prev_size = -1
        while asyncio.get_event_loop().time() - start < timeout:
            if path.exists():
                size = path.stat().st_size
                if size > 0 and size == prev_size:
                    return path
                prev_size = size
            await asyncio.sleep(0.5)
        raise RuntimeError(f"출력 파일 대기 타임아웃: {path}")
