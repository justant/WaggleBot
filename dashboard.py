"""
WaggleBot 관리자 대시보드

Streamlit 기반 웹 UI
- 게시글 승인/거절
- 진행 상태 모니터링
- 갤러리 및 업로드 관리
"""

import json
import logging
from datetime import timezone, timedelta
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from sqlalchemy import func, or_

from config.settings import TTS_VOICES, load_pipeline_config, save_pipeline_config, MEDIA_DIR
from db.models import Post, PostStatus, Comment, Content
from db.session import SessionLocal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="WaggleBot 관리자",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 자동 새로고침 (30초마다)
st_autorefresh(interval=30000, key="datarefresh")

st.title("🤖 WaggleBot 관리자 대시보드")

# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def to_kst(dt):
    """UTC 시간을 KST로 변환"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


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


def update_status(post_id: int, new_status: PostStatus):
    """게시글 상태 업데이트"""
    with SessionLocal() as session:
        post = session.query(Post).get(post_id)
        if post:
            post.status = new_status
            session.commit()
            log.info(f"Post {post_id} status changed to {new_status.value}")


def delete_post(post_id: int):
    """게시글 삭제"""
    with SessionLocal() as session:
        post = session.query(Post).get(post_id)
        if post:
            session.delete(post)
            session.commit()
            log.info(f"Post {post_id} deleted")


STATUS_COLORS = {
    PostStatus.COLLECTED: "gray",
    PostStatus.APPROVED: "blue",
    PostStatus.PROCESSING: "orange",
    PostStatus.RENDERED: "green",
    PostStatus.UPLOADED: "violet",
    PostStatus.DECLINED: "red",
    PostStatus.FAILED: "red",
}

STATUS_EMOJI = {
    PostStatus.COLLECTED: "📥",
    PostStatus.APPROVED: "✅",
    PostStatus.PROCESSING: "⚙️",
    PostStatus.RENDERED: "🎬",
    PostStatus.UPLOADED: "📤",
    PostStatus.DECLINED: "❌",
    PostStatus.FAILED: "⚠️",
}

# ---------------------------------------------------------------------------
# 탭 구성
# ---------------------------------------------------------------------------

tab_inbox, tab_progress, tab_gallery, tab_settings = st.tabs(
    ["📥 수신함", "⚙️ 진행현황", "🎬 갤러리", "⚙️ 설정"]
)

# ===========================================================================
# Tab 1: 수신함 (Inbox)
# ===========================================================================

with tab_inbox:
    st.header("📥 수신함 (Collected)")
    st.caption("검토 대기 중인 게시글을 승인하거나 거절하세요")

    # 필터링 옵션
    filter_col1, filter_col2, filter_col3 = st.columns(3)

    with filter_col1:
        site_filter = st.multiselect(
            "사이트 필터",
            ["nate_pann", "nate_tok"],
            default=[],
            placeholder="전체"
        )

    with filter_col2:
        image_filter = st.selectbox(
            "이미지 필터",
            ["전체", "이미지 있음", "이미지 없음"],
            index=0
        )

    with filter_col3:
        sort_by = st.selectbox(
            "정렬",
            ["최신순", "조회수순", "추천수순"],
            index=0
        )

    st.divider()

    # 데이터 조회
    with SessionLocal() as session:
        query = session.query(Post).filter(Post.status == PostStatus.COLLECTED)

        # 사이트 필터 적용
        if site_filter:
            query = query.filter(Post.site_code.in_(site_filter))

        # 이미지 필터 적용
        if image_filter == "이미지 있음":
            query = query.filter(Post.images.isnot(None), Post.images != "[]")
        elif image_filter == "이미지 없음":
            query = query.filter(or_(Post.images.is_(None), Post.images == "[]"))

        # 정렬
        if sort_by == "조회수순":
            # JSON 필드 정렬은 복잡하므로 Python에서 처리
            posts = query.all()
            posts = sorted(
                posts,
                key=lambda p: (p.stats or {}).get("views", 0),
                reverse=True
            )
        elif sort_by == "추천수순":
            posts = query.all()
            posts = sorted(
                posts,
                key=lambda p: (p.stats or {}).get("likes", 0),
                reverse=True
            )
        else:  # 최신순
            posts = query.order_by(Post.created_at.desc()).all()

        # 게시글 카운트
        st.caption(f"총 {len(posts)}건")

        if not posts:
            st.info("✨ 검토 대기 중인 게시글이 없습니다.")
        else:
            # 게시글 카드 렌더링
            for post in posts:
                views, likes, comments = stats_display(post.stats)
                best_comments = top_comments(post.id, session, limit=2)

                with st.container(border=True):
                    col_main, col_actions = st.columns([5, 1])

                    with col_main:
                        # 제목
                        img_badge = " 🖼" if (post.images and post.images != "[]") else ""
                        st.markdown(f"### {post.title}{img_badge}")

                        # 메타 정보
                        meta_parts = [
                            f"🌐 {post.site_code}",
                            f"👁️ {views:,}",
                            f"👍 {likes:,}",
                        ]
                        if comments > 0:
                            meta_parts.append(f"💬 {comments:,}")
                        meta_parts.append(f"🕐 {to_kst(post.created_at)}")
                        st.caption(" | ".join(meta_parts))

                        # 내용 미리보기
                        with st.expander("📄 내용 미리보기"):
                            if post.content:
                                preview_text = post.content[:500]
                                if len(post.content) > 500:
                                    preview_text += "..."
                                st.write(preview_text)
                            else:
                                st.caption("내용 없음")

                            # 이미지 미리보기
                            if post.images and post.images != "[]":
                                try:
                                    images = json.loads(post.images) if isinstance(post.images, str) else post.images
                                    if images and len(images) > 0:
                                        st.image(images[0], width=300, caption="첫 번째 이미지")
                                except Exception as e:
                                    st.caption(f"이미지 로드 실패: {e}")

                        # 베스트 댓글
                        if best_comments:
                            st.markdown("**💬 베스트 댓글**")
                            for comment in best_comments:
                                likes_str = f" (+{comment.likes})" if comment.likes else ""
                                comment_text = comment.content[:100]
                                if len(comment.content) > 100:
                                    comment_text += "..."
                                st.text(f"{comment.author}: {comment_text}{likes_str}")

                    with col_actions:
                        st.write("")  # 간격
                        st.write("")
                        if st.button(
                            "✅ 승인",
                            key=f"approve_{post.id}",
                            type="primary",
                            use_container_width=True
                        ):
                            update_status(post.id, PostStatus.APPROVED)
                            st.success("승인됨")
                            st.rerun()

                        if st.button(
                            "❌ 거절",
                            key=f"decline_{post.id}",
                            use_container_width=True
                        ):
                            update_status(post.id, PostStatus.DECLINED)
                            st.warning("거절됨")
                            st.rerun()

# ===========================================================================
# Tab 2: 진행현황 (Progress)
# ===========================================================================

with tab_progress:
    st.header("⚙️ 진행 현황")
    st.caption("AI 워커 처리 상태 및 실시간 모니터링")

    progress_statuses = [
        PostStatus.APPROVED,
        PostStatus.PROCESSING,
        PostStatus.RENDERED,
        PostStatus.UPLOADED,
        PostStatus.FAILED,
    ]

    with SessionLocal() as session:
        # 상태별 카운트
        counts = dict(
            session.query(Post.status, func.count(Post.id))
            .filter(Post.status.in_(progress_statuses))
            .group_by(Post.status)
            .all()
        )

        # 메트릭 표시
        metric_cols = st.columns(len(progress_statuses))
        for col, status in zip(metric_cols, progress_statuses):
            emoji = STATUS_EMOJI.get(status, "")
            col.metric(
                f"{emoji} {status.value}",
                counts.get(status, 0)
            )

        st.divider()

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
                with col2:
                    if status == PostStatus.FAILED:
                        if st.button("🔄 재시도", key=f"retry_{post.id}"):
                            update_status(post.id, PostStatus.APPROVED)
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

# ===========================================================================
# Tab 3: 갤러리 (Gallery)
# ===========================================================================

with tab_gallery:
    st.header("🎬 갤러리")
    st.caption("렌더링 완료 및 업로드된 영상")

    with SessionLocal() as session:
        # 영상이 있는 게시글 조회
        contents = (
            session.query(Content)
            .join(Post)
            .filter(Post.status.in_([PostStatus.RENDERED, PostStatus.UPLOADED]))
            .order_by(Content.created_at.desc())
            .limit(20)  # 최대 20개
            .all()
        )

        if not contents:
            st.info("🎥 아직 렌더링된 영상이 없습니다.")
        else:
            st.caption(f"총 {len(contents)}개의 영상")

            # 3열 그리드 레이아웃
            cols = st.columns(3)

            for idx, content in enumerate(contents):
                with cols[idx % 3]:
                    post = content.post

                    # 영상 파일 확인
                    video_path = MEDIA_DIR / content.video_path if content.video_path else None

                    # 컨테이너
                    with st.container(border=True):
                        # 상태 배지
                        color = STATUS_COLORS[post.status]
                        emoji = STATUS_EMOJI[post.status]
                        st.markdown(f":{color}[{emoji} {post.status.value}]")

                        # 제목
                        st.markdown(f"**{post.title[:40]}**")

                        # 통계
                        views, likes, _ = stats_display(post.stats)
                        st.caption(f"👁️ {views:,} | 👍 {likes:,}")

                        # 영상 플레이어
                        if video_path and video_path.exists():
                            st.video(str(video_path))
                        else:
                            st.caption("영상 파일 없음")

                        # 요약 텍스트
                        if content.summary_text:
                            with st.expander("📝 요약"):
                                st.write(content.summary_text)

                        # 액션 버튼
                        btn_col1, btn_col2 = st.columns(2)

                        with btn_col1:
                            if post.status == PostStatus.RENDERED:
                                if st.button(
                                    "📤 업로드",
                                    key=f"upload_{content.id}",
                                    use_container_width=True
                                ):
                                    # TODO: 업로드 트리거
                                    st.info("업로드 기능은 Phase 3에서 구현됩니다.")

                        with btn_col2:
                            if st.button(
                                "🗑️ 삭제",
                                key=f"delete_{content.id}",
                                use_container_width=True
                            ):
                                if st.session_state.get(f"confirm_delete_{content.id}"):
                                    delete_post(post.id)
                                    st.success("삭제됨")
                                    st.rerun()
                                else:
                                    st.session_state[f"confirm_delete_{content.id}"] = True
                                    st.warning("한 번 더 클릭하면 삭제됩니다.")

# ===========================================================================
# Tab 4: 설정 (Settings)
# ===========================================================================

with tab_settings:
    st.header("⚙️ 파이프라인 설정")

    cfg = load_pipeline_config()

    # TTS 설정
    st.subheader("🎙️ TTS 설정")

    engine_list = list(TTS_VOICES.keys())
    engine_idx = engine_list.index(cfg["tts_engine"]) if cfg["tts_engine"] in engine_list else 0
    selected_engine = st.selectbox("TTS 엔진", engine_list, index=engine_idx)

    voices = TTS_VOICES[selected_engine]
    voice_ids = [v["id"] for v in voices]
    voice_labels = [f'{v["name"]} ({v["id"]})' for v in voices]
    voice_idx = voice_ids.index(cfg["tts_voice"]) if cfg["tts_voice"] in voice_ids else 0
    selected_voice_label = st.selectbox("TTS 목소리", voice_labels, index=voice_idx)
    selected_voice = voice_ids[voice_labels.index(selected_voice_label)]

    st.divider()

    # LLM 설정
    st.subheader("🧠 LLM 설정")
    llm_model = st.text_input("LLM 모델 (Ollama)", value=cfg.get("llm_model", "eeve-korean:10.8b"))

    st.divider()

    # 업로드 설정
    st.subheader("📤 업로드 설정")

    available_platforms = ["youtube"]
    current_platforms = json.loads(cfg.get("upload_platforms", '["youtube"]'))
    selected_platforms = st.multiselect(
        "업로드 플랫폼",
        available_platforms,
        default=[p for p in current_platforms if p in available_platforms],
    )

    privacy_options = ["unlisted", "private", "public"]
    current_privacy = cfg.get("upload_privacy", "unlisted")
    privacy_idx = privacy_options.index(current_privacy) if current_privacy in privacy_options else 0
    selected_privacy = st.selectbox("공개 설정", privacy_options, index=privacy_idx)

    if "youtube" in selected_platforms:
        st.caption("YouTube 인증 상태")
        try:
            from uploaders.youtube import YouTubeUploader
            yt = YouTubeUploader()
            if yt.validate_credentials():
                st.success("✅ YouTube 인증 완료")
            else:
                st.warning("⚠️ YouTube 인증 필요 — OAuth2 토큰을 설정하세요")
        except Exception as exc:
            st.warning(f"⚠️ YouTube 인증 확인 불가: {exc}")

    st.divider()

    # 저장 버튼
    if st.button("💾 설정 저장", type="primary"):
        new_cfg = {
            "tts_engine": selected_engine,
            "tts_voice": selected_voice,
            "llm_model": llm_model,
            "upload_platforms": json.dumps(selected_platforms),
            "upload_privacy": selected_privacy,
        }
        save_pipeline_config(new_cfg)
        st.success("✅ 설정이 저장되었습니다.")

    # 현재 설정 표시
    with st.expander("🔍 현재 저장된 설정 보기"):
        st.json(load_pipeline_config())
