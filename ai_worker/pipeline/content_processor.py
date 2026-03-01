"""Phase 5: 전체 통합 진입점 (Content Processor)

Phase 1 ~ 5를 순서대로 실행해 SceneDecision 목록을 반환한다.

    [Phase 1] analyze_resources  - ResourceProfile
    [Phase 2] chunk_with_llm     - raw script dict
    [Phase 3] validate_and_fix   - validated script dict
    [Phase 4] SceneDirector      - list[SceneDecision]
    [Phase 5] TTS 사전 생성      - scene.text_lines[j] = {"text": str, "audio": str|None}
"""
import collections
import json as _json
import logging
from pathlib import Path

from ai_worker.pipeline.llm_chunker import chunk_with_llm
from ai_worker.pipeline.resource_analyzer import ResourceProfile, analyze_resources
from ai_worker.pipeline.scene_director import SceneDecision, SceneDirector
from ai_worker.pipeline.text_validator import validate_and_fix
from ai_worker.tts.fish_client import synthesize

logger = logging.getLogger(__name__)


async def process_content(post, images: list[str], cfg: dict | None = None) -> list[SceneDecision]:
    """콘텐츠 처리 전체 파이프라인.

    Args:
        post:   Post 객체 (post.content, post.title 사용)
        images: 이미지 URL/경로 목록
        cfg:    파이프라인 설정 dict (선택). comment_voices 등.

    Returns:
        렌더러에 전달할 SceneDecision 목록.
        Phase 5 이후 scene.text_lines 요소는 {"text": str, "audio": str|None} dict.

    Raises:
        requests.RequestException: Ollama 통신 오류
        ValueError: 대본 필수 키 누락 등 검증 실패
    """
    _cfg = cfg or {}
    comment_voices: list[str] = _json.loads(_cfg.get("comment_voices", "[]"))
    # ── Phase 1: 자원 분석 ────────────────────────────────────────
    profile: ResourceProfile = analyze_resources(post, images)
    logger.info(
        "[content_processor] Phase 1 완료: 전략=%s 이미지=%d 예상문장≈%d",
        profile.strategy, profile.image_count, profile.estimated_sentences,
    )

    # ── Phase 2: LLM 청킹 (의미 단위) ────────────────────────────
    llm_output: dict = await chunk_with_llm(post.content or "", profile)

    # ── Phase 3: 물리적 검증 (max_chars 보정) ─────────────────────
    script: dict = validate_and_fix(llm_output)
    logger.info(
        "[content_processor] Phase 3 완료: hook(%d자) + body(%d줄) + closer(%d자)",
        len(script.get("hook", "")),
        len(script.get("body", [])),
        len(script.get("closer", "")),
    )

    # ── Phase 4: 씬 배분 ──────────────────────────────────────────
    director = SceneDirector(profile, images, script, comment_voices=comment_voices)
    scenes: list[SceneDecision] = director.direct()

    counter = collections.Counter(s.type for s in scenes)
    logger.info(
        "[content_processor] Phase 4 완료: %d씬 구성 %s",
        len(scenes), dict(counter),
    )

    # ── Phase 4.5: video_mode 할당 ────────────────────────────────
    from config.settings import VIDEO_GEN_ENABLED

    if VIDEO_GEN_ENABLED:
        from ai_worker.pipeline.scene_director import assign_video_modes
        from config.settings import VIDEO_I2V_THRESHOLD, MEDIA_DIR

        image_cache_dir = MEDIA_DIR / "tmp" / f"vid_img_cache_{post.id}"
        image_cache_dir.mkdir(parents=True, exist_ok=True)

        scenes = assign_video_modes(
            scenes=scenes,
            image_cache_dir=image_cache_dir,
            i2v_threshold=VIDEO_I2V_THRESHOLD,
        )
        logger.info("[content_processor] Phase 4.5 완료: video_mode 할당")
    else:
        logger.info("[content_processor] VIDEO_GEN_ENABLED=false — Phase 4.5 스킵")

    # ── Phase 5: TTS 사전 생성 ────────────────────────────────────
    tts_ok = 0
    tts_fail = 0
    for scene in scenes:
        for j, line in enumerate(scene.text_lines):
            text = line if isinstance(line, str) else line.get("text", "")
            try:
                tts_kwargs: dict = {"text": text, "scene_type": scene.type}
                if scene.voice_override:
                    tts_kwargs["voice_key"] = scene.voice_override
                audio_path = await synthesize(**tts_kwargs)
                scene.text_lines[j] = {"text": text, "audio": str(audio_path)}
                tts_ok += 1
            except Exception as exc:
                logger.warning(
                    "[content_processor] TTS 실패 (씬=%s, 줄=%d): %s",
                    scene.type, j, exc,
                )
                scene.text_lines[j] = {"text": text, "audio": None}
                tts_fail += 1

    logger.info(
        "[content_processor] Phase 5 완료: TTS 성공=%d 실패=%d",
        tts_ok, tts_fail,
    )

    # ── Phase 6: Video Prompt 생성 (GPU 불필요, LLM CPU 호출) ─────
    if VIDEO_GEN_ENABLED:
        from ai_worker.video.prompt_engine import VideoPromptEngine

        logger.info("[content_processor] Phase 6: video_prompt 생성 시작")
        prompt_engine = VideoPromptEngine()

        body_texts: list[str] = []
        for block in list(script.get("body", [])):
            if isinstance(block, dict):
                body_texts.extend(block.get("lines", []))
            else:
                body_texts.append(str(block))
        body_summary = " ".join(body_texts)[:500]

        scenes = prompt_engine.generate_batch(
            scenes=scenes,
            mood=script.get("mood", "daily"),
            title=post.title or "",
            body_summary=body_summary,
        )
        logger.info(
            "[content_processor] Phase 6 완료: %d개 프롬프트 생성",
            sum(1 for s in scenes if s.video_prompt),
        )
    else:
        logger.info("[content_processor] VIDEO_GEN_ENABLED=false — Phase 6 스킵")

    # ── Phase 7: Video Clip 생성 (GPU 필요, ComfyUI 경유) ─────────
    if VIDEO_GEN_ENABLED:
        import gc

        # ★ 2막 전환: LLM + TTS VRAM 완전 해제 시퀀스
        await _clear_vram_for_video()

        from ai_worker.video.manager import VideoManager
        from ai_worker.video.comfy_client import ComfyUIClient
        from config.settings import (
            get_comfyui_url, VIDEO_GEN_TIMEOUT, VIDEO_GEN_TIMEOUT_DISTILLED,
            VIDEO_RESOLUTION,
            VIDEO_RESOLUTION_FALLBACK, VIDEO_NUM_FRAMES, VIDEO_NUM_FRAMES_FALLBACK,
            VIDEO_MAX_CLIPS_PER_POST, VIDEO_MAX_RETRY,
        )

        logger.info("[content_processor] Phase 7: 비디오 클립 생성 시작 (VRAM 해제 완료)")

        comfy = ComfyUIClient(base_url=get_comfyui_url())
        video_config = {
            "VIDEO_RESOLUTION": VIDEO_RESOLUTION,
            "VIDEO_RESOLUTION_FALLBACK": VIDEO_RESOLUTION_FALLBACK,
            "VIDEO_NUM_FRAMES": VIDEO_NUM_FRAMES,
            "VIDEO_NUM_FRAMES_FALLBACK": VIDEO_NUM_FRAMES_FALLBACK,
            "VIDEO_GEN_TIMEOUT": VIDEO_GEN_TIMEOUT,
            "VIDEO_GEN_TIMEOUT_DISTILLED": VIDEO_GEN_TIMEOUT_DISTILLED,
            "VIDEO_MAX_CLIPS_PER_POST": VIDEO_MAX_CLIPS_PER_POST,
            "VIDEO_MAX_RETRY": VIDEO_MAX_RETRY,
        }

        manager = VideoManager(
            comfy_client=comfy,
            prompt_engine=prompt_engine,
            config=video_config,
        )

        scenes = await manager.generate_all_clips(
            scenes=scenes,
            mood=script.get("mood", "daily"),
            post_id=post.id,
            title=post.title or "",
            body_summary=body_summary,
        )

        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()

        video_ok = sum(1 for s in scenes if s.video_clip_path)
        video_fail = sum(1 for r in manager.results if not r.success)
        logger.info(
            "[content_processor] Phase 7 완료: 성공=%d, 실패(삭제)=%d, 최종 씬 수=%d",
            video_ok, video_fail, len(scenes),
        )
    else:
        logger.info("[content_processor] VIDEO_GEN_ENABLED=false — Phase 7 스킵")

    return scenes


