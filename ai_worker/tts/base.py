from abc import ABC, abstractmethod
from pathlib import Path


class BaseTTS(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice_id: str, output_path: Path, emotion: str = "") -> Path:
        """텍스트를 음성으로 변환하여 output_path에 저장 후 경로 반환.

        Args:
            emotion: TTS 감정 톤 키 (예: "gentle", "cheerful"). 미지원 엔진은 무시.
        """
        ...
