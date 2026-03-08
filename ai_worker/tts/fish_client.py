"""Fish Speech 1.5 TTS 클라이언트.

실행 방식: HTTP API 서버 (fish-speech 컨테이너) 호출.
참조 오디오: assets/voices/ 내 WAV 파일로 zero-shot 클로닝.
"""
import asyncio
import base64
import logging
import subprocess
import threading
from pathlib import Path

import httpx

# Fish Speech 서버는 단일 GPU 모델이므로 동시 요청을 처리하지 못한다.
# render_stage(스레드 풀)와 llm_tts_stage(메인 이벤트 루프)가 동시에 요청을 보내면
# baize.ClientDisconnect 오류가 발생하므로, threading.Lock으로 전역 직렬화한다.
_FISH_SPEECH_LOCK = threading.Lock()

from config.settings import (
    FISH_SPEECH_REPETITION_PENALTY,
    FISH_SPEECH_TEMPERATURE,
    FISH_SPEECH_TIMEOUT,
    FISH_SPEECH_URL,
    TTS_OUTPUT_FORMAT,
    VOICE_DEFAULT,
    VOICE_PRESETS,
    VOICE_REFERENCE_TEXTS,
)
from ai_worker.tts.normalizer import normalize_for_tts

logger = logging.getLogger(__name__)

VOICES_DIR = Path(__file__).parent.parent.parent / "assets" / "voices"

# Fish Speech 첫 음절 garbling 방지용 프라이머 — 비활성화 (2026-03-04).
# warmup 9회(3라운드×3회)로 모델이 충분히 안정화되어 primer 불필요.
# primer를 사용하면 atrim이 실제 음성 시작부를 잘라먹는 부작용이 있었음.
_TTS_PRIMER = ""
_TTS_PRIMER_TRIM_SECS = 0.0

# Fish Speech 짧은 텍스트 중국어 회귀 방지 — 한국어 패딩 (2026-03-08).
# 비기본 음성 + 짧은 텍스트(<20자) 조합 시 한국어 컨텍스트 부족으로
# 중국어 출력이 발생한다. 패딩 문장을 앞에 붙여 한국어 컨텍스트를 확보하고
# 오디오에서 해당 구간을 ffmpeg atrim으로 제거한다.
_MIN_SAFE_TEXT_LEN = 20
_SHORT_TEXT_PREFIX = "다음은 한국어 내용입니다. "
_SHORT_TEXT_PREFIX_LEN = len(_SHORT_TEXT_PREFIX)  # 패딩 문자수 (동적 트림 계산용)


