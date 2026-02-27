"""갤러리 (Gallery) 탭."""

import json
import threading as _gal_threading
from pathlib import Path

import streamlit as st
from sqlalchemy import func

from config.settings import MEDIA_DIR, load_pipeline_config
from db.models import Post, PostStatus, Content, ScriptData
from db.session import SessionLocal

from dashboard.components.status_utils import (
    stats_display, delete_post, STATUS_COLORS, STATUS_EMOJI, STATUS_TEXT,
)
from dashboard.workers.hd_render import (
    hd_render_pending, hd_render_errors, enqueue_hd_render,
)

# 플랫폼별 업로드 작업 레지스트리: "{post_id}_{platform}" → {"status": ..., "error": ...}
_upload_tasks: dict[str, dict] = {}
_upload_lock = _gal_threading.Lock()

# 플랫폼별 표시 정보 (label, help)
_PLATFORM_DISPLAY: dict[str, tuple[str, str]] = {
    "youtube": ("▶️ YouTube", "YouTube에 업로드"),
    "tiktok": ("🎵 TikTok", "TikTok에 업로드"),
}


@st.cache_data(ttl=60)
def _get_upload_platforms() -> tuple[str, ...]:
    """설정에서 업로드 대상 플랫폼 목록을 가져온다 (60초 캐시)."""
    cfg = load_pipeline_config()
    return tuple(json.loads(cfg.get("upload_platforms", '["youtube"]')))


# ---------------------------------------------------------------------------
# 갤러리 액션 버튼 fragment
# ---------------------------------------------------------------------------

