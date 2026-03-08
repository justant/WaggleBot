"""Phase 1: 자원 분석 (Resource Analyzer) + TTS 예상 시간 계산

게시글 텍스트 길이와 이미지 수 비율을 분석해
LLM 청킹 전략(image_heavy / balanced / text_heavy)을 결정한다.

Phase 4-A에서 사용하는 TTS 예상 시간 계산 함수도 포함한다.
"""
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# 한국어 평균 문장당 글자 수 (경험적 기준값)
_KO_CHARS_PER_SENTENCE = 25

# 한국어 평균 발화 속도 (TTS 1.2배속 후처리 포함)
KOREAN_CHARS_PER_SEC = 4.0

Strategy = Literal["image_heavy", "balanced", "text_heavy"]


@dataclass
class ResourceProfile:
    image_count: int
    text_length: int          # 원문 글자 수
    estimated_sentences: int  # 예상 문장 수
    ratio: float              # 이미지 / 문장 비율
    strategy: Strategy


def analyze_resources(post, images: list[str]) -> ResourceProfile:
    """게시글과 이미지 목록을 분석해 ResourceProfile을 반환한다.

    Args:
        post:   Post 객체 (post.content 사용)
        images: 이미지 URL/경로 목록

    Returns:
        ResourceProfile (strategy: image_heavy | balanced | text_heavy)
    """
    image_count = len(images)
    text_length = len(post.content or "")
    estimated_sentences = max(1, text_length // _KO_CHARS_PER_SENTENCE)
    ratio = image_count / estimated_sentences if estimated_sentences > 0 else 0.0

    if ratio >= 0.7:
        strategy: Strategy = "image_heavy"   # 거의 모든 문장에 이미지
    elif ratio >= 0.3:
        strategy = "balanced"              # 중요 문장에만 이미지
    else:
        strategy = "text_heavy"            # 텍스트 위주, 이미지 절약

    profile = ResourceProfile(
        image_count=image_count,
        text_length=text_length,
        estimated_sentences=estimated_sentences,
        ratio=round(ratio, 3),
        strategy=strategy,
    )
    logger.debug(
        "자원 분석: 이미지=%d, 글자=%d, 예상문장=%d, 비율=%.2f, 전략=%s",
        image_count, text_length, estimated_sentences, ratio, strategy,
    )
    return profile


def estimate_tts_duration(text: str) -> float:
    """씬 텍스트의 TTS 예상 시간(초)을 반환한다.

    한국어 평균 발화 속도 약 4자/초 기준.
    소수점 첫째 자리까지 반올림하여 LLM 프롬프트 가독성을 높인다.
    """
    clean_text = text.strip()
    if not clean_text:
        return 0.0
    char_count = len(clean_text)
    return round(char_count / KOREAN_CHARS_PER_SEC, 1)
