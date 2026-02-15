from abc import ABC, abstractmethod
from pathlib import Path


class BaseUploader(ABC):
    platform: str = ""

    @abstractmethod
    def upload(self, video_path: Path, metadata: dict) -> dict:
        """영상을 플랫폼에 업로드.

        Args:
            video_path: 영상 파일 경로
            metadata: {title, description, tags, privacy}

        Returns:
            {platform, platform_id, url}
        """
        ...

    @abstractmethod
    def validate_credentials(self) -> bool:
        """인증 정보 유효성 검사. 유효하면 True."""
        ...
