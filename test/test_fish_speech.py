"""Fish Speech TTS 집중 테스트 — 워밍업 + 중국어 혼입 + prefix 이상 검증.

실행: python test/test_fish_speech.py

사전 조건:
  - fish-speech 컨테이너 실행 중 (docker compose up fish-speech)
  - assets/voices/ 에 참조 오디오 존재

결과물: _result/tts_test/ 에 WAV 파일 + 로그 저장
"""
import asyncio
import base64
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# 프로젝트 루트를 sys.path에 추가
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

# edge_tts가 호스트에 없을 수 있으므로 fish_client를 직접 import하지 않고
# _normalize_for_tts만 단독 로드
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "fish_client",
    PROJECT_ROOT / "ai_worker" / "tts" / "fish_client.py",
    submodule_search_locations=[],
)
_mod = _ilu.module_from_spec(_spec)
# config.settings는 이미 import됨 — fish_client가 필요로 하는 모듈 주입
sys.modules.setdefault("config.settings", sys.modules["config.settings"])
try:
    _spec.loader.exec_module(_mod)
    _normalize_for_tts = _mod._normalize_for_tts
except Exception as _e:
    logger.warning("fish_client 로드 실패 (%s), 정규화 없이 진행", _e)
    _normalize_for_tts = lambda text: text  # noqa: E731

# ── 설정 ──
# 호스트에서 실행 시 localhost 사용
FISH_URL = FISH_SPEECH_URL.replace("fish-speech", "localhost")
OUTPUT_DIR = PROJECT_ROOT / "_result" / "tts_test"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VOICES_DIR = PROJECT_ROOT / "assets" / "voices"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUTPUT_DIR / "test_log.txt", mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("fish_test")

# ── 테스트 문장 5개 (다양한 유형) ──
TEST_SENTENCES = [
    {
        "id": 1,
        "text": "안녕하세요, 오늘 소개할 이야기는 정말 놀라운 사연입니다.",
        "desc": "인트로 — 기본 내레이션",
    },
    {
        "id": 2,
        "text": "어제 퇴근길에 편의점에서 있었던 일인데, 진짜 소름 돋았어요.",
        "desc": "본문 — 일상 서술체",
    },
    {
        "id": 3,
        "text": "댓글: 이거 실화냐? ㅋㅋㅋ 나도 비슷한 경험 있음",
        "desc": "댓글 — 인터넷 슬랭 포함",
    },
    {
        "id": 4,
        "text": "3년 전에 200만원짜리 중고차를 샀는데, 알고보니 사고차였습니다.",
        "desc": "숫자 + 한국어 변환 테스트",
    },
    {
        "id": 5,
        "text": "이 영상이 도움이 되셨다면 좋아요와 구독 부탁드립니다. 다음에 또 만나요!",
        "desc": "아웃트로 — 마무리 멘트",
    },
]


def _prepare_references(voice_key: str = VOICE_DEFAULT) -> list[dict]:
    """참조 오디오 + 참조 텍스트 페이로드 생성."""
    ref_filename = VOICE_PRESETS.get(voice_key, VOICE_PRESETS[VOICE_DEFAULT])
    ref_audio = VOICES_DIR / ref_filename
    if not ref_audio.exists():
        logger.warning("참조 오디오 없음: %s", ref_audio)
        return []
    ref_text = VOICE_REFERENCE_TEXTS.get(voice_key, VOICE_REFERENCE_TEXTS.get(VOICE_DEFAULT, ""))
    audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode()
    return [{"audio": audio_b64, "text": ref_text}]


