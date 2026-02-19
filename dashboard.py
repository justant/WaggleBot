"""
WaggleBot 관리자 대시보드

Streamlit 기반 웹 UI
- 게시글 승인/거절
- 진행 상태 모니터링
- 갤러리 및 업로드 관리
"""

import json
import logging
import queue as _queue
import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests as _http
import streamlit as st
from sqlalchemy import func, or_

from config.settings import (
    TTS_VOICES, MEDIA_DIR, ASSETS_DIR,
    PLATFORM_CREDENTIAL_FIELDS,
    get_ollama_host, OLLAMA_MODEL,
    load_pipeline_config, save_pipeline_config,
    load_credentials_config, save_credentials_config,
)
from db.models import Post, PostStatus, Comment, Content, LLMLog
from db.session import SessionLocal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HD 렌더 큐 & 상태 추적 (프로세스 레벨 — 재런 간 유지)
# ---------------------------------------------------------------------------
# 큐 대기 중 또는 렌더 중인 post_id 집합 (UI 버튼 상태 판별용)
_hd_render_pending: set[int] = set()
# post_id → 에러 메시지 (렌더 실패 시)
_hd_render_errors: dict[int, str] = {}
# FIFO 렌더 요청 큐 — 워커 스레드가 순서대로 소비
_hd_render_queue: _queue.Queue[int] = _queue.Queue()
_hd_worker_lock = threading.Lock()
_hd_worker_started = False


def _run_hd_render(post_id: int) -> None:
    """HD 렌더 실행 (워커 스레드 내부에서 호출)."""
    try:
        from ai_worker.video import render_video
        from config.settings import load_pipeline_config, MEDIA_DIR as _MEDIA_DIR
        with SessionLocal() as _s:
            _post = _s.get(Post, post_id)
            _content = _s.query(Content).filter_by(post_id=post_id).first()
            _cfg = load_pipeline_config()
            _audio = Path(_content.audio_path)
            _preview_path = (
                _MEDIA_DIR / _content.video_path if _content.video_path else None
            )
            _video = render_video(_post, _audio, _content.summary_text, _cfg)
            _content.video_path = str(_video.relative_to(_MEDIA_DIR))
            _post.status = PostStatus.RENDERED
            _s.commit()
        # SD 프리뷰 삭제 (_SD.mp4 vs _FHD.mp4로 항상 다른 파일)
        if _preview_path and _preview_path.exists():
            _preview_path.unlink()
            log.info("SD 프리뷰 삭제: %s", _preview_path)
        log.info("HD 렌더링 완료: post_id=%d", post_id)
    except Exception as _e:
        log.exception("HD 렌더링 실패: post_id=%d", post_id)
        _hd_render_errors[post_id] = str(_e)
    finally:
        _hd_render_pending.discard(post_id)


def _hd_render_worker() -> None:
    """HD 렌더 큐를 순서대로 소비하는 영구 워커 스레드."""
    while True:
        post_id = _hd_render_queue.get()
        try:
            log.info("HD 렌더 워커 시작: post_id=%d (대기 중=%d)", post_id, _hd_render_queue.qsize())
            _run_hd_render(post_id)
        finally:
            _hd_render_queue.task_done()


def _enqueue_hd_render(post_id: int) -> None:
    """HD 렌더 요청을 큐에 추가. 워커 스레드가 없으면 생성."""
    global _hd_worker_started
    _hd_render_pending.add(post_id)
    _hd_render_queue.put(post_id)
    with _hd_worker_lock:
        if not _hd_worker_started:
            _hd_worker_started = True
            threading.Thread(
                target=_hd_render_worker, daemon=True, name="hd-render-worker"
            ).start()
            log.info("HD 렌더 워커 스레드 시작")


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

    _hd_err = _hd_render_errors.pop(post_id, None)
    if _hd_err:
        st.error(f"렌더링 실패: {_hd_err}")

    if post_id in _hd_render_pending:
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
            _enqueue_hd_render(post_id)


# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="WaggleBot 관리자",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed"
)

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


