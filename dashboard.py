"""
WaggleBot 관리자 대시보드

Streamlit 기반 웹 UI
- 게시글 승인/거절
- 진행 상태 모니터링
- 갤러리 및 업로드 관리
"""

import json
import logging
import re
from datetime import timezone, timedelta
from pathlib import Path

import requests as _http
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from sqlalchemy import func, or_

from config.settings import (
    TTS_VOICES, MEDIA_DIR, ASSETS_DIR,
    PLATFORM_CREDENTIAL_FIELDS,
    get_ollama_host, OLLAMA_MODEL,
    load_pipeline_config, save_pipeline_config,
    load_credentials_config, save_credentials_config,
)
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
    PostStatus.RENDERED: "🎬",
    PostStatus.UPLOADED: "📤",
    PostStatus.DECLINED: "❌",
    PostStatus.FAILED: "⚠️",
}

# ---------------------------------------------------------------------------
# 탭 구성
# ---------------------------------------------------------------------------

tab_inbox, tab_editor, tab_progress, tab_gallery, tab_settings = st.tabs(
    ["📥 수신함", "✏️ 편집실", "⚙️ 진행현황", "🎬 갤러리", "⚙️ 설정"]
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
        if st.button("🔄 새로고침", use_container_width=True):
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
                use_container_width=True,
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
                use_container_width=True,
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
                        "",
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
                        if st.button("🔍 AI 적합도 분석", key=ai_key, use_container_width=False):
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
                        use_container_width=True,
                        help="승인",
                    ):
                        update_status(post.id, PostStatus.EDITING)
                        st.session_state["selected_posts"].discard(post.id)
                        st.rerun()
                    if st.button(
                        "❌",
                        key=f"decline_{tier_key}_{post.id}",
                        use_container_width=True,
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
                    use_container_width=True,
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
                        use_container_width=True,
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
                        use_container_width=True,
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
                if st.button("◀", use_container_width=True, help="이전 게시글",
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
                if st.button("⏭ 건너뛰기", use_container_width=True,
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
                        use_container_width=True,
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
                    use_container_width=True,
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
                    if st.button("▶ TTS 미리듣기", use_container_width=True,
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
                        use_container_width=True,
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
                        use_container_width=True,
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
    st.header("⚙️ 진행 현황")
    st.caption("AI 워커 처리 상태 및 실시간 모니터링")

    progress_statuses = [
        PostStatus.EDITING,
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
    st.caption("렌더링 완료 및 업로드된 영상 (썸네일 있는 경우 표시)")

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

                        # 썸네일
                        thumb_path_str = (content.upload_meta or {}).get("thumbnail_path")
                        if thumb_path_str:
                            thumb_path = Path(thumb_path_str)
                            if thumb_path.exists():
                                st.image(str(thumb_path), use_container_width=True)

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
                            if post.status == PostStatus.RENDERED:
                                if st.button(
                                    "📤 업로드",
                                    key=f"upload_{content.id}",
                                    use_container_width=True
                                ):
                                    try:
                                        from uploaders.uploader import upload_post
                                        with SessionLocal() as upload_session:
                                            _post = upload_session.get(Post, post.id)
                                            _content = upload_session.query(Content).filter_by(post_id=post.id).first()
                                            ok = upload_post(_post, _content, upload_session)
                                            if ok:
                                                _post.status = PostStatus.UPLOADED
                                                upload_session.commit()
                                                st.success("업로드 완료!")
                                                st.rerun()
                                            else:
                                                st.error("일부 플랫폼 업로드 실패. 로그를 확인하세요.")
                                    except Exception as _e:
                                        st.error(f"업로드 오류: {_e}")

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
                    if st.button(btn_label, key=f"edit_btn_{platform}", use_container_width=True):
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
                    if st.button("💾 저장", key=f"save_{platform}", type="primary", use_container_width=True):
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
                    if st.button("취소", key=f"cancel_{platform}", use_container_width=True):
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

    # 저장 버튼 (파이프라인 설정만)
    if st.button("💾 파이프라인 설정 저장", type="primary"):
        new_cfg = {
            "tts_engine": selected_engine,
            "tts_voice": selected_voice,
            "llm_model": llm_model,
            "upload_platforms": json.dumps(selected_platforms),
            "upload_privacy": selected_privacy,
            "auto_approve_enabled": "true" if auto_approve_on else "false",
            "auto_approve_threshold": str(auto_approve_thresh),
        }
        save_pipeline_config(new_cfg)
        st.success("✅ 설정이 저장되었습니다.")

    # 현재 설정 표시
    with st.expander("🔍 현재 저장된 설정 보기"):
        st.json(load_pipeline_config())
