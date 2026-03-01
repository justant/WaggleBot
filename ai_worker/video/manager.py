"""비디오 생성 파이프라인 총괄 매니저.

SceneDirector로부터 씬 리스트를 받아:
1. 각 씬의 video_mode(t2v/i2v) 결정
2. prompt_engine으로 영어 프롬프트 생성
3. comfy_client로 비디오 클립 생성
4. 실패 시 재시도 → 최종 실패 시 씬 삭제 + 대본 병합
5. 성공한 씬 목록 반환

의존성 규칙:
- ai_worker.tts 모듈을 절대 import하지 않는다.
- ai_worker.llm.client의 call_ollama_raw()만 간접 사용 (prompt_engine 경유).
"""

import gc
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VideoGenerationResult:
    """단일 씬의 비디오 생성 결과."""
    scene_index: int
    success: bool
    clip_path: Path | None = None
    attempts: int = 0
    failure_reason: str | None = None
    merged_into: int | None = None


class VideoManager:
    """비디오 생성 파이프라인 매니저."""

    def __init__(self, comfy_client, prompt_engine, config: dict):
        self.comfy = comfy_client
        self.prompt_engine = prompt_engine
        self.config = config
        self.results: list[VideoGenerationResult] = []

    async def generate_all_clips(
        self,
        scenes: list,
        mood: str,
        post_id: int,
        title: str,
        body_summary: str,
    ) -> list:
        """전체 씬에 대해 비디오 클립을 생성하고, 실패 씬을 처리한 최종 씬 리스트를 반환한다.

        처리 순서:
        1. ComfyUI 서버 health_check — 실패 시 빈 리스트 반환 (전체 스킵)
        2. 씬별 순차 클립 생성 (VRAM 안전)
        3. 실패 씬 처리: 재시도 → 씬 삭제 + 대본 병합
        4. 최종 유효 씬 리스트 반환
        """
        # 1. health check
        if not await self.comfy.health_check():
            logger.error("[video] ComfyUI 서버 다운 — 전체 비디오 생성 스킵 (post=%d)", post_id)
            return scenes

        self.results = []
        max_clips = self.config.get("VIDEO_MAX_CLIPS_PER_POST", 8)
        clip_count = 0

        # 2. 씬별 순차 생성
        for i, scene in enumerate(scenes):
            video_mode = getattr(scene, "video_mode", None)
            if video_mode not in ("t2v", "i2v"):
                continue

            if clip_count >= max_clips:
                logger.info("[video] post=%d 최대 클립 수 (%d) 도달 — 나머지 스킵", post_id, max_clips)
                break

            video_prompt = getattr(scene, "video_prompt", None)
            if not video_prompt:
                logger.warning("[video] post=%d 씬=%d video_prompt 없음 — 스킵", post_id, i)
                continue

            result = await self._generate_single_clip(scene, i, post_id)
            self.results.append(result)

            if result.success:
                scene.video_clip_path = str(result.clip_path)
                clip_count += 1
            else:
                scene.video_generation_failed = True

        # 3. 실패 씬 병합
        scenes = self._merge_failed_scenes(scenes, self.results)

        return scenes

    async def _generate_single_clip(
        self,
        scene,
        scene_index: int,
        post_id: int,
    ) -> VideoGenerationResult:
        """단일 씬의 비디오 클립을 생성한다.

        4단계 재시도 전략:
        1차: 1280×720, 97프레임, 20스텝, 풀 모델
        2차: 프롬프트 단순화, 동일 설정
        3차: 768×512, 65프레임, 15스텝, 풀 모델
        4차: 768×512, 65프레임, 8스텝, Distilled 폴백 (CFG=1.0)
        모두 실패: VideoGenerationResult(success=False) 반환
        """
        max_attempts = self.config.get("VIDEO_MAX_RETRY", 4)
        result = VideoGenerationResult(scene_index=scene_index, success=False)

        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            try:
                if attempt == 1:
                    width, height = self.config["VIDEO_RESOLUTION"]
                    num_frames = self.config["VIDEO_NUM_FRAMES"]
                    steps = self.config.get("VIDEO_STEPS", 20)
                    cfg = self.config.get("VIDEO_CFG", 3.5)
                    prompt = scene.video_prompt
                    use_distilled = False
                elif attempt == 2:
                    prompt = self.prompt_engine.simplify_prompt(scene.video_prompt)
                    width, height = self.config["VIDEO_RESOLUTION"]
                    num_frames = self.config["VIDEO_NUM_FRAMES"]
                    steps = self.config.get("VIDEO_STEPS", 20)
                    cfg = self.config.get("VIDEO_CFG", 3.5)
                    use_distilled = False
                    logger.warning(
                        "[video] post=%d 씬=%d 프롬프트 단순화 재시도 (attempt=%d)",
                        post_id, scene_index, attempt,
                    )
                elif attempt == 3:
                    prompt = self.prompt_engine.simplify_prompt(scene.video_prompt)
                    width, height = self.config.get("VIDEO_RESOLUTION_FALLBACK", (768, 512))
                    num_frames = self.config.get("VIDEO_NUM_FRAMES_FALLBACK", 65)
                    steps = 15
                    cfg = self.config.get("VIDEO_CFG", 3.5)
                    use_distilled = False
                    logger.warning(
                        "[video] post=%d 씬=%d 해상도 다운그레이드 재시도 %dx%d (attempt=%d)",
                        post_id, scene_index, width, height, attempt,
                    )
                else:
                    # 4차: Distilled 폴백
                    prompt = self.prompt_engine.simplify_prompt(scene.video_prompt)
                    width, height = self.config.get("VIDEO_RESOLUTION_FALLBACK", (768, 512))
                    num_frames = self.config.get("VIDEO_NUM_FRAMES_FALLBACK", 65)
                    steps = self.config.get("VIDEO_STEPS_DISTILLED", 8)
                    cfg = self.config.get("VIDEO_CFG_DISTILLED", 1.0)
                    use_distilled = True
                    logger.warning(
                        "[video] post=%d 씬=%d Distilled 폴백 재시도 %dx%d (attempt=%d)",
                        post_id, scene_index, width, height, attempt,
                    )

                fps = self.config.get("VIDEO_FPS", 24)

                if scene.video_mode == "t2v":
                    clip_path = await self.comfy.generate_t2v(
                        prompt=prompt,
                        width=width, height=height,
                        num_frames=num_frames,
                        fps=fps,
                        steps=steps,
                        cfg=cfg,
                        use_distilled=use_distilled,
                    )
                elif scene.video_mode == "i2v":
                    clip_path = await self.comfy.generate_i2v(
                        prompt=prompt,
                        init_image_path=Path(scene.video_init_image),
                        width=width, height=height,
                        num_frames=num_frames,
                        fps=fps,
                        steps=steps,
                        cfg=cfg,
                    )
                else:
                    result.failure_reason = f"unknown video_mode: {scene.video_mode}"
                    break

                result.success = True
                result.clip_path = clip_path
                logger.info(
                    "[video] post=%d 씬=%d 클립 생성 성공 (attempt=%d, path=%s)",
                    post_id, scene_index, attempt, clip_path,
                )
                return result

            except Exception as e:
                error_str = str(e).lower()
                result.failure_reason = str(e)
                logger.error(
                    "[video] post=%d 씬=%d attempt=%d 실패: %s",
                    post_id, scene_index, attempt, e,
                )

                if "out of memory" in error_str:
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except ImportError:
                        pass
                    gc.collect()

                if attempt < max_attempts:
                    continue

        logger.error(
            "[video] post=%d 씬=%d 최종 실패 (%d회 시도) — 씬 삭제 예정. 사유: %s",
            post_id, scene_index, max_attempts, result.failure_reason,
        )
        return result

    def _merge_failed_scenes(
        self,
        scenes: list,
        results: list[VideoGenerationResult],
    ) -> list:
        """실패한 씬을 삭제하고 text_lines를 인접 씬에 병합한다.

        병합 규칙:
        - 실패 씬의 text_lines를 직전 성공 씬에 append
        - 직전 씬이 없으면(첫 씬이 실패) 직후 성공 씬에 prepend
        - 연속 실패 시 가장 가까운 성공 씬에 모두 병합
        - 병합된 text_lines는 원본 순서를 유지
        """
        success_indices = {r.scene_index for r in results if r.success}
        failed_indices = [r.scene_index for r in results if not r.success]

        if not failed_indices:
            return scenes

        # 병합 매핑 생성: failed_idx → merge_target_idx
        merge_map: dict[int, int] = {}
        for fi in failed_indices:
            target = None
            for j in range(fi - 1, -1, -1):
                if j in success_indices or j not in [f for f in failed_indices]:
                    target = j
                    break
            if target is None:
                for j in range(fi + 1, len(scenes)):
                    if j in success_indices or j not in [f for f in failed_indices]:
                        target = j
                        break
            if target is not None:
                merge_map[fi] = target

        # 병합 실행
        for fi, ti in merge_map.items():
            if fi >= len(scenes) or ti >= len(scenes):
                continue
            failed_scene = scenes[fi]
            target_scene = scenes[ti]

            failed_lines: list = []
            for line in failed_scene.text_lines:
                if isinstance(line, dict):
                    failed_lines.append(line)
                else:
                    failed_lines.append({"text": str(line), "audio": None})

            if fi < ti:
                target_scene.text_lines = failed_lines + list(target_scene.text_lines)
            else:
                target_scene.text_lines = list(target_scene.text_lines) + failed_lines

            logger.info(
                "[video] 씬 %d 삭제 → 씬 %d에 %d줄 병합 (사유: %s)",
                fi, ti, len(failed_lines),
                next((r.failure_reason for r in results if r.scene_index == fi), "unknown"),
            )

        # 실패 씬 제거 (뒤에서부터 제거해야 인덱스 안 밀림)
        for fi in sorted(failed_indices, reverse=True):
            if fi < len(scenes):
                scenes.pop(fi)

        logger.info(
            "[video] 병합 완료: %d개 씬 삭제, 최종 %d개 씬 유지",
            len(failed_indices), len(scenes),
        )
        return scenes
