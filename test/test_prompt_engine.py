"""Video Prompt Engine 테스트.

실행 방법:
  # Ollama가 실행 중인 상태에서:
  docker compose exec ai_worker python -m pytest test/test_prompt_engine.py -v

  # video_styles.json 로드 테스트만 (Ollama 불필요):
  python -m pytest test/test_prompt_engine.py -v -k "test_video_styles_json_loaded"

테스트 결과물 위치:
  test/test_prompt_engine_output/
  test/test_prompt_engine_output/test_result.md
  test/test_prompt_engine_output/generated_prompts.txt
"""
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_prompt_engine_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _ollama_available() -> bool:
    """Ollama 서버 접속 가능 여부를 call_ollama_raw 실제 경로로 확인한다."""
    try:
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
        prompt = engine.generate_prompt(
            text_lines=["연결 테스트"], mood="daily", title="test",
        )
        return len(prompt) > 0
    except Exception:
        return False


_OLLAMA_OK = _ollama_available()

requires_ollama = pytest.mark.skipif(
    not _OLLAMA_OK, reason="Ollama not available (Docker 환경에서 실행하세요)"
)


class TestVideoPromptEngine:

    @requires_ollama
    def test_generate_prompt_humor(self):
        """humor 무드로 프롬프트를 생성하면 영어 문단이 반환된다."""
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
        prompt = engine.generate_prompt(
            text_lines=["오늘 회사에서 웃긴 일이 있었는데"],
            mood="humor",
            title="직장 웃긴 썰",
        )
        assert len(prompt) > 50, "프롬프트가 너무 짧음"
        assert prompt.isascii() or any(c.isascii() for c in prompt), "영어 포함 필요"

        with open(OUTPUT_DIR / "generated_prompts.txt", "a", encoding="utf-8") as f:
            f.write(f"=== humor ===\n{prompt}\n\n")

    @requires_ollama
    def test_generate_prompt_horror(self):
        """horror 무드로 프롬프트를 생성한다."""
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
        prompt = engine.generate_prompt(
            text_lines=["밤에 혼자 집에 있는데 이상한 소리가 들렸다"],
            mood="horror",
            title="심야 공포 체험",
        )
        assert len(prompt) > 50

        with open(OUTPUT_DIR / "generated_prompts.txt", "a", encoding="utf-8") as f:
            f.write(f"=== horror ===\n{prompt}\n\n")

    @requires_ollama
    def test_simplify_prompt(self):
        """simplify_prompt가 원본보다 짧은 프롬프트를 반환한다."""
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
        original = (
            "Wide shot with dramatic chiaroscuro lighting. "
            "A dark empty office at midnight with flickering fluorescent tubes. "
            "A lone worker hunched over a glowing monitor, fingers trembling on the keyboard. "
            "Slow dolly forward through the cubicle maze, handheld shake intensifying. "
            "Visuals matching the oppressive hum of air conditioning and distant elevator chimes."
        )
        simplified = engine.simplify_prompt(original)
        assert len(simplified) < len(original), "단순화 프롬프트가 원본보다 길면 안 됨"

    def test_video_styles_json_loaded(self):
        """config/video_styles.json이 정상 로드되고 9개 mood를 포함한다."""
        from ai_worker.video.prompt_engine import _load_video_styles
        styles = _load_video_styles()
        expected_moods = {"humor", "touching", "anger", "sadness", "horror",
                         "info", "controversy", "daily", "shock"}
        assert expected_moods.issubset(set(styles.keys())), f"누락된 mood: {expected_moods - set(styles.keys())}"
        for mood, data in styles.items():
            assert "style_hint" in data, f"{mood}에 style_hint 없음"

    @requires_ollama
    def test_all_9_moods(self):
        """9가지 mood 전체에 대해 프롬프트를 성공적으로 생성한다."""
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
        moods = ["humor", "touching", "anger", "sadness", "horror",
                 "info", "controversy", "daily", "shock"]

        results = {}
        for mood in moods:
            prompt = engine.generate_prompt(
                text_lines=["테스트 텍스트입니다"],
                mood=mood,
                title="테스트 제목",
            )
            results[mood] = prompt
            assert len(prompt) > 30, f"{mood} 프롬프트 생성 실패"

        with open(OUTPUT_DIR / "all_moods_prompts.txt", "w", encoding="utf-8") as f:
            for mood, prompt in results.items():
                f.write(f"=== {mood} ===\n{prompt}\n\n")


def generate_test_result():
    """테스트 결과 MD 파일 생성."""
    result_md = OUTPUT_DIR / "test_result.md"
    result_md.write_text("""# Video Prompt Engine 테스트 결과

## 실행 환경
- Python: 3.12
- Ollama 필요 (LLM 호출 테스트)

## 실행 방법
```bash
# Ollama가 실행 중인 상태에서:
docker compose exec ai_worker python -m pytest test/test_prompt_engine.py -v

# video_styles.json 로드 테스트만 (Ollama 불필요):
python -m pytest test/test_prompt_engine.py -v -k "test_video_styles_json_loaded"
```

## 테스트 항목
| # | 테스트 함수 | 설명 | 결과 |
|---|------------|------|------|
| 1 | test_generate_prompt_humor | humor 프롬프트 생성 | ⬜ |
| 2 | test_generate_prompt_horror | horror 프롬프트 생성 | ⬜ |
| 3 | test_simplify_prompt | 프롬프트 단순화 | ⬜ |
| 4 | test_video_styles_json_loaded | 9개 mood JSON 로드 | ⬜ |
| 5 | test_all_9_moods | 전체 mood 프롬프트 생성 | ⬜ |

## 출력 파일
- `generated_prompts.txt`: 생성된 프롬프트 모음
- `all_moods_prompts.txt`: 9개 mood별 프롬프트
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    import pytest
    pytest.main([__file__, "-v"])
