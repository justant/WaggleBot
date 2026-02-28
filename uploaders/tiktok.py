"""TikTok Content Posting API 업로더.

TikTok v2 API를 사용하여 영상을 업로드한다.
미심사(unaudited) 앱은 SELF_ONLY(비공개) 모드로만 업로드 가능.

업로드 플로우:
    1. POST /v2/post/publish/video/init/ → publish_id, upload_url
    2. PUT upload_url (청크 업로드, Content-Range 헤더)
    3. POST /v2/post/publish/status/fetch/ 폴링 → 완료 확인
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from uploaders.base import BaseUploader, UploaderRegistry

logger = logging.getLogger(__name__)

# 재시도 설정 — YouTube 업로더와 동일 패턴
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2

# 청크 업로드 크기 (10MB)
_CHUNK_SIZE = 10 * 1024 * 1024

# TikTok API 엔드포인트
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_PUBLISH_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_PUBLISH_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

# 캡션 제한 (UTF-16 기준 2200자)
_MAX_CAPTION_LENGTH = 2200

# 폴링 설정
_POLL_INTERVAL = 5  # 초
_POLL_MAX_ATTEMPTS = 30


@UploaderRegistry.register("tiktok")
class TikTokUploader(BaseUploader):
    platform = "tiktok"

    def __init__(self, token_path: Optional[str] = None):
        from config.settings import _PROJECT_ROOT

        self._token_path = Path(
            token_path or (_PROJECT_ROOT / "config" / "tiktok_token.json")
        )

    def _load_and_refresh_token(self) -> dict:
        """토큰 파일을 로드하고, 만료 임박 시 자동 갱신한다.

        Returns:
            토큰 데이터 dict (access_token, refresh_token, client_key 등)

        Raises:
            FileNotFoundError: 토큰 파일이 없을 때
        """
        if not self._token_path.exists():
            raise FileNotFoundError(
                f"TikTok 토큰 파일 없음: {self._token_path}  "
                "(scripts/tiktok_auth.py를 먼저 실행하세요)"
            )

        token_data = json.loads(self._token_path.read_text(encoding="utf-8"))

        # 만료 5분 전이면 갱신 시도
        issued_at = token_data.get("issued_at", 0)
        expires_in = token_data.get("expires_in", 86400)
        if time.time() > issued_at + expires_in - 300:
            refresh_token = token_data.get("refresh_token", "")
            if refresh_token:
                logger.info("TikTok 토큰 만료 임박, 갱신 시도...")
                token_data = self._refresh_token(token_data)
            else:
                logger.warning("refresh_token 없음 — 토큰 갱신 불가")

        return token_data

    def _refresh_token(self, token_data: dict) -> dict:
        """refresh_token으로 access_token을 갱신한다."""
        payload = {
            "client_key": token_data["client_key"],
            "client_secret": token_data["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        }
        resp = httpx.post(_TOKEN_URL, data=payload, timeout=30)
        resp.raise_for_status()
        new_data = resp.json()

        if "access_token" not in new_data:
            logger.error("토큰 갱신 실패: %s", new_data)
            raise RuntimeError(f"TikTok 토큰 갱신 실패: {new_data}")

        # 기존 데이터에 새 값 병합 후 저장
        token_data["access_token"] = new_data["access_token"]
        token_data["refresh_token"] = new_data.get("refresh_token", token_data["refresh_token"])
        token_data["open_id"] = new_data.get("open_id", token_data.get("open_id", ""))
        token_data["expires_in"] = new_data.get("expires_in", 86400)
        token_data["refresh_expires_in"] = new_data.get("refresh_expires_in", 0)
        token_data["issued_at"] = int(time.time())

        self._token_path.write_text(
            json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("TikTok 토큰 갱신 완료")
        return token_data

    def validate_credentials(self) -> bool:
        """User Info API를 호출하여 토큰 유효성을 확인한다."""
        try:
            token_data = self._load_and_refresh_token()
            resp = httpx.get(
                _USER_INFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                params={"fields": "display_name"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # 에러 코드 확인
            if data.get("error", {}).get("code", "ok") != "ok":
                logger.warning("TikTok 인증 실패: %s", data.get("error"))
                return False
            return True
        except Exception as exc:
            logger.warning("TikTok 인증 실패: %s", exc)
            return False

    def upload(self, video_path: Path, metadata: dict) -> dict:
        """TikTok에 영상을 업로드한다 (3단계 플로우).

        Args:
            video_path: mp4 영상 경로
            metadata: {title, description, tags, privacy}

        Returns:
            {platform: "tiktok", platform_id: str, url: str}
        """
        token_data = self._load_and_refresh_token()
        access_token = token_data["access_token"]
        file_size = video_path.stat().st_size

        caption = self._build_caption(metadata)

        # 1단계: 업로드 초기화
        publish_id, upload_url = self._init_upload(
            access_token=access_token,
            file_size=file_size,
            caption=caption,
        )
        logger.info("업로드 초기화 완료: publish_id=%s", publish_id)

        # 2단계: 청크 업로드
        self._upload_chunks(upload_url, video_path, file_size)
        logger.info("영상 파일 전송 완료")

        # 3단계: 게시 상태 폴링
        self._poll_publish_status(access_token, publish_id)

        url = f"https://www.tiktok.com/@me/video/{publish_id}"
        logger.info("TikTok 업로드 완료: %s", url)

        return {"platform": self.platform, "platform_id": publish_id, "url": url}

    def _build_caption(self, metadata: dict) -> str:
        """title + tags → 2200자 제한 캡션을 생성한다."""
        title = metadata.get("title", "")
        tags = metadata.get("tags", [])

        hashtags = " ".join(f"#{t.lstrip('#')}" for t in tags if t.strip())
        parts = [title]
        if hashtags:
            parts.append(hashtags)
        caption = "\n\n".join(parts)

        # UTF-16 기준 2200자 제한
        if len(caption.encode("utf-16-le")) // 2 > _MAX_CAPTION_LENGTH:
            # 간단히 문자 수 기준으로 잘라냄
            caption = caption[:_MAX_CAPTION_LENGTH]

        return caption

    def _init_upload(self, access_token: str, file_size: int, caption: str) -> tuple[str, str]:
        """POST /v2/post/publish/video/init/ — 업로드를 초기화한다.

        Returns:
            (publish_id, upload_url) 튜플
        """
        # 청크 수 계산
        chunk_count = max(1, (file_size + _CHUNK_SIZE - 1) // _CHUNK_SIZE)

        body = {
            "post_info": {
                "title": caption,
                "privacy_level": "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": _CHUNK_SIZE,
                "total_chunk_count": chunk_count,
            },
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = httpx.post(
                    _PUBLISH_INIT_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    json=body,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                error = data.get("error", {})
                if error.get("code") != "ok":
                    raise RuntimeError(f"Init 실패: {error}")

                publish_data = data.get("data", {})
                publish_id = publish_data.get("publish_id", "")
                upload_url = publish_data.get("upload_url", "")

                if not publish_id or not upload_url:
                    raise RuntimeError(f"Init 응답 불완전: {data}")

                return publish_id, upload_url

            except Exception as exc:
                if attempt >= _MAX_RETRIES:
                    raise
                wait = _RETRY_BACKOFF ** attempt
                logger.warning(
                    "Init 재시도 %d/%d (%s초 대기): %s",
                    attempt, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)

        raise RuntimeError("Init 업로드 최대 재시도 초과")  # pragma: no cover

    def _upload_chunks(self, upload_url: str, video_path: Path, file_size: int) -> None:
        """PUT 청크 업로드 (Content-Range 헤더)."""
        with open(video_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(_CHUNK_SIZE)
                chunk_end = offset + len(chunk) - 1
                content_range = f"bytes {offset}-{chunk_end}/{file_size}"

                for attempt in range(1, _MAX_RETRIES + 1):
                    try:
                        resp = httpx.put(
                            upload_url,
                            headers={
                                "Content-Type": "video/mp4",
                                "Content-Range": content_range,
                            },
                            content=chunk,
                            timeout=300,
                        )
                        resp.raise_for_status()
                        break
                    except Exception as exc:
                        if attempt >= _MAX_RETRIES:
                            raise
                        wait = _RETRY_BACKOFF ** attempt
                        logger.warning(
                            "청크 업로드 재시도 %d/%d (offset=%d, %s초 대기): %s",
                            attempt, _MAX_RETRIES, offset, wait, exc,
                        )
                        time.sleep(wait)

                progress = min(100, int((chunk_end + 1) / file_size * 100))
                logger.info("업로드 진행률: %d%%", progress)
                offset += len(chunk)

    def _poll_publish_status(self, access_token: str, publish_id: str) -> None:
        """POST /v2/post/publish/status/fetch/ — 게시 상태를 폴링한다."""
        for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
            try:
                resp = httpx.post(
                    _PUBLISH_STATUS_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    json={"publish_id": publish_id},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                error = data.get("error", {})
                if error.get("code") != "ok":
                    raise RuntimeError(f"상태 조회 실패: {error}")

                status = data.get("data", {}).get("status", "")
                logger.info(
                    "게시 상태 폴링 [%d/%d]: %s",
                    attempt, _POLL_MAX_ATTEMPTS, status,
                )

                if status == "PUBLISH_COMPLETE":
                    return
                if status in ("FAILED", "PUBLISH_FAILED"):
                    fail_reason = data.get("data", {}).get("fail_reason", "알 수 없음")
                    raise RuntimeError(f"TikTok 게시 실패: {fail_reason}")

            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("상태 폴링 오류 (무시): %s", exc)

            time.sleep(_POLL_INTERVAL)

        raise TimeoutError(
            f"TikTok 게시 상태 폴링 타임아웃 ({_POLL_MAX_ATTEMPTS * _POLL_INTERVAL}초)"
        )