def render_image_slider(images_raw: "str | list | None", key_prefix: str, width: int = 320) -> None:
    """이미지 URL 목록을 슬라이드로 렌더링한다.

    - 서버에서 이미지를 프록시로 가져와 핫링크 차단 우회
    - 여러 장이면 ◀ / ▶ 버튼으로 슬라이드 이동
    """
    if not images_raw or images_raw == "[]":
        return
    try:
        imgs: list[str] = (
            json.loads(images_raw) if isinstance(images_raw, str) else list(images_raw)
        )
    except Exception:
        return
    if not imgs:
        return

    slide_key = f"slide_{key_prefix}"
    if slide_key not in st.session_state:
        st.session_state[slide_key] = 0
    cur = max(0, min(st.session_state[slide_key], len(imgs) - 1))

    if len(imgs) > 1:
        nav_l, nav_mid, nav_r = st.columns([1, 6, 1])
        with nav_l:
            if st.button("◀", key=f"img_prev_{key_prefix}", disabled=(cur == 0)):
                st.session_state[slide_key] = cur - 1
                st.rerun()
        with nav_mid:
            st.caption(f"{cur + 1} / {len(imgs)}")
        with nav_r:
            if st.button("▶", key=f"img_next_{key_prefix}", disabled=(cur == len(imgs) - 1)):
                st.session_state[slide_key] = cur + 1
                st.rerun()

    try:
        resp = _http.get(
            imgs[cur], timeout=8,
            headers={"Referer": imgs[cur], "User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        st.image(resp.content, width=width)
    except Exception:
        st.caption(f"이미지 로드 실패: {imgs[cur]}")


def run_ai_fit_analysis(post: Post, model: str) -> dict:
    """Ollama LLM으로 쇼츠 적합도 분석 (1~10점) 요청.

    Returns:
        {"score": int, "reason": str, "issues": list[str]}
    """
    prompt = (
        "다음 게시글의 YouTube 쇼츠 영상 적합도를 분석하세요.\n\n"
        f"제목: {post.title}\n"
        f"내용: {(post.content or '')[:300]}\n\n"
        "반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 금지):\n"
        '{"score": 7, "reason": "판단 근거 요약 2~3문장", "issues": ["문제점1"]}\n\n'
        "평가 기준:\n"
        "- 논쟁적·공감적 주제: +3점\n"
        "- 강한 감정 반응 유발(분노·감동·웃음): +3점\n"
        "- 댓글 활성화 가능성: +2점\n"
        "- 이미지 있음: +1점\n"
        "- 민감·저작권·광고 문제: -3점\n"
        'issues 예시: ["광고성 게시글", "저작권 이미지", "민감 주제", "정치적 내용"]\n'
        "문제 없으면 issues는 [] 로 작성"
    )
    try:
        resp = _http.post(
            f"{get_ollama_host()}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=40,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as exc:
        log.warning("AI 적합도 분석 실패: %s", exc)
    return {"score": 0, "reason": "분석 실패 또는 LLM 응답 오류", "issues": []}


def suggest_bgm(mood: str) -> str:
    """mood에 맞는 BGM 파일명을 탐색 후 반환한다. 없으면 '없음'."""
    bgm_dir = ASSETS_DIR / "bgm"
    if not bgm_dir.exists():
        return "없음"
    all_files = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))
    if not all_files:
        return "없음"
    mood_keywords: dict[str, list[str]] = {
        "shocking":     ["tense", "dramatic", "shock", "shocking"],
        "funny":        ["funny", "upbeat", "comic", "light"],
        "serious":      ["serious", "calm", "news", "neutral"],
        "heartwarming": ["warm", "heartwarming", "sweet", "soft"],
    }
    for kw in mood_keywords.get(mood, []):
        for f in all_files:
            if kw in f.stem.lower():
                return f.name
    return all_files[0].name  # 매칭 없으면 첫 번째 파일


def _write_youtube_token(token_json_str: str) -> bool:
    """credentials.json의 token_json을 youtube_token.json 파일로 동기화."""
    from config.settings import _PROJECT_ROOT
    token_path = _PROJECT_ROOT / "config" / "youtube_token.json"
    try:
        json.loads(token_json_str)  # JSON 유효성 검사
        token_path.write_text(token_json_str, encoding="utf-8")
        log.info("youtube_token.json 갱신 완료")
        return True
    except json.JSONDecodeError:
        return False


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
# 탭 구성
# ---------------------------------------------------------------------------

tab_inbox, tab_editor, tab_progress, tab_gallery, tab_analytics, tab_settings, tab_llm_log = st.tabs(
    ["📥 수신함", "✏️ 편집실", "⚙️ 진행현황", "🎬 갤러리", "📊 분석", "⚙️ 설정", "🔬 LLM 이력"]
)

# ===========================================================================
# Tab 1: 수신함 (Inbox) — 스마트 수신함
# ===========================================================================

with tab_inbox:
    # ---------------------------------------------------------------------------
    # session_state 초기화
    # ---------------------------------------------------------------------------
    if "selected_posts" not in st.session_state:
        st.session_state["selected_posts"] = set()
    if "auto_approved_ids" not in st.session_state:
        st.session_state["auto_approved_ids"] = set()
    if "ai_analysis" not in st.session_state:
        st.session_state["ai_analysis"] = {}

    inbox_cfg = load_pipeline_config()
    auto_approve_enabled = inbox_cfg.get("auto_approve_enabled") == "true"
    auto_threshold = int(inbox_cfg.get("auto_approve_threshold", "80"))

    # ---------------------------------------------------------------------------
    # 자동 승인: COLLECTED + score >= threshold → EDITING (편집실 대기)
    # ---------------------------------------------------------------------------
    if auto_approve_enabled:
        with SessionLocal() as _asess:
            _qualify = (
                _asess.query(Post)
                .filter(
                    Post.status == PostStatus.COLLECTED,
                    Post.engagement_score >= auto_threshold,
                )
                .all()
            )
            _new_auto = [
                p for p in _qualify
                if p.id not in st.session_state["auto_approved_ids"]
            ]
            if _new_auto:
                for _p in _new_auto:
                    _p.status = PostStatus.EDITING
                    st.session_state["auto_approved_ids"].add(_p.id)
                _asess.commit()
                st.toast(
                    f"🤖 {len(_new_auto)}건 자동 승인됨 (Score ≥ {auto_threshold})",
                    icon="✅",
                )

    # ---------------------------------------------------------------------------
    # 헤더 & 필터
    # ---------------------------------------------------------------------------
    hdr_col, ref_col = st.columns([5, 1])
    with hdr_col:
        st.header("📥 수신함 (Collected)")
        if auto_approve_enabled:
            st.caption(f"🤖 자동 승인 활성화 중 — Score ≥ {auto_threshold} 자동 처리")
        else:
            st.caption("검토 대기 중인 게시글을 승인하거나 거절하세요")
    with ref_col:
        if st.button("🔄 새로고침", width="stretch"):
            st.rerun()

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        site_filter = st.multiselect(
            "사이트 필터", ["nate_pann", "nate_tok"], default=[], placeholder="전체"
        )
    with filter_col2:
        image_filter = st.selectbox(
            "이미지 필터", ["전체", "이미지 있음", "이미지 없음"], index=0
        )
    with filter_col3:
        sort_by = st.selectbox(
            "정렬", ["인기도순", "최신순", "조회수순", "추천수순"], index=0
        )

    st.divider()

    # ---------------------------------------------------------------------------
    # 데이터 조회
    # ---------------------------------------------------------------------------
    with SessionLocal() as session:
        query = session.query(Post).filter(Post.status == PostStatus.COLLECTED)
        if site_filter:
            query = query.filter(Post.site_code.in_(site_filter))
        if image_filter == "이미지 있음":
            query = query.filter(Post.images.isnot(None), Post.images != "[]")
        elif image_filter == "이미지 없음":
            query = query.filter(or_(Post.images.is_(None), Post.images == "[]"))
        posts = query.all()

        if sort_by == "인기도순":
            posts = sorted(posts, key=lambda p: p.engagement_score or 0, reverse=True)
        elif sort_by == "조회수순":
            posts = sorted(posts, key=lambda p: (p.stats or {}).get("views", 0), reverse=True)
        elif sort_by == "추천수순":
            posts = sorted(posts, key=lambda p: (p.stats or {}).get("likes", 0), reverse=True)
        else:
            posts = sorted(posts, key=lambda p: p.created_at or 0, reverse=True)

        # 3단계 티어 분류
        high_posts   = [p for p in posts if (p.engagement_score or 0) >= 80]
        normal_posts = [p for p in posts if 30 <= (p.engagement_score or 0) < 80]
        low_posts    = [p for p in posts if (p.engagement_score or 0) < 30]

        # ---------------------------------------------------------------------------
        # 글로벌 배치 액션 바
        # ---------------------------------------------------------------------------
        n_selected = len(st.session_state["selected_posts"])
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button(
                f"✅ 선택 ({n_selected}건) 일괄 승인",
                disabled=n_selected == 0,
                width="stretch",
                type="primary",
            ):
                for pid in list(st.session_state["selected_posts"]):
                    update_status(pid, PostStatus.EDITING)
                st.session_state["selected_posts"] = set()
                st.rerun()
        with bc2:
            if st.button(
                f"❌ 선택 ({n_selected}건) 일괄 거절",
                disabled=n_selected == 0,
                width="stretch",
            ):
                for pid in list(st.session_state["selected_posts"]):
                    update_status(pid, PostStatus.DECLINED)
                st.session_state["selected_posts"] = set()
                st.rerun()

        st.caption(
            f"총 {len(posts)}건 | 🏆 추천 {len(high_posts)}건 "
            f"| 📋 일반 {len(normal_posts)}건 | 📉 낮음 {len(low_posts)}건"
        )

        if not posts:
            st.info("✨ 검토 대기 중인 게시글이 없습니다.")

        # ---------------------------------------------------------------------------
        # 게시글 카드 렌더링 헬퍼 (인라인 함수)
        # ---------------------------------------------------------------------------
        def _render_post_card(post: Post, tier_key: str) -> None:
            """게시글 카드 1개를 렌더링한다."""
            views, likes, n_comments = stats_display(post.stats)
            score = post.engagement_score or 0
            best_coms = top_comments(post.id, session, limit=2)
            has_img = bool(post.images and post.images != "[]")

            if score >= 80:
                score_badge, score_color = f"🔥 {score:.0f}", "red"
            elif score >= 30:
                score_badge, score_color = f"📊 {score:.0f}", "orange"
            else:
                score_badge, score_color = f"📉 {score:.0f}", "gray"

            with st.container(border=True):
                col_chk, col_main, col_act = st.columns([0.5, 5, 1.2])

                with col_chk:
                    checked = st.checkbox(
                        "선택",
                        key=f"chk_{tier_key}_{post.id}",
                        value=post.id in st.session_state["selected_posts"],
                        label_visibility="collapsed",
                    )
                    if checked:
                        st.session_state["selected_posts"].add(post.id)
                    else:
                        st.session_state["selected_posts"].discard(post.id)

                with col_main:
                    img_icon = " 🖼" if has_img else ""
                    st.markdown(f"**{post.title}{img_icon}**")

                    meta = [
                        f":{score_color}[{score_badge} pts]",
                        f"🌐 {post.site_code}",
                        f"👁️ {views:,}",
                        f"👍 {likes:,}",
                    ]
                    if n_comments:
                        meta.append(f"💬 {n_comments:,}")
                    meta.append(f"🕐 {to_kst(post.created_at)}")
                    st.caption(" | ".join(meta))

                    # 예상 조회수 (score 기반 rough estimate)
                    low_est  = max(100, int(score * 40))
                    high_est = max(500, int(score * 120))
                    st.caption(f"📊 예상 조회수: {low_est:,}~{high_est:,}")

                    with st.expander("📄 내용 미리보기"):
                        if post.content:
                            st.write(post.content[:500] + ("..." if len(post.content) > 500 else ""))
                        else:
                            st.caption("내용 없음")
                        if has_img:
                            render_image_slider(post.images, key_prefix=f"inbox_{post.id}", width=320)

                    if best_coms:
                        st.markdown("**💬 베스트 댓글**")
                        for c in best_coms:
                            lk = f" (+{c.likes})" if c.likes else ""
                            st.text(f"{c.author}: {c.content[:100]}{lk}")

                    # AI 적합도 분석
                    ai_key = f"ai_btn_{tier_key}_{post.id}"
                    cached = st.session_state["ai_analysis"].get(post.id)
                    if cached:
                        ai_score = cached.get("score", 0)
                        ai_color = "green" if ai_score >= 7 else ("orange" if ai_score >= 4 else "red")
                        st.markdown(
                            f"**🤖 AI 적합도:** :{ai_color}[{ai_score}/10]  "
                            f"{cached.get('reason', '')}"
                        )
                        issues = cached.get("issues", [])
                        if issues:
                            st.warning("⚠️ " + " / ".join(issues))
                    else:
                        if st.button("🔍 AI 적합도 분석", key=ai_key, width="content"):
                            with st.spinner("LLM 분석 중..."):
                                result = run_ai_fit_analysis(
                                    post, inbox_cfg.get("llm_model", OLLAMA_MODEL)
                                )
                                st.session_state["ai_analysis"][post.id] = result
                                st.rerun()

                with col_act:
                    st.write("")
                    if st.button(
                        "✅",
                        key=f"approve_{tier_key}_{post.id}",
                        type="primary",
                        width="stretch",
                        help="승인",
                    ):
                        update_status(post.id, PostStatus.EDITING)
                        st.session_state["selected_posts"].discard(post.id)
                        st.rerun()
                    if st.button(
                        "❌",
                        key=f"decline_{tier_key}_{post.id}",
                        width="stretch",
                        help="거절",
                    ):
                        update_status(post.id, PostStatus.DECLINED)
                        st.session_state["selected_posts"].discard(post.id)
                        st.rerun()

        # ---------------------------------------------------------------------------
        # 🏆 추천 티어 (Score 80+) — 기본 펼침
        # ---------------------------------------------------------------------------
        tier_h_label = f"🏆 추천 (Score 80+) — {len(high_posts)}건"
        if high_posts:
            # 티어별 일괄 승인 버튼
            th_c1, th_c2 = st.columns([4, 1])
            with th_c1:
                st.subheader(tier_h_label)
            with th_c2:
                if st.button(
                    f"✅ 전체 승인 ({len(high_posts)}건)",
                    key="approve_all_high",
                    width="stretch",
                    type="primary",
                ):
                    for p in high_posts:
                        update_status(p.id, PostStatus.EDITING)
                    st.session_state["selected_posts"] -= {p.id for p in high_posts}
                    st.rerun()
            for post in high_posts:
                _render_post_card(post, "high")
        else:
            st.subheader(tier_h_label)
            st.caption("해당 게시글 없음")

        st.divider()

        # ---------------------------------------------------------------------------
        # 📋 일반 티어 (Score 30~79) — 기본 접힘
        # ---------------------------------------------------------------------------
        tier_n_label = f"📋 일반 (Score 30~79) — {len(normal_posts)}건"
        with st.expander(tier_n_label, expanded=False):
            if normal_posts:
                tn_c1, tn_c2 = st.columns([4, 1])
                with tn_c2:
                    if st.button(
                        f"❌ 전체 거절 ({len(normal_posts)}건)",
                        key="decline_all_normal",
                        width="stretch",
                    ):
                        for p in normal_posts:
                            update_status(p.id, PostStatus.DECLINED)
                        st.session_state["selected_posts"] -= {p.id for p in normal_posts}
                        st.rerun()
                for post in normal_posts:
                    _render_post_card(post, "normal")
            else:
                st.caption("해당 게시글 없음")

        # ---------------------------------------------------------------------------
        # 📉 낮음 티어 (Score 0~29) — 기본 접힘 + 전체 거절
        # ---------------------------------------------------------------------------
        tier_l_label = f"📉 낮음 (Score 0~29) — {len(low_posts)}건"
        with st.expander(tier_l_label, expanded=False):
            if low_posts:
                tl_c1, tl_c2 = st.columns([4, 1])
                with tl_c2:
                    if st.button(
                        f"❌ 전체 거절 ({len(low_posts)}건)",
                        key="decline_all_low",
                        width="stretch",
                    ):
                        for p in low_posts:
                            update_status(p.id, PostStatus.DECLINED)
                        st.session_state["selected_posts"] -= {p.id for p in low_posts}
                        st.rerun()
                for post in low_posts:
                    _render_post_card(post, "low")
            else:
                st.caption("해당 게시글 없음")

# ===========================================================================
# Tab 2: 편집실 (Editor) — 개선된 대본 편집기
# ===========================================================================

with tab_editor:
    import pandas as pd

    _ed_hdr, _ed_ref = st.columns([5, 1])
    with _ed_hdr:
        st.header("✏️ 편집실")
    with _ed_ref:
        if st.button("🔄 새로고침", key="editor_refresh_btn", width="stretch"):
            st.rerun()

    # ---------------------------------------------------------------------------
    # session_state 초기화
    # ---------------------------------------------------------------------------
    if "editor_idx" not in st.session_state:
        st.session_state["editor_idx"] = 0

    with SessionLocal() as session:
        approved_posts = (
            session.query(Post)
            .filter(Post.status == PostStatus.EDITING)
            .order_by(Post.created_at.desc())
            .all()
        )

        if not approved_posts:
            st.info("✏️ 편집 대기 게시글이 없습니다. 수신함에서 먼저 승인하세요.")
        else:
            # ---------------------------------------------------------------------------
            # 네비게이션 바
            # ---------------------------------------------------------------------------
            n_posts = len(approved_posts)
            idx = min(st.session_state["editor_idx"], n_posts - 1)

            nav_col, sel_col, skip_col = st.columns([1, 5, 1])
            with nav_col:
                if st.button("◀", width="stretch", help="이전 게시글",
                             disabled=idx == 0):
                    st.session_state["editor_idx"] = max(0, idx - 1)
                    st.rerun()
            with sel_col:
                post_labels = [f"[{p.id}] {p.title[:45]}" for p in approved_posts]
                new_idx = st.selectbox(
                    "게시글 선택",
                    range(n_posts),
                    index=idx,
                    format_func=lambda i: post_labels[i],
                    label_visibility="collapsed",
                )
                if new_idx != idx:
                    st.session_state["editor_idx"] = new_idx
                    st.rerun()
            with skip_col:
                if st.button("⏭ 건너뛰기", width="stretch",
                             help="편집 없이 AI 처리 대기열로 이동"):
                    update_status(approved_posts[idx].id, PostStatus.APPROVED)
                    st.session_state["editor_idx"] = max(0, idx - 1)
                    st.rerun()

            selected_post = approved_posts[idx]
            selected_post_id = selected_post.id
            st.caption(f"{idx + 1} / {n_posts}  |  Post ID: {selected_post_id}")

            # ---------------------------------------------------------------------------
            # 기존 Content / ScriptData 로드
            # ---------------------------------------------------------------------------
            existing_content = (
                session.query(Content)
                .filter(Content.post_id == selected_post_id)
                .first()
            )
            script_data = None
            if existing_content and existing_content.summary_text:
                try:
                    from ai_worker.llm import ScriptData
                    script_data = ScriptData.from_json(existing_content.summary_text)
                except Exception:
                    pass

            cfg_editor = load_pipeline_config()

            # ---------------------------------------------------------------------------
            # 좌우 분할: 원본 | AI 대본 편집
            # ---------------------------------------------------------------------------
            col_orig, col_edit = st.columns([4, 6])

            # --- 왼쪽: 원본 게시글 ---
            with col_orig:
                st.subheader("📄 원본 게시글")
                views, likes, n_coms = stats_display(selected_post.stats)
                score = selected_post.engagement_score or 0
                st.markdown(f"**{selected_post.title}**")
                st.caption(
                    f"🔥 {score:.0f}pts | 👁️ {views:,} | 👍 {likes:,} | 💬 {n_coms:,}"
                    f" | 🌐 {selected_post.site_code}"
                )

                render_image_slider(selected_post.images, key_prefix=f"editor_{selected_post_id}", width=360)

                if selected_post.content:
                    st.markdown(selected_post.content[:600] + (
                        "..." if len(selected_post.content) > 600 else ""
                    ))

                best_coms = top_comments(selected_post_id, session, limit=3)
                if best_coms:
                    st.markdown("**💬 베스트 댓글**")
                    for c in best_coms:
                        lk = f" (+{c.likes})" if c.likes else ""
                        st.markdown(
                            f"> {c.author}: {c.content[:100]}{lk}"
                        )

            # --- 오른쪽: AI 대본 편집 ---
            with col_edit:
                st.subheader("🤖 AI 대본 편집기")

                # --- 재생성 파라미터 ---
                with st.expander("⚙️ 재생성 파라미터", expanded=script_data is None):
                    _STYLE_PRESETS: dict[str, str] = {
                        "기본 (쇼츠 최적화)": "",
                        "자극적": "최대한 자극적이고 충격적인 표현을 사용하라. 감탄사와 강렬한 단어로 시작하라.",
                        "공감형": "시청자가 깊이 공감할 수 있는 감성적 접근. 따뜻하고 진정성 있는 말투.",
                        "유머러스": "가볍고 재미있는 말투, ㅋㅋ/ㄷㄷ 구어체 활용, 이모티콘 1~2개 포함.",
                        "뉴스형": "뉴스 앵커 스타일, 객관적 서술, 중립적 어조.",
                    }
                    style_choice = st.selectbox(
                        "스타일 프리셋",
                        list(_STYLE_PRESETS.keys()),
                        key=f"style_preset_{selected_post_id}",
                    )
                    extra_inst = st.text_area(
                        "추가 지시사항",
                        placeholder="예: 청소년 시청자 고려, 특정 키워드 반드시 포함...",
                        height=68,
                        key=f"extra_inst_{selected_post_id}",
                    )
                    full_extra = (
                        (_STYLE_PRESETS[style_choice] + " " + extra_inst).strip()
                        or None
                    )

                    if st.button(
                        "🔄 대본 재생성" if script_data else "🤖 AI 대본 생성",
                        width="stretch",
                        type="primary",
                        key=f"gen_{selected_post_id}",
                    ):
                        with st.spinner("LLM 대본 생성 중..."):
                            try:
                                from ai_worker.llm import generate_script, ScriptData
                                best_list = sorted(
                                    selected_post.comments,
                                    key=lambda c: c.likes,
                                    reverse=True,
                                )[:5]
                                comment_texts = [
                                    f"{c.author}: {c.content[:100]}" for c in best_list
                                ]
                                script_data = generate_script(
                                    title=selected_post.title,
                                    body=selected_post.content or "",
                                    comments=comment_texts,
                                    model=cfg_editor.get("llm_model"),
                                    extra_instructions=full_extra,
                                )
                                # 위젯 키 초기화 → 새 값 주입
                                for _k, _v in [
                                    (f"hook_{selected_post_id}", script_data.hook),
                                    (f"closer_{selected_post_id}", script_data.closer),
                                    (f"title_{selected_post_id}", script_data.title_suggestion),
                                    (f"tags_{selected_post_id}", ", ".join(script_data.tags)),
                                    (f"body_{selected_post_id}", script_data.body),
                                ]:
                                    st.session_state[_k] = _v
                                # data_editor 강제 재초기화
                                _de_key = f"body_editor_{selected_post_id}"
                                if _de_key in st.session_state:
                                    del st.session_state[_de_key]
                                st.success("대본 생성 완료!")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"대본 생성 실패: {exc}")

                st.divider()

                # --- 편집 필드 ---
                mood_options = ["funny", "serious", "shocking", "heartwarming"]

                hook = st.text_area(
                    "🎣 후킹 (Hook)",
                    value=script_data.hook if script_data else "",
                    max_chars=60,
                    height=80,
                    key=f"hook_{selected_post_id}",
                )

                st.markdown("**📝 본문 항목** (행 추가/삭제 가능)")
                _body_init = st.session_state.get(
                    f"body_{selected_post_id}",
                    script_data.body if script_data else [],
                )
                body_df_edited = st.data_editor(
                    pd.DataFrame({"내용": pd.Series(_body_init, dtype="object")}),
                    num_rows="dynamic",
                    width="stretch",
                    column_config={
                        "내용": st.column_config.TextColumn(
                            "내용", width="large", max_chars=200
                        )
                    },
                    key=f"body_editor_{selected_post_id}",
                    height=220,
                )
                body_lines = [
                    str(s).strip()
                    for s in body_df_edited["내용"].dropna()
                    if str(s).strip()
                ]

                closer = st.text_area(
                    "🔚 마무리 (Closer)",
                    value=script_data.closer if script_data else "",
                    max_chars=100,
                    height=80,
                    key=f"closer_{selected_post_id}",
                )

                st.divider()

                title_sug = st.text_input(
                    "🎬 영상 제목",
                    value=script_data.title_suggestion if script_data else "",
                    key=f"title_{selected_post_id}",
                )
                tags_input = st.text_input(
                    "🏷️ 태그 (쉼표 구분)",
                    value=", ".join(script_data.tags) if script_data else "",
                    key=f"tags_{selected_post_id}",
                )

                mood_val = script_data.mood if script_data else "funny"
                mood_idx = mood_options.index(mood_val) if mood_val in mood_options else 0
                mood = st.selectbox(
                    "🎭 분위기",
                    mood_options,
                    index=mood_idx,
                    key=f"mood_{selected_post_id}",
                )

                # BGM 제안
                bgm_name = suggest_bgm(mood)
                st.caption(f"🎵 선택 BGM: `{bgm_name}`")

                st.divider()

                # --- 예상 길이 + TTS 미리듣기 ---
                plain_preview = " ".join([hook] + body_lines + [closer])
                char_count = len(plain_preview)
                est_seconds = round(char_count / 5.5)
                len_color = "green" if 35 <= est_seconds <= 60 else "orange"

                info_c1, info_c2 = st.columns(2)
                with info_c1:
                    st.markdown(
                        f"⏱️ 예상 길이: :{len_color}[{char_count}자 ≈ **{est_seconds}초**]"
                    )
                    if est_seconds < 35:
                        st.caption("⚠️ 너무 짧습니다 (권장 40~55초)")
                    elif est_seconds > 60:
                        st.caption("⚠️ 너무 깁니다 (권장 40~55초)")

                with info_c2:
                    _has_content = bool(plain_preview.strip())
                    if st.button("▶ TTS 미리듣기", width="stretch",
                                 key=f"tts_preview_{selected_post_id}",
                                 disabled=not _has_content):
                        with st.spinner("TTS 생성 중..."):
                            try:
                                import asyncio
                                from ai_worker.tts import get_tts_engine
                                tts_engine = get_tts_engine(cfg_editor["tts_engine"])
                                preview_dir = MEDIA_DIR / "tmp"
                                preview_dir.mkdir(parents=True, exist_ok=True)
                                preview_path = (
                                    preview_dir / f"preview_{selected_post_id}.mp3"
                                )
                                asyncio.run(
                                    tts_engine.synthesize(
                                        plain_preview,
                                        cfg_editor["tts_voice"],
                                        preview_path,
                                    )
                                )
                                st.session_state[f"tts_audio_{selected_post_id}"] = str(
                                    preview_path
                                )
                                st.rerun()
                            except Exception as exc:
                                st.error(f"TTS 미리듣기 실패: {exc}")

                # TTS 오디오 재생 (캐시)
                audio_cache_key = f"tts_audio_{selected_post_id}"
                if audio_cache_key in st.session_state:
                    st.audio(st.session_state[audio_cache_key])

                st.divider()

                # --- 저장 & 건너뛰기 ---
                save_c, skip_c = st.columns(2)
                with save_c:
                    if st.button(
                        "💾 저장 & 확정",
                        width="stretch",
                        type="primary",
                        key=f"save_{selected_post_id}",
                    ):
                        try:
                            from ai_worker.llm import ScriptData
                            tags_list = [t.strip() for t in tags_input.split(",") if t.strip()]
                            confirmed = ScriptData(
                                hook=hook,
                                body=body_lines,
                                closer=closer,
                                title_suggestion=title_sug,
                                tags=tags_list,
                                mood=mood,
                            )
                            content_rec = (
                                session.query(Content)
                                .filter(Content.post_id == selected_post_id)
                                .first()
                            )
                            if content_rec is None:
                                content_rec = Content(post_id=selected_post_id)
                                session.add(content_rec)
                            content_rec.summary_text = confirmed.to_json()
                            # 편집 완료 → AI 워커 대기 상태로 전환
                            _edit_post = session.get(Post, selected_post_id)
                            if _edit_post and _edit_post.status == PostStatus.EDITING:
                                _edit_post.status = PostStatus.APPROVED
                            session.commit()
                            st.success("✅ 저장 완료! AI Worker 처리 대기열에 추가됩니다.")
                            st.session_state["editor_idx"] = max(0, idx - 1)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"저장 실패: {exc}")
                with skip_c:
                    if st.button(
                        "⏭ 건너뛰기",
                        width="stretch",
                        key=f"skip_bottom_{selected_post_id}",
                        help="편집 없이 AI 처리 대기열로 이동",
                    ):
                        update_status(selected_post_id, PostStatus.APPROVED)
                        st.session_state["editor_idx"] = max(0, idx - 1)
                        st.rerun()

