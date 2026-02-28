"""이미지 필터 적합성 판별 테스트.

실행 방법:
  python -m pytest test/test_image_filter.py -v

테스트 결과물 위치:
  test/test_image_filter_output/
  test/test_image_filter_output/test_result.md
"""
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_image_filter_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _create_test_images():
    """테스트용 합성 이미지를 생성한다."""
    # 1. 좋은 사진 시뮬레이션 (다양한 색상 + 복잡한 엣지)
    arr = np.random.randint(50, 200, (768, 1024, 3), dtype=np.uint8)
    Image.fromarray(arr).save(OUTPUT_DIR / "photo_good.jpg")

    # 2. 텍스트 캡처 시뮬레이션 (흰 배경 + 검은 텍스트 = bimodal)
    text_img = np.full((600, 800, 3), 250, dtype=np.uint8)
    text_img[100:500, 100:700] = 20
    Image.fromarray(text_img).save(OUTPUT_DIR / "text_capture.png")

    # 3. 밈 시뮬레이션 (단색 배경)
    meme = np.full((400, 400, 3), 128, dtype=np.uint8)
    Image.fromarray(meme).save(OUTPUT_DIR / "meme_simple.jpg")

    # 4. 너무 작은 이미지
    tiny = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
    Image.fromarray(tiny).save(OUTPUT_DIR / "tiny_image.jpg")


class TestImageFilter:

    def test_photo_high_score(self):
        """풍경/인물 사진이 score >= 0.6으로 평가된다."""
        from ai_worker.video.image_filter import evaluate_image
        _create_test_images()
        result = evaluate_image(OUTPUT_DIR / "photo_good.jpg")
        assert result.score >= 0.6, f"Expected >= 0.6, got {result.score}"
        assert result.category == "photo"

    def test_text_capture_low_score(self):
        """텍스트 캡처본이 score < 0.4로 평가된다."""
        from ai_worker.video.image_filter import evaluate_image
        _create_test_images()
        result = evaluate_image(OUTPUT_DIR / "text_capture.png")
        assert result.score < 0.4, f"Expected < 0.4, got {result.score}"
        assert result.category == "text_capture"

    def test_meme_low_score(self):
        """단색 밈이 score < 0.5로 평가된다."""
        from ai_worker.video.image_filter import evaluate_image
        _create_test_images()
        result = evaluate_image(OUTPUT_DIR / "meme_simple.jpg")
        assert result.score < 0.5, f"Expected < 0.5, got {result.score}"

    def test_tiny_image_penalty(self):
        """64x64 이미지에 해상도 패널티가 적용된다."""
        from ai_worker.video.image_filter import evaluate_image
        _create_test_images()
        result = evaluate_image(OUTPUT_DIR / "tiny_image.jpg")
        assert "resolution" in result.reason.lower() or result.score < 0.5

    def test_nonexistent_file(self):
        """존재하지 않는 파일에 대해 score=0.0을 반환한다."""
        from ai_worker.video.image_filter import evaluate_image
        result = evaluate_image(Path("/nonexistent/file.jpg"))
        assert result.score == 0.0
        assert result.category == "unknown"


def generate_test_result():
    """테스트 결과 MD 파일 생성."""
    result_md = OUTPUT_DIR / "test_result.md"
    result_md.write_text("""# Image Filter 테스트 결과

## 실행 환경
- Python: 3.12
- GPU: 불필요

## 실행 방법
```bash
python -m pytest test/test_image_filter.py -v --tb=short
```

## 테스트 항목
| # | 테스트 함수 | 설명 | 결과 |
|---|------------|------|------|
| 1 | test_photo_high_score | 사진 score >= 0.6 | ⬜ |
| 2 | test_text_capture_low_score | 텍스트 캡처 score < 0.4 | ⬜ |
| 3 | test_meme_low_score | 단색 밈 score < 0.5 | ⬜ |
| 4 | test_tiny_image_penalty | 해상도 패널티 | ⬜ |
| 5 | test_nonexistent_file | 없는 파일 score=0.0 | ⬜ |

## 출력 파일
- `photo_good.jpg`: 합성 사진 (적합)
- `text_capture.png`: 텍스트 캡처 (부적합)
- `meme_simple.jpg`: 단색 밈 (부적합)
- `tiny_image.jpg`: 64x64 소형 이미지
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    import pytest
    pytest.main([__file__, "-v"])
