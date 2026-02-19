"""Fish Speech 1.5 TTS 클라이언트.

실행 방식: HTTP API 서버 (fish-speech 컨테이너) 호출.
참조 오디오: assets/voices/ 내 WAV 파일로 zero-shot 클로닝.
"""
import asyncio
import base64
import logging
import re
from pathlib import Path

import httpx

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

VOICES_DIR = Path(__file__).parent.parent / "assets" / "voices"


def _normalize_for_tts(text: str) -> str:
    """TTS 전달 전 한국어 인터넷 슬랭/이모티콘 정규화.

    Fish Speech 토크나이저가 처리하지 못하는 비표준 문자를 제거해
    중국어 폴백 발음과 어색한 합성을 방지한다.
    """
    # 화자 접두어 제거: "ㅇㅇ: ", "댓글: " 등 (최대 8자 + 콜론)
    text = re.sub(r'^[가-힣ㄱ-ㅎㅏ-ㅣ\w]{1,8}:\s+', '', text)
    # 자음 반복 이모티콘 제거 (ㅋ, ㅎ, ㄷ, ㅠ, ㅜ 2회 이상 연속)
    text = re.sub(r'[ㅋㅎㄷㅠㅜ]{2,}', '', text)
    # ^ 이모티콘 제거 (^^, ^^^)
    text = re.sub(r'\^+', '', text)
    # 연속 물결 축약 (~~~~ → ~)
    text = re.sub(r'~{2,}', '~', text)
    return text.strip()


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

    logger.info(
        "TTS 생성 완료: scene=%s text=%d자→%d자 → %s (%dKB)",
        scene_type, len(text), len(final_text), output_path.name, len(resp.content) // 1024,
    )
    return output_path


async def wait_for_fish_speech(retries: int = 10, delay: float = 5.0) -> bool:
    """Fish Speech 서버 기동 대기 (컨테이너 시작 직후 호출).

    Args:
        retries: 최대 재시도 횟수
        delay:   재시도 간격 (초)

    Returns:
        True if 서버 준비 완료, False if 최종 실패
    """
    async with httpx.AsyncClient(timeout=5) as client:
        for i in range(retries):
            try:
                r = await client.get(f"{FISH_SPEECH_URL}/")
                if r.status_code < 400:
                    logger.info("Fish Speech 서버 준비 완료 (%s)", FISH_SPEECH_URL)
                    return True
            except httpx.ConnectError:
                logger.warning(
                    "Fish Speech 대기 중 (%d/%d) — %s",
                    i + 1, retries, FISH_SPEECH_URL,
                )
            await asyncio.sleep(delay)
    logger.error("Fish Speech 서버 연결 실패: %s", FISH_SPEECH_URL)
    return False
