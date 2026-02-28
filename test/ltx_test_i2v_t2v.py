"""LTX-Video T2V / I2V 직접 테스트 스크립트.

Docker 컨테이너 안에서 실행:
    docker compose exec ai_worker python /app/test/ltx_test_i2v_t2v.py t2v
    docker compose exec ai_worker python /app/test/ltx_test_i2v_t2v.py i2v
    docker compose exec ai_worker python /app/test/ltx_test_i2v_t2v.py all

프롬프트는 ai_worker.video.prompt_engine의 VideoPromptEngine을 사용한다.
결과물은 /app/test/ltx_output/ 에 저장된다.
"""

import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ltx_test")

# ──────────────────────────────────────────────
# ★ 여기서 텍스트/무드를 직접 수정하세요 ★
# ──────────────────────────────────────────────
TEST_TEXT = "장원영 중안부 소멸 직전이네"
TEST_MOOD = "daily"
TEST_TITLE = ""

# I2V용 테스트 이미지 경로 (test_image 폴더의 첫 번째 이미지 자동 선택)
TEST_IMAGE_DIR = Path("/app/test/ltx_output/test_image")

# 출력 디렉터리
OUTPUT_DIR = Path("/app/test/ltx_output")

# ComfyUI 생성 파라미터
WIDTH = 512
HEIGHT = 512
NUM_FRAMES = 81
STEPS = 20
CFG_SCALE = 3.5
# ──────────────────────────────────────────────


def _find_test_image() -> Path:
    """test_image 폴더에서 첫 번째 이미지 파일을 찾는다."""
    extensions = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    for f in sorted(TEST_IMAGE_DIR.iterdir()):
        if f.suffix.lower() in extensions and not f.name.startswith("resized_"):
            return f
    raise FileNotFoundError(f"테스트 이미지 없음: {TEST_IMAGE_DIR}")


def generate_prompt(mode: str) -> str:
    """prompt_engine을 사용해 영어 프롬프트를 생성한다."""
    from ai_worker.video.prompt_engine import VideoPromptEngine

    engine = VideoPromptEngine()
    prompt = engine.generate_prompt(
        text_lines=[TEST_TEXT],
        mood=TEST_MOOD,
        title=TEST_TITLE,
        has_init_image=(mode == "i2v"),
    )
    return prompt


