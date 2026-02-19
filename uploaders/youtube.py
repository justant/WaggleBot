import logging
import time
from pathlib import Path
from typing import Optional

from uploaders.base import BaseUploader, UploaderRegistry

logger = logging.getLogger(__name__)

# resumable upload 재시도 설정
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2


@UploaderRegistry.register("youtube")
class YouTubeUploader(BaseUploader):
    platform = "youtube"

    def __init__(self, credentials_path: Optional[str] = None):
        from config.settings import load_pipeline_config

        cfg = load_pipeline_config()
        self._credentials_path = Path(
            credentials_path or cfg.get("youtube_credentials_path", "config/youtube_credentials.json")
        )
        self._service = None

    def _get_service(self):
        """YouTube API 서비스 객체를 lazy-init으로 생성."""
        if self._service is not None:
            return self._service

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_path = self._credentials_path.with_name("youtube_token.json")
        if not token_path.exists():
            raise FileNotFoundError(
                f"YouTube 토큰 파일 없음: {token_path}  "
                "(OAuth2 인증 플로우를 먼저 실행하세요)"
            )

        creds = Credentials.from_authorized_user_file(str(token_path))

        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            logger.info("YouTube 토큰 갱신 완료")

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def validate_credentials(self) -> bool:
        """토큰 파일 존재 여부 및 API 호출 가능 여부 확인."""
        try:
            svc = self._get_service()
            svc.channels().list(part="id", mine=True).execute()
            return True
        except Exception as exc:
            logger.warning("YouTube 인증 실패: %s", exc)
            return False

    def upload(self, video_path: Path, metadata: dict) -> dict:
        """YouTube Shorts 업로드 (resumable, 재시도 포함).

        Args:
            video_path: mp4 영상 경로
            metadata: {title, description, tags, privacy}

        Returns:
            {platform: "youtube", platform_id: str, url: str}
        """
        from googleapiclient.http import MediaFileUpload

        svc = self._get_service()

        title = metadata.get("title", "")[:100]
        description = metadata.get("description", "")
        tags = list(metadata.get("tags", []))
        if "#Shorts" not in tags:
            tags.append("#Shorts")
        privacy = metadata.get("privacy", "unlisted")

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            chunksize=10 * 1024 * 1024,
            resumable=True,
            mimetype="video/mp4",
        )

        request = svc.videos().insert(part="snippet,status", body=body, media_body=media)

        response = self._resumable_upload(request)
        video_id = response["id"]
        url = f"https://youtube.com/shorts/{video_id}"
        logger.info("YouTube 업로드 완료: %s", url)

        # 썸네일 업로드 (upload_meta에 저장된 경로 사용)
        thumbnail_path = metadata.get("thumbnail_path")
        if thumbnail_path:
            try:
                self._upload_thumbnail(video_id, Path(thumbnail_path))
            except Exception:
                logger.warning("썸네일 업로드 실패 (비치명적)", exc_info=True)

        return {"platform": self.platform, "platform_id": video_id, "url": url}

    def _upload_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        """업로드된 영상에 썸네일을 설정한다."""
        from googleapiclient.http import MediaFileUpload

        if not thumbnail_path.exists():
            logger.warning("썸네일 파일 없음: %s", thumbnail_path)
            return

        svc = self._get_service()
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
        svc.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info("썸네일 업로드 완료: video_id=%s", video_id)

    def fetch_analytics(self, video_id: str) -> dict | None:
        """YouTube Data API v3로 영상 통계(조회수·좋아요·댓글)를 가져온다.

        YouTube Analytics API 대신 videos.list statistics 파트를 사용하므로
        별도 Analytics API 스코프 없이 동작한다.

        Returns:
            {"views": int, "likes": int, "comments": int} 또는 None
        """
        try:
            svc = self._get_service()
            resp = svc.videos().list(
                part="statistics",
                id=video_id,
            ).execute()
            items = resp.get("items", [])
            if not items:
                logger.warning("video_id=%s 통계 없음", video_id)
                return None
            stats = items[0].get("statistics", {})
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
        except Exception as exc:
            logger.warning("Analytics 수집 실패 video_id=%s: %s", video_id, exc)
            return None

    @staticmethod
    def _resumable_upload(request) -> dict:
        """resumable upload 실행 (재시도 포함)."""
        response = None
        retries = 0

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    logger.info("업로드 진행률: %d%%", int(status.progress() * 100))
            except Exception as exc:
                retries += 1
                if retries > _MAX_RETRIES:
                    raise
                wait = _RETRY_BACKOFF ** retries
                logger.warning("업로드 재시도 %d/%d (%s초 대기): %s", retries, _MAX_RETRIES, wait, exc)
                time.sleep(wait)

        return response
