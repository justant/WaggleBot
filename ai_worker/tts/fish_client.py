"""Fish Speech 1.5 TTS 클라이언트.

실행 방식: HTTP API 서버 (fish-speech 컨테이너) 호출.
참조 오디오: assets/voices/ 내 WAV 파일로 zero-shot 클로닝.
"""
import asyncio
import base64
import json
import logging
import re
import subprocess
import threading
from pathlib import Path

import httpx

# Fish Speech 서버는 단일 GPU 모델이므로 동시 요청을 처리하지 못한다.
# render_stage(스레드 풀)와 llm_tts_stage(메인 이벤트 루프)가 동시에 요청을 보내면
# baize.ClientDisconnect 오류가 발생하므로, threading.Lock으로 전역 직렬화한다.
_FISH_SPEECH_LOCK = threading.Lock()

from config.settings import (
    EMOTION_TAGS,
    FISH_SPEECH_TIMEOUT,
    FISH_SPEECH_URL,
    TTS_OUTPUT_FORMAT,
    VOICE_DEFAULT,
    VOICE_PRESETS,
    VOICE_REFERENCE_TEXTS,
)

logger = logging.getLogger(__name__)

VOICES_DIR = Path(__file__).parent.parent.parent / "assets" / "voices"

# ── 선택적 라이브러리 (미설치 시 내장 구현으로 폴백) ──
try:
    from soynlp.normalizer import repeat_normalize as _soynlp_repeat_normalize
    _SOYNLP_AVAILABLE = True
    logger.debug("soynlp 로드 완료")
except ImportError:
    _SOYNLP_AVAILABLE = False

# ── 인터넷 축약어 → 표준어 사전 ──
_SLANG_MAP_PATH = Path(__file__).parent.parent.parent / "assets" / "slang_map.json"

# 내장 사전 (slang_map.json 없을 때 폴백)
_SLANG_MAP_BUILTIN: dict[str, str] = {
    "남친": "남자친구", "여친": "여자친구",
    "베댓": "베스트 댓글",
    "갑분싸": "갑자기 분위기 싸해짐",
    "시모": "시어머니", "장모": "장모님",
    "솔까말": "솔직히 까놓고 말해서",
    "킹받": "화가 남", "억까": "억지로 까는",
    "ㄹㅇ": "진짜", "ㅇㅇ": "응", "ㄴㄴ": "아니",
    "ㅇㅋ": "오케이", "ㄱㅅ": "감사", "ㅈㅅ": "죄송",
    "ㅂㅂ": "바이바이", "ㄷㄷ": "",
    "TMI": "티엠아이", "MBTI": "엠비티아이",
}


def _load_slang_map() -> dict[str, str]:
    """assets/slang_map.json에서 축약어 사전 로드. 파일 없으면 내장 사전 사용."""
    if _SLANG_MAP_PATH.exists():
        try:
            loaded: dict[str, str] = json.loads(_SLANG_MAP_PATH.read_text(encoding="utf-8"))
            logger.debug("slang_map.json 로드: %d 항목", len(loaded))
            return loaded
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("slang_map.json 로드 실패, 내장 사전 사용: %s", exc)
    return dict(_SLANG_MAP_BUILTIN)


_SLANG_MAP: dict[str, str] = _load_slang_map()

# ── 숫자 읽기 변환 테이블 ──
_SINO_DIGITS: dict[str, str] = {
    "0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
    "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구",
}
_SINO_UNITS = ["", "십", "백", "천"]
_SINO_LARGE = ["", "만", "억", "조"]

# 고유어 수사 — 단위 앞 형태 (한/두/세/네, 스물/서른…)
_NATIVE_MAP: dict[int, str] = {
    1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯",
    6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉", 10: "열",
    20: "스물", 30: "서른", 40: "마흔", 50: "쉰",
    60: "예순", 70: "일흔", 80: "여든", 90: "아흔",
}

# 단위별 수사 분류
_NATIVE_COUNTERS: frozenset[str] = frozenset({
    "살", "세", "명", "개", "시", "잔", "번", "마리", "벌", "켤레",
})
_SINO_COUNTERS: frozenset[str] = frozenset({
    "년", "월", "일", "원", "호", "층", "도", "분", "초",
    "km", "kg", "cm", "mm", "대", "위", "회", "장", "편", "권", "쪽", "점",
})