# ===========================================================================
# Tab 3: 진행현황 (Progress)
# ===========================================================================

with tab_progress:
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
    _gal_hdr, _gal_ref = st.columns([5, 1])
    with _gal_hdr:
        st.header("🎬 갤러리")
        st.caption("렌더링 완료 및 업로드된 영상 (썸네일 있는 경우 표시)")
    with _gal_ref:
        if st.button("🔄 새로고침", key="gallery_refresh_btn", width="stretch"):
            st.rerun()

    with SessionLocal() as session:
        # 영상이 있는 게시글 조회
        contents = (
            session.query(Content)
            .join(Post)
            .filter(Post.status.in_([PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED]))
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
                                    from ai_worker.llm import ScriptData
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
                            ) or post.id in _hd_render_pending:
                                _gallery_action_btn(post.id, content.id)

                        with btn_col2:
                            if st.button(
                                "🗑️ 삭제",
                                key=f"delete_{content.id}",
                                width="stretch"
                            ):
                                if st.session_state.get(f"confirm_delete_{content.id}"):
                                    delete_post(post.id)
                                    st.success("삭제됨")
                                    st.rerun()
                                else:
                                    st.session_state[f"confirm_delete_{content.id}"] = True
                                    st.warning("한 번 더 클릭하면 삭제됩니다.")

