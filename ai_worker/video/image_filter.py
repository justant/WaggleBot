"""I2V(Image-to-Video) 적합성 판별 모듈.

크롤링된 이미지를 분석하여 LTX-Video I2V 입력으로 사용할 수 있는지 평가한다.
텍스트 캡처본, 밈, 저해상도 이미지를 자동으로 걸러낸다.

적합성 점수 0.0 ~ 1.0:
  >= 0.6: I2V 후보 (Init Image로 사용)
  < 0.6: I2V 부적합 (T2V로 전환 또는 씬 삭제)

의존성: Pillow, numpy (이미 requirements.txt에 있음)
추가 의존성 없음.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)


@dataclass
class ImageSuitability:
    """I2V 적합성 평가 결과."""
    score: float           # 0.0 ~ 1.0
    reason: str            # "suitable" 또는 콤마 구분 부적합 사유
    category: str          # "photo", "screenshot", "meme", "text_capture", "diagram", "unknown"
    width: int
    height: int


def evaluate_image(image_path: Path) -> ImageSuitability:
    """이미지 파일의 I2V 적합성을 평가한다.

    평가 기준 (각 항목의 가중치 합계 = 1.0):
    1. 해상도 (0.20): min(w,h) >= 512
    2. 종횡비 (0.20): max/min <= 2.0
    3. 텍스트 밀도 (0.30): 텍스트 캡처본이 아닌지
    4. 색상 다양성 (0.15): 단색/밈이 아닌지
    5. 엣지 밀도 (0.15): 피사체가 뚜렷한지

    파일 열기 실패 시 score=0.0, reason="file_error" 반환.
    """
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        logger.warning("[image_filter] 이미지 열기 실패: %s — %s", image_path, e)
        return ImageSuitability(score=0.0, reason="file_error", category="unknown", width=0, height=0)

    w, h = img.size
    score = 0.0
    reasons: list[str] = []

    # 1. 해상도
    if min(w, h) >= 512:
        score += 0.20
    elif min(w, h) >= 256:
        score += 0.10
        reasons.append("low_resolution")
    else:
        reasons.append("very_low_resolution")

    # 2. 종횡비
    ratio = max(w, h) / max(min(w, h), 1)
    if ratio <= 2.0:
        score += 0.20
    elif ratio <= 3.0:
        score += 0.10
        reasons.append("wide_aspect_ratio")
    else:
        reasons.append("extreme_aspect_ratio")

    # 3. 텍스트 밀도
    if not _is_text_heavy(img):
        score += 0.30
    else:
        reasons.append("text_heavy_image")

    # 4. 색상 다양성
    diversity = _color_diversity(img)
    if diversity > 30:
        score += 0.15
    elif diversity > 15:
        score += 0.07
        reasons.append("low_color_diversity")
    else:
        reasons.append("very_low_color_diversity")

    # 5. 엣지 밀도
    edges = _edge_density(img)
    if edges > 0.05:
        score += 0.15
    elif edges > 0.02:
        score += 0.07
        reasons.append("low_edge_density")
    else:
        reasons.append("flat_image")

    category = _classify_image(img, reasons)

    result = ImageSuitability(
        score=round(score, 3),
        reason=", ".join(reasons) if reasons else "suitable",
        category=category,
        width=w,
        height=h,
    )
    logger.debug(
        "[image_filter] %s: score=%.3f category=%s reason=%s",
        image_path.name, result.score, result.category, result.reason,
    )
    return result


def _is_text_heavy(img: Image.Image) -> bool:
    """히스토그램 기반 텍스트 캡처 감지.
    텍스트 캡처본: 배경 단색 + 텍스트 = 양극단 bimodal 분포.
    """
    gray = img.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    if total == 0:
        return False
    low_end = sum(hist[:25])
    high_end = sum(hist[230:])
    bimodal_ratio = (low_end + high_end) / total
    return bimodal_ratio > 0.70


def _color_diversity(img: Image.Image) -> float:
    """RGB 채널별 표준편차 평균. 높을수록 색상이 다양."""
    import numpy as np
    arr = np.array(img)
    return float(np.mean([arr[:, :, c].std() for c in range(3)]))


def _edge_density(img: Image.Image) -> float:
    """엣지 픽셀 비율 (0.0~1.0). 높을수록 디테일이 풍부."""
    import numpy as np
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    arr = np.array(edges)
    return float((arr > 30).sum() / max(arr.size, 1))


def _classify_image(img: Image.Image, reasons: list[str]) -> str:
    """이미지를 카테고리로 분류한다."""
    if "text_heavy_image" in reasons:
        return "text_capture"
    if "very_low_color_diversity" in reasons and "flat_image" in reasons:
        return "meme"
    if "very_low_resolution" in reasons:
        return "screenshot"
    w, h = img.size
    if w == h and min(w, h) < 200:
        return "diagram"
    return "photo"
