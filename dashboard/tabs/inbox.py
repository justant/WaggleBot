"""수신함 (Inbox) 탭."""

import json
import logging
import re

import streamlit as st
from sqlalchemy import func, or_

from ai_worker.llm.client import call_ollama_raw
from config.settings import load_pipeline_config, OLLAMA_MODEL
from crawlers.plugin_manager import list_crawlers
from db.models import Post, PostStatus
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, top_comments, update_status, check_ollama_health,
)
from dashboard.components.image_slider import render_image_slider

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 탭 전용 헬퍼
# ---------------------------------------------------------------------------

def _run_ai_fit_analysis(post: Post, model: str) -> dict:
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
        raw = call_ollama_raw(prompt=prompt, model=model)
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as exc:
        log.warning("AI 적합도 분석 실패: %s", exc)
    return {"score": 0, "reason": "분석 실패 또는 LLM 응답 오류", "issues": []}


# ---------------------------------------------------------------------------
# 탭 렌더
# ---------------------------------------------------------------------------

def render() -> None:
    """수신함 탭 렌더링."""

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

    # 처리 현황 progress bar
    with SessionLocal() as _psess:
        _total_ever = _psess.query(func.count(Post.id)).scalar() or 0
        _total_decided = _psess.query(func.count(Post.id)).filter(
            Post.status.notin_([PostStatus.COLLECTED])
        ).scalar() or 0
    if _total_ever:
        _pct = _total_decided / _total_ever
        st.progress(_pct, text=f"전체 처리율: {_total_decided}/{_total_ever} ({_pct*100:.1f}%)")

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        site_filter = st.multiselect(
            "사이트 필터", [c["site_code"] for c in list_crawlers()], default=[], placeholder="전체"
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
        _all_post_ids = {p.id for p in posts}
        _all_selected = bool(_all_post_ids) and _all_post_ids.issubset(
            st.session_state["selected_posts"]
        )

        def _on_select_all_toggle() -> None:
            if st.session_state.get("inbox_select_all_cb"):
                st.session_state["selected_posts"] = _all_post_ids.copy()
            else:
                st.session_state["selected_posts"] = set()

        # 체크박스 표시값을 실제 선택 상태에 동기화
        st.session_state["inbox_select_all_cb"] = _all_selected

        bc0, bc1, bc2 = st.columns([1, 1, 1])
        with bc0:
            st.checkbox(
                "전체 선택/해제",
                key="inbox_select_all_cb",
                on_change=_on_select_all_toggle,
            )
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
                            if not check_ollama_health():
                                st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
                            else:
                                with st.spinner("LLM 분석 중..."):
                                    result = _run_ai_fit_analysis(
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