def _post_process_audio(path: Path, trim_prefix_secs: float = 0.0) -> None:
    """FFmpeg 후처리: (선택) 프라이머/패딩 트림 + 무음 단축 + 1.2배속 (피치 보존).

    0. atrim        — 프라이머 또는 짧은 텍스트 패딩 구간 제거
    1. silenceremove — 단어 사이 200ms 이상 무음 구간 제거
    2. atempo=1.2   — WSOLA 알고리즘으로 피치 변화 없이 배속만 1.2배
                      (목소리 톤·음색 유지, 발화 속도만 빨라짐)

    ffmpeg 미설치 환경은 조용히 건너뜀.
    """
    tmp = path.with_name(path.stem + "_proc.wav")
    filters: list[str] = []
    effective_trim = max(_TTS_PRIMER_TRIM_SECS, trim_prefix_secs)
    if effective_trim > 0:
        filters.append(f"atrim=start={effective_trim},asetpts=PTS-STARTPTS")
    filters.append("silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-50dB")
    filters.append("atempo=1.2")
    af_chain = ",".join(filters)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-af", af_chain,
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
    scene_type: str = "image_text",
    voice_key: str = VOICE_DEFAULT,
    output_path: Path | None = None,
    emotion: str = "",
) -> Path:
    """Fish Speech API로 TTS를 생성하고 wav 파일 경로를 반환한다.

    Args:
        text:        읽을 텍스트
        scene_type:  씬 타입 (로깅용)
        voice_key:   VOICE_PRESETS 키
        output_path: 저장 경로 (None이면 /tmp 임시파일)
        emotion:     TTS 감정 톤 키 (예: "gentle", "cheerful"). API가 지원하지 않으면 무시.

    Returns:
        생성된 wav 파일 경로

    Raises:
        httpx.HTTPStatusError: Fish Speech 서버 오류 (5xx)
    """
    # synthesize() 첫 호출 시 자동 warmup
    global _warmup_done
    if not _warmup_done:
        logger.info("synthesize() 첫 호출 — 자동 웜업 실행")
        await _warmup_model()

    # 텍스트 전처리: 슬랭/이모티콘 제거
    normalized = normalize_for_tts(text)

    # 정규화 후 빈 텍스트 가드 — 자음만('ㅂㅁㄱ') 등 정규화 시 전부 제거되는 경우
    stripped = normalized.strip().rstrip(".")
    if not stripped:
        logger.warning("TTS 스킵: 정규화 후 빈 텍스트 (원문: '%s')", text[:50])
        raise ValueError(f"TTS 입력 텍스트가 정규화 후 비어있음 (원문: '{text[:50]}')")

    # primer 비활성화 상태: warmup으로 첫 토큰 안정화 완료.
    # primer가 설정되어 있으면 _post_process_audio()의 atrim이 해당 구간 제거.
    final_text = (_TTS_PRIMER + normalized) if _TTS_PRIMER else normalized

    # 짧은 텍스트 패딩 — 모든 음성에서 짧은 텍스트 중국어 회귀 방지
    _needs_padding = len(final_text) < _MIN_SAFE_TEXT_LEN
    if _needs_padding:
        logger.info(
            "TTS 짧은 텍스트 패딩 적용: voice=%s text='%s' (%d자 < %d자)",
            voice_key, final_text[:30], len(final_text), _MIN_SAFE_TEXT_LEN,
        )
        final_text = _SHORT_TEXT_PREFIX + final_text

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
    # 한국어 발화 속도 기준: ~3~6자/초 → 1자당 최대 0.35초
    # 이보다 현저히 길면 중국어/일본어 혼입으로 판단
    _MAX_SECS_PER_CHAR = 0.35
    _MAX_QUALITY_RETRIES = 2  # 품질 검증 실패 시 추가 재시도

    _loop = asyncio.get_running_loop()
    await _loop.run_in_executor(None, _FISH_SPEECH_LOCK.acquire)
    _MAX_TTS_RETRIES = 2
    _best_audio: bytes | None = None
    _best_ratio: float = float("inf")
    try:
        for _quality_attempt in range(_MAX_QUALITY_RETRIES + 1):
            for _attempt in range(_MAX_TTS_RETRIES + 1):
                try:
                    async with httpx.AsyncClient(timeout=_timeout) as client:
                        payload: dict = {
                            "text": final_text,
                            "format": TTS_OUTPUT_FORMAT,
                            "references": references,
                            "temperature": FISH_SPEECH_TEMPERATURE,
                            "repetition_penalty": FISH_SPEECH_REPETITION_PENALTY,
                            "language": "ko",
                        }
                        resp = await client.post(
                            f"{FISH_SPEECH_URL}/v1/tts",
                            json=payload,
                        )
                        resp.raise_for_status()
                    break  # HTTP 성공 시 재시도 루프 탈출
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500 and _attempt < _MAX_TTS_RETRIES:
                        _wait = 30 * (_attempt + 1)
                        logger.warning(
                            "Fish Speech %d (attempt %d/%d) — %d초 후 재시도",
                            e.response.status_code, _attempt + 1,
                            _MAX_TTS_RETRIES + 1, _wait,
                        )
                        await asyncio.sleep(_wait)
                    else:
                        raise
                except httpx.ReadTimeout:
                    if _attempt < _MAX_TTS_RETRIES:
                        logger.warning(
                            "Fish Speech ReadTimeout (attempt %d/%d) — 즉시 재시도",
                            _attempt + 1, _MAX_TTS_RETRIES + 1,
                        )
                    else:
                        logger.error("Fish Speech ReadTimeout — 최대 재시도 횟수(%d) 초과", _MAX_TTS_RETRIES + 1)
                        raise

            # 오디오 길이 검증 — 중국어/일본어 혼입 감지
            # WAV 헤더(44바이트) 이후 PCM 데이터, 44100Hz 16bit mono 기준
            audio_bytes = len(resp.content) - 44
            audio_secs = audio_bytes / (44100 * 2) if audio_bytes > 0 else 0.0
            text_len = max(len(final_text), 1)
            secs_per_char = audio_secs / text_len

            if secs_per_char <= _MAX_SECS_PER_CHAR or _quality_attempt == _MAX_QUALITY_RETRIES:
                # 정상 범위이거나 최종 시도 → 채택
                if secs_per_char > _MAX_SECS_PER_CHAR and _best_audio is not None:
                    # 최종 시도도 실패했지만 이전에 더 나은 결과가 있으면 사용
                    resp_content = _best_audio
                    logger.warning(
                        "TTS 품질 검증 최종 실패 — 이전 최적 결과 사용 (%.2f초/자)",
                        _best_ratio,
                    )
                else:
                    resp_content = resp.content
                output_path.write_bytes(resp_content)
                break

            # 현재 결과가 이전보다 나으면 보관
            if secs_per_char < _best_ratio:
                _best_ratio = secs_per_char
                _best_audio = resp.content

            logger.warning(
                "TTS 품질 의심: %.1f초/%.0f자 = %.2f초/자 (임계값 %.2f) — 재생성 %d/%d",
                audio_secs, text_len, secs_per_char, _MAX_SECS_PER_CHAR,
                _quality_attempt + 1, _MAX_QUALITY_RETRIES,
            )
    finally:
        _FISH_SPEECH_LOCK.release()

    # 패딩 트림: 실제 발화 속도(secs_per_char)로 패딩 문장 길이만큼 동적 계산
    _dynamic_trim = _SHORT_TEXT_PREFIX_LEN * secs_per_char if _needs_padding else 0.0
    _post_process_audio(output_path, trim_prefix_secs=_dynamic_trim)

    logger.info(
        "TTS 생성 완료: scene=%s emotion=%s text=%d자→%d자 → %s (%dKB, %.2f초/자)%s",
        scene_type, emotion or "—", len(text), len(final_text),
        output_path.name, output_path.stat().st_size // 1024, secs_per_char,
        " [패딩 적용]" if _needs_padding else "",
    )
    return output_path