def _sino_number(n: int) -> str:
    """정수를 한자어 수사로 변환 (4자리씩 만/억/조 처리)."""
    if n == 0:
        return "영"
    if n < 0:
        return "마이너스 " + _sino_number(-n)

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


def _native_number(n: int) -> str:
    """1~99 고유어 수사 변환 (단위 앞 형태). 100 이상은 한자어 폴백."""
    if n > 99 or n <= 0:
        return _sino_number(n)
    tens = (n // 10) * 10
    ones = n % 10
    result = ""
    if tens > 0:
        result += _NATIVE_MAP.get(tens, _sino_number(tens))
    if ones > 0:
        result += _NATIVE_MAP.get(ones, _sino_number(ones))
    return result


def _has_jongseong(char: str) -> bool:
    """받침 유무 확인 (유니코드 연산)."""
    if not char or not ('가' <= char <= '힣'):
        return False
    return (ord(char) - 0xAC00) % 28 != 0


def _fix_particles(text: str) -> str:
    """받침 유무에 따른 조사 자동 교정.

    축약어 치환 후 받침이 달라져 조사가 맞지 않는 경우 교정.
    예: '남친과' → (축약어 치환) → '남자친구과' → (교정) → '남자친구와'
    """
    particle_pairs = [
        ("과", "와"),
        ("은", "는"),
        ("이", "가"),
        ("을", "를"),
        ("으로", "로"),
        ("아", "야"),
    ]
    for with_jong, without_jong in particle_pairs:
        pattern = re.compile(
            r'([가-힣])('
            + re.escape(with_jong) + '|' + re.escape(without_jong)
            + r')(?=\s|$|[^가-힣])'
        )
        def _replace(m, wj=with_jong, woj=without_jong) -> str:
            return m.group(1) + (wj if _has_jongseong(m.group(1)) else woj)
        text = pattern.sub(_replace, text)
    return text


def _convert_number_with_counter(match: re.Match) -> str:  # type: ignore[type-arg]
    """숫자+단위 조합을 한국어 읽기로 변환하는 re.sub 콜백."""
    try:
        n = int(match.group(1))
    except ValueError:
        return match.group(0)
    counter = match.group(2)
    if counter in _NATIVE_COUNTERS:
        return _native_number(n) + " " + counter
    return _sino_number(n) + " " + counter


def _convert_standalone_number(match: re.Match) -> str:  # type: ignore[type-arg]
    """단독 숫자를 한자어 수사로 변환하는 re.sub 콜백."""
    try:
        return _sino_number(int(match.group(0)))
    except ValueError:
        return match.group(0)


def _normalize_for_tts(text: str) -> str:
    """TTS 전달 전 한국어 인터넷 슬랭/이모티콘 정규화.

    Fish Speech 토크나이저가 처리하지 못하는 비표준 문자를 제거해
    중국어 폴백 발음과 어색한 합성을 방지한다.
    """
    # 0. 화자 접두어 제거: "ㅇㅇ: ", "댓글: " 등
    text = re.sub(r'^[가-힣A-Za-z0-9]{1,8}:\s*', '', text)

    # 1. 인터넷 축약어 치환 (긴 키워드 우선)
    for slang, replacement in sorted(_SLANG_MAP.items(), key=lambda x: -len(x[0])):
        text = text.replace(slang, replacement)

    # 1-1. 조사 자동 교정 (축약어 치환 후 받침 변경 대응)
    text = _fix_particles(text)

    # 1-2. soynlp: 반복 자모 정규화 — 삭제 전 반복 횟수 통일 (ㅋㅋㅋㅋ → ㅋ)
    if _SOYNLP_AVAILABLE:
        try:
            text = _soynlp_repeat_normalize(text, num_repeats=1)
        except Exception:
            logger.warning("soynlp repeat_normalize 실패, 건너뜀")

    # 2. 자모 이모티콘 제거
    text = re.sub(r'[ㅋㅎㅠㅜㅡ]{2,}', '', text)  # 2회 이상 반복 자모 삭제
    text = re.sub(r'[ㄱ-ㅎㅏ-ㅣ]+', '', text)       # 나머지 단독 자모 삭제
    text = re.sub(r'\^+', '', text)                  # ^ 이모티콘 제거

    # 3. 숫자 → 한국어 읽기 변환 (built-in만 사용)
    # ⚠️ g2pk는 전체 텍스트를 발음형으로 변환하므로 Fish Speech와 이중 G2P 충돌 발생.
    #    Fish Speech는 자체 G2P가 있으므로 표준 맞춤법 텍스트를 입력해야 한다.
    all_counters = "|".join(
        re.escape(c) for c in sorted(_NATIVE_COUNTERS | _SINO_COUNTERS, key=len, reverse=True)
    )
    text = re.sub(rf'(\d+)\s*({all_counters})', _convert_number_with_counter, text)
    text = re.sub(r'(\d+)\s*%', lambda m: _sino_number(int(m.group(1))) + " 퍼센트", text)
    text = re.sub(r'\d+', _convert_standalone_number, text)

    # 4. 특수문자 정리
    text = text.replace("。", ".").replace("、", ",")   # 중국어/일본어 구두점
    text = text.replace("~", " ").replace("～", " ")    # 물결표 → 공백
    text = re.sub(r"['\"""''「」『』【】`]", " ", text)   # 따옴표/인용부호 → 공백
    text = re.sub(r'\.{3,}', '…', text)                 # 말줄임표 통일
    text = re.sub(r'\s+', ' ', text)                    # 연속 공백 정리

    # 5. 문장 완성 — 마침표 없는 끝에 마침표 추가 (프로소디 안정화)
    text = text.strip()
    if text and text[-1] not in '.!?…':
        text += '.'

    return text


def _post_process_audio(path: Path) -> None:
    """FFmpeg 후처리: 무음 단축 + 1.2배속 (피치 보존).

    1. silenceremove — 단어 사이 200ms 이상 무음 구간 제거
    2. atempo=1.2   — WSOLA 알고리즘으로 피치 변화 없이 배속만 1.2배
                      (목소리 톤·음색 유지, 발화 속도만 빨라짐)

    ffmpeg 미설치 환경은 조용히 건너뜀.
    """
    tmp = path.with_name(path.stem + "_proc.wav")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-af", "silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-50dB,atempo=1.2",
                str(tmp),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(path)
            logger.debug("오디오 후처리 완료 (무음단축+1.2배속): %s", path.name)
        else:
            logger.warning(
                "오디오 후처리 실패 (rc=%d): %s",
                result.returncode,
                result.stderr[-200:].decode(errors="replace") if result.stderr else "",
            )
    except FileNotFoundError:
        logger.debug("ffmpeg 미설치 — 오디오 후처리 건너뜀")
    except Exception as exc:
        logger.warning("오디오 후처리 오류: %s", exc)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


async def synthesize(
    text: str,
    scene_type: str = "img_text",
    voice_key: str = VOICE_DEFAULT,
    output_path: Path | None = None,
) -> Path:
    """Fish Speech API로 TTS를 생성하고 wav 파일 경로를 반환한다.

    Args:
        text:        읽을 텍스트
        scene_type:  씬 타입 (감정 태그 자동 결정 — EMOTION_TAGS 참조)
        voice_key:   VOICE_PRESETS 키
        output_path: 저장 경로 (None이면 /tmp 임시파일)

    Returns:
        생성된 wav 파일 경로

    Raises:
        httpx.HTTPStatusError: Fish Speech 서버 오류 (5xx)
    """
    # 텍스트 전처리: 슬랭/이모티콘 제거
    normalized = _normalize_for_tts(text)
    # 감정 태그 prefix (EMOTION_TAGS 모두 빈 문자열 → 현재 no-op)
    emotion = EMOTION_TAGS.get(scene_type, "")
    final_text = f"{emotion} {normalized}".strip() if emotion else normalized

    # 참조 오디오 경로
    ref_filename = VOICE_PRESETS.get(voice_key, VOICE_PRESETS[VOICE_DEFAULT])
    ref_audio = VOICES_DIR / ref_filename

    # 출력 경로
    if output_path is None:
        output_path = Path(f"/tmp/tts_{abs(hash(final_text))}.{TTS_OUTPUT_FORMAT}")

    # 참조 오디오가 있으면 zero-shot 클로닝, 없으면 기본 음성으로 폴백
    if ref_audio.exists():
        ref_text = VOICE_REFERENCE_TEXTS.get(voice_key, VOICE_REFERENCE_TEXTS.get(VOICE_DEFAULT, ""))
        audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode()
        references = [{"audio": audio_b64, "text": ref_text}]
    else:
        logger.warning("참조 오디오 없음 — 기본 음성으로 폴백: %s", ref_audio)
        references = []

    # write timeout 을 read 와 별도 지정 — 대형 base64 참조 오디오 전송 중
    # Fish Speech 가 앞선 요청을 처리 중일 때 write phase 에서 타임아웃 방지
    _timeout = httpx.Timeout(
        connect=10.0,
        write=FISH_SPEECH_TIMEOUT,
        read=FISH_SPEECH_TIMEOUT,
        pool=5.0,
    )
    # render_stage(스레드 풀)와 llm_tts_stage(메인 루프)가 동시에 fish-speech에
    # 요청을 보내면 ClientDisconnect가 발생한다. run_in_executor로 블로킹 락
    # 획득을 이벤트 루프 밖으로 위임해 직렬화한다.
    _loop = asyncio.get_running_loop()
    await _loop.run_in_executor(None, _FISH_SPEECH_LOCK.acquire)
    try:
        async with httpx.AsyncClient(timeout=_timeout) as client:
            resp = await client.post(
                f"{FISH_SPEECH_URL}/v1/tts",
                json={
                    "text": final_text,
                    "format": TTS_OUTPUT_FORMAT,
                    "references": references,
                },
            )
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
    finally:
        _FISH_SPEECH_LOCK.release()

    _post_process_audio(output_path)

    logger.info(
        "TTS 생성 완료: scene=%s text=%d자→%d자 → %s (%dKB)",
        scene_type, len(text), len(final_text), output_path.name, len(resp.content) // 1024,
    )
    return output_path


async def _warmup_model() -> None:
    """Fish Speech TTS 모델 + 음성 클로닝 모듈 웜업.

    Fish Speech는 첫 번째 TTS 요청이 들어올 때 모델을 lazy-load 한다.
    로딩 중에 들어온 요청은 voice cloning이 적용되지 않아 기본 중국어
    음성으로 출력될 수 있으므로, 실제 합성 전에 짧은 웜업 요청을 보내
    모델을 완전히 로드시킨다.
    """
    ref_filename = VOICE_PRESETS.get(VOICE_DEFAULT, "")
    ref_audio = VOICES_DIR / ref_filename
    if ref_audio.exists():
        ref_text = VOICE_REFERENCE_TEXTS.get(VOICE_DEFAULT, "")
        audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode()
        references = [{"audio": audio_b64, "text": ref_text}]
    else:
        references = []

    _timeout = httpx.Timeout(
        connect=10.0,
        write=FISH_SPEECH_TIMEOUT,
        read=FISH_SPEECH_TIMEOUT,
        pool=5.0,
    )
    _loop = asyncio.get_running_loop()
    await _loop.run_in_executor(None, _FISH_SPEECH_LOCK.acquire)
    try:
        async with httpx.AsyncClient(timeout=_timeout) as client:
            try:
                await client.post(
                    f"{FISH_SPEECH_URL}/v1/tts",
                    json={"text": "안녕하세요.", "format": TTS_OUTPUT_FORMAT, "references": references},
                )
                logger.info("Fish Speech 모델 웜업 완료")
            except Exception as exc:
                logger.warning("Fish Speech 웜업 실패 (무시): %s", exc)
    finally:
        _FISH_SPEECH_LOCK.release()


async def wait_for_fish_speech(retries: int = 10, delay: float = 5.0) -> bool:
    """Fish Speech 서버 기동 대기 (컨테이너 시작 직후 호출).

    HTTP 연결 확인 후 모델 웜업까지 완료해야 True를 반환한다.
    웜업 없이 첫 TTS 요청이 들어오면 모델 lazy-load 중에 voice cloning이
    적용되지 않아 중국어 음성이 출력될 수 있다.

    Args:
        retries: 최대 재시도 횟수
        delay:   재시도 간격 (초)

    Returns:
        True if 서버 준비 완료 + 웜업 완료, False if 최종 실패
    """
    async with httpx.AsyncClient(timeout=5) as client:
        for i in range(retries):
            try:
                r = await client.get(f"{FISH_SPEECH_URL}/")
                if r.status_code < 400:
                    logger.info("Fish Speech 서버 준비 완료 (%s)", FISH_SPEECH_URL)
                    await _warmup_model()
                    return True
            except httpx.ConnectError:
                logger.warning(
                    "Fish Speech 대기 중 (%d/%d) — %s",
                    i + 1, retries, FISH_SPEECH_URL,
                )
            await asyncio.sleep(delay)
    logger.error("Fish Speech 서버 연결 실패: %s", FISH_SPEECH_URL)
    return False
