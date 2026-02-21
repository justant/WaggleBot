"""편집실 (Editor) 탭."""

import logging
from pathlib import Path

import streamlit as st

from config.settings import load_pipeline_config, MEDIA_DIR, ASSETS_DIR
from db.models import Post, PostStatus, Content, ScriptData
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, top_comments, check_ollama_health,
)
from dashboard.components.image_slider import render_image_slider
from dashboard.components.style_presets import load_style_presets

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 탭 전용 헬퍼
# ---------------------------------------------------------------------------

def _suggest_bgm(mood: str) -> str:
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


def _body_to_scene_strs(body: list) -> list[str]:
    """ScriptData.body (list[dict] v2) → 씬 편집기용 list[str] 변환.

    각 씬의 lines를 줄바꿈으로 연결한 문자열로 변환.
    """
    result: list[str] = []
    for item in body:
        if isinstance(item, dict):
            result.append("\n".join(item.get("lines", [""])))
        else:
            result.append(str(item))
    return result


def _collect_scenes(pid: int, n: int) -> list[str]:
    """씬 편집기의 현재 입력 값을 body_scenes_{pid} 형식(줄바꿈 조인)으로 수집."""
    result: list[str] = []
    for _i in range(n):
        _nl = st.session_state.get(f"bscene_{pid}_{_i}_nlines", 1)
        _l0 = st.session_state.get(f"bscene_{pid}_{_i}_L0", "")
        _l1 = st.session_state.get(f"bscene_{pid}_{_i}_L1", "")
        if _nl >= 2 and _l1:
            result.append(f"{_l0}\n{_l1}")
        else:
            result.append(_l0)
    return result


