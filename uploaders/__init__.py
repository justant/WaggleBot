from uploaders.base import BaseUploader
from uploaders.youtube import YouTubeUploader

UPLOADERS: dict[str, type[BaseUploader]] = {
    "youtube": YouTubeUploader,
}


def get_uploader(name: str) -> BaseUploader:
    cls = UPLOADERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown uploader: {name!r}  (available: {list(UPLOADERS)})")
    return cls()
