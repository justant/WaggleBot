from abc import ABC, abstractmethod
from pathlib import Path


class BaseTTS(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        """텍스트를 음성으로 변환하여 output_path에 저장 후 경로 반환."""
        ...
