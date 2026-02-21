"""대시보드 공통 유틸리티 — 상태, 시간, 통계 헬퍼."""

import logging
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

def update_status(post_id: int, new_status: PostStatus):
    """게시글 상태 업데이트"""
    with SessionLocal() as session:
        post = session.get(Post, post_id)
        if post:
            post.status = new_status
            session.commit()
            log.info(f"Post {post_id} status changed to {new_status.value}")


def delete_post(post_id: int):
    """게시글 삭제 (Content → Post 순서로 삭제해 FK 제약 위반 방지)"""
    with SessionLocal() as session:
        content = session.query(Content).filter_by(post_id=post_id).first()
        if content:
            session.delete(content)
            session.flush()
        post = session.get(Post, post_id)
        if post:
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


# ---------------------------------------------------------------------------
# Ollama 헬스체크
# ---------------------------------------------------------------------------

def check_ollama_health() -> bool:
    """Ollama 서버 응답 여부를 빠르게 확인 (2초 타임아웃)."""
    from config.settings import get_ollama_host
    try:
        _http.get(f"{get_ollama_host()}/api/tags", timeout=2)
        return True
    except Exception:
        return False
