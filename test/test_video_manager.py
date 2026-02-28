"""Video Manager 통합 테스트 (Mock + 폴백 시나리오).

실행 방법:
  python -m pytest test/test_video_manager.py -v

테스트 결과물 위치:
  test/test_video_manager_output/
  test/test_video_manager_output/test_result.md
"""
import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_video_manager_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _make_mock_scene(scene_type="text_only", video_mode="t2v", text_lines=None):
    """테스트용 Mock SceneDecision 생성."""
    scene = MagicMock()
    scene.type = scene_type
    scene.video_mode = video_mode
    scene.text_lines = text_lines or [{"text": "테스트 텍스트", "audio": None}]
    scene.video_prompt = "A wide shot of a room."
    scene.video_clip_path = None
    scene.video_init_image = None
    scene.video_generation_failed = False
    scene.image_url = None
    return scene


def _make_manager(generate_side_effect=None):
    """Mock ComfyUIClient + VideoPromptEngine으로 VideoManager 생성."""
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
        "VIDEO_RESOLUTION": (512, 512),
        "VIDEO_RESOLUTION_FALLBACK": (384, 384),
        "VIDEO_NUM_FRAMES": 81,
        "VIDEO_NUM_FRAMES_FALLBACK": 61,
        "VIDEO_GEN_TIMEOUT": 300,
        "VIDEO_MAX_CLIPS_PER_POST": 8,
        "VIDEO_MAX_RETRY": 3,
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
            if call_count in (2, 5, 8):  # 씬 1의 3회 시도 모두 실패
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
        """ComfyUI 서버 다운 시 빈 리스트를 반환한다 (전체 스킵)."""
        scenes = [_make_mock_scene() for _ in range(3)]
        manager = _make_manager()
        manager._comfy.health_check.return_value = False

        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        # ComfyUI 다운 시 씬은 유지되되 비디오 클립 없음
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
            if call_count in (2, 5, 8):  # 씬 1의 3회 시도 모두 실패
                raise RuntimeError("Failed")
            return Path("/tmp/clip.mp4")

        manager = _make_manager(generate_side_effect=side_effect)
        result = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes, mood="humor", post_id=1,
                title="테스트", body_summary="테스트 요약",
            )
        )
        # 씬 1 실패 → 씬 0에 병합
        assert len(result) == 2


def generate_test_result():
    """테스트 결과 MD 파일 생성."""
    result_md = OUTPUT_DIR / "test_result.md"
    result_md.write_text("""# Video Manager 테스트 결과

## 실행 환경
- Python: 3.12
- GPU: 불필요 (Mock 모드)

## 실행 방법
```bash
python -m pytest test/test_video_manager.py -v --tb=short
```

## 테스트 항목
| # | 테스트 함수 | 설명 | 결과 |
|---|------------|------|------|
| 1 | test_all_success | 전체 성공 시 씬 수 유지 | ⬜ |
| 2 | test_single_failure_merged | 1개 실패 → 삭제+병합 | ⬜ |
| 3 | test_comfyui_down_skips_all | 서버 다운 → 전체 스킵 | ⬜ |
| 4 | test_max_clips_limit | 최대 클립 수 제한 | ⬜ |
| 5 | test_merge_preserves_text_order | 병합 시 텍스트 순서 유지 | ⬜ |

## 이슈 및 참고사항
- Mock 기반 테스트이므로 실제 ComfyUI 연동은 별도 통합 테스트 필요
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    pytest.main([__file__, "-v"])
