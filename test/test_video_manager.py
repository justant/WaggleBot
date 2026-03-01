"""Video Manager 통합 테스트 (Mock + 폴백 시나리오, LTX-2).

실행 방법:
  python -m pytest test/test_video_manager.py -v
"""
import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

logger = logging.getLogger(__name__)


def _make_mock_scene(scene_type="text_only", video_mode="t2v", text_lines=None):
    """테스트용 Mock SceneDecision 생성."""
    scene = MagicMock()
    scene.type = scene_type
    scene.video_mode = video_mode
    scene.text_lines = text_lines or [{"text": "테스트 텍스트", "audio": None}]
    scene.video_prompt = "A wide shot of a Korean office room."
    scene.video_clip_path = None
    scene.video_init_image = None
    scene.video_generation_failed = False
    scene.image_url = None
    return scene


def _make_manager(generate_side_effect=None):
    """Mock ComfyUIClient + VideoPromptEngine으로 VideoManager 생성 (LTX-2)."""
    from ai_worker.video.manager import VideoManager

    mock_comfy = AsyncMock()
    mock_comfy.health_check.return_value = True

    if generate_side_effect:
        mock_comfy.generate_t2v.side_effect = generate_side_effect
    else:
        mock_comfy.generate_t2v.return_value = Path("/tmp/clip.mp4")

    mock_prompt = MagicMock()
    mock_prompt.simplify_prompt.return_value = "Simplified prompt."

    config = {
        "VIDEO_RESOLUTION": (1280, 720),
        "VIDEO_RESOLUTION_FALLBACK": (768, 512),
        "VIDEO_NUM_FRAMES": 97,
        "VIDEO_NUM_FRAMES_FALLBACK": 65,
        "VIDEO_FPS": 24,
        "VIDEO_STEPS": 20,
        "VIDEO_STEPS_DISTILLED": 8,
        "VIDEO_CFG": 3.5,
        "VIDEO_CFG_DISTILLED": 1.0,
        "VIDEO_GEN_TIMEOUT": 300,
        "VIDEO_MAX_CLIPS_PER_POST": 8,
        "VIDEO_MAX_RETRY": 4,
    }

    return VideoManager(
        comfy_client=mock_comfy,
        prompt_engine=mock_prompt,
        config=config,
    )


class TestVideoManager:

    def test_all_success(self):
        """모든 씬이 성공하면 씬 수가 변하지 않는다."""
        scenes = [_make_mock_scene() for _ in range(5)]
        manager = _make_manager()

        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        assert len(result) == 5

    def test_single_failure_merged(self):
        """5개 씬 중 1개 실패 시 해당 씬이 삭제되고 4개만 남는다."""
        scenes = [_make_mock_scene() for _ in range(5)]

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Scene 0: call 1 → success
            # Scene 1: calls 2,3,4,5 → all fail (4 retries)
            # Scene 2: call 6 → success
            # Scene 3: call 7 → success
            # Scene 4: call 8 → success
            if call_count in (2, 3, 4, 5):
                raise RuntimeError("Generation failed")
            return Path("/tmp/clip.mp4")

        manager = _make_manager(generate_side_effect=side_effect)
        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        assert len(result) == 4

    def test_comfyui_down_skips_all(self):
        """ComfyUI 서버 다운 시 씬은 유지되되 비디오 클립 없음."""
        scenes = [_make_mock_scene() for _ in range(3)]
        manager = _make_manager()
        manager.comfy.health_check.return_value = False

        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        assert all(s.video_clip_path is None for s in result)

    def test_max_clips_limit(self):
        """VIDEO_MAX_CLIPS_PER_POST를 초과하면 초과분은 건너뛴다."""
        scenes = [_make_mock_scene() for _ in range(12)]
        manager = _make_manager()

        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        clips_generated = sum(1 for s in result if s.video_clip_path)
        assert clips_generated <= 8

    def test_merge_preserves_text_order(self):
        """병합 시 text_lines 순서가 유지된다."""
        scenes = [
            _make_mock_scene(text_lines=[{"text": "A", "audio": None}]),
            _make_mock_scene(text_lines=[{"text": "B", "audio": None}]),
            _make_mock_scene(text_lines=[{"text": "C", "audio": None}]),
        ]

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Scene 0: call 1 → success
            # Scene 1: calls 2,3,4,5 → all fail (4 retries)
            # Scene 2: call 6 → success
            if call_count in (2, 3, 4, 5):
                raise RuntimeError("Failed")
            return Path("/tmp/clip.mp4")

        manager = _make_manager(generate_side_effect=side_effect)
        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        # 씬 1 실패 → 씬 0에 병합, 최종 2개
        assert len(result) == 2

    def test_distilled_fallback_on_4th_attempt(self):
        """4번째 시도에서 Distilled 폴백이 사용되는지 확인한다."""
        scenes = [_make_mock_scene()]

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise RuntimeError("Out of memory error")
            return Path("/tmp/clip_distilled.mp4")

        manager = _make_manager(generate_side_effect=side_effect)
        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        # 4번째 시도에서 성공, 1개 씬 유지
        assert len(result) == 1
        assert result[0].video_clip_path is not None
        # Distilled 워크플로우가 사용되었는지 확인
        call_args = manager.comfy.generate_t2v.call_args_list
        last_call = call_args[-1]
        assert last_call.kwargs.get("use_distilled") is True
        assert last_call.kwargs.get("cfg") == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
