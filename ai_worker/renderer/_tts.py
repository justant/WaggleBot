"""ai_worker/renderer/_tts.py — TTS 청크 생성·병합 로직 (internal)"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_INTRO_PAUSE_SEC: float = 0.5  # 제목 읽기 후 본문 시작 전 숨고르기 (초)


def _get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _unpack_line(item) -> tuple[str, str | None]:
    """text_lines 요소에서 (text, audio_path)를 추출한다.

    content_processor Phase 5 이후 text_lines 요소가
    str → {"text": str, "audio": str|None} dict로 교체되므로 양쪽 형식 모두 처리.
    """
    if isinstance(item, dict):
        return item.get("text", ""), item.get("audio")
    return str(item), None


async def _tts_chunk_async(
    text: str,
    idx: int,
    output_dir: Path,
    scene_type: str = "image_text",
    pre_audio: str | None = None,
    voice_key: str = "default",
) -> float:
    """문장 TTS 생성. pre_audio가 유효하면 재사용, 없으면 Fish Speech 호출."""
    import asyncio
    import shutil
    from ai_worker.tts.fish_client import synthesize as fish_synthesize

    out_path = output_dir / f"chunk_{idx:03d}.wav"
    if not text or not text.strip():
        return 0.0

    # 사전 생성된 오디오 재사용
    if pre_audio:
        pre_path = Path(pre_audio)
        if pre_path.exists() and pre_path.stat().st_size > 0:
            shutil.copy2(pre_path, out_path)
            logger.debug("[layout] TTS 재사용: 프레임=%d %s", idx, pre_path.name)
            return _get_audio_duration(out_path)

    # Fish Speech 신규 생성
    for attempt in range(2):
        try:
            await fish_synthesize(text=text, scene_type=scene_type, voice_key=voice_key, output_path=out_path)
            break
        except Exception:
            if attempt == 0:
                logger.warning("[layout] TTS 청크 %d 실패 — 5초 후 재시도", idx, exc_info=True)
                await asyncio.sleep(5.0)
            else:
                logger.error("[layout] TTS 청크 %d 최종 실패", idx)
                return 0.0

    if not out_path.exists() or out_path.stat().st_size == 0:
        return 0.0
    return _get_audio_duration(out_path)


async def _generate_tts_chunks(
    plan: list[dict],
    sentences: list[dict],
    output_dir: Path,
    voice: str,
    rate: str,
    outro_duration: float = 1.5,
) -> list[float]:
    """plan 순서로 TTS를 생성하고 각 프레임의 지속 시간 목록을 반환한다."""
    durations: list[float] = []
    for frame_idx, entry in enumerate(plan):
        sent_idx = entry.get("sent_idx")
        if sent_idx is not None and sent_idx < len(sentences):
            sent = sentences[sent_idx]
            text = sent["text"]
            pre_audio = sent.get("audio")
            scene_type = entry.get("type", "image_text")
            chunk_voice = sent.get("voice_override") or voice
            dur = await _tts_chunk_async(text, frame_idx, output_dir, scene_type, pre_audio, chunk_voice)

            # 제목(intro) 읽기 후 본문 시작 전 숨고르기 삽입
            if scene_type == "intro" and dur > 0:
                chunk_path = output_dir / f"chunk_{frame_idx:03d}.wav"
                tmp_pad = chunk_path.with_suffix(".padded.wav")
                pad_result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(chunk_path),
                        "-af", f"apad=pad_dur={_INTRO_PAUSE_SEC}",
                        "-c:a", "pcm_s16le", str(tmp_pad),
                    ],
                    capture_output=True,
                )
                if pad_result.returncode == 0 and tmp_pad.exists() and tmp_pad.stat().st_size > 0:
                    tmp_pad.replace(chunk_path)
                    dur += _INTRO_PAUSE_SEC
                    logger.debug(
                        "[layout] intro TTS 뒤 %.1f초 숨고르기 삽입 (프레임=%d)", _INTRO_PAUSE_SEC, frame_idx
                    )
        else:
            out_path = output_dir / f"chunk_{frame_idx:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                 "-t", str(outro_duration), "-c:a", "pcm_s16le", str(out_path)],
                capture_output=True, check=True,
            )
            dur = outro_duration
        durations.append(dur)
        logger.debug("[layout] TTS 프레임 %d: %.2fs", frame_idx, dur)
    return durations


def _merge_chunks(chunk_paths: list[Path], output_path: Path) -> None:
    valid = [c for c in chunk_paths if c.exists() and c.stat().st_size > 0]
    if not valid:
        raise RuntimeError("유효한 TTS 청크 없음")
    concat_file = output_path.parent / "tts_concat.txt"
    concat_file.write_text("".join(f"file '{c.resolve()}'\n" for c in valid), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", str(output_path)],
        capture_output=True, check=True,
    )
    concat_file.unlink(missing_ok=True)
