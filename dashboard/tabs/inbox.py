"""수신함 (Inbox) 탭."""

import logging
import threading

import streamlit as st
from sqlalchemy import func, or_

from config.settings import load_pipeline_config, OLLAMA_MODEL
from crawlers.plugin_manager import list_crawlers
from db.models import Post, PostStatus
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, update_status, check_ollama_health, batch_update_status,
)
from dashboard.components.image_slider import render_image_slider
from dashboard.workers.ai_analysis_tasks import (
    get_analysis_task, submit_analysis_task, clear_analysis_task,
)

log = logging.getLogger(__name__)


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
    if "hidden_post_ids" not in st.session_state:
        st.session_state["hidden_post_ids"] = set()

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
            # 새로고침 시 hidden_post_ids 초기화 (DB 상태와 동기화)
            st.session_state["hidden_post_ids"] = set()
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
    # 데이터 조회 (N+1 방지: 댓글 일괄 사전 로드)
    # ---------------------------------------------------------------------------
    from db.models import Comment

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

        # 댓글 일괄 사전 로드 (N+1 → 1+1 쿼리)
        _all_comments: dict[int, list] = {}
        _post_ids = [p.id for p in posts]
        if _post_ids:
            _comments_raw = (
                session.query(Comment)
                .filter(Comment.post_id.in_(_post_ids))
                .order_by(Comment.likes.desc())
                .all()
            )
            for _c in _comments_raw:
                _all_comments.setdefault(_c.post_id, []).append(_c)

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
                _ids = list(st.session_state["selected_posts"])
                threading.Thread(
                    target=batch_update_status,
                    args=(_ids, PostStatus.EDITING),
                    daemon=True,
                ).start()
                st.session_state["hidden_post_ids"].update(_ids)
                st.session_state["selected_posts"] = set()
                st.rerun()
        with bc2:
            if st.button(
                f"❌ 선택 ({n_selected}건) 일괄 거절",
                disabled=n_selected == 0,
                width="stretch",
            ):
                _ids = list(st.session_state["selected_posts"])
                threading.Thread(
                    target=batch_update_status,
                    args=(_ids, PostStatus.DECLINED),
                    daemon=True,
                ).start()
                st.session_state["hidden_post_ids"].update(_ids)
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
        def _render_post_card(
            post: Post, tier_key: str, preloaded_comments: dict
        ) -> None:
            """게시글 카드 1개를 렌더링한다."""
            # 낙관적 UI — 이미 처리된 카드는 렌더링 스킵
            if post.id in st.session_state.get("hidden_post_ids", set()):
                return

            views, likes, n_comments = stats_display(post.stats)
            score = post.engagement_score or 0
            best_coms = preloaded_comments.get(post.id, [])[:2]
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

                    # AI 적합도 분석 (비동기)
                    ai_key = f"ai_btn_{tier_key}_{post.id}"
                    cached = st.session_state["ai_analysis"].get(post.id)
                    _task = get_analysis_task(post.id)

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

                    elif _task and _task["status"] == "running":
                        st.info("🔍 AI 분석 중...")

                    elif _task and _task["status"] in ("done", "error"):
                        # 완료 → ai_analysis cache에 저장 후 task 정리
                        st.session_state["ai_analysis"][post.id] = _task["result"]
                        clear_analysis_task(post.id)
                        st.rerun(scope="fragment")

                    else:
                        if st.button("🔍 AI 적합도 분석", key=ai_key, width="content"):
                            if not check_ollama_health():
                                st.error("❌ LLM 서버에 연결할 수 없습니다.")
                            else:
                                submit_analysis_task(
                                    post.id,
                                    title=post.title,
                                    content=post.content or "",
                                    model=inbox_cfg.get("llm_model", OLLAMA_MODEL),
                                )
                                st.rerun(scope="fragment")

                with col_act:
                    st.write("")
                    if st.button(
                        "✅",
                        key=f"approve_{tier_key}_{post.id}",
                        type="primary",
                        width="stretch",
                        help="승인",
                    ):
                        # DB 업데이트 — 백그라운드 스레드로 위임 (Fire & Forget)
                        threading.Thread(
                            target=update_status,
                            args=(post.id, PostStatus.EDITING),
                            daemon=True,
                        ).start()
                        # 낙관적 UI — session_state에서 즉시 제거
                        st.session_state["hidden_post_ids"].add(post.id)
                        st.session_state["selected_posts"].discard(post.id)
                        st.rerun(scope="fragment")
                    if st.button(
                        "❌",
                        key=f"decline_{tier_key}_{post.id}",
                        width="stretch",
                        help="거절",
                    ):
                        threading.Thread(
                            target=update_status,
                            args=(post.id, PostStatus.DECLINED),
                            daemon=True,
                        ).start()
                        st.session_state["hidden_post_ids"].add(post.id)
                        st.session_state["selected_posts"].discard(post.id)
                        st.rerun(scope="fragment")

        # ---------------------------------------------------------------------------
        # 티어별 렌더링 — @st.fragment로 감싸 버튼 클릭 시 해당 티어만 재실행
        # ---------------------------------------------------------------------------
        @st.fragment
        def _render_tier(tier_posts: list, tier_key: str, preloaded_comments: dict) -> None:
            """티어별 카드 렌더링 fragment — 버튼 클릭 시 이 블록만 재실행."""
            for post in tier_posts:
                _render_post_card(post, tier_key, preloaded_comments)

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
                    _ids = [p.id for p in high_posts]
                    threading.Thread(
                        target=batch_update_status,
                        args=(_ids, PostStatus.EDITING),
                        daemon=True,
                    ).start()
                    st.session_state["hidden_post_ids"].update(_ids)
                    st.session_state["selected_posts"] -= set(_ids)
                    st.rerun()
            _render_tier(high_posts, "high", _all_comments)
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
                        _ids = [p.id for p in normal_posts]
                        threading.Thread(
                            target=batch_update_status,
                            args=(_ids, PostStatus.DECLINED),
                            daemon=True,
                        ).start()
                        st.session_state["hidden_post_ids"].update(_ids)
                        st.session_state["selected_posts"] -= set(_ids)
                        st.rerun()
                _render_tier(normal_posts, "normal", _all_comments)
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
                        _ids = [p.id for p in low_posts]
                        threading.Thread(
                            target=batch_update_status,
                            args=(_ids, PostStatus.DECLINED),
                            daemon=True,
                        ).start()
                        st.session_state["hidden_post_ids"].update(_ids)
                        st.session_state["selected_posts"] -= set(_ids)
                        st.rerun()
                _render_tier(low_posts, "low", _all_comments)
            else:
                st.caption("해당 게시글 없음")
