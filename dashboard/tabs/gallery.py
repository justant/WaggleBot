"""갤러리 (Gallery) 탭."""

from pathlib import Path

import streamlit as st

from config.settings import MEDIA_DIR
from db.models import Post, PostStatus, Content, ScriptData
from db.session import SessionLocal

from dashboard.components.status_utils import (
    stats_display, delete_post, STATUS_COLORS, STATUS_EMOJI,
)
from dashboard.workers.hd_render import (
    hd_render_pending, hd_render_errors, enqueue_hd_render,
)


# ---------------------------------------------------------------------------
# 갤러리 액션 버튼 fragment
# ---------------------------------------------------------------------------

@st.fragment(run_every="3s")
def _gallery_action_btn(post_id: int, content_id: int) -> None:
    """갤러리 btn_col1 fragment.

    3초마다 DB를 재조회하여 렌더 완료 즉시 버튼을 자동 전환:
      PREVIEW_RENDERED + pending  →  🎬 렌더링 중… (disabled)
      PREVIEW_RENDERED             →  🎬 고화질
      RENDERED                     →  📤 업로드
    버튼 클릭 시 fragment만 재실행 → 전체 페이지 lock 없음.
    """
    with SessionLocal() as _s:
        _post = _s.get(Post, post_id)
        if _post is None:
            return

    _hd_err = hd_render_errors.pop(post_id, None)
    if _hd_err:
        st.error(f"렌더링 실패: {_hd_err}")

    if post_id in hd_render_pending:
        st.button(
            "🎬렌더링 중",
            key=f"hd_{content_id}",
            width="stretch",
            disabled=True,
            help="고화질 렌더링이 대기 중이거나 진행 중입니다.",
        )
    elif _post.status == PostStatus.RENDERED:
        if st.button("📤 업로드", key=f"upload_{content_id}", width="stretch"):
            try:
                from uploaders.uploader import upload_post
                with SessionLocal() as upload_session:
                    _up = upload_session.get(Post, post_id)
                    _uc = upload_session.query(Content).filter_by(post_id=post_id).first()
                    ok = upload_post(_up, _uc, upload_session)
                    if ok:
                        _up.status = PostStatus.UPLOADED
                        upload_session.commit()
                        st.success("업로드 완료!")
                        st.rerun()
                    else:
                        upload_session.refresh(_uc)
                        _fail_info = {
                            k: v.get("error", "알 수 없는 오류")
                            for k, v in (_uc.upload_meta or {}).items()
                            if isinstance(v, dict) and v.get("error")
                        }
                        if _fail_info:
                            for _plat, _err in _fail_info.items():
                                st.error(f"❌ {_plat}: {_err}")
                        else:
                            st.error("일부 플랫폼 업로드 실패. 로그를 확인하세요.")
            except Exception as _e:
                st.error(f"업로드 오류: {_e}")
    elif _post.status == PostStatus.PREVIEW_RENDERED:
        if st.button(
            "🎬 고화질",
            key=f"hd_{content_id}",
            width="stretch",
            help="1080×1920 고화질로 재렌더링",
        ):
            enqueue_hd_render(post_id)


# ---------------------------------------------------------------------------
# 탭 렌더
# ---------------------------------------------------------------------------

def render() -> None:
    """갤러리 탭 렌더링."""

    _gal_hdr, _gal_ref = st.columns([5, 1])
    with _gal_hdr:
        st.header("🎬 갤러리")
        st.caption("렌더링 완료 및 업로드된 영상 (썸네일 있는 경우 표시)")
    with _gal_ref:
        if st.button("🔄 새로고침", key="gallery_refresh_btn", width="stretch"):
            st.rerun()

    _gal_filter = st.multiselect(
        "상태 필터",
        ["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
        default=["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
        key="gallery_status_filter",
        label_visibility="collapsed",
    )
    _gal_statuses = (
        [PostStatus(s) for s in _gal_filter]
        if _gal_filter
        else [PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED]
    )

    with SessionLocal() as session:
        # 영상이 있는 게시글 조회
        contents = (
            session.query(Content)
            .join(Post)
            .filter(Post.status.in_(_gal_statuses))
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

                        # 썸네일
                        thumb_path_str = (content.upload_meta or {}).get("thumbnail_path")
                        if thumb_path_str:
                            thumb_path = Path(thumb_path_str)
                            if thumb_path.exists():
                                st.image(str(thumb_path), width="stretch")

                        # 영상 플레이어
                        if video_path and video_path.exists():
                            st.video(str(video_path))
                        else:
                            st.caption("영상 파일 없음")

                        # 요약 텍스트
                        if content.summary_text:
                            with st.expander("📝 대본"):
                                try:
                                    script = ScriptData.from_json(content.summary_text)
                                    st.write(f"**후킹:** {script.hook}")
                                    for line in script.body:
                                        st.write(f"- {line}")
                                    st.write(f"**마무리:** {script.closer}")
                                except Exception:
                                    st.write(content.summary_text)

                        # 액션 버튼
                        btn_col1, btn_col2 = st.columns(2)

                        with btn_col1:
                            if post.status in (
                                PostStatus.PREVIEW_RENDERED,
                                PostStatus.RENDERED,
                            ) or post.id in hd_render_pending:
                                _gallery_action_btn(post.id, content.id)

                        with btn_col2:
                            with st.popover("🗑️ 삭제", use_container_width=True):
                                st.warning(f"**{post.title[:30]}** 게시글과 영상이 영구 삭제됩니다.")
                                if st.button(
                                    "⚠️ 삭제 확인",
                                    key=f"confirm_del_{content.id}",
                                    type="primary",
                                ):
                                    delete_post(post.id)
                                    st.success("삭제됨")
                                    st.rerun()
