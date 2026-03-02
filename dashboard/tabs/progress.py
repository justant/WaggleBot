"""진행현황 (Progress) 탭."""

import json
import threading
from datetime import datetime, timezone, timedelta

import streamlit as st
from sqlalchemy import func

from db.models import Post, PostStatus, Content
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, update_status, delete_post,
    STATUS_COLORS, STATUS_EMOJI, STATUS_TEXT,
)


def _render_processing_detail(posts: list, session) -> None:
    """PROCESSING 포스트를 파이프라인 진행 상태 테이블로 렌더링한다."""
    if not posts:
        st.caption("해당 게시글 없음")
        return

    post_ids = [p.id for p in posts]
    contents = {
        c.post_id: c
        for c in session.query(Content).filter(Content.post_id.in_(post_ids)).all()
    }

    # 테이블 헤더
    cols = st.columns([1, 3, 1.5, 1.5, 2, 1])
    cols[0].markdown("**post_id**")
    cols[1].markdown("**제목**")
    cols[2].markdown("**TTS**")
    cols[3].markdown("**렌더링**")
    cols[4].markdown("**비디오 클립**")
    cols[5].markdown("**경과**")

    for post in posts:
        content = contents.get(post.id)
        has_audio = bool(content and content.audio_path)
        pipeline_state = None
        if content and content.pipeline_state:
            try:
                pipeline_state = (
                    content.pipeline_state
                    if isinstance(content.pipeline_state, dict)
                    else json.loads(content.pipeline_state)
                )
            except (json.JSONDecodeError, TypeError):
                pass

        # TTS 상태
        tts_status = "✅ 완료" if has_audio else "⏳ 대기"

        # 렌더링/비디오 상태
        if pipeline_state and pipeline_state.get("phase") == 7:
            done_scenes = pipeline_state.get("video_scenes_done", [])
            total_scenes = pipeline_state.get("total_scenes", 0)
            render_status = "🎬 Phase 7 진행 중"
            video_status = f"**{len(done_scenes)}/{total_scenes}**씬 완료"
        elif has_audio:
            render_status = "📋 렌더 큐 대기"
            video_status = "0/N"
        else:
            render_status = "⏳ TTS 대기"
            video_status = "—"

        # 경과 시간
        elapsed = ""
        if post.updated_at:
            delta = datetime.now(timezone.utc) - post.updated_at.replace(tzinfo=timezone.utc)
            minutes = int(delta.total_seconds() // 60)
            if minutes < 60:
                elapsed = f"{minutes}분"
            else:
                elapsed = f"{minutes // 60}시간 {minutes % 60}분"

        cols = st.columns([1, 3, 1.5, 1.5, 2, 1])
        cols[0].code(str(post.id))
        cols[1].markdown(
            f"{post.title[:30]}{'…' if len(post.title or '') > 30 else ''}"
        )
        cols[2].markdown(tts_status)
        cols[3].markdown(render_status)
        cols[4].markdown(video_status)
        cols[5].caption(elapsed)


def render() -> None:
    """진행현황 탭 렌더링."""

    _prog_hdr, _prog_ref = st.columns([5, 1])
    with _prog_hdr:
        st.header("⚙️ 진행 현황")
        st.caption("AI 워커 처리 상태 모니터링 (20초마다 자동 갱신)")
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

    # 전체 페이지를 fragment로 감싸 20초마다 자동 갱신
    @st.fragment(run_every="20s")
    def _progress_full():
        """진행현황 전체 자동 갱신 (20초 간격)."""
        with SessionLocal() as _ms:
            _counts = dict(
                _ms.query(Post.status, func.count(Post.id))
                .filter(Post.status.in_(progress_statuses))
                .group_by(Post.status)
                .all()
            )
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
                "`docker compose logs --tail 50 ai_worker` 로 AI 워커 로그를 확인하세요.",
                icon="🚨",
            )

        st.divider()

        with SessionLocal() as session:
            for status in progress_statuses:
                count = _counts.get(status, 0)
                color = STATUS_COLORS[status]
                emoji = STATUS_EMOJI[status]
                text = STATUS_TEXT.get(status, status.value)
                label = f":{color}[{emoji} {status.value} — {text}] ({count}건)"

                with st.expander(label, expanded=False):
                    posts = (
                        session.query(Post)
                        .filter(Post.status == status)
                        .order_by(Post.updated_at.desc())
                        .limit(10)
                        .all()
                    )
                    if not posts:
                        st.caption("해당 게시글 없음")
                        continue

                    # PROCESSING: 상세 테이블 렌더링
                    if status == PostStatus.PROCESSING:
                        _render_processing_detail(posts, session)
                        continue

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
                                    with st.expander("❌ 실패 원인 (전체 보기)"):
                                        st.code(str(_fail_error))
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

    _progress_full()