def _unload_ollama() -> None:
    """Ollama 모델을 GPU에서 언로드하여 ComfyUI에 VRAM을 확보한다."""
    import requests

    try:
        resp = requests.post(
            "http://host.docker.internal:11434/api/generate",
            json={"model": "qwen2.5:14b", "keep_alive": 0},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Ollama 모델 언로드 완료 (VRAM 확보)")
        else:
            logger.warning("Ollama 언로드 응답: %d", resp.status_code)
    except Exception as e:
        logger.warning("Ollama 언로드 실패 (무시): %s", e)


async def run_t2v() -> Path:
    """T2V 테스트: 텍스트 → 프롬프트 → 비디오 생성."""
    from ai_worker.video.comfy_client import ComfyUIClient
    from ai_worker.video.prompt_engine import NEGATIVE_PROMPT
    from config.settings import get_comfyui_url

    logger.info("=" * 60)
    logger.info("T2V 테스트 시작")
    logger.info("=" * 60)
    logger.info("텍스트: %s", TEST_TEXT)
    logger.info("무드: %s", TEST_MOOD)

    # 1. 프롬프트 생성
    t0 = time.time()
    prompt = generate_prompt("t2v")
    logger.info("생성된 프롬프트: %s", prompt)
    logger.info("프롬프트 생성 소요: %.1fs", time.time() - t0)

    # 1.5. Ollama 모델 언로드 (ComfyUI VRAM 확보)
    _unload_ollama()

    # 2. ComfyUI로 비디오 생성
    client = ComfyUIClient(base_url=get_comfyui_url())
    healthy = await client.health_check()
    if not healthy:
        raise ConnectionError("ComfyUI 서버 응답 없음")
    logger.info("ComfyUI 연결 확인 완료")

    t1 = time.time()
    output_path = await client.generate_t2v(
        prompt=prompt,
        negative_prompt=NEGATIVE_PROMPT,
        width=WIDTH,
        height=HEIGHT,
        num_frames=NUM_FRAMES,
        steps=STEPS,
        cfg_scale=CFG_SCALE,
    )
    elapsed = time.time() - t1
    logger.info("비디오 생성 완료: %.1fs", elapsed)

    # 3. 결과 파일 복사
    dest = OUTPUT_DIR / "t2v_result.mp4"
    shutil.copy2(output_path, dest)
    logger.info("결과 저장: %s (%.2f MB)", dest, dest.stat().st_size / 1024 / 1024)

    # 4. ffprobe로 검증
    _verify_video(dest)

    logger.info("-" * 40)
    logger.info("T2V 프롬프트: %s", prompt)
    logger.info("T2V 결과: %s", dest)
    return dest


async def run_i2v() -> Path:
    """I2V 테스트: 이미지 + 텍스트 → 프롬프트 → 비디오 생성."""
    from ai_worker.video.comfy_client import ComfyUIClient
    from ai_worker.video.prompt_engine import NEGATIVE_PROMPT
    from config.settings import get_comfyui_url

    logger.info("=" * 60)
    logger.info("I2V 테스트 시작")
    logger.info("=" * 60)
    logger.info("텍스트: %s", TEST_TEXT)
    logger.info("무드: %s", TEST_MOOD)

    # 0. 테스트 이미지 확인
    image_path = _find_test_image()
    logger.info("테스트 이미지: %s", image_path)

    # 1. 프롬프트 생성
    t0 = time.time()
    prompt = generate_prompt("i2v")
    logger.info("생성된 프롬프트: %s", prompt)
    logger.info("프롬프트 생성 소요: %.1fs", time.time() - t0)

    # 1.5. Ollama 모델 언로드 (ComfyUI VRAM 확보)
    _unload_ollama()

    # 2. ComfyUI로 비디오 생성
    client = ComfyUIClient(base_url=get_comfyui_url())
    healthy = await client.health_check()
    if not healthy:
        raise ConnectionError("ComfyUI 서버 응답 없음")
    logger.info("ComfyUI 연결 확인 완료")

    t1 = time.time()
    output_path = await client.generate_i2v(
        prompt=prompt,
        init_image_path=image_path,
        negative_prompt=NEGATIVE_PROMPT,
        width=WIDTH,
        height=HEIGHT,
        num_frames=NUM_FRAMES,
        steps=STEPS,
        cfg_scale=CFG_SCALE,
    )
    elapsed = time.time() - t1
    logger.info("비디오 생성 완료: %.1fs", elapsed)

    # 3. 결과 파일 복사
    dest = OUTPUT_DIR / "i2v_result.mp4"
    shutil.copy2(output_path, dest)
    logger.info("결과 저장: %s (%.2f MB)", dest, dest.stat().st_size / 1024 / 1024)

    # 4. ffprobe로 검증
    _verify_video(dest)

    logger.info("-" * 40)
    logger.info("I2V 프롬프트: %s", prompt)
    logger.info("I2V 이미지: %s", image_path)
    logger.info("I2V 결과: %s", dest)
    return dest


def _verify_video(path: Path) -> None:
    """ffprobe로 비디오 속성을 출력한다."""
    import json
    import subprocess

    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", str(path),
        ],
        capture_output=True, text=True,
    )
    info = json.loads(result.stdout)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            w = s.get("width", "?")
            h = s.get("height", "?")
            dur = s.get("duration", "?")
            frames = s.get("nb_frames", "?")
            logger.info("검증: %dx%d, %s초, %s프레임", w, h, dur, frames)


async def main(mode: str) -> None:
    total_start = time.time()

    if mode in ("t2v", "all"):
        await run_t2v()
    if mode in ("i2v", "all"):
        await run_i2v()

    logger.info("=" * 60)
    logger.info("전체 소요 시간: %.1fs", time.time() - total_start)
    logger.info("출력 디렉터리: %s", OUTPUT_DIR)
    logger.info("=" * 60)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode not in ("t2v", "i2v", "all"):
        print("사용법: python ltx_test_i2v_t2v.py [t2v|i2v|all]")
        sys.exit(1)
    asyncio.run(main(mode))
