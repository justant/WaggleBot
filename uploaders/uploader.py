import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from config.settings import load_pipeline_config, MEDIA_DIR
from db.models import Post, Content, PostStatus
from uploaders import get_uploader

logger = logging.getLogger(__name__)


def upload_post(
    post: Post,
    content: Content,
    session: Session,
    *,
    target_platform: str | None = None,
) -> bool:
    """승인된 영상을 설정된 플랫폼들에 업로드.

    Args:
        target_platform: 특정 플랫폼만 업로드할 경우 플랫폼 이름 (예: "youtube").
                         None이면 설정된 모든 플랫폼에 업로드.

    Returns:
        모든 대상 플랫폼 업로드 성공 시 True
    """
    cfg = load_pipeline_config()
    if target_platform:
        platforms = [target_platform]
    else:
        platforms = json.loads(cfg.get("upload_platforms", '["youtube"]'))
    privacy = cfg.get("upload_privacy", "unlisted")

    video_path = _resolve_video_path(content.video_path)
    if video_path is None or not video_path.exists():
        logger.error("영상 파일 없음: %s (post_id=%d)", content.video_path, post.id)
        return False

    raw_meta = content.upload_meta
    if isinstance(raw_meta, dict):
        upload_meta: dict = raw_meta
    elif raw_meta:
        upload_meta = json.loads(raw_meta)
    else:
        upload_meta = {}
    thumbnail_path = upload_meta.get("thumbnail_path", "")

    # ScriptData에서 LLM 생성 태그 추출
    script = content.get_script()
    script_tags: list[str] = script.tags if script and script.tags else []

    # description: 제목 + LLM 태그를 해시태그로 변환 + 기본 해시태그
    tag_hashtags = " ".join(
        f"#{t.lstrip('#')}" for t in script_tags if t.strip()
    )
    desc_parts = [post.title]
    if tag_hashtags:
        desc_parts.append(tag_hashtags)
    desc_parts.append("#Shorts #커뮤니티")
    description = "\n\n".join(desc_parts)

    # tags: LLM 태그 + 기본 태그 병합 (중복 제거)
    all_tags = list(dict.fromkeys(script_tags + ["Shorts", "커뮤니티", post.site_code]))

    metadata = {
        "title": post.title[:100],
        "description": description,
        "tags": all_tags,
        "privacy": privacy,
        "thumbnail_path": thumbnail_path,
    }
    all_ok = True
    any_attempted = False  # 실제 업로드 시도 여부

    for platform_name in platforms:
        if platform_name in upload_meta:
            logger.info("이미 업로드됨: %s (post_id=%d)", platform_name, post.id)
            continue

        try:
            uploader = get_uploader(platform_name)

            if not uploader.validate_credentials():
                # 인증 미설정은 업로드 실패가 아님 — FAILED 처리 안 함
                logger.warning("%s 인증 실패, 건너뜀 (post_id=%d)", platform_name, post.id)
                continue

            any_attempted = True
            result = uploader.upload(video_path, metadata)
            upload_meta[platform_name] = result
            logger.info("업로드 성공: %s → %s (post_id=%d)", platform_name, result.get("url"), post.id)

        except Exception:
            logger.exception("업로드 실패: %s (post_id=%d)", platform_name, post.id)
            all_ok = False

    content.upload_meta = upload_meta
    session.flush()
    # 인증 미설정으로 모든 플랫폼이 건너뛰어진 경우: 실패로 처리하지 않음
    if not any_attempted:
        return True
    return all_ok


def _resolve_video_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return MEDIA_DIR / p