@st.fragment
def _gallery_action_btn(post_id: int, content_id: int) -> None:
    """갤러리 btn_col1 fragment — HD 렌더/플랫폼별 업로드 버튼."""
    with SessionLocal() as _s:
        _post = _s.get(Post, post_id)
        _content = _s.query(Content).filter_by(post_id=post_id).first()
        if _post is None:
            return
        _upload_meta: dict = {}
        if _content and _content.upload_meta:
            _raw = _content.upload_meta
            _upload_meta = json.loads(_raw) if isinstance(_raw, str) else (_raw or {})

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
    elif _post.status in (PostStatus.RENDERED, PostStatus.UPLOADED):
        # ── 플랫폼별 업로드 버튼 ──
        platforms = _get_upload_platforms()
        _cols = st.columns(len(platforms)) if len(platforms) > 1 else [st.container()]

        for _i, _plat in enumerate(platforms):
            with _cols[_i]:
                _task_key = f"{post_id}_{_plat}"
                _task = _upload_tasks.get(_task_key)
                _label, _help = _PLATFORM_DISPLAY.get(
                    _plat, (f"📤 {_plat}", f"{_plat}에 업로드"),
                )
                _already = (
                    _plat in _upload_meta
                    and isinstance(_upload_meta[_plat], dict)
                    and not _upload_meta[_plat].get("error")
                )

                if _task and _task["status"] == "running":
                    st.button(
                        f"⏳ {_plat}",
                        key=f"up_{content_id}_{_plat}",
                        width="stretch",
                        disabled=True,
                    )
                elif _task and _task["status"] == "error":
                    st.error(f"{_plat}: {_task.get('error', '')}")
                    with _upload_lock:
                        _upload_tasks.pop(_task_key, None)
                elif _task and _task["status"] == "done":
                    st.success(f"{_plat} ✅")
                    with _upload_lock:
                        _upload_tasks.pop(_task_key, None)
                    st.rerun()
                elif _already:
                    st.button(
                        f"✅ {_plat}",
                        key=f"up_{content_id}_{_plat}",
                        width="stretch",
                        disabled=True,
                        help="이미 업로드됨",
                    )
                else:
                    if st.button(
                        _label,
                        key=f"up_{content_id}_{_plat}",
                        width="stretch",
                        help=_help,
                    ):
                        _target = _plat  # 클로저 캡처용

                        def _do_upload(pid: int, plat: str) -> None:
                            _tk = f"{pid}_{plat}"
                            try:
                                from uploaders.uploader import upload_post

                                with SessionLocal() as _us:
                                    _up = _us.get(Post, pid)
                                    _uc = _us.query(Content).filter_by(post_id=pid).first()
                                    ok = upload_post(_up, _uc, _us, target_platform=plat)
                                    if ok:
                                        if _up.status == PostStatus.RENDERED:
                                            _up.status = PostStatus.UPLOADED
                                        _us.commit()
                                        with _upload_lock:
                                            _upload_tasks[_tk] = {"status": "done"}
                                    else:
                                        _us.refresh(_uc)
                                        _pm = (_uc.upload_meta or {}).get(plat, {})
                                        _err = (
                                            _pm.get("error", "업로드 실패")
                                            if isinstance(_pm, dict)
                                            else "업로드 실패"
                                        )
                                        with _upload_lock:
                                            _upload_tasks[_tk] = {
                                                "status": "error",
                                                "error": _err,
                                            }
                            except Exception as _e:
                                with _upload_lock:
                                    _upload_tasks[f"{pid}_{plat}"] = {
                                        "status": "error",
                                        "error": str(_e),
                                    }

                        with _upload_lock:
                            _upload_tasks[_task_key] = {"status": "running"}
                        _gal_threading.Thread(
                            target=_do_upload,
                            args=(post_id, _target),
                            daemon=True,
                        ).start()

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

    # HD 렌더 또는 업로드 진행 중일 때 자동 감지 fragment
    @st.fragment(run_every="10s")
    def _gallery_task_monitor() -> None:
        """HD 렌더/업로드 완료 시 자동 새로고침."""
        _has_pending = bool(hd_render_pending) or any(
            t.get("status") == "running" for t in _upload_tasks.values()
        )
        _has_done = bool(hd_render_errors) or any(
            t.get("status") in ("done", "error") for t in _upload_tasks.values()
        )
        if _has_done:
            st.rerun()  # 완료 감지 → 전체 갱신
        elif _has_pending:
            st.caption("⏳ 렌더링/업로드 작업 진행 중... (자동 감지)")

    _gallery_task_monitor()

    # HD 렌더 진행 상태 표시
    if hd_render_pending:
        st.info(f"🎬 HD 렌더링 진행 중: {len(hd_render_pending)}건 (완료 시 자동 새로고침)")
    if hd_render_errors:
        for _err_pid, _err_msg in list(hd_render_errors.items()):
            st.error(f"❌ Post #{_err_pid} HD 렌더링 실패: {_err_msg}")
            hd_render_errors.pop(_err_pid, None)

    _gal_filter = st.multiselect(
        "상태 필터",
        ["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
        default=["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
        key="gallery_status_filter",
        placeholder="상태 선택 (기본: 전체)",
    )
    _gal_statuses = (
        [PostStatus(s) for s in _gal_filter]
        if _gal_filter
        else [PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED]
    )

    if "gallery_page" not in st.session_state:
        st.session_state["gallery_page"] = 0

    _GAL_PAGE_SIZE = 12  # 3열 × 4행

    with SessionLocal() as session:
        _total_gal = (
            session.query(func.count(Content.id))
            .join(Post)
            .filter(Post.status.in_(_gal_statuses))
            .scalar() or 0
        )
        contents = (
            session.query(Content)
            .join(Post)
            .filter(Post.status.in_(_gal_statuses))
            .order_by(Content.created_at.desc())
            .offset(st.session_state["gallery_page"] * _GAL_PAGE_SIZE)
            .limit(_GAL_PAGE_SIZE)
            .all()
        )

        if not contents:
            st.info("🎥 아직 렌더링된 영상이 없습니다.")
        else:
            st.caption(f"총 {_total_gal}개의 영상")

            # 3열 그리드 레이아웃
            cols = st.columns(3)

            for idx, content in enumerate(contents):
                with cols[idx % 3]:
                    post = content.post

                    # 영상 파일 확인
                    video_path = MEDIA_DIR / content.video_path if content.video_path else None

                    # 컨테이너
                    with st.container(border=True):
                        # 상태 배지 (색상 + 이모지 + 텍스트)
                        color = STATUS_COLORS[post.status]
                        emoji = STATUS_EMOJI[post.status]
                        text = STATUS_TEXT.get(post.status, post.status.value)
                        st.markdown(f":{color}[{emoji} {post.status.value} — {text}]")

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

                        # 영상 플레이어 (주문형 로드 — 초기 미디어 요청 최소화)
                        if video_path and video_path.exists():
                            if st.checkbox("▶️ 영상 재생", key=f"vid_{content.id}"):
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
                                PostStatus.UPLOADED,
                            ) or post.id in hd_render_pending:
                                _gallery_action_btn(post.id, content.id)

                        with btn_col2:
                            if st.button(
                                "🗑️ 삭제",
                                key=f"confirm_del_{content.id}",
                                use_container_width=True,
                            ):
                                delete_post(post.id)
                                st.toast("🗑️ 삭제됨")
                                st.rerun()

    # 페이지네이션 버튼 (12건 초과 시)
    if _total_gal > _GAL_PAGE_SIZE:
        _gp1, _gp2, _gp3 = st.columns([1, 3, 1])
        with _gp1:
            if st.button("◀", disabled=st.session_state["gallery_page"] == 0, key="gal_prev"):
                st.session_state["gallery_page"] -= 1
                st.rerun()
        with _gp2:
            _cur_page = st.session_state["gallery_page"]
            _total_pages = (_total_gal + _GAL_PAGE_SIZE - 1) // _GAL_PAGE_SIZE
            st.caption(f"페이지 {_cur_page + 1} / {_total_pages} (전체 {_total_gal}건)")
        with _gp3:
            _has_next = (_cur_page + 1) * _GAL_PAGE_SIZE < _total_gal
            if st.button("▶", disabled=not _has_next, key="gal_next"):
                st.session_state["gallery_page"] += 1
                st.rerun()