# ===========================================================================
# Tab 5: 분석 (Analytics)
# ===========================================================================

with tab_analytics:
    from datetime import datetime, timedelta

    st.header("📊 분석")

    # ---------------------------------------------------------------------------
    # 기간 선택
    # ---------------------------------------------------------------------------
    hdr_c1, hdr_c2 = st.columns([4, 1])
    with hdr_c1:
        period_days = st.selectbox(
            "분석 기간",
            [7, 14, 30],
            format_func=lambda d: f"최근 {d}일",
            label_visibility="collapsed",
        )
    with hdr_c2:
        if st.button("🔄 새로고침", key="analytics_refresh", width="stretch"):
            st.rerun()

    since_dt = datetime.now(timezone.utc) - timedelta(days=period_days)

    # ---------------------------------------------------------------------------
    # DB 집계
    # ---------------------------------------------------------------------------
    with SessionLocal() as _db:
        _total_collected = (
            _db.query(func.count(Post.id))
            .filter(Post.created_at >= since_dt)
            .scalar() or 0
        )
        _total_approved = (
            _db.query(func.count(Post.id))
            .filter(
                Post.created_at >= since_dt,
                Post.status.in_([
                    PostStatus.APPROVED, PostStatus.PROCESSING,
                    PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED,
                    PostStatus.UPLOADED,
                ]),
            )
            .scalar() or 0
        )
        _total_rendered = (
            _db.query(func.count(Post.id))
            .filter(
                Post.created_at >= since_dt,
                Post.status.in_([PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED]),
            )
            .scalar() or 0
        )
        _total_uploaded = (
            _db.query(func.count(Post.id))
            .filter(Post.created_at >= since_dt, Post.status == PostStatus.UPLOADED)
            .scalar() or 0
        )
        # 업로드된 컨텐츠 목록 (analytics 데이터 포함)
        _uploaded_contents: list[tuple[Post, Content]] = (
            _db.query(Post, Content)
            .join(Content, Content.post_id == Post.id)
            .filter(Post.status == PostStatus.UPLOADED)
            .order_by(Post.updated_at.desc())
            .all()
        )

    _conversion_rate = (_total_uploaded / _total_collected * 100) if _total_collected else 0.0

    # ---------------------------------------------------------------------------
    # 📈 주간 생산성
    # ---------------------------------------------------------------------------
    st.subheader("📈 파이프라인 생산성")
    with st.container(border=True):
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("수집", f"{_total_collected:,}건")
        m2.metric("승인", f"{_total_approved:,}건")
        m3.metric("렌더링", f"{_total_rendered:,}건")
        m4.metric("업로드", f"{_total_uploaded:,}건")
        m5.metric("전환율", f"{_conversion_rate:.1f}%")

        # 퍼널 프로그레스바
        if _total_collected:
            st.markdown("**수집 → 업로드 전환 퍼널**")
            stages = [
                ("수집", _total_collected, "#4e8cff"),
                ("승인", _total_approved, "#48bb78"),
                ("렌더링", _total_rendered, "#ed8936"),
                ("업로드", _total_uploaded, "#e53e3e"),
            ]
            for label, count, color in stages:
                pct = count / _total_collected if _total_collected else 0
                st.markdown(
                    f"""<div style="margin:4px 0">
<span style="display:inline-block;width:60px;font-size:0.8rem">{label}</span>
<span style="display:inline-block;height:18px;width:{int(pct*400)}px;
background:{color};border-radius:3px;vertical-align:middle"></span>
<span style="margin-left:8px;font-size:0.85rem">{count:,}건 ({pct*100:.1f}%)</span>
</div>""",
                    unsafe_allow_html=True,
                )

    # ---------------------------------------------------------------------------
    # 🏆 Top 5 영상
    # ---------------------------------------------------------------------------
    st.subheader("🏆 Top 5 영상 (조회수 기준)")

    # upload_meta 또는 post.stats 에서 조회수 수집
    _ranked: list[dict] = []
    for _post, _cnt in _uploaded_contents:
        _meta = _cnt.upload_meta or {}
        # YouTube Analytics에서 수집된 최신 analytics 우선, 없으면 post.stats
        _yt = _meta.get("youtube", {})
        _analytics = _yt.get("analytics", {})
        _views = _analytics.get("views") or (_post.stats or {}).get("views", 0)
        _likes = _analytics.get("likes") or (_post.stats or {}).get("likes", 0)
        _yt_url = _yt.get("url", "")
        _ranked.append({
            "title": _post.title,
            "views": int(_views),
            "likes": int(_likes),
            "url": _yt_url,
            "post_id": _post.id,
            "analytics": _analytics,
        })

    _ranked.sort(key=lambda x: x["views"], reverse=True)

    if _ranked:
        with st.container(border=True):
            for rank, item in enumerate(_ranked[:5], 1):
                rc1, rc2, rc3 = st.columns([6, 2, 2])
                with rc1:
                    _title_str = item["title"][:55] + "..." if len(item["title"]) > 55 else item["title"]
                    if item["url"]:
                        st.markdown(f"**{rank}.** [{_title_str}]({item['url']})")
                    else:
                        st.markdown(f"**{rank}.** {_title_str}")
                with rc2:
                    st.markdown(f"👁️ **{item['views']:,}**회")
                with rc3:
                    st.markdown(f"👍 {item['likes']:,}")
                if item["analytics"].get("avg_watch_pct"):
                    st.caption(
                        f"   평균 시청률 {item['analytics']['avg_watch_pct']:.1f}% · "
                        f"수집일: {item['analytics'].get('collected_at', '?')[:10]}"
                    )
    else:
        st.info("업로드된 영상이 없습니다.")

    # ---------------------------------------------------------------------------
    # 📉 성과 분석
    # ---------------------------------------------------------------------------
    st.subheader("📉 성과 분석")
    with st.container(border=True):
        if _ranked:
            _all_views = [r["views"] for r in _ranked]
            _all_likes = [r["likes"] for r in _ranked]
            _analytics_items = [r["analytics"] for r in _ranked if r["analytics"]]

            avg_views = sum(_all_views) / len(_all_views) if _all_views else 0
            avg_likes = sum(_all_likes) / len(_all_likes) if _all_likes else 0
            avg_watch = (
                sum(a["avg_watch_pct"] for a in _analytics_items if "avg_watch_pct" in a)
                / len([a for a in _analytics_items if "avg_watch_pct" in a])
                if any("avg_watch_pct" in a for a in _analytics_items) else None
            )
            sub_conv = (
                sum(a.get("subscriber_gained", 0) for a in _analytics_items)
            )

            pa1, pa2, pa3, pa4 = st.columns(4)
            pa1.metric("평균 조회수", f"{avg_views:,.0f}회")
            pa2.metric("평균 좋아요", f"{avg_likes:,.0f}")
            pa3.metric(
                "평균 시청 유지율",
                f"{avg_watch:.1f}%" if avg_watch is not None else "데이터 없음"
            )
            pa4.metric("구독 전환 합계", f"{sub_conv:,}명")
        else:
            st.caption("업로드 후 YouTube Analytics 수집 시 성과 지표가 표시됩니다.")

        # YouTube Analytics 수집 버튼
        st.divider()
        st.markdown("**YouTube Analytics 수동 수집**")
        st.caption("업로드된 영상의 조회수·좋아요·시청 유지율을 YouTube Analytics API에서 가져옵니다.")
        if st.button("📡 Analytics 수집", key="fetch_analytics", width="content"):
            _fetched, _errors = 0, 0
            with st.spinner("YouTube Analytics 수집 중..."):
                for _post, _cnt in _uploaded_contents:
                    _meta = dict(_cnt.upload_meta or {})
                    _yt = _meta.get("youtube", {})
                    _video_id = _yt.get("video_id")
                    if not _video_id:
                        continue
                    try:
                        from uploaders.youtube import YouTubeUploader
                        _uploader = YouTubeUploader()
                        _stats = _uploader.fetch_analytics(_video_id)
                        if _stats:
                            _yt["analytics"] = {
                                **_stats,
                                "collected_at": datetime.now(timezone.utc).isoformat(),
                            }
                            _meta["youtube"] = _yt
                            with SessionLocal() as _s:
                                _c = _s.query(Content).filter_by(post_id=_post.id).first()
                                if _c:
                                    _c.upload_meta = _meta
                                    _s.commit()
                            _fetched += 1
                    except Exception as _ex:
                        log.warning("Analytics 수집 실패 post_id=%d: %s", _post.id, _ex)
                        _errors += 1
            if _fetched:
                st.success(f"✅ {_fetched}건 수집 완료" + (f" ({_errors}건 실패)" if _errors else ""))
                st.rerun()
            else:
                st.warning("수집된 데이터가 없습니다. YouTube 인증 정보를 확인하세요.")

    # ---------------------------------------------------------------------------
    # 🎯 AI 인사이트
    # ---------------------------------------------------------------------------
    st.subheader("🎯 AI 인사이트")

    _insight_key = f"analytics_insight_{period_days}"
    with st.container(border=True):
        if st.button("✨ 인사이트 생성", key="gen_insight", width="content", type="primary"):
            if not _ranked:
                st.warning("업로드된 영상 데이터가 없습니다.")
            else:
                with st.spinner("LLM 분석 중..."):
                    try:
                        import requests as _req
                        _data_summary = "\n".join(
                            f"- {r['title'][:60]}: 조회수 {r['views']:,}, 좋아요 {r['likes']:,}"
                            + (f", 시청유지율 {r['analytics']['avg_watch_pct']:.1f}%" if r['analytics'].get('avg_watch_pct') else "")
                            for r in _ranked[:10]
                        )
                        _prompt = f"""당신은 유튜브 쇼츠 채널 성과 분석 전문가입니다.
아래 최근 {period_days}일 업로드 영상 성과 데이터를 분석하고,
운영자에게 유용한 인사이트 3~5가지를 간결하게 한국어로 작성하세요.

## 성과 데이터
수집: {_total_collected}건 → 승인: {_total_approved}건 → 업로드: {_total_uploaded}건 (전환율 {_conversion_rate:.1f}%)
업로드 영상 목록:
{_data_summary}

## 인사이트 형식
- 어떤 주제/패턴이 잘 됐는지
- 개선이 필요한 부분
- 다음 {period_days}일 운영 전략 제안
각 항목은 "- " 로 시작하는 한 줄 문장으로 작성하세요."""

                        _resp = _req.post(
                            f"{get_ollama_host()}/api/generate",
                            json={
                                "model": load_pipeline_config().get("llm_model", OLLAMA_MODEL),
                                "prompt": _prompt,
                                "stream": False,
                                "options": {"num_predict": 512, "temperature": 0.7},
                            },
                            timeout=120,
                        )
                        _resp.raise_for_status()
                        _insight_text = _resp.json().get("response", "").strip()
                        st.session_state[_insight_key] = _insight_text
                    except Exception as _ex:
                        st.error(f"인사이트 생성 실패: {_ex}")

        _saved_insight = st.session_state.get(_insight_key)
        if _saved_insight:
            st.markdown(_saved_insight)
        else:
            st.caption("'인사이트 생성' 버튼을 눌러 LLM 분석을 시작하세요.")

    # ---------------------------------------------------------------------------
    # 🎯 피드백 파이프라인 반영
    # ---------------------------------------------------------------------------
    st.subheader("🎯 피드백 파이프라인 반영")

    with st.container(border=True):
        from analytics.feedback import (
            load_feedback_config, generate_structured_insights,
            apply_feedback, build_performance_summary,
        )

        _fb_cfg = load_feedback_config()
        _fb_updated = _fb_cfg.get("updated_at")
        if _fb_updated:
            st.caption(f"마지막 반영: {_fb_updated[:19].replace('T', ' ')} UTC")

        _col_fb1, _col_fb2 = st.columns([1, 1])
        with _col_fb1:
            if st.button(
                "🔄 구조화 인사이트 생성 후 반영",
                key="apply_feedback_btn",
                width="stretch",
                type="primary",
                help="LLM이 성과 데이터를 분석해 대본 프롬프트·mood 가중치를 자동 업데이트합니다.",
            ):
                with st.spinner("성과 분석 + LLM 인사이트 생성 중..."):
                    try:
                        with SessionLocal() as _fb_s:
                            _perf = build_performance_summary(_fb_s, days_back=period_days)
                        if not _perf:
                            st.warning("분석할 업로드 데이터가 없습니다.")
                        else:
                            _insights = generate_structured_insights(
                                _perf,
                                llm_model=load_pipeline_config().get("llm_model"),
                            )
                            apply_feedback(_insights)
                            st.success("✅ 피드백이 파이프라인에 반영되었습니다.")
                            st.rerun()
                    except Exception as _ex:
                        st.error(f"피드백 반영 실패: {_ex}")

        with _col_fb2:
            if st.button(
                "🗑️ 피드백 초기화",
                key="reset_feedback_btn",
                width="stretch",
                help="feedback_config.json을 기본값으로 초기화합니다.",
            ):
                from config.settings import FEEDBACK_CONFIG_PATH
                FEEDBACK_CONFIG_PATH.unlink(missing_ok=True)
                st.success("✅ 피드백 설정이 초기화되었습니다.")
                st.rerun()

        # 현재 피드백 설정 표시
        _extra = _fb_cfg.get("extra_instructions", "")
        _weights = _fb_cfg.get("mood_weights", {})
        if _extra:
            st.info(f"**현재 대본 지시사항:** {_extra[:200]}")
        if any(v != 1.0 for v in _weights.values()):
            _w_lines = " | ".join(f"{k}: ×{v:.1f}" for k, v in _weights.items() if v != 1.0)
            st.caption(f"Mood 가중치 조정: {_w_lines}")

    # ---------------------------------------------------------------------------
    # 🧪 A/B 테스트
    # ---------------------------------------------------------------------------
    st.subheader("🧪 A/B 테스트")

    from analytics.ab_test import (
        list_tests, create_test, cancel_test,
        evaluate_group, apply_winner, VARIANT_PRESETS,
    )

    _ab_tests = list_tests()
    _active_tests  = [t for t in _ab_tests if t.status == "active"]
    _done_tests    = [t for t in _ab_tests if t.status == "completed"]
    _all_tests     = _ab_tests  # cancelled 포함 전체

    # ── 진행 중인 테스트 ──────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**진행 중인 테스트**")
        if not _active_tests:
            st.caption("활성 A/B 테스트 없음")
        else:
            for _t in _active_tests:
                _tc1, _tc2, _tc3 = st.columns([4, 2, 2])
                with _tc1:
                    st.markdown(
                        f"🟢 **{_t.name}**  \n"
                        f"`{_t.group_id}` · "
                        f"A: {_t.config_a.get('label', _t.config_a.get('preset_key','?'))} / "
                        f"B: {_t.config_b.get('label', _t.config_b.get('preset_key','?'))}"
                    )
                with _tc2:
                    if st.button("📊 결과 평가", key=f"eval_{_t.group_id}", width="stretch"):
                        with SessionLocal() as _es:
                            _w = evaluate_group(_t.group_id, _es)
                        if _w:
                            st.success(f"승자: Variant {_w}")
                        else:
                            st.warning("데이터 부족 (최소 3건/변형 필요)")
                        st.rerun()
                with _tc3:
                    if st.button("❌ 취소", key=f"cancel_{_t.group_id}", width="stretch"):
                        cancel_test(_t.group_id)
                        st.rerun()

    # ── 완료된 테스트 ──────────────────────────────────────────────────────────
    if _done_tests:
        with st.container(border=True):
            st.markdown("**완료된 테스트**")
            for _t in _done_tests:
                _dc1, _dc2 = st.columns([5, 2])
                with _dc1:
                    _a_avg = _t.stats.get("A", {}).get("avg_views", 0)
                    _b_avg = _t.stats.get("B", {}).get("avg_views", 0)
                    _a_n   = _t.stats.get("A", {}).get("posts", 0)
                    _b_n   = _t.stats.get("B", {}).get("posts", 0)
                    _winner_badge = f"🏆 승자: {_t.winner}" if _t.winner else "판정 없음"
                    st.markdown(
                        f"✅ **{_t.name}**  \n"
                        f"A: {_a_avg:,.0f}회/{_a_n}건 | B: {_b_avg:,.0f}회/{_b_n}건  \n"
                        f"{_winner_badge}"
                        + (" ✔ 적용됨" if _t.winner_applied else "")
                    )
                with _dc2:
                    if _t.winner and not _t.winner_applied:
                        if st.button(
                            f"✨ 승자({_t.winner}) 반영",
                            key=f"apply_winner_{_t.group_id}",
                            width="stretch",
                            type="primary",
                        ):
                            if apply_winner(_t.group_id):
                                st.success(f"Variant {_t.winner} 설정이 파이프라인에 반영되었습니다.")
                            else:
                                st.error("반영 실패")
                            st.rerun()

    # ── 새 테스트 생성 ──────────────────────────────────────────────────────────
    with st.expander("➕ 새 A/B 테스트 생성", expanded=False):
        _preset_options = list(VARIANT_PRESETS.keys())
        _preset_labels  = {k: v["label"] for k, v in VARIANT_PRESETS.items()}

        _new_name = st.text_input("테스트 이름", placeholder="예: hook 스타일 테스트 2026-02")
        _col_a, _col_b = st.columns(2)
        with _col_a:
            _preset_a = st.selectbox(
                "Variant A",
                _preset_options,
                format_func=lambda k: f"{k} — {_preset_labels[k]}",
                key="ab_preset_a",
            )
        with _col_b:
            _preset_b = st.selectbox(
                "Variant B",
                _preset_options,
                index=1,
                format_func=lambda k: f"{k} — {_preset_labels[k]}",
                key="ab_preset_b",
            )

        if _preset_a == _preset_b:
            st.warning("Variant A와 B가 동일합니다. 다른 프리셋을 선택하세요.")
        elif st.button("테스트 시작", key="create_ab_test", type="primary", width="content"):
            if not _new_name.strip():
                st.error("테스트 이름을 입력하세요.")
            else:
                _new_test = create_test(_new_name.strip(), _preset_a, _preset_b)
                st.success(
                    f"✅ A/B 테스트 생성 완료! (group_id: `{_new_test.group_id}`)  \n"
                    f"이후 APPROVED 포스트는 자동으로 A/B 변형이 배정됩니다."
                )
                st.rerun()


