import json
from datetime import timezone, timedelta

import streamlit as st
from sqlalchemy import func

from config.settings import TTS_VOICES, load_pipeline_config, save_pipeline_config
from db.models import Post, PostStatus, Comment
from db.session import SessionLocal

st.set_page_config(page_title="WaggleBot 관리자", layout="wide")
st.title("WaggleBot 관리자 대시보드")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def to_kst(dt):
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")

def _stats_display(stats: dict | None) -> str:
    if not stats:
        return ""
    parts = []
    if "views" in stats:
        parts.append(f"조회 {stats['views']:,}")
    if "likes" in stats:
        parts.append(f"좋아요 {stats['likes']:,}")
    if "comment_count" in stats:
        parts.append(f"댓글 {stats['comment_count']:,}")
    return " · ".join(parts)


def _top_comments(post_id: int, session, limit: int = 2) -> list[Comment]:
    return (
        session.query(Comment)
        .filter(Comment.post_id == post_id)
        .order_by(Comment.likes.desc())
        .limit(limit)
        .all()
    )


def _update_status(post_id: int, new_status: PostStatus):
    with SessionLocal() as session:
        post = session.query(Post).get(post_id)
        if post:
            post.status = new_status
            session.commit()


STATUS_COLORS = {
    PostStatus.COLLECTED: "gray",
    PostStatus.APPROVED: "blue",
    PostStatus.PROCESSING: "orange",
    PostStatus.RENDERED: "green",
    PostStatus.UPLOADED: "violet",
    PostStatus.DECLINED: "red",
}

# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------

tab_inbox, tab_progress, tab_gallery, tab_settings = st.tabs(
    ["받은함 (Inbox)", "진행현황 (Progress)", "갤러리 (Gallery)", "설정 (Settings)"]
)

# ── Tab 1: Inbox ──────────────────────────────────────────────────────────

with tab_inbox:
    with SessionLocal() as session:
        posts = (
            session.query(Post)
            .filter(Post.status == PostStatus.COLLECTED)
            .order_by(Post.created_at.desc())
            .all()
        )

        if not posts:
            st.info("검토 대기 중인 게시물이 없습니다.")
        else:
            st.caption(f"검토 대기: {len(posts)}건")

        for post in posts:
            stats_text = _stats_display(post.stats)
            top_comments = _top_comments(post.id, session)

            with st.container(border=True):
                col_main, col_actions = st.columns([4, 1])

                with col_main:
                    img_badge = " 🖼" if post.images else ""
                    st.markdown(f"**{post.title}**{img_badge}")
                    st.caption(f"수집: {to_kst(post.created_at)}")
                    if stats_text:
                        st.caption(stats_text)
                    for c in top_comments:
                        likes_str = f" (+{c.likes})" if c.likes else ""
                        st.text(f"💬 {c.author}: {c.content[:80]}{'…' if len(c.content) > 80 else ''}{likes_str}")

                with col_actions:
                    if st.button("승인", key=f"approve_{post.id}", type="primary"):
                        _update_status(post.id, PostStatus.APPROVED)
                        st.rerun()
                    if st.button("거절", key=f"decline_{post.id}"):
                        _update_status(post.id, PostStatus.DECLINED)
                        st.rerun()

# ── Tab 2: Progress ──────────────────────────────────────────────────────

with tab_progress:
    progress_statuses = [
        PostStatus.APPROVED,
        PostStatus.PROCESSING,
        PostStatus.RENDERED,
        PostStatus.UPLOADED,
    ]

    with SessionLocal() as session:
        counts = dict(
            session.query(Post.status, func.count(Post.id))
            .filter(Post.status.in_(progress_statuses))
            .group_by(Post.status)
            .all()
        )

        metric_cols = st.columns(len(progress_statuses))
        for col, s in zip(metric_cols, progress_statuses):
            col.metric(s.value, counts.get(s, 0))

        st.divider()

        for status in progress_statuses:
            posts = (
                session.query(Post)
                .filter(Post.status == status)
                .order_by(Post.updated_at.desc())
                .all()
            )
            if not posts:
                continue

            st.subheader(f":{STATUS_COLORS[status]}[{status.value}] ({len(posts)})")
            for post in posts:
                stats_text = _stats_display(post.stats)
                st.markdown(f"- **{post.title}**  {stats_text}  _{to_kst(post.updated_at)}_")

# ── Tab 3: Gallery ────────────────────────────────────────────────────────

with tab_gallery:
    st.caption("렌더링 완료 및 업로드 완료 게시물")

    with SessionLocal() as session:
        posts = (
            session.query(Post)
            .filter(Post.status.in_([PostStatus.RENDERED, PostStatus.UPLOADED]))
            .order_by(Post.updated_at.desc())
            .all()
        )

        if not posts:
            st.info("아직 렌더링 완료된 게시물이 없습니다.")
        else:
            for post in posts:
                with st.container(border=True):
                    badge_color = STATUS_COLORS[post.status]
                    st.markdown(f":{badge_color}[{post.status.value}] **{post.title}**")
                    st.caption(_stats_display(post.stats))
                    st.markdown("_영상 플레이어는 Phase 3에서 연결됩니다._")

# ── Tab 4: Settings ──────────────────────────────────────────────────────

with tab_settings:
    st.subheader("파이프라인 설정")

    cfg = load_pipeline_config()

    engine_list = list(TTS_VOICES.keys())
    engine_idx = engine_list.index(cfg["tts_engine"]) if cfg["tts_engine"] in engine_list else 0
    selected_engine = st.selectbox("TTS 엔진", engine_list, index=engine_idx)

    voices = TTS_VOICES[selected_engine]
    voice_ids = [v["id"] for v in voices]
    voice_labels = [f'{v["name"]} ({v["id"]})' for v in voices]
    voice_idx = voice_ids.index(cfg["tts_voice"]) if cfg["tts_voice"] in voice_ids else 0
    selected_voice_label = st.selectbox("TTS 목소리", voice_labels, index=voice_idx)
    selected_voice = voice_ids[voice_labels.index(selected_voice_label)]

    llm_model = st.text_input("LLM 모델 (Ollama)", value=cfg.get("llm_model", "eeve-korean:10.8b"))

    st.divider()
    st.subheader("업로드 설정")

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
                st.success("YouTube 인증 완료")
            else:
                st.warning("YouTube 인증 필요 — OAuth2 토큰을 설정하세요")
        except Exception as exc:
            st.warning(f"YouTube 인증 확인 불가: {exc}")

    if st.button("저장", type="primary"):
        new_cfg = {
            "tts_engine": selected_engine,
            "tts_voice": selected_voice,
            "llm_model": llm_model,
            "upload_platforms": json.dumps(selected_platforms),
            "upload_privacy": selected_privacy,
        }
        save_pipeline_config(new_cfg)
        st.success("설정이 저장되었습니다.")

    st.divider()
    st.caption("현재 저장된 설정")
    st.json(load_pipeline_config())
