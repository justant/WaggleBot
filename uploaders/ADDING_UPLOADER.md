# 업로더 추가 가이드

WaggleBot의 업로더는 `BaseUploader`를 상속하는 플러그인 방식으로 동작합니다.
`uploaders/` 디렉토리에 파일을 추가하고 `UploaderRegistry`에 등록하면 됩니다.

---

## BaseUploader 인터페이스

```python
# uploaders/base.py
class BaseUploader(ABC):
    platform: str = ""          # 고유 플랫폼 식별자

    @abstractmethod
    def validate_credentials(self) -> bool:
        """인증 정보 유효성 검증. 유효하면 True."""

    @abstractmethod
    def upload(self, video_path: Path, metadata: dict) -> dict:
        """영상 업로드.

        Args:
            video_path: 영상 파일 경로
            metadata: {title, description, tags, privacy, thumbnail_path}

        Returns:
            {platform, platform_id, url}
        """
```

---

## 구현 단계

### 1. 업로더 파일 생성

`uploaders/tiktok.py` 파일을 생성합니다.

```python
import logging
from pathlib import Path

from uploaders.base import BaseUploader, UploaderRegistry

logger = logging.getLogger(__name__)


@UploaderRegistry.register("tiktok")
class TikTokUploader(BaseUploader):
    platform = "tiktok"

    def validate_credentials(self) -> bool:
        # config/credentials.json에서 인증 정보 확인
        from config.settings import load_credentials_config
        creds = load_credentials_config().get("tiktok", {})
        return bool(creds.get("access_token"))

    def upload(self, video_path: Path, metadata: dict) -> dict:
        # TikTok API로 업로드 구현
        ...
        return {
            "platform": "tiktok",
            "platform_id": video_id,
            "url": f"https://www.tiktok.com/@user/video/{video_id}",
        }
```

### 2. `uploaders/__init__.py`에 import 추가

```python
from uploaders.tiktok import TikTokUploader  # noqa: F401 — 레지스트리 등록
```

### 3. `pipeline.json` 활성화

대시보드 **설정** 탭 또는 직접 파일 수정:

```json
{"upload_platforms": "[\"youtube\", \"tiktok\"]"}
```

### 4. 인증 정보 등록

`config/settings.py`의 `PLATFORM_CREDENTIAL_FIELDS`에 TikTok 인증 필드를 추가하면
대시보드 설정 탭에서 관리할 수 있습니다.

---

## 네이밍 규칙

| 항목 | 규칙 | 예시 |
|------|------|------|
| 파일명 | `uploaders/{platform}.py` | `uploaders/tiktok.py` |
| 클래스명 | `{Platform}Uploader` | `TikTokUploader` |
| `platform` | snake_case | `"tiktok"` |

---

## 기존 업로더 참고

- [uploaders/youtube.py](youtube.py) — YouTube Shorts 업로더 (실제 구현 예시)
- [uploaders/base.py](base.py) — BaseUploader / UploaderRegistry 전체 코드
