"""ai_worker/tts/number_reader.py — 숫자→한국어 읽기 변환"""

import re
import logging

logger = logging.getLogger(__name__)

# ── 숫자 읽기 변환 테이블 ──
_SINO_DIGITS: dict[str, str] = {
    "0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
    "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구",
}
_SINO_UNITS = ["", "십", "백", "천"]
_SINO_LARGE = ["", "만", "억", "조"]

# 고유어 수사 — 단위 앞 형태 (한/두/세/네, 스물/서른…)
NATIVE_NUMBERS: dict[int, str] = {
    1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯",
    6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉", 10: "열",
    20: "스물", 30: "서른", 40: "마흔", 50: "쉰",
    60: "예순", 70: "일흔", 80: "여든", 90: "아흔",
}

# 단위별 수사 분류
NATIVE_COUNTERS: frozenset[str] = frozenset({
    "살", "세", "명", "개", "시", "잔", "번", "마리", "벌", "켤레",
})
SINO_COUNTERS: frozenset[str] = frozenset({
    "년", "월", "일", "원", "호", "층", "도", "분", "초",
    "km", "kg", "cm", "mm", "대", "위", "회", "장", "편", "권", "쪽", "점",
})


def sino_number(n: int) -> str:
    """정수를 한자어 수사로 변환 (4자리씩 만/억/조 처리)."""
    if n == 0:
        return "영"
    if n < 0:
        return "마이너스 " + sino_number(-n)

    str_n = str(n)
    groups: list[str] = []
    while str_n:
        groups.append(str_n[-4:])
        str_n = str_n[:-4]

    parts: list[str] = []
    for i, group in enumerate(groups):
        group_result = ""
        for j, digit in enumerate(reversed(group)):
            d = int(digit)
            if d == 0:
                continue
            unit = _SINO_UNITS[j]
            # 십/백/천 앞 "일"은 생략 (예: 100 → 백, 1000 → 천)
            if d == 1 and j > 0:
                group_result = unit + group_result
            else:
                group_result = _SINO_DIGITS[str(d)] + unit + group_result
        if group_result:
            parts.append(group_result + _SINO_LARGE[i])

    return "".join(reversed(parts))


def native_number(n: int) -> str:
    """1~99 고유어 수사 변환 (단위 앞 형태). 100 이상은 한자어 폴백."""
    if n > 99 or n <= 0:
        return sino_number(n)
    tens = (n // 10) * 10
    ones = n % 10
    result = ""
    if tens > 0:
        result += NATIVE_NUMBERS.get(tens, sino_number(tens))
    if ones > 0:
        result += NATIVE_NUMBERS.get(ones, sino_number(ones))
    return result


def convert_number_with_counter(match: re.Match) -> str:  # type: ignore[type-arg]
    """숫자+단위 조합을 한국어 읽기로 변환하는 re.sub 콜백."""
    try:
        n = int(match.group(1))
    except ValueError:
        return match.group(0)
    counter = match.group(2)
    if counter in NATIVE_COUNTERS:
        return native_number(n) + " " + counter
    return sino_number(n) + " " + counter


def convert_standalone_number(match: re.Match) -> str:  # type: ignore[type-arg]
    """단독 숫자를 한자어 수사로 변환하는 re.sub 콜백."""
    try:
        return sino_number(int(match.group(0)))
    except ValueError:
        return match.group(0)
