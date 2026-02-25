from uploaders.base import BaseUploader, UploaderRegistry
from uploaders.youtube import YouTubeUploader  # noqa: F401 — 레지스트리 등록
from uploaders.tiktok import TikTokUploader  # noqa: F401 — 레지스트리 등록


def get_uploader(name: str) -> BaseUploader:
    return UploaderRegistry.get_uploader(name)
