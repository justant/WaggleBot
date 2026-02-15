import logging
from pathlib import Path

from ai_worker.tts.base import BaseTTS

logger = logging.getLogger(__name__)


class GptSoVITS(BaseTTS):
    """GPT-SoVITS API 호출 (GPU 전용). 3080Ti 환경에서 구현 예정."""

    def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        raise NotImplementedError(
            "GptSoVITS는 GPU 환경에서만 실행 가능합니다. "
            "3080Ti PC에서 GPT-SoVITS 서버를 구동한 후 이 메서드를 구현하세요."
        )