async def check_server() -> bool:
    """Fish Speech 서버 연결 확인."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{FISH_URL}/")
            logger.info("서버 상태: HTTP %d (%s)", r.status_code, FISH_URL)
            return r.status_code < 400
    except Exception as exc:
        logger.error("서버 연결 실패: %s", exc)
        return False


async def warmup_test() -> dict:
    """워밍업 테스트 — 3회 요청, 각 요청의 응답 시간 및 오디오 크기 기록."""
    logger.info("=" * 60)
    logger.info("워밍업 테스트 (3회)")
    logger.info("=" * 60)

    references = _prepare_references()
    warmup_texts = ["안녕하세요.", "테스트 문장입니다.", "오늘 날씨가 좋습니다."]
    results = []

    _timeout = httpx.Timeout(connect=10.0, write=FISH_SPEECH_TIMEOUT, read=FISH_SPEECH_TIMEOUT, pool=5.0)
    async with httpx.AsyncClient(timeout=_timeout) as client:
        for i, text in enumerate(warmup_texts):
            out_path = OUTPUT_DIR / f"warmup_{i+1}.wav"
            t0 = time.time()
            try:
                resp = await client.post(
                    f"{FISH_URL}/v1/tts",
                    json={"text": text, "format": TTS_OUTPUT_FORMAT, "references": references},
                )
                resp.raise_for_status()
                elapsed = time.time() - t0
                out_path.write_bytes(resp.content)
                size_kb = len(resp.content) // 1024
                logger.info(
                    "  워밍업 %d/3: text=%r → %dKB, %.1f초",
                    i + 1, text, size_kb, elapsed,
                )
                results.append({
                    "attempt": i + 1,
                    "text": text,
                    "size_kb": size_kb,
                    "elapsed_s": round(elapsed, 2),
                    "file": str(out_path.name),
                    "status": "ok",
                })
            except Exception as exc:
                elapsed = time.time() - t0
                logger.error("  워밍업 %d/3 실패: %s (%.1fs)", i + 1, exc, elapsed)
                results.append({
                    "attempt": i + 1,
                    "text": text,
                    "elapsed_s": round(elapsed, 2),
                    "status": f"error: {exc}",
                })

    return {"warmup": results}


async def synthesize_test() -> dict:
    """5문장 합성 테스트 — 정규화 전후 텍스트 비교 + 오디오 저장."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("5문장 합성 테스트")
    logger.info("=" * 60)

    references = _prepare_references()
    results = []

    _timeout = httpx.Timeout(connect=10.0, write=FISH_SPEECH_TIMEOUT, read=FISH_SPEECH_TIMEOUT, pool=5.0)
    async with httpx.AsyncClient(timeout=_timeout) as client:
        for item in TEST_SENTENCES:
            idx = item["id"]
            raw_text = item["text"]
            normalized = _normalize_for_tts(raw_text)

            logger.info("")
            logger.info("  [문장 %d] %s", idx, item["desc"])
            logger.info("    원본:    %r", raw_text)
            logger.info("    정규화:  %r", normalized)

            # 실제 API에 전달되는 텍스트 (emotion_tag 없으므로 normalized 그대로)
            final_text = normalized
            logger.info("    최종전달: %r", final_text)

            out_path = OUTPUT_DIR / f"sentence_{idx}.wav"
            t0 = time.time()
            try:
                resp = await client.post(
                    f"{FISH_URL}/v1/tts",
                    json={"text": final_text, "format": TTS_OUTPUT_FORMAT, "references": references},
                )
                resp.raise_for_status()
                elapsed = time.time() - t0
                out_path.write_bytes(resp.content)
                size_kb = len(resp.content) // 1024
                logger.info("    → 생성 완료: %s (%dKB, %.1f초)", out_path.name, size_kb, elapsed)
                results.append({
                    "id": idx,
                    "desc": item["desc"],
                    "raw_text": raw_text,
                    "normalized": normalized,
                    "final_text": final_text,
                    "size_kb": size_kb,
                    "elapsed_s": round(elapsed, 2),
                    "file": str(out_path.name),
                    "status": "ok",
                })
            except Exception as exc:
                elapsed = time.time() - t0
                logger.error("    → 실패: %s (%.1fs)", exc, elapsed)
                results.append({
                    "id": idx,
                    "desc": item["desc"],
                    "raw_text": raw_text,
                    "normalized": normalized,
                    "final_text": final_text,
                    "elapsed_s": round(elapsed, 2),
                    "status": f"error: {exc}",
                })

    return {"sentences": results}