_warmup_done: bool = False


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
        _warmup_payload = {
            "format": TTS_OUTPUT_FORMAT,
            "references": references,
            "temperature": FISH_SPEECH_TEMPERATURE,
            "repetition_penalty": FISH_SPEECH_REPETITION_PENALTY,
            "language": "ko",
        }
        async with httpx.AsyncClient(timeout=_timeout) as client:
            try:
                # 1회: 모델 로드 트리거 (중국어 출력 흡수)
                await client.post(
                    f"{FISH_SPEECH_URL}/v1/tts",
                    json={**_warmup_payload, "text": "안녕하세요."},
                )
                # 2회: voice cloning 안정화
                await client.post(
                    f"{FISH_SPEECH_URL}/v1/tts",
                    json={**_warmup_payload, "text": "테스트 문장입니다."},
                )
                # 3회: 한국어 컨디셔닝 강화
                await client.post(
                    f"{FISH_SPEECH_URL}/v1/tts",
                    json={**_warmup_payload, "text": "오늘도 좋은 하루 되세요."},
                )
                logger.info("Fish Speech 기본 음성 웜업 완료 (3회)")

                # 비기본 음성 프리셋 웜업 (각 1회)
                # 각 음성의 참조 오디오를 한 번 이상 처리해야 클로닝 안정화
                _warmed_voices = 0
                for _vk, _vf in VOICE_PRESETS.items():
                    if _vk == VOICE_DEFAULT:
                        continue
                    _vpath = VOICES_DIR / _vf
                    if not _vpath.exists():
                        continue
                    _vref_text = VOICE_REFERENCE_TEXTS.get(
                        _vk, VOICE_REFERENCE_TEXTS.get(VOICE_DEFAULT, ""),
                    )
                    _vaudio_b64 = base64.b64encode(_vpath.read_bytes()).decode()
                    _vpayload = {
                        "text": "안녕하세요, 한국어 테스트 문장입니다.",
                        "format": TTS_OUTPUT_FORMAT,
                        "references": [{"audio": _vaudio_b64, "text": _vref_text}],
                        "temperature": FISH_SPEECH_TEMPERATURE,
                        "repetition_penalty": FISH_SPEECH_REPETITION_PENALTY,
                        "language": "ko",
                    }
                    try:
                        await client.post(
                            f"{FISH_SPEECH_URL}/v1/tts",
                            json=_vpayload,
                        )
                        _warmed_voices += 1
                    except Exception as _vexc:
                        logger.warning("음성 프리셋 웜업 실패 (%s): %s", _vk, _vexc)
                logger.info("비기본 음성 프리셋 웜업 완료 (%d개)", _warmed_voices)

                global _warmup_done
                _warmup_done = True
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