# ===========================================================================
# Tab 6: 설정 (Settings)
# ===========================================================================

with tab_settings:
    _set_hdr, _set_ref = st.columns([5, 1])
    with _set_hdr:
        st.header("⚙️ 파이프라인 설정")
    with _set_ref:
        if st.button("🔄 새로고침", key="settings_refresh_btn", width="stretch"):
            st.rerun()

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
    llm_model = st.text_input("LLM 모델 (Ollama)", value=cfg.get("llm_model", "qwen2.5:14b"))

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

    st.divider()

    # ---------------------------------------------------------------------------
    # 플랫폼 인증
    # ---------------------------------------------------------------------------
    st.subheader("🔑 플랫폼 인증")
    st.caption("저장 후 인증 정보는 마스킹되며 수정만 가능합니다.")

    all_creds = load_credentials_config()

    for platform, fields in PLATFORM_CREDENTIAL_FIELDS.items():
        platform_creds: dict = all_creds.get(platform, {})
        is_configured = bool(platform_creds)
        edit_key = f"editing_{platform}"

        if edit_key not in st.session_state:
            st.session_state[edit_key] = False

        with st.container(border=True):
            col_title, col_btn = st.columns([4, 1])
            with col_title:
                status_badge = "✅ 설정됨" if is_configured else "⚠️ 미설정"
                st.markdown(f"**{platform.upper()}** — {status_badge}")
            with col_btn:
                if not st.session_state[edit_key]:
                    btn_label = "✏️ 수정" if is_configured else "➕ 설정"
                    if st.button(btn_label, key=f"edit_btn_{platform}", width="stretch"):
                        st.session_state[edit_key] = True
                        st.rerun()

            if st.session_state[edit_key]:
                # 수정 모드 — 입력 필드 표시 (기존 값 미노출)
                new_values: dict[str, str] = {}
                for field in fields:
                    kwargs = {
                        "label": field["label"],
                        "key": f"cred_{platform}_{field['key']}",
                        "placeholder": "값을 입력하세요 (빈칸이면 기존 값 유지)",
                        "help": field.get("help", ""),
                    }
                    if field["type"] == "textarea":
                        new_values[field["key"]] = st.text_area(**kwargs, height=120)
                    elif field["type"] == "password":
                        new_values[field["key"]] = st.text_input(**kwargs, type="password")
                    else:
                        new_values[field["key"]] = st.text_input(**kwargs)

                save_col, cancel_col = st.columns(2)
                with save_col:
                    if st.button("💾 저장", key=f"save_{platform}", type="primary", width="stretch"):
                        # 입력된 값만 병합 (빈칸은 기존 값 유지)
                        merged = dict(platform_creds)
                        updated_keys = [k for k, v in new_values.items() if v.strip()]

                        if not updated_keys:
                            st.warning("변경된 값이 없습니다.")
                        else:
                            for k in updated_keys:
                                merged[k] = new_values[k].strip()

                            all_creds[platform] = merged
                            save_credentials_config(all_creds)

                            # YouTube: token_json → youtube_token.json 동기화
                            if platform == "youtube" and "token_json" in updated_keys:
                                if not _write_youtube_token(merged["token_json"]):
                                    st.error("token_json이 유효한 JSON 형식이 아닙니다.")
                                    st.stop()

                            st.session_state[edit_key] = False
                            st.success(f"{platform.upper()} 인증 정보가 저장되었습니다.")
                            st.rerun()

                with cancel_col:
                    if st.button("취소", key=f"cancel_{platform}", width="stretch"):
                        st.session_state[edit_key] = False
                        st.rerun()

            else:
                # 뷰 모드 — 마스킹된 값 표시
                if platform_creds:
                    for field in fields:
                        has_value = bool(platform_creds.get(field["key"], ""))
                        masked = "●●●●●●●●" if has_value else "미설정"
                        st.text(f"{field['label']}: {masked}")
                else:
                    st.caption("인증 정보가 설정되지 않았습니다.")

    st.divider()

    st.divider()

    # 자동 업로드 설정
    st.subheader("📤 자동 업로드")
    st.caption("RENDERED 상태 영상을 AI 워커가 자동으로 업로드합니다. 비활성화 시 갤러리의 '업로드' 버튼으로만 업로드합니다.")

    auto_upload_on = st.checkbox(
        "자동 업로드 활성화",
        value=cfg.get("auto_upload", "false") == "true",
        help="활성화 시 고화질 렌더링 완료 즉시 자동으로 플랫폼에 업로드됩니다.",
    )

    st.divider()

    # 자동 승인 설정
    st.subheader("🤖 자동 승인")
    st.caption("점수 임계값 이상의 게시글을 수신함 진입 즉시 자동으로 승인합니다.")

    auto_approve_on = st.checkbox(
        "자동 승인 활성화",
        value=cfg.get("auto_approve_enabled") == "true",
        help="활성화 시 수신함 로드마다 임계값 이상 게시글이 자동 승인됩니다.",
    )
    auto_approve_thresh = st.number_input(
        "자동 승인 임계값 (Engagement Score)",
        min_value=0,
        max_value=100,
        value=int(cfg.get("auto_approve_threshold", "80")),
        step=5,
        help="이 점수 이상인 게시글이 자동 승인됩니다. 80점 권장.",
    )

    st.divider()

    # LLM 파이프라인 설정
    st.subheader("🔬 LLM 파이프라인")
    st.caption(
        "활성화 시 resource_analyzer → llm_chunker → text_validator → scene_director "
        "5-Phase 파이프라인으로 대본을 생성합니다."
    )
    use_content_processor = st.checkbox(
        "content_processor 사용 (5-Phase 파이프라인)",
        value=cfg.get("use_content_processor") == "true",
        help="비활성화 시 기존 generate_script() 경로(레거시)를 사용합니다.",
    )

    st.divider()

    # 저장 버튼 (파이프라인 설정만)
    if st.button("💾 파이프라인 설정 저장", type="primary"):
        new_cfg = {
            "tts_engine": selected_engine,
            "tts_voice": selected_voice,
            "llm_model": llm_model,
            "upload_platforms": json.dumps(selected_platforms),
            "upload_privacy": selected_privacy,
            "auto_upload": "true" if auto_upload_on else "false",
            "auto_approve_enabled": "true" if auto_approve_on else "false",
            "auto_approve_threshold": str(auto_approve_thresh),
            "use_content_processor": "true" if use_content_processor else "false",
        }
        save_pipeline_config(new_cfg)
        st.success("✅ 설정이 저장되었습니다.")

    # 현재 설정 표시
    with st.expander("🔍 현재 저장된 설정 보기"):
        st.json(load_pipeline_config())