async def no_reference_test() -> dict:
    """참조 오디오 없이 합성 — 중국어 출력 여부 확인."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("참조 오디오 없이 합성 (중국어 출력 비교용)")
    logger.info("=" * 60)

    text = "안녕하세요, 테스트입니다."
    out_path = OUTPUT_DIR / "no_reference.wav"

    _timeout = httpx.Timeout(connect=10.0, write=FISH_SPEECH_TIMEOUT, read=FISH_SPEECH_TIMEOUT, pool=5.0)
    async with httpx.AsyncClient(timeout=_timeout) as client:
        t0 = time.time()
        try:
            resp = await client.post(
                f"{FISH_URL}/v1/tts",
                json={"text": text, "format": TTS_OUTPUT_FORMAT, "references": []},
            )
            resp.raise_for_status()
            elapsed = time.time() - t0
            out_path.write_bytes(resp.content)
            size_kb = len(resp.content) // 1024
            logger.info("  참조없음 합성: %dKB, %.1f초 → %s", size_kb, elapsed, out_path.name)
            return {"no_reference": {"size_kb": size_kb, "elapsed_s": round(elapsed, 2), "file": str(out_path.name), "status": "ok"}}
        except Exception as exc:
            elapsed = time.time() - t0
            logger.error("  참조없음 합성 실패: %s", exc)
            return {"no_reference": {"elapsed_s": round(elapsed, 2), "status": f"error: {exc}"}}


async def main() -> None:
    logger.info("Fish Speech TTS 집중 테스트 시작")
    logger.info("서버: %s", FISH_URL)
    logger.info("출력: %s", OUTPUT_DIR)
    logger.info("")

    # 1) 서버 연결 확인
    if not await check_server():
        logger.error("Fish Speech 서버 연결 불가 — 테스트 중단")
        sys.exit(1)

    # 2) 워밍업
    warmup_results = await warmup_test()

    # 3) 5문장 합성
    sentence_results = await synthesize_test()

    # 4) 참조 없는 합성 (비교용)
    no_ref_results = await no_reference_test()

    # 5) 결과 요약 저장
    all_results = {
        "timestamp": datetime.now().isoformat(),
        "server_url": FISH_URL,
        "voice_key": VOICE_DEFAULT,
        **warmup_results,
        **sentence_results,
        **no_ref_results,
    }
    result_json_path = OUTPUT_DIR / "test_results.json"
    result_json_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 6) 콘솔 요약
    logger.info("")
    logger.info("=" * 60)
    logger.info("테스트 결과 요약")
    logger.info("=" * 60)

    warmup_ok = sum(1 for r in all_results["warmup"] if r["status"] == "ok")
    logger.info("  워밍업: %d/3 성공", warmup_ok)

    sentence_ok = sum(1 for r in all_results["sentences"] if r["status"] == "ok")
    logger.info("  5문장:  %d/5 성공", sentence_ok)

    no_ref_status = all_results["no_reference"]["status"]
    logger.info("  참조없음: %s", no_ref_status)

    logger.info("")
    logger.info("결과 파일: %s", OUTPUT_DIR)
    logger.info("  WAV: warmup_1~3.wav, sentence_1~5.wav, no_reference.wav")
    logger.info("  로그: test_log.txt")
    logger.info("  JSON: test_results.json")

    # 정규화 비교표
    logger.info("")
    logger.info("── 정규화 전후 비교 ──")
    for r in all_results["sentences"]:
        logger.info("  [%d] 원본:   %s", r["id"], r["raw_text"])
        logger.info("      정규화: %s", r["normalized"])
        logger.info("      전달:   %s", r["final_text"])
        logger.info("")


if __name__ == "__main__":
    asyncio.run(main())
