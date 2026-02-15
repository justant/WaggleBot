import logging
from pathlib import Path

import edge_tts as _edge_tts

from ai_worker.tts.base import BaseTTS

logger = logging.getLogger(__name__)


class EdgeTTS(BaseTTS):
    async def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = _edge_tts.Communicate(text, voice_id)
        await communicate.save(str(output_path))
        logger.info("Edge-TTS 생성 완료: %s", output_path)
        return output_path