async def _clear_vram_for_video() -> None:
    """Phase 7 진입 전 — LLM/TTS VRAM 완전 해제 시퀀스.

    1막(LLM+TTS) → 2막(비디오) 전환 시 Ollama와 Fish Speech의 VRAM을
    명시적으로 해제하고, nvidia-smi로 실제 여유 VRAM을 확인한다.
    """
    import gc

    from ai_worker.gpu_manager import GPUMemoryManager, get_gpu_manager
    from config.settings import get_ollama_host, OLLAMA_MODEL, FISH_SPEECH_URL

    logger.info("[VRAM] 2막 전환 시작: LLM/TTS VRAM 해제 시퀀스")

    # 1) Ollama 강제 언로드 (keep_alive=0 → VRAM 즉시 해제)
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{get_ollama_host()}/api/generate",
                json={"model": OLLAMA_MODEL, "keep_alive": 0},
                timeout=10.0,
            )
        logger.info("[VRAM] Ollama 모델 언로드 완료 (keep_alive=0)")
    except Exception as e:
        logger.warning("[VRAM] Ollama 언로드 실패 (계속 진행): %s", e)

    # 2) Fish Speech 모델 언로드 — 컨테이너는 유지하되 GPU 메모리만 해제
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{FISH_SPEECH_URL}/v1/models/unload",
                timeout=10.0,
            )
            if resp.status_code in (200, 404):
                logger.info("[VRAM] Fish Speech 모델 언로드 완료")
            else:
                logger.warning("[VRAM] Fish Speech 언로드 응답: %d", resp.status_code)
    except Exception as e:
        logger.warning("[VRAM] Fish Speech 언로드 실패 (계속 진행): %s", e)

    # 3) ai_worker 자체 PyTorch 캐시 정리
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass
    gc.collect()

    # 4) 실제 여유 VRAM 확인 (nvidia-smi 기반 — 모든 프로세스 포함)
    _gm = get_gpu_manager()
    free_vram = GPUMemoryManager.get_system_available_vram()
    if free_vram > 0:
        logger.info("[VRAM] 시스템 여유 VRAM: %.1fGB", free_vram)
        if free_vram < 20.0:
            logger.warning(
                "[VRAM] 여유 VRAM 부족 (%.1fGB < 20GB) — 긴급 정리 시도", free_vram,
            )
            _gm.emergency_cleanup()
            free_vram = GPUMemoryManager.get_system_available_vram()
            logger.info("[VRAM] 긴급 정리 후 여유 VRAM: %.1fGB", free_vram)
    else:
        # nvidia-smi 사용 불가 시 PyTorch 기준 폴백
        free_vram = _gm.get_available_vram()
        logger.info("[VRAM] PyTorch 기준 여유 VRAM: %.1fGB (nvidia-smi 사용 불가)", free_vram)

    logger.info("[VRAM] 2막 전환 완료: %.1fGB 여유", free_vram)
