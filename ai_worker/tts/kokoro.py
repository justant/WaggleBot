import logging
from pathlib import Path

from ai_worker.tts.base import BaseTTS

logger = logging.getLogger(__name__)


class KokoroTTS(BaseTTS):
    """Kokoro-82M 로컬 TTS (GPU 전용). 3080Ti 환경에서 구현 예정."""

    def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        raise NotImplementedError(
            "KokoroTTS는 GPU 환경에서만 실행 가능합니다. "
            "3080Ti PC에서 kokoro 패키지를 설치한 후 이 메서드를 구현하세요."
        )