@st.fragment
def _scene_editor_frag(pid: int, init_body: list) -> None:
    """씬 기반 본문 편집기.

    - 줄당 st.text_input(max_chars=21) → 21자 초과 입력 자체 차단
    - 씬당 최대 2줄: 2줄일 때 "+ 줄 추가" 버튼 숨김
    - 씬 추가/삭제/줄 추가/삭제 시 fragment만 재실행
    """
    _sk = f"body_scenes_{pid}"

    # ── body_scenes 초기화 ────────────────────────────────────────────────────
    if _sk not in st.session_state:
        _init = _body_to_scene_strs(init_body)
        st.session_state[_sk] = _init if _init else [""]

    _scenes: list[str] = st.session_state[_sk]
    _n = len(_scenes)

    # ── 각 씬의 nlines / L0 / L1 키 초기화 (없을 때만) ─────────────────────
    for _i, _st_txt in enumerate(_scenes):
        _nk  = f"bscene_{pid}_{_i}_nlines"
        _l0k = f"bscene_{pid}_{_i}_L0"
        _l1k = f"bscene_{pid}_{_i}_L1"
        if _nk not in st.session_state:
            _parts = [l for l in _st_txt.split("\n") if l]
            _nl = min(len(_parts), 2) if _parts else 1
            st.session_state[_nk]  = _nl
            st.session_state[_l0k] = _parts[0] if len(_parts) > 0 else ""
            st.session_state[_l1k] = _parts[1] if len(_parts) > 1 else ""

    st.markdown("**📝 본문 항목** (씬 단위 · 각 줄 최대 21자 · 최대 2줄)")

    _del_idx: int | None      = None
    _add_line_idx: int | None = None
    _del_line_idx: int | None = None

    for _si in range(_n):
        _nk  = f"bscene_{pid}_{_si}_nlines"
        _l0k = f"bscene_{pid}_{_si}_L0"
        _l1k = f"bscene_{pid}_{_si}_L1"
        _nl  = st.session_state.get(_nk, 1)

        with st.container(border=True):
            # ── 씬 헤더: 번호 + 씬 삭제 ──────────────────────────────────
            _hc, _dc = st.columns([9, 1])
            with _hc:
                st.markdown(f"**씬 {_si + 1}**")
            with _dc:
                if st.button("✕", key=f"dsc_{pid}_{_si}", help="씬 삭제"):
                    _del_idx = _si

            # ── 줄 1 ─────────────────────────────────────────────────────
            if _nl == 1:
                # 1줄: 입력 + "+ 줄" 버튼
                _lc, _bc = st.columns([9, 1])
                with _lc:
                    st.text_input(
                        "줄 1",
                        key=_l0k,
                        max_chars=21,
                        label_visibility="collapsed",
                        placeholder="줄 1 (최대 21자)",
                    )
                with _bc:
                    if st.button("+ 줄", key=f"aln_{pid}_{_si}", help="줄 추가"):
                        _add_line_idx = _si
            else:
                # 2줄: 줄 1 단독 (전체 너비)
                st.text_input(
                    "줄 1",
                    key=_l0k,
                    max_chars=21,
                    label_visibility="collapsed",
                    placeholder="줄 1 (최대 21자)",
                )

            # ── 줄 2 (nlines == 2 일 때만) ───────────────────────────────
            if _nl >= 2:
                _l2c, _dlc = st.columns([9, 1])
                with _l2c:
                    st.text_input(
                        "줄 2",
                        key=_l1k,
                        max_chars=21,
                        label_visibility="collapsed",
                        placeholder="줄 2 (최대 21자)",
                    )
                with _dlc:
                    if st.button("✕", key=f"dln_{pid}_{_si}", help="줄 삭제"):
                        _del_line_idx = _si

    if st.button("+ 씬 추가", key=f"asc_{pid}"):
        _cur = _collect_scenes(pid, _n)
        _new_i = len(_cur)
        _cur.append("")
        st.session_state[_sk] = _cur
        st.session_state[f"bscene_{pid}_{_new_i}_nlines"] = 1
        st.session_state[f"bscene_{pid}_{_new_i}_L0"]     = ""
        st.session_state[f"bscene_{pid}_{_new_i}_L1"]     = ""
        st.rerun(scope="fragment")

    # ── 줄 추가 처리 ─────────────────────────────────────────────────────────
    if _add_line_idx is not None:
        st.session_state[_sk] = _collect_scenes(pid, _n)
        st.session_state[f"bscene_{pid}_{_add_line_idx}_nlines"] = 2
        st.rerun(scope="fragment")

    # ── 줄 삭제 처리 ─────────────────────────────────────────────────────────
    elif _del_line_idx is not None:
        st.session_state[_sk] = _collect_scenes(pid, _n)
        st.session_state[f"bscene_{pid}_{_del_line_idx}_nlines"] = 1
        st.session_state[f"bscene_{pid}_{_del_line_idx}_L1"]     = ""
        st.rerun(scope="fragment")

    # ── 씬 삭제 처리 ─────────────────────────────────────────────────────────
    elif _del_idx is not None:
        _cur = _collect_scenes(pid, _n)
        _cur.pop(_del_idx)
        for _dk in list(st.session_state.keys()):
            if _dk.startswith(f"bscene_{pid}_"):
                del st.session_state[_dk]
        st.session_state[_sk] = _cur
        for _ri, _rt in enumerate(_cur):
            _parts = [l for l in _rt.split("\n") if l]
            _nl2   = min(len(_parts), 2) if _parts else 1
            st.session_state[f"bscene_{pid}_{_ri}_nlines"] = _nl2
            st.session_state[f"bscene_{pid}_{_ri}_L0"]     = _parts[0] if _parts else ""
            st.session_state[f"bscene_{pid}_{_ri}_L1"]     = _parts[1] if len(_parts) > 1 else ""
        st.rerun(scope="fragment")

    else:
        # 현재 입력 값을 body_scenes_{pid} 에 동기화
        st.session_state[_sk] = _collect_scenes(pid, _n)

    # max_chars=21 로 네이티브 강제하므로 항상 유효
    st.session_state[f"scene_valid_{pid}"] = True


# ---------------------------------------------------------------------------
# 탭 렌더
# ---------------------------------------------------------------------------

