"""Fish Speech TTS 심화 진단 — 워밍업 부족 + 참조 텍스트 prefix 현상 테스트.

실행: python test/test_fish_speech_diag.py

테스트 항목:
  A. 워밍업 없이 바로 합성 → 중국어 혼입 확인
  B. 참조 텍스트를 빈 문자열로 합성 → 중국어 출력 확인
  C. 워밍업 후 합성 → 정상 품질 확인
  D. 참조 텍스트 변형 → prefix 생성 여부 확인

결과물: _result/tts_test/diag/ 에 WAV + 로그 저장
"""
import asyncio
import base64
import json
import logging
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    FISH_SPEECH_TIMEOUT,
    FISH_SPEECH_URL,
    TTS_OUTPUT_FORMAT,
    VOICE_DEFAULT,
    VOICE_PRESETS,
    VOICE_REFERENCE_TEXTS,
)

FISH_URL = FISH_SPEECH_URL.replace("fish-speech", "localhost")
OUTPUT_DIR = PROJECT_ROOT / "_result" / "tts_test" / "diag"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
VOICES_DIR = PROJECT_ROOT / "assets" / "voices"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUTPUT_DIR / "diag_log.txt", mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("diag")

# 테스트 문장 (동일 문장으로 조건만 다르게)
SAMPLE_TEXT = "오늘 소개할 이야기는 정말 놀라운 사연입니다."

_timeout = httpx.Timeout(connect=10.0, write=FISH_SPEECH_TIMEOUT, read=FISH_SPEECH_TIMEOUT, pool=5.0)


def _get_references(voice_key: str = VOICE_DEFAULT, ref_text_override: str | None = None) -> list[dict]:
    """참조 오디오 + 텍스트 페이로드."""
    ref_filename = VOICE_PRESETS.get(voice_key, VOICE_PRESETS[VOICE_DEFAULT])
    ref_audio = VOICES_DIR / ref_filename
    if not ref_audio.exists():
        return []
    ref_text = ref_text_override if ref_text_override is not None else VOICE_REFERENCE_TEXTS.get(voice_key, "")
    audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode()
    return [{"audio": audio_b64, "text": ref_text}]


async def _do_tts(label: str, text: str, references: list[dict], filename: str) -> dict:
    """단일 TTS 요청 + 결과 기록."""
    out = OUTPUT_DIR / filename
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=_timeout) as c:
            resp = await c.post(f"{FISH_URL}/v1/tts", json={"text": text, "format": TTS_OUTPUT_FORMAT, "references": references})
            resp.raise_for_status()
        elapsed = time.time() - t0
        out.write_bytes(resp.content)
        size_kb = len(resp.content) // 1024

        # ffprobe로 정확한 오디오 길이
        import subprocess
        dur_str = subprocess.run(
            ["ffprobe", "-i", str(out), "-show_entries", "format=duration", "-v", "quiet", "-of", "csv=p=0"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dur = float(dur_str) if dur_str else 0.0

        logger.info("  [%s] %dKB, %.1f초 오디오, %.1f초 소요 → %s", label, size_kb, dur, elapsed, filename)
        return {"label": label, "text": text, "size_kb": size_kb, "duration_s": round(dur, 2), "elapsed_s": round(elapsed, 2), "file": filename, "status": "ok"}
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("  [%s] 실패: %s (%.1fs)", label, exc, elapsed)
        return {"label": label, "text": text, "elapsed_s": round(elapsed, 2), "status": f"error: {exc}"}


async def main() -> None:
    logger.info("Fish Speech 심화 진단 시작")
    logger.info("서버: %s", FISH_URL)
    logger.info("테스트 문장: %r", SAMPLE_TEXT)
    logger.info("")

    results = []

    # ── A. 참조 오디오 + 정상 참조 텍스트 (기본 워밍업 된 상태) ──
    logger.info("=" * 60)
    logger.info("A. 참조 오디오 + 정상 참조 텍스트 (기본)")
    logger.info("=" * 60)
    refs_normal = _get_references()
    r = await _do_tts("A_normal", SAMPLE_TEXT, refs_normal, "A_normal.wav")
    results.append(r)

    # ── B. 참조 오디오 + 빈 참조 텍스트 → 중국어 출력 예상 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("B. 참조 오디오 + 빈 참조 텍스트")
    logger.info("=" * 60)
    refs_empty = _get_references(ref_text_override="")
    r = await _do_tts("B_empty_ref_text", SAMPLE_TEXT, refs_empty, "B_empty_ref_text.wav")
    results.append(r)

    # ── C. 참조 오디오 없음 (zero-shot 없이) → 기본 중국어 음성 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("C. 참조 오디오 없음 (기본 음성)")
    logger.info("=" * 60)
    r = await _do_tts("C_no_ref", SAMPLE_TEXT, [], "C_no_ref.wav")
    results.append(r)

    # ── D. 참조 텍스트에 의도적으로 다른 텍스트 → prefix 생성 여부 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("D. 참조 텍스트 불일치 (다른 텍스트)")
    logger.info("=" * 60)
    refs_mismatch = _get_references(ref_text_override="이것은 완전히 다른 문장입니다. 참조 텍스트 불일치 테스트.")
    r = await _do_tts("D_mismatch", SAMPLE_TEXT, refs_mismatch, "D_mismatch.wav")
    results.append(r)

    # ── E. 다양한 보이스 프리셋 (anna, han) ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("E. 다른 보이스 프리셋 (anna, han)")
    logger.info("=" * 60)
    for voice_key in ["anna", "han"]:
        refs_voice = _get_references(voice_key=voice_key)
        r = await _do_tts(f"E_{voice_key}", SAMPLE_TEXT, refs_voice, f"E_{voice_key}.wav")
        results.append(r)

    # ── 결과 저장 ──
    result_path = OUTPUT_DIR / "diag_results.json"
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("")
    logger.info("=" * 60)
    logger.info("진단 결과 요약")
    logger.info("=" * 60)
    logger.info("%-20s %8s %8s %8s %s", "라벨", "크기KB", "길이s", "소요s", "상태")
    logger.info("-" * 60)
    for r in results:
        logger.info(
            "%-20s %8s %8s %8s %s",
            r["label"],
            r.get("size_kb", "-"),
            r.get("duration_s", "-"),
            r["elapsed_s"],
            r["status"],
        )
    logger.info("")
    logger.info("결과: %s", OUTPUT_DIR)
    logger.info("")
    logger.info("── 청취 포인트 ──")
    logger.info("A_normal.wav:         정상 기준 (참조 텍스트 일치)")
    logger.info("B_empty_ref_text.wav: 참조 텍스트 빈 문자열 → 중국어 혼입?")
    logger.info("C_no_ref.wav:         참조 오디오 없음 → 기본 음성(중국어?)")
    logger.info("D_mismatch.wav:       참조 텍스트 불일치 → prefix 발화?")
    logger.info("E_anna/han.wav:       다른 보이스 → 품질 비교")


if __name__ == "__main__":
    asyncio.run(main())
