from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Type


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


class UploaderRegistry:
    """업로더 플러그인 레지스트리."""

    _uploaders: Dict[str, Type[BaseUploader]] = {}

    @classmethod
    def register(cls, platform: str):
        """업로더 등록 데코레이터.

        Example:
            @UploaderRegistry.register("youtube")
            class YouTubeUploader(BaseUploader):
                platform = "youtube"
                ...
        """
        def decorator(uploader_class: Type[BaseUploader]):
            cls._uploaders[platform] = uploader_class
            return uploader_class
        return decorator

    @classmethod
    def get_uploader(cls, platform: str) -> BaseUploader:
        """플랫폼명으로 업로더 인스턴스 반환.

        Raises:
            ValueError: 등록되지 않은 플랫폼
        """
        if platform not in cls._uploaders:
            available = ", ".join(cls._uploaders.keys())
            raise ValueError(
                f"Unknown platform: '{platform}'. Available: {available}"
            )
        return cls._uploaders[platform]()

    @classmethod
    def list_platforms(cls) -> list[str]:
        """등록된 플랫폼 목록 반환."""
        return list(cls._uploaders.keys())