def render() -> None:
    """편집실 탭 렌더링."""

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
                if st.button("🤖 자동생성", width="stretch",
                             help="AI 워커에 자동 처리를 맡기고 진행현황으로 이동합니다"):
                    try:
                        with SessionLocal() as _aws:
                            _apost = _aws.get(Post, approved_posts[idx].id)
                            if _apost:
                                _apost.status = PostStatus.APPROVED
                            _aws.commit()
                        st.session_state["_auto_queued"] = True
                        st.session_state["editor_idx"] = max(0, idx - 1)
                        st.rerun()
                    except Exception as _ae:
                        st.error(f"자동 전송 실패: {_ae}")

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
                        p["name"]: p["prompt"] for p in load_style_presets()
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
                        if not check_ollama_health():
                            st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
                        else:
                            with st.spinner("LLM 대본 생성 중..."):
                                try:
                                    from ai_worker.llm.client import generate_script
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
                                        post_id=selected_post_id,
                                        call_type="generate_script_editor",
                                    )
                                    # 다음 런의 pre-init 블록에서 주입할 결과 저장
                                    _gid = selected_post_id
                                    _new_sc = _body_to_scene_strs(script_data.body)
                                    st.session_state[f"_ai_result_{_gid}"] = {
                                        "hook":        script_data.hook,
                                        "closer":      script_data.closer,
                                        "title":       script_data.title_suggestion,
                                        "tags":        ", ".join(script_data.tags),
                                        "mood":        script_data.mood or "funny",
                                        "body_scenes": _new_sc if _new_sc else [""],
                                    }
                                    # 기존 위젯 키 삭제 → pre-init에서 새 값으로 채움
                                    for _ok in list(st.session_state.keys()):
                                        if _ok in (
                                            f"hook_{_gid}", f"closer_{_gid}",
                                            f"title_{_gid}", f"tags_{_gid}", f"mood_{_gid}",
                                            f"body_scenes_{_gid}",
                                        ) or _ok.startswith(f"bscene_{_gid}_"):
                                            del st.session_state[_ok]
                                    st.success("대본 생성 완료!")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"대본 생성 실패: {exc}")

                st.divider()

                # --- 편집 필드 ---
                mood_options = ["funny", "serious", "shocking", "heartwarming"]
                _pid = selected_post_id
                _sd = script_data  # DB 로드값 (저장 전이면 None)

                # ── AI 생성 결과 주입 (이전 런 핸들러가 _ai_result_* 에 저장한 값) ──────────
                _ai_pending = st.session_state.pop(f"_ai_result_{_pid}", None)
                if _ai_pending is not None:
                    # 위젯 렌더 직전(같은 런)에 session_state 덮어씀 → 위젯이 즉시 반영
                    st.session_state[f"hook_{_pid}"]   = _ai_pending["hook"]
                    st.session_state[f"closer_{_pid}"] = _ai_pending["closer"]
                    st.session_state[f"title_{_pid}"]  = _ai_pending["title"]
                    st.session_state[f"tags_{_pid}"]   = _ai_pending["tags"]
                    _pm = _ai_pending["mood"]
                    st.session_state[f"mood_{_pid}"]   = _pm if _pm in mood_options else "funny"
                    # body_scenes 설정 (bscene_ 키는 fragment init 블록이 초기화)
                    st.session_state[f"body_scenes_{_pid}"] = _ai_pending["body_scenes"]
                    for _ok in list(st.session_state.keys()):
                        if _ok.startswith(f"bscene_{_pid}_"):
                            del st.session_state[_ok]
                else:
                    # ── 최초 방문: DB 값으로 초기화 (이후 방문은 기존 state 유지) ──────────
                    if f"hook_{_pid}" not in st.session_state:
                        st.session_state[f"hook_{_pid}"] = _sd.hook if _sd else ""
                    if f"closer_{_pid}" not in st.session_state:
                        st.session_state[f"closer_{_pid}"] = _sd.closer if _sd else ""
                    if f"title_{_pid}" not in st.session_state:
                        st.session_state[f"title_{_pid}"] = _sd.title_suggestion if _sd else ""
                    if f"tags_{_pid}" not in st.session_state:
                        st.session_state[f"tags_{_pid}"] = ", ".join(_sd.tags) if _sd else ""
                    if f"mood_{_pid}" not in st.session_state:
                        _m0 = (_sd.mood if _sd else "funny") or "funny"
                        st.session_state[f"mood_{_pid}"] = _m0 if _m0 in mood_options else "funny"

                # value= 없이 key= 만으로 위젯 렌더 (session_state가 단일 진실 소스)
                hook = st.text_area(
                    "🎣 후킹 (Hook)",
                    max_chars=60,
                    height=80,
                    key=f"hook_{_pid}",
                )

                # --- 씬 기반 본문 편집기 (fragment) ---
                _scene_editor_frag(
                    _pid,
                    _sd.body if _sd else [],
                )

                # fragment 외부: session_state 에서 body 값 읽기 (저장·미리듣기용)
                _body_scenes_v2: list[dict] = []
                body_lines: list[str] = []
                for _sc_txt in st.session_state.get(f"body_scenes_{_pid}", []):
                    _sc_lines = [l.strip() for l in _sc_txt.split("\n") if l.strip()]
                    if _sc_lines:
                        _body_scenes_v2.append({"line_count": len(_sc_lines), "lines": _sc_lines})
                        body_lines.append(" ".join(_sc_lines))

                closer = st.text_area(
                    "🔚 마무리 (Closer)",
                    max_chars=100,
                    height=80,
                    key=f"closer_{_pid}",
                )

                st.divider()

                title_sug = st.text_input(
                    "🎬 영상 제목",
                    key=f"title_{_pid}",
                )
                tags_input = st.text_input(
                    "🏷️ 태그 (쉼표 구분)",
                    key=f"tags_{_pid}",
                )

                mood = st.selectbox(
                    "🎭 분위기",
                    mood_options,
                    key=f"mood_{_pid}",
                )

                # BGM 제안
                bgm_name = _suggest_bgm(mood)
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

                # --- 저장 / 확정 ---
                def _build_script() -> ScriptData:
                    """현재 편집 상태에서 ScriptData 생성."""
                    _tags = [t.strip() for t in tags_input.split(",") if t.strip()]
                    return ScriptData(
                        hook=hook,
                        body=_body_scenes_v2,
                        closer=closer,
                        title_suggestion=title_sug,
                        tags=_tags,
                        mood=mood,
                    )

                def _persist_script(new_status: PostStatus | None = None) -> None:
                    """ScriptData를 DB에 저장. new_status가 주어지면 Post.status도 변경."""
                    _sd = _build_script()
                    with SessionLocal() as _ws:
                        _cr = _ws.query(Content).filter(
                            Content.post_id == selected_post_id
                        ).first()
                        if _cr is None:
                            _cr = Content(post_id=selected_post_id)
                            _ws.add(_cr)
                        _cr.summary_text = _sd.to_json()
                        if new_status is not None:
                            _ep = _ws.get(Post, selected_post_id)
                            if _ep:
                                _ep.status = new_status
                        _ws.commit()

                def _validate_editor() -> bool:
                    if not hook.strip():
                        st.error("🎣 후킹(Hook)을 입력하세요.")
                        return False
                    if not body_lines:
                        st.error("📝 본문 항목을 1개 이상 입력하세요.")
                        return False
                    if not closer.strip():
                        st.error("🔚 마무리(Closer)를 입력하세요.")
                        return False
                    if est_seconds < 15:
                        st.error("⏱️ 대본이 너무 짧습니다 (최소 15초 이상).")
                        return False
                    if not st.session_state.get(f"scene_valid_{_pid}", True):
                        st.error("📝 본문 항목에 오류가 있습니다. 🔴 표시 씬을 수정하세요.")
                        return False
                    return True

                save_c, confirm_c = st.columns(2)
                with save_c:
                    if st.button(
                        "💾 저장",
                        width="stretch",
                        key=f"draft_save_{selected_post_id}",
                        help="편집 내용을 저장합니다. 편집실에 계속 머뭅니다.",
                    ):
                        if _validate_editor():
                            try:
                                _persist_script(new_status=None)
                                st.toast("✅ 저장 완료")
                            except Exception as exc:
                                st.error(f"저장 실패: {exc}")
                with confirm_c:
                    if st.button(
                        "✅ 확정",
                        width="stretch",
                        type="primary",
                        key=f"confirm_{selected_post_id}",
                        help="저장 후 AI 워커 처리 대기열로 이동합니다.",
                    ):
                        if _validate_editor():
                            try:
                                _persist_script(new_status=PostStatus.APPROVED)
                                st.success("✅ 확정 완료! AI Worker 처리 대기열에 추가됩니다.")
                                st.session_state["editor_idx"] = max(0, idx - 1)
                                st.rerun()
                            except Exception as exc:
                                st.error(f"확정 실패: {exc}")
