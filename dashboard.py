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
    TTS_VOICES, MEDIA_DIR,
    PLATFORM_CREDENTIAL_FIELDS,
    OLLAMA_HOST, OLLAMA_MODEL,
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
            f"{OLLAMA_HOST}/api/generate",
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
    # 자동 승인: COLLECTED + score >= threshold → APPROVED
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
                    _p.status = PostStatus.APPROVED
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
                    update_status(pid, PostStatus.APPROVED)
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
                            try:
                                imgs = (
                                    json.loads(post.images)
                                    if isinstance(post.images, str)
                                    else post.images
                                )
                                if imgs:
                                    st.image(imgs[0], width=280, caption="대표 이미지")
                            except Exception:
                                pass

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
                        update_status(post.id, PostStatus.APPROVED)
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
                        update_status(p.id, PostStatus.APPROVED)
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
# Tab 2: 편집실 (Editor)
# ===========================================================================

with tab_editor:
    st.header("✏️ 편집실")
    st.caption("AI 대본을 생성하고 편집한 후 확정하세요")

    with SessionLocal() as session:
        approved_posts = (
            session.query(Post)
            .filter(Post.status == PostStatus.APPROVED)
            .order_by(Post.created_at.desc())
            .all()
        )

        if not approved_posts:
            st.info("✅ 승인된 게시글이 없습니다. 수신함에서 먼저 승인하세요.")
        else:
            post_options = {f"[{p.id}] {p.title[:50]}": p.id for p in approved_posts}
            selected_label = st.selectbox("게시글 선택", list(post_options.keys()))
            selected_post_id = post_options[selected_label]
            selected_post = next(p for p in approved_posts if p.id == selected_post_id)

            # 기존 Content 조회
            existing_content = (
                session.query(Content)
                .filter(Content.post_id == selected_post_id)
                .first()
            )

            col_orig, col_edit = st.columns([5, 5])

            with col_orig:
                st.subheader("📄 원본 게시글")
                st.markdown(f"**{selected_post.title}**")
                views, likes, comments_cnt = stats_display(selected_post.stats)
                score = selected_post.engagement_score or 0
                st.caption(f"🔥 {score} pts | 👁️ {views:,} | 👍 {likes:,}")

                if selected_post.content:
                    st.write(selected_post.content[:500] + ("..." if len(selected_post.content) > 500 else ""))

                if selected_post.images and selected_post.images != "[]":
                    try:
                        imgs = json.loads(selected_post.images) if isinstance(selected_post.images, str) else selected_post.images
                        if imgs:
                            st.image(imgs[0], width=300)
                    except Exception:
                        pass

                best_coms = top_comments(selected_post_id, session, limit=3)
                if best_coms:
                    st.markdown("**💬 베스트 댓글**")
                    for c in best_coms:
                        lk = f" (+{c.likes})" if c.likes else ""
                        st.text(f"{c.author}: {c.content[:100]}{lk}")

            with col_edit:
                st.subheader("🤖 AI 대본 편집기")

                # 기존 대본 로드 시도
                script_data = None
                if existing_content and existing_content.summary_text:
                    try:
                        from ai_worker.llm import ScriptData
                        script_data = ScriptData.from_json(existing_content.summary_text)
                    except Exception:
                        pass

                if st.button("🤖 AI 대본 생성", use_container_width=True, type="primary"):
                    with st.spinner("LLM 대본 생성 중..."):
                        try:
                            from ai_worker.llm import generate_script
                            best_comments_list = sorted(
                                selected_post.comments, key=lambda c: c.likes, reverse=True
                            )[:5]
                            comment_texts = [f"{c.author}: {c.content[:100]}" for c in best_comments_list]
                            cfg = load_pipeline_config()
                            script_data = generate_script(
                                title=selected_post.title,
                                body=selected_post.content or "",
                                comments=comment_texts,
                                model=cfg.get("llm_model"),
                            )
                            st.success("대본 생성 완료!")
                        except Exception as e:
                            st.error(f"대본 생성 실패: {e}")

                # 편집 필드
                hook_val = script_data.hook if script_data else ""
                body_val = "\n".join(script_data.body) if script_data else ""
                closer_val = script_data.closer if script_data else ""
                title_val = script_data.title_suggestion if script_data else ""
                tags_val = ", ".join(script_data.tags) if script_data else ""
                mood_val = script_data.mood if script_data else "funny"
                mood_options = ["funny", "serious", "shocking", "heartwarming"]

                hook = st.text_area("🎣 후킹", value=hook_val, max_chars=50, height=80)
                body_text = st.text_area("📝 본문", value=body_val, height=200)
                closer = st.text_area("🔚 마무리", value=closer_val, max_chars=80, height=80)
                title_sug = st.text_input("🎬 제목", value=title_val)
                tags_input = st.text_input("🏷️ 태그", value=tags_val)
                mood_idx = mood_options.index(mood_val) if mood_val in mood_options else 0
                mood = st.selectbox("🎭 분위기", mood_options, index=mood_idx)

                # 예상 길이
                body_lines = [ln for ln in body_text.splitlines() if ln.strip()]
                plain = " ".join([hook] + body_lines + [closer])
                char_count = len(plain)
                est_seconds = round(char_count / 5.5)  # 한국어 평균 낭독 속도 ~5.5자/초
                st.caption(f"예상 TTS 길이: {char_count}자 ≈ {est_seconds}초")

                # TTS 미리듣기
                if st.button("🔊 TTS 미리듣기", use_container_width=True):
                    if plain.strip():
                        with st.spinner("TTS 생성 중..."):
                            try:
                                import asyncio
                                from ai_worker.tts import get_tts_engine
                                cfg = load_pipeline_config()
                                tts_engine = get_tts_engine(cfg["tts_engine"])
                                preview_dir = MEDIA_DIR / "tmp"
                                preview_dir.mkdir(parents=True, exist_ok=True)
                                preview_path = preview_dir / f"preview_{selected_post_id}.mp3"
                                asyncio.run(tts_engine.synthesize(plain, cfg["tts_voice"], preview_path))
                                st.audio(str(preview_path))
                            except Exception as e:
                                st.error(f"TTS 미리듣기 실패: {e}")
                    else:
                        st.warning("대본 내용이 없습니다.")

                # 대본 확정 저장
                if st.button("💾 대본 확정", use_container_width=True):
                    try:
                        from ai_worker.llm import ScriptData
                        tags_list = [t.strip() for t in tags_input.split(",") if t.strip()]
                        confirmed_script = ScriptData(
                            hook=hook,
                            body=body_lines,
                            closer=closer,
                            title_suggestion=title_sug,
                            tags=tags_list,
                            mood=mood,
                        )

                        # DB 저장
                        content_rec = (
                            session.query(Content)
                            .filter(Content.post_id == selected_post_id)
                            .first()
                        )
                        if content_rec is None:
                            content_rec = Content(post_id=selected_post_id)
                            session.add(content_rec)
                        content_rec.summary_text = confirmed_script.to_json()
                        session.commit()
                        st.success("대본이 저장되었습니다. AI Worker가 이 대본을 재사용합니다.")
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

# ===========================================================================
# Tab 3: 진행현황 (Progress)
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