# ===========================================================================
# Tab 7: LLM 이력
# ===========================================================================

with tab_llm_log:
    _llm_hdr, _llm_ref = st.columns([5, 1])
    with _llm_hdr:
        st.header("🔬 LLM 호출 이력")
    with _llm_ref:
        if st.button("🔄 새로고침", key="llm_refresh_btn", width="stretch"):
            st.rerun()

    # 필터 컨트롤
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_call_type = st.selectbox(
            "호출 유형", ["전체", "chunk", "generate_script"], key="llm_filter_type"
        )
    with col_f2:
        filter_success = st.selectbox(
            "성공 여부", ["전체", "성공", "실패"], key="llm_filter_success"
        )
    with col_f3:
        filter_days = st.selectbox(
            "기간",
            [7, 30, 90],
            format_func=lambda d: f"최근 {d}일",
            key="llm_filter_days",
        )

    with SessionLocal() as _db:
        _cutoff = datetime.now(timezone.utc) - timedelta(days=filter_days)

        # 전체 기간 통계 (호출유형/성공여부 필터 무관)
        _base_q = _db.query(LLMLog).filter(LLMLog.created_at >= _cutoff)
        _total_period = _base_q.count()
        _success_period = _base_q.filter(LLMLog.success == True).count()  # noqa: E712
        _avg_dur = (
            _db.query(func.avg(LLMLog.duration_ms))
            .filter(LLMLog.created_at >= _cutoff)
            .scalar()
            or 0
        )

        # 필터 적용 로그 목록
        _fq = _db.query(LLMLog).filter(LLMLog.created_at >= _cutoff)
        if filter_call_type != "전체":
            _fq = _fq.filter(LLMLog.call_type == filter_call_type)
        if filter_success == "성공":
            _fq = _fq.filter(LLMLog.success == True)  # noqa: E712
        elif filter_success == "실패":
            _fq = _fq.filter(LLMLog.success == False)  # noqa: E712

        _logs = _fq.order_by(LLMLog.created_at.desc()).limit(200).all()

    # 통계 카드
    _sc1, _sc2, _sc3 = st.columns(3)
    _sc1.metric("총 호출 (기간)", _total_period)
    _sc2.metric(
        "성공률",
        f"{(_success_period / _total_period * 100):.1f}%" if _total_period else "N/A",
    )
    _sc3.metric("평균 응답시간", f"{_avg_dur:.0f}ms" if _avg_dur else "N/A")

    st.divider()

    if not _logs:
        st.info("조건에 맞는 이력이 없습니다.")
    else:
        st.caption(f"최근 {filter_days}일 이력 (최대 200건 표시)")
        for _log in _logs:
            _icon = "✅" if _log.success else "❌"
            _strat = _log.strategy or "-"
            _hdr = (
                f"{_icon} #{_log.id} "
                f"[{_log.call_type}] "
                f"{to_kst(_log.created_at)} | "
                f"전략={_strat} | 이미지={_log.image_count}장 | {_log.duration_ms}ms"
            )
            with st.expander(_hdr):
                _mc, _rc = st.columns(2)
                with _mc:
                    st.markdown(
                        f"**모델:** `{_log.model_name or '-'}`  \n"
                        f"**본문 길이:** {_log.content_length}자"
                    )
                    if _log.error_message:
                        st.error(_log.error_message)
                with _rc:
                    if _log.parsed_result:
                        st.markdown("**파싱 결과**")
                        st.json(_log.parsed_result)

                st.markdown("**프롬프트**")
                st.code(_log.prompt_text or "(없음)", language="text")
                st.markdown("**LLM 응답**")
                st.code(_log.raw_response or "(없음)", language="text")
