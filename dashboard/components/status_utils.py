"""대시보드 공통 유틸리티 — 상태, 시간, 통계 헬퍼."""

import logging
import time as _time_util
from datetime import timezone, timedelta

import requests as _http
import streamlit as st

from db.models import Post, PostStatus, Comment, Content
from db.session import SessionLocal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 시간 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def to_kst(dt):
    """UTC 시간을 KST로 변환"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 통계 / 댓글
# ---------------------------------------------------------------------------

def stats_display(stats: dict | None) -> tuple[int, int, int]:
    """통계 파싱"""
    if not stats:
        return 0, 0, 0
    views = stats.get("views", 0)
    likes = stats.get("likes", 0)
    comments = stats.get("comment_count", 0)
    return views, likes, comments


def top_comments(post_id: int, session, limit: int = 2) -> list[Comment]:
    """베스트 댓글 조회"""
    return (
        session.query(Comment)
        .filter(Comment.post_id == post_id)
        .order_by(Comment.likes.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# 상태 변경 / 삭제
# ---------------------------------------------------------------------------

def update_status(post_id: int, new_status: PostStatus) -> None:
    """게시글 상태 업데이트 (직접 SQL UPDATE — 동시 수정 충돌·1020 에러 방지)."""
    from datetime import datetime, timezone
    from sqlalchemy import update as _sql_update

    try:
        with SessionLocal() as session:
            result = session.execute(
                _sql_update(Post)
                .where(Post.id == post_id)
                .values(status=new_status, updated_at=datetime.now(timezone.utc))
            )
            session.commit()
            if result.rowcount > 0:
                log.info("Post %d → %s", post_id, new_status.value)
            else:
                log.warning("Post %d 상태 업데이트: 0 rows (이미 변경됨?)", post_id)
    except Exception as exc:
        log.exception("Post %d 상태 업데이트 실패: %s", post_id, exc)
        try:
            import streamlit as _st_fb
            _st_fb.session_state["_status_update_error"] = {
                "post_id": post_id,
                "target": new_status.value,
                "error": str(exc),
            }
        except Exception:
            pass


def batch_update_status(post_ids: list[int], new_status: PostStatus) -> int:
    """여러 게시글 상태를 단일 SQL UPDATE로 일괄 변경 (루프 N회 → 1회).

    성공/실패 결과를 session_state에 기록해 UI 피드백으로 표시.
    """
    from datetime import datetime, timezone
    from sqlalchemy import update as _sql_update

    if not post_ids:
        return 0
    try:
        with SessionLocal() as session:
            result = session.execute(
                _sql_update(Post)
                .where(Post.id.in_(post_ids))
                .values(status=new_status, updated_at=datetime.now(timezone.utc))
            )
            session.commit()
            cnt = result.rowcount
            log.info("Batch %d posts → %s (%d rows)", len(post_ids), new_status.value, cnt)
            # 성공 피드백 저장
            try:
                import streamlit as _st_fb
                _st_fb.session_state["_batch_result"] = {
                    "status": "done",
                    "count": cnt,
                    "target": new_status.value,
                }
            except Exception:
                pass
            return cnt
    except Exception as exc:
        log.exception("Batch update failed: %s", exc)
        try:
            import streamlit as _st_fb
            _st_fb.session_state["_batch_result"] = {
                "status": "error",
                "error": str(exc),
                "target": new_status.value,
            }
        except Exception:
            pass
        return 0


def delete_post(post_id: int):
    """게시글 삭제 (Content → Post 순서로 삭제해 FK 제약 위반 방지).

    삭제 전 (site_code, origin_id)를 crawl_blocklist에 등록하여 재수집을 방지한다.
    """
    from db.models import CrawlBlocklist

    with SessionLocal() as session:
        post = session.get(Post, post_id)
        if post:
            # 블록리스트 등록 (재수집 방지)
            exists = session.query(CrawlBlocklist).filter_by(
                site_code=post.site_code, origin_id=post.origin_id
            ).first()
            if not exists:
                session.add(CrawlBlocklist(
                    site_code=post.site_code, origin_id=post.origin_id
                ))
                session.flush()

            # Content → Post 순서로 삭제
            content = session.query(Content).filter_by(post_id=post_id).first()
            if content:
                session.delete(content)
                session.flush()
            session.delete(post)
        session.commit()
        log.info("Post %d deleted", post_id)


# ---------------------------------------------------------------------------
# 상태 표시 상수
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    PostStatus.COLLECTED: "gray",
    PostStatus.EDITING: "blue",
    PostStatus.APPROVED: "violet",
    PostStatus.PROCESSING: "orange",
    PostStatus.PREVIEW_RENDERED: "blue",
    PostStatus.RENDERED: "green",
    PostStatus.UPLOADED: "violet",
    PostStatus.DECLINED: "red",
    PostStatus.FAILED: "red",
}

STATUS_EMOJI = {
    PostStatus.COLLECTED: "📥",
    PostStatus.EDITING: "✏️",
    PostStatus.APPROVED: "✅",
    PostStatus.PROCESSING: "⚙️",
    PostStatus.PREVIEW_RENDERED: "🔍",
    PostStatus.RENDERED: "🎬",
    PostStatus.UPLOADED: "📤",
    PostStatus.DECLINED: "❌",
    PostStatus.FAILED: "⚠️",
}

# 접근성용 텍스트 라벨 (색상/이모지 외에 텍스트로 상태 구분 가능)
STATUS_TEXT = {
    PostStatus.COLLECTED: "수집됨",
    PostStatus.EDITING: "편집중",
    PostStatus.APPROVED: "승인됨",
    PostStatus.PROCESSING: "처리중",
    PostStatus.PREVIEW_RENDERED: "미리보기",
    PostStatus.RENDERED: "렌더완료",
    PostStatus.UPLOADED: "업로드됨",
    PostStatus.DECLINED: "거절됨",
    PostStatus.FAILED: "실패",
}


# ---------------------------------------------------------------------------
# Ollama 헬스체크
# ---------------------------------------------------------------------------

_ollama_health_cache: dict = {"status": None, "checked_at": 0.0}
_OLLAMA_HEALTH_TTL = 30  # 30초 캐싱


def check_ollama_health() -> bool:
    """Ollama 서버 응답 여부를 확인 (30초 캐싱으로 반복 요청 방지)."""
    from config.settings import get_ollama_host
    _now = _time_util.time()
    if (
        _ollama_health_cache["status"] is not None
        and _now - _ollama_health_cache["checked_at"] < _OLLAMA_HEALTH_TTL
    ):
        return _ollama_health_cache["status"]
    try:
        _http.get(f"{get_ollama_host()}/api/tags", timeout=2)
        _ollama_health_cache.update({"status": True, "checked_at": _now})
        return True
    except Exception:
        _ollama_health_cache.update({"status": False, "checked_at": _now})
        return False
