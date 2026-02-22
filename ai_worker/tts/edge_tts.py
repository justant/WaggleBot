import logging
from pathlib import Path

import edge_tts as _edge_tts

from ai_worker.tts.base import BaseTTS

logger = logging.getLogger(__name__)


class EdgeTTS(BaseTTS):
    def __init__(self, rate: str = "+0%") -> None:
        self.rate = rate

    async def synthesize(self, text: str, voice_id: str, output_path: Path, emotion: str = "") -> Path:
        # emotion 파라미터는 Edge-TTS가 지원하지 않으므로 무시
        output_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = _edge_tts.Communicate(text, voice_id, rate=self.rate)
        await communicate.save(str(output_path))
        logger.info("Edge-TTS 생성 완료: %s", output_path)
        return output_path
