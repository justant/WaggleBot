"""진행현황 (Progress) 탭."""

import threading
from datetime import datetime, timezone, timedelta

import streamlit as st
from sqlalchemy import func

from db.models import Post, PostStatus, Content
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, update_status, delete_post,
    STATUS_COLORS, STATUS_EMOJI,
)


def render() -> None:
    """진행현황 탭 렌더링."""

    _prog_hdr, _prog_ref = st.columns([5, 1])
    with _prog_hdr:
        st.header("⚙️ 진행 현황")
        st.caption("AI 워커 처리 상태 모니터링")
    with _prog_ref:
        if st.button("🔄 새로고침", key="progress_refresh_btn", width="stretch"):
            st.rerun()

    progress_statuses = [
        PostStatus.EDITING,
        PostStatus.APPROVED,
        PostStatus.PROCESSING,
        PostStatus.PREVIEW_RENDERED,
        PostStatus.RENDERED,
        PostStatus.UPLOADED,
        PostStatus.FAILED,
    ]

    @st.fragment(run_every="5s")
    def _progress_metrics():
        """진행현황 메트릭 자동 갱신 (5초 간격)."""
        with SessionLocal() as _ms:
            _counts = dict(
                _ms.query(Post.status, func.count(Post.id))
                .filter(Post.status.in_(progress_statuses))
                .group_by(Post.status)
                .all()
            )
            # PROCESSING 상태가 10분 이상 지속되면 경고
            _stuck_count = (
                _ms.query(func.count(Post.id))
                .filter(
                    Post.status == PostStatus.PROCESSING,
                    Post.updated_at < datetime.now(timezone.utc) - timedelta(minutes=10),
                )
                .scalar() or 0
            )
        metric_cols = st.columns(len(progress_statuses))
        for col, status in zip(metric_cols, progress_statuses):
            emoji = STATUS_EMOJI.get(status, "")
            col.metric(f"{emoji} {status.value}", _counts.get(status, 0))
        if _stuck_count:
            st.warning(
                f"⚠️ {_stuck_count}건의 PROCESSING 작업이 10분 이상 멈춰있습니다. "
                "AI 워커 로그를 확인하세요.",
                icon="🚨",
            )

    _progress_metrics()

    st.divider()

    with SessionLocal() as session:

        # 상태별 상세 정보
        for status in progress_statuses:
            posts = (
                session.query(Post)
                .filter(Post.status == status)
                .order_by(Post.updated_at.desc())
                .limit(10)  # 최대 10개만 표시
                .all()
            )

            if not posts:
                continue

            color = STATUS_COLORS[status]
            emoji = STATUS_EMOJI[status]
            st.subheader(f":{color}[{emoji} {status.value}] ({len(posts)}건)")

            for post in posts:
                views, likes, comments = stats_display(post.stats)
                stats_text = f"👁️ {views:,} | 👍 {likes:,}"

                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f"**{post.title}**")
                    st.caption(f"{stats_text} | 🕐 {to_kst(post.updated_at)}")
                    if status == PostStatus.FAILED:
                        _fail_content = session.query(Content).filter_by(post_id=post.id).first()
                        _fail_meta = (_fail_content.upload_meta or {}) if _fail_content else {}
                        _fail_error = _fail_meta.get("error") or _fail_meta.get("last_error")
                        if _fail_error:
                            st.caption(f"❌ 실패 원인: {str(_fail_error)[:200]}")
                with col2:
                    if status == PostStatus.FAILED:
                        col_retry, col_del = st.columns(2)
                        with col_retry:
                            if st.button("🔄 재시도", key=f"retry_{post.id}"):
                                threading.Thread(
                                    target=update_status,
                                    args=(post.id, PostStatus.APPROVED),
                                    daemon=True,
                                ).start()
                                st.rerun()
                        with col_del:
                            if st.button("🗑️", key=f"del_failed_{post.id}", help="삭제"):
                                delete_post(post.id)
                                st.rerun()

            st.divider()

        # 실시간 통계
        st.subheader("📊 실시간 통계")
        total_collected = session.query(Post).filter(Post.status == PostStatus.COLLECTED).count()
        total_processed = session.query(Post).filter(
            Post.status.in_([PostStatus.RENDERED, PostStatus.UPLOADED])
        ).count()
        total_failed = session.query(Post).filter(Post.status == PostStatus.FAILED).count()

        stat_col1, stat_col2, stat_col3 = st.columns(3)
        stat_col1.metric("대기 중", total_collected)
        stat_col2.metric("완료", total_processed)
        stat_col3.metric("실패", total_failed)
