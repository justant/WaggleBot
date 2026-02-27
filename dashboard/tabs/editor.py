"""편집실 (Editor) 탭."""

import logging
import threading
import time as _perf_time
from pathlib import Path

import streamlit as st

from config.settings import load_pipeline_config, MEDIA_DIR, ASSETS_DIR
from db.models import Post, PostStatus, Content, ScriptData
from db.session import SessionLocal

from dashboard.components.status_utils import (
    to_kst, stats_display, check_ollama_health, update_status, delete_post,
)
from dashboard.components.image_slider import render_image_slider
from dashboard.components.style_presets import load_style_presets
from dashboard.workers.editor_tasks import (
    get_llm_task, get_tts_task,
    clear_llm_task, clear_tts_task,
    submit_llm_task, submit_tts_task,
)

log = logging.getLogger(__name__)


def _safe_rerun_fragment() -> None:
    """fragment rerun 컨텍스트에서만 scope='fragment' 사용, 아니면 전체 rerun."""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


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


def _split_body_items(body: list) -> tuple[list[str], list[dict]]:
    """ScriptData.body → (body scene strings, comment dicts with text+author) 분리."""
    body_strs: list[str] = []
    comment_dicts: list[dict] = []
    for item in body:
        if isinstance(item, dict):
            if item.get("type") == "comment":
                comment_dicts.append({
                    "text": "\n".join(item.get("lines", [""])),
                    "author": item.get("author", ""),
                })
            else:
                body_strs.append("\n".join(item.get("lines", [""])))
        else:
            body_strs.append(str(item))
    return body_strs, comment_dicts


def _collect_scenes(pid: int, n: int, prefix: str = "bscene") -> list[str]:
    """씬 편집기의 현재 입력 값을 수집. prefix로 body(bscene)/comment(cscene) 구분."""
    result: list[str] = []
    for _i in range(n):
        _nl = st.session_state.get(f"{prefix}_{pid}_{_i}_nlines", 1)
        _l0 = st.session_state.get(f"{prefix}_{pid}_{_i}_L0", "")
        _l1 = st.session_state.get(f"{prefix}_{pid}_{_i}_L1", "")
        if _nl >= 2 and _l1:
            result.append(f"{_l0}\n{_l1}")
        else:
            result.append(_l0)
    return result


def _collect_authors(pid: int, n: int, prefix: str = "cscene") -> list[str]:
    """댓글 씬의 작성자 값을 수집."""
    return [st.session_state.get(f"{prefix}_{pid}_{_i}_author", "") for _i in range(n)]


@st.fragment
def _scene_editor_frag(pid: int, init_body: list) -> None:
    """씬 기반 본문/댓글 편집기.

    - 본문(body)과 댓글(comment)을 분리하여 각각 편집
    - 줄당 st.text_input(max_chars=21) → 21자 초과 입력 자체 차단
    - 씬당 최대 2줄: 2줄일 때 "+ 줄 추가" 버튼 숨김
    - 씬 사이에 "+" 삽입 버튼 배치 (맨 위/사이/맨 아래)
    - 씬 추가/삭제/줄 추가/삭제 시 fragment만 재실행
    """
    _bsk = f"body_scenes_{pid}"
    _csk = f"comment_scenes_{pid}"
    _cak = f"comment_authors_{pid}"

    # 초기화: 둘 다 있어야 함 (하위 호환: 기존 body_scenes만 있으면 재초기화)
    if _bsk not in st.session_state or _csk not in st.session_state:
        for _dk in list(st.session_state.keys()):
            if _dk.startswith(f"bscene_{pid}_") or _dk.startswith(f"cscene_{pid}_"):
                del st.session_state[_dk]
        _body_strs, _comment_dicts = _split_body_items(init_body)
        st.session_state[_bsk] = _body_strs if _body_strs else [""]
        st.session_state[_csk] = (
            [d["text"] for d in _comment_dicts] if _comment_dicts else []
        )
        st.session_state[_cak] = (
            [d["author"] for d in _comment_dicts] if _comment_dicts else []
        )

    # ── 공통 씬 리스트 렌더 함수 ─────────────────────────────────────────────
    def _render_scene_list(
        scenes_key: str,
        prefix: str,
        label: str,
        show_author: bool,
    ) -> None:
        """body 또는 comment 한 섹션의 씬 리스트를 렌더링하고 액션을 처리."""
        _scenes: list[str] = st.session_state[scenes_key]
        _n = len(_scenes)

        # 씬 키 초기화
        for _i, _st_txt in enumerate(_scenes):
            _nk = f"{prefix}_{pid}_{_i}_nlines"
            if _nk not in st.session_state:
                _parts = [l for l in _st_txt.split("\n") if l]
                _nl = min(len(_parts), 2) if _parts else 1
                st.session_state[_nk] = _nl
                st.session_state[f"{prefix}_{pid}_{_i}_L0"] = (
                    _parts[0] if len(_parts) > 0 else ""
                )
                st.session_state[f"{prefix}_{pid}_{_i}_L1"] = (
                    _parts[1] if len(_parts) > 1 else ""
                )
            if show_author and f"{prefix}_{pid}_{_i}_author" not in st.session_state:
                _authors = st.session_state.get(_cak, [])
                st.session_state[f"{prefix}_{pid}_{_i}_author"] = (
                    _authors[_i] if _i < len(_authors) else ""
                )

        st.markdown(f"**{label}** — 씬 단위 편집 (줄당 21자, 씬당 최대 2줄)")

        _del_idx: int | None = None
        _add_line_idx: int | None = None
        _del_line_idx: int | None = None
        _insert_idx: int | None = None

        # 맨 위 삽입 버튼
        _p1, _p2, _p3 = st.columns([5, 2, 5])
        with _p2:
            if st.button("＋", key=f"ins_{prefix}_{pid}_0", help="씬 삽입"):
                _insert_idx = 0

        for _si in range(_n):
            _nk = f"{prefix}_{pid}_{_si}_nlines"
            _l0k = f"{prefix}_{pid}_{_si}_L0"
            _l1k = f"{prefix}_{pid}_{_si}_L1"
            _nl = st.session_state.get(_nk, 1)

            with st.container(border=True):
                _hc, _dc = st.columns([9, 1])
                with _hc:
                    st.markdown(f"**씬 {_si + 1}**")
                with _dc:
                    if st.button("✕", key=f"dsc_{prefix}_{pid}_{_si}", help="씬 삭제"):
                        _del_idx = _si

                # 작성자 필드 (댓글 전용)
                if show_author:
                    st.text_input(
                        "작성자", key=f"{prefix}_{pid}_{_si}_author",
                        max_chars=20, placeholder="작성자 닉네임",
                    )

                if _nl == 1:
                    _lc, _bc = st.columns([9, 1])
                    with _lc:
                        st.text_input(
                            "줄 1", key=_l0k, max_chars=21,
                            label_visibility="collapsed",
                            placeholder="줄 1 (최대 21자)",
                        )
                    with _bc:
                        if st.button(
                            "＋", key=f"aln_{prefix}_{pid}_{_si}",
                            help="줄 추가",
                        ):
                            _add_line_idx = _si
                else:
                    st.text_input(
                        "줄 1", key=_l0k, max_chars=21,
                        label_visibility="collapsed",
                        placeholder="줄 1 (최대 21자)",
                    )

                if _nl >= 2:
                    _l2c, _dlc = st.columns([9, 1])
                    with _l2c:
                        st.text_input(
                            "줄 2", key=_l1k, max_chars=21,
                            label_visibility="collapsed",
                            placeholder="줄 2 (최대 21자)",
                        )
                    with _dlc:
                        if st.button(
                            "✕", key=f"dln_{prefix}_{pid}_{_si}",
                            help="줄 삭제",
                        ):
                            _del_line_idx = _si

            # 씬 사이 삽입 버튼
            _p1, _p2, _p3 = st.columns([5, 2, 5])
            with _p2:
                if st.button(
                    "＋", key=f"ins_{prefix}_{pid}_{_si + 1}",
                    help="씬 삽입",
                ):
                    _insert_idx = _si + 1

        # ── 액션 처리 ────────────────────────────────────────────────────────
        def _rebuild_keys(cur: list[str], authors: list[str] | None = None) -> None:
            """씬 키를 전체 재구성."""
            for _dk in list(st.session_state.keys()):
                if _dk.startswith(f"{prefix}_{pid}_"):
                    del st.session_state[_dk]
            st.session_state[scenes_key] = cur
            for _ri, _rt in enumerate(cur):
                _parts = [l for l in _rt.split("\n") if l]
                _nl2 = min(len(_parts), 2) if _parts else 1
                st.session_state[f"{prefix}_{pid}_{_ri}_nlines"] = _nl2
                st.session_state[f"{prefix}_{pid}_{_ri}_L0"] = (
                    _parts[0] if _parts else ""
                )
                st.session_state[f"{prefix}_{pid}_{_ri}_L1"] = (
                    _parts[1] if len(_parts) > 1 else ""
                )
            if show_author and authors is not None:
                st.session_state[_cak] = authors
                for _ri, _a in enumerate(authors):
                    st.session_state[f"{prefix}_{pid}_{_ri}_author"] = _a

        if _insert_idx is not None:
            _cur = _collect_scenes(pid, _n, prefix)
            _cur.insert(_insert_idx, "")
            _au: list[str] | None = None
            if show_author:
                _au = _collect_authors(pid, _n, prefix)
                _au.insert(_insert_idx, "")
            _rebuild_keys(_cur, _au)
            st.rerun(scope="fragment")
        elif _add_line_idx is not None:
            st.session_state[scenes_key] = _collect_scenes(pid, _n, prefix)
            st.session_state[f"{prefix}_{pid}_{_add_line_idx}_nlines"] = 2
            st.rerun(scope="fragment")
        elif _del_line_idx is not None:
            st.session_state[scenes_key] = _collect_scenes(pid, _n, prefix)
            st.session_state[f"{prefix}_{pid}_{_del_line_idx}_nlines"] = 1
            st.session_state[f"{prefix}_{pid}_{_del_line_idx}_L1"] = ""
            st.rerun(scope="fragment")
        elif _del_idx is not None:
            _cur = _collect_scenes(pid, _n, prefix)
            _cur.pop(_del_idx)
            _au = None
            if show_author:
                _au = _collect_authors(pid, _n, prefix)
                if _del_idx < len(_au):
                    _au.pop(_del_idx)
            # body: 최소 1씬 유지 / comment: 빈 리스트 허용
            if not show_author and not _cur:
                _cur = [""]
            if show_author and _au is not None and not _au and not _cur:
                pass  # 댓글 전체 삭제 허용
            _rebuild_keys(_cur, _au)
            st.rerun(scope="fragment")
        else:
            st.session_state[scenes_key] = _collect_scenes(pid, _n, prefix)
            if show_author:
                st.session_state[_cak] = _collect_authors(pid, _n, prefix)

    # ── 본문 섹션 렌더 ───────────────────────────────────────────────────────
    _render_scene_list(_bsk, "bscene", "📝 본문 항목", show_author=False)

    # ── 댓글 섹션 렌더 ───────────────────────────────────────────────────────
    _c_scenes = st.session_state.get(_csk, [])
    if _c_scenes:
        st.divider()
        _render_scene_list(_csk, "cscene", "💬 댓글 항목", show_author=True)
    else:
        _p1, _p2, _p3 = st.columns([3, 4, 3])
        with _p2:
            if st.button("+ 댓글 항목 추가", key=f"add_csection_{pid}"):
                st.session_state[_csk] = [""]
                st.session_state[_cak] = [""]
                st.rerun(scope="fragment")

    st.session_state[f"scene_valid_{pid}"] = True


def _inject_ai_result(pid: int, script_data: ScriptData) -> None:
    """비동기 LLM 결과를 session_state에 주입한다 (다음 rerun에서 위젯에 반영)."""
    mood_options = ["funny", "serious", "shocking", "heartwarming"]
    _body_strs, _comment_dicts = _split_body_items(script_data.body)
    st.session_state[f"_ai_result_{pid}"] = {
        "hook":             script_data.hook,
        "closer":           script_data.closer,
        "title":            script_data.title_suggestion,
        "tags":             ", ".join(script_data.tags),
        "mood":             script_data.mood if script_data.mood in mood_options else "funny",
        "body_scenes":      _body_strs if _body_strs else [""],
        "comment_scenes":   [d["text"] for d in _comment_dicts] if _comment_dicts else [],
        "comment_authors":  [d["author"] for d in _comment_dicts] if _comment_dicts else [],
    }
    for _ok in list(st.session_state.keys()):
        if _ok in (
            f"hook_{pid}", f"closer_{pid}",
            f"title_{pid}", f"tags_{pid}", f"mood_{pid}",
            f"body_scenes_{pid}", f"comment_scenes_{pid}", f"comment_authors_{pid}",
        ) or _ok.startswith(f"bscene_{pid}_") or _ok.startswith(f"cscene_{pid}_"):
            del st.session_state[_ok]


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
            st.session_state["hidden_editor_ids"] = set()
            _safe_rerun_fragment()

    if "editor_idx" not in st.session_state:
        st.session_state["editor_idx"] = 0
    if "editor_page_offset" not in st.session_state:
        st.session_state["editor_page_offset"] = 0
    if "hidden_editor_ids" not in st.session_state:
        st.session_state["hidden_editor_ids"] = set()

    _EDITOR_PAGE_SIZE = 30  # 한 번에 로드할 최대 게시물 수

    # ── 1. 편집 대기 게시글 로드 (페이지네이션 적용) ───────────────────────────
    # 성능: selectinload(Post.comments) 제거 — 30개 전체 댓글 로드 → 선택 게시글만 로드
    _t0_db = _perf_time.perf_counter()
    with SessionLocal() as _ds:
        _total_editing = (
            _ds.query(Post)
            .filter(Post.status == PostStatus.EDITING)
            .count()
        )
        approved_posts = (
            _ds.query(Post)
            .filter(Post.status == PostStatus.EDITING)
            .order_by(Post.created_at.desc())
            .offset(st.session_state["editor_page_offset"])
            .limit(_EDITOR_PAGE_SIZE)
            .all()
        )
    _dur_db = (_perf_time.perf_counter() - _t0_db) * 1000
    if _dur_db > 100:
        log.warning("[PERF] editor DB 게시글 로드: %.0fms (SLOW)", _dur_db)

    # 낙관적 UI — 자동생성으로 전송한 게시글은 즉시 목록에서 제외
    _hidden = st.session_state["hidden_editor_ids"]
    approved_posts = [p for p in approved_posts if p.id not in _hidden]

    if not approved_posts:
        st.info("✏️ 편집 대기 게시글이 없습니다. 수신함에서 먼저 승인하세요.")
        return

    # ── 2. 네비게이션 바 ────────────────────────────────────────────────────────
    n_posts = len(approved_posts)
    idx = min(st.session_state["editor_idx"], n_posts - 1)

    selected_post = approved_posts[idx]
    selected_post_id = selected_post.id
    _pid = selected_post_id

    post_labels = [f"[{p.id}] {p.title[:45]}" for p in approved_posts]
    col_sel, col_del = st.columns([8, 2])
    with col_sel:
        new_idx = st.selectbox(
            "게시글 선택", range(n_posts), index=idx,
            format_func=lambda i: post_labels[i],
            placeholder="편집할 게시글 선택",
            label_visibility="collapsed",
        )
    with col_del:
        if st.button(
            "🗑️ 삭제", width="stretch",
            key=f"del_post_{selected_post_id}",
            help="이 게시글을 영구 삭제합니다",
        ):
            clear_llm_task(selected_post_id)
            clear_tts_task(selected_post_id)
            threading.Thread(
                target=delete_post,
                args=(selected_post_id,),
                daemon=True,
            ).start()
            st.session_state["hidden_editor_ids"].add(selected_post_id)
            st.session_state["editor_idx"] = max(0, idx - 1)
            _safe_rerun_fragment()
    if new_idx != idx:
        st.session_state["editor_idx"] = new_idx
        _safe_rerun_fragment()

    # ── 3. Content / ScriptData + 선택 게시글 댓글 로드 (단일 세션) ──────────────
    from db.models import Comment
    with SessionLocal() as _cs:
        existing_content = (
            _cs.query(Content)
            .filter(Content.post_id == selected_post_id)
            .first()
        )
        script_data: ScriptData | None = None
        if existing_content and existing_content.summary_text:
            try:
                script_data = ScriptData.from_json(existing_content.summary_text)
            except Exception:
                pass
        # 선택 게시글의 댓글만 로드 (기존: 30개 전체 selectinload → 1개만 쿼리)
        _selected_comments = (
            _cs.query(Comment)
            .filter(Comment.post_id == selected_post_id)
            .order_by(Comment.likes.desc())
            .limit(5)
            .all()
        )

    cfg_editor = load_pipeline_config()

    # ── 4. 비동기 작업 완료 처리 (rerun 트리거) ──────────────────────────────────
    _llm_task = get_llm_task(_pid)
    if _llm_task:
        if _llm_task["status"] == "done":
            _inject_ai_result(_pid, _llm_task["result"])
            clear_llm_task(_pid)
            st.session_state.pop(f"_llm_gen_requested_{_pid}", None)
            # 전체 rerun 필수: _scene_editor_frag (nested fragment)가
            # fragment-scoped rerun에서는 재실행되지 않아 body가 갱신 안 됨
            st.rerun()
        elif _llm_task["status"] == "error":
            st.error(f"❌ 대본 생성 실패: {_llm_task['error']}")
            clear_llm_task(_pid)
            st.session_state.pop(f"_llm_gen_requested_{_pid}", None)
    elif st.session_state.pop(f"_llm_gen_requested_{_pid}", None) and script_data:
        # 인메모리 태스크가 GC됐지만(5분 TTL) DB에 결과가 저장된 경우:
        # DB script_data로 강제 주입 → 전체 rerun으로 nested fragment 갱신
        _inject_ai_result(_pid, script_data)
        st.rerun()

    _tts_task = get_tts_task(_pid)
    if _tts_task:
        if _tts_task["status"] == "done":
            st.session_state[f"tts_audio_{_pid}"] = _tts_task["path"]
            clear_tts_task(_pid)
            _safe_rerun_fragment()
        elif _tts_task["status"] == "error":
            st.error(f"❌ TTS 미리듣기 실패: {_tts_task['error']}")
            clear_tts_task(_pid)

    # ── 5. 좌우 분할: 원본 | AI 대본 편집 ────────────────────────────────────────
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

        with st.expander("📷 이미지 미리보기", expanded=False):
            render_image_slider(
                selected_post.images,
                key_prefix=f"editor_{selected_post_id}",
                width=360,
            )

        if selected_post.content:
            st.markdown(
                selected_post.content[:600]
                + ("..." if len(selected_post.content) > 600 else "")
            )

        # 댓글 — 선택 게시글만 별도 쿼리로 로드 (likes 내림차순, limit 5)
        best_coms = _selected_comments[:3]
        if best_coms:
            st.markdown("**💬 베스트 댓글**")
            for c in best_coms:
                lk = f" (+{c.likes})" if c.likes else ""
                st.markdown(f"> {c.author}: {c.content[:100]}{lk}")

    # --- 오른쪽: AI 대본 편집 ---
    with col_edit:
        st.subheader("🤖 AI 대본 편집기")

        # ── 재생성 파라미터 & LLM 생성 버튼 ────────────────────────────────────
        _llm_running = _llm_task is not None and _llm_task.get("status") == "running"

        with st.expander("⚙️ 재생성 파라미터", expanded=False):
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
                (_STYLE_PRESETS[style_choice] + " " + extra_inst).strip() or None
            )

        # 버튼은 expander 밖: 항상 표시 (재생성 가능)
        if _llm_running:
            _gen_start = st.session_state.get(f"_llm_gen_requested_{_pid}")
            _elapsed_str = ""
            if _gen_start:
                _elapsed_sec = int(_perf_time.time() - _gen_start)
                _elapsed_str = f" ({_elapsed_sec}초 경과)"
            st.info(f"🤖 AI 대본 생성 중...{_elapsed_str} 완료되면 자동으로 반영됩니다.")
            st.progress(0.0, text="LLM 처리 대기 중")
            st.button(
                "🔄 대본 재생성" if script_data else "🤖 AI 대본 생성",
                width="stretch", type="primary",
                key=f"gen_{selected_post_id}",
                disabled=True,
            )
        elif st.button(
            "🔄 대본 재생성" if script_data else "🤖 AI 대본 생성",
            width="stretch", type="primary",
            key=f"gen_{selected_post_id}",
        ):
            if not check_ollama_health():
                st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
            else:
                best_list = _selected_comments[:5]
                comment_texts = [
                    f"{c.author}: {c.content[:100]}" for c in best_list
                ]
                submitted = submit_llm_task(
                    _pid,
                    title=selected_post.title,
                    body=selected_post.content or "",
                    comments=comment_texts,
                    model=cfg_editor.get("llm_model"),
                    extra_instructions=full_extra,
                    call_type="generate_script_editor",
                )
                if submitted:
                    st.session_state[f"_llm_gen_requested_{_pid}"] = _perf_time.time()
                    _safe_rerun_fragment()
                else:
                    st.info("이미 생성 중입니다.")

        st.divider()

        # ── 편집 필드 ────────────────────────────────────────────────────────────
        mood_options = ["funny", "serious", "shocking", "heartwarming"]
        _sd = script_data

        # AI 생성 결과 주입 (이전 런 핸들러가 _ai_result_* 에 저장한 값)
        _ai_pending = st.session_state.pop(f"_ai_result_{_pid}", None)
        if _ai_pending is not None:
            st.session_state[f"hook_{_pid}"]   = _ai_pending["hook"]
            st.session_state[f"closer_{_pid}"] = _ai_pending["closer"]
            st.session_state[f"title_{_pid}"]  = _ai_pending["title"]
            st.session_state[f"tags_{_pid}"]   = _ai_pending["tags"]
            _pm = _ai_pending["mood"]
            st.session_state[f"mood_{_pid}"]   = _pm if _pm in mood_options else "funny"
            st.session_state[f"body_scenes_{_pid}"] = _ai_pending["body_scenes"]
            st.session_state[f"comment_scenes_{_pid}"] = _ai_pending["comment_scenes"]
            st.session_state[f"comment_authors_{_pid}"] = _ai_pending["comment_authors"]
            for _ok in list(st.session_state.keys()):
                if _ok.startswith(f"bscene_{_pid}_") or _ok.startswith(f"cscene_{_pid}_"):
                    del st.session_state[_ok]
        else:
            # 최초 방문: DB 값으로 초기화 (이후 방문은 기존 state 유지)
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

        hook = st.text_area(
            "🎣 후킹 (Hook)",
            max_chars=60, height=80,
            key=f"hook_{_pid}",
        )

        _scene_editor_frag(_pid, _sd.body if _sd else [])

        _body_scenes_v2: list[dict] = []
        body_lines: list[str] = []
        for _sc_txt in st.session_state.get(f"body_scenes_{_pid}", []):
            _sc_lines = [l.strip() for l in _sc_txt.split("\n") if l.strip()]
            if _sc_lines:
                _body_scenes_v2.append({
                    "type": "body", "line_count": len(_sc_lines), "lines": _sc_lines,
                })
                body_lines.append(" ".join(_sc_lines))

        _comment_scenes_v2: list[dict] = []
        comment_lines: list[str] = []
        _c_authors = st.session_state.get(f"comment_authors_{_pid}", [])
        for _ci, _sc_txt in enumerate(
            st.session_state.get(f"comment_scenes_{_pid}", [])
        ):
            _sc_lines = [l.strip() for l in _sc_txt.split("\n") if l.strip()]
            if _sc_lines:
                _author = _c_authors[_ci] if _ci < len(_c_authors) else ""
                _comment_scenes_v2.append({
                    "type": "comment", "author": _author,
                    "line_count": len(_sc_lines), "lines": _sc_lines,
                })
                comment_lines.append(" ".join(_sc_lines))

        closer = st.text_area(
            "🔚 마무리 (Closer)",
            max_chars=100, height=80,
            key=f"closer_{_pid}",
        )

        st.divider()

        title_sug = st.text_input("🎬 영상 제목", key=f"title_{_pid}")
        tags_input = st.text_input("🏷️ 태그 (쉼표 구분)", key=f"tags_{_pid}")
        mood = st.selectbox("🎭 분위기", mood_options, key=f"mood_{_pid}")

        bgm_name = _suggest_bgm(mood)
        st.caption(f"🎵 선택 BGM: `{bgm_name}`")

        st.divider()

        # ── 예상 길이 + TTS 미리듣기 ─────────────────────────────────────────────
        plain_preview = " ".join([hook] + body_lines + comment_lines + [closer])
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

        _tts_running = _tts_task is not None and _tts_task.get("status") == "running"
        _has_content = bool(plain_preview.strip())

        with info_c2:
            if _tts_running:
                st.info("🎙️ TTS 생성 중...")
                st.progress(0.0, text="TTS 처리 대기 중")
            elif st.button(
                "▶ TTS 미리듣기", width="stretch",
                key=f"tts_preview_{selected_post_id}",
                disabled=not _has_content,
            ):
                preview_path = MEDIA_DIR / "tmp" / f"preview_{selected_post_id}.mp3"
                submitted = submit_tts_task(
                    _pid,
                    text=plain_preview,
                    engine_name=cfg_editor["tts_engine"],
                    voice=cfg_editor["tts_voice"],
                    output_path=preview_path,
                )
                if submitted:
                    _safe_rerun_fragment()
                else:
                    st.info("이미 생성 중입니다.")

        audio_cache_key = f"tts_audio_{selected_post_id}"
        if audio_cache_key in st.session_state:
            st.audio(st.session_state[audio_cache_key])

        st.divider()

        # ── 저장 / 확정 ───────────────────────────────────────────────────────────
        def _build_script() -> ScriptData:
            _tags = [t.strip() for t in tags_input.split(",") if t.strip()]
            _all_body = _body_scenes_v2 + _comment_scenes_v2
            return ScriptData(
                hook=hook,
                body=_all_body,
                closer=closer,
                title_suggestion=title_sug,
                tags=_tags,
                mood=mood,
            )

        def _persist_script(new_status: PostStatus | None = None) -> None:
            """ScriptData를 DB에 저장. new_status가 있으면 Post 상태도 직접 SQL로 변경."""
            from datetime import datetime, timezone
            from sqlalchemy import update as _sql_update

            _built = _build_script()
            with SessionLocal() as _ws:
                _cr = _ws.query(Content).filter(
                    Content.post_id == selected_post_id
                ).first()
                if _cr is None:
                    _cr = Content(post_id=selected_post_id)
                    _ws.add(_cr)
                _cr.summary_text = _built.to_json()
                _ws.flush()
                if new_status is not None:
                    # 직접 SQL UPDATE — ai_worker 동시 수정 충돌(1020) 방지
                    _ws.execute(
                        _sql_update(Post)
                        .where(Post.id == selected_post_id)
                        .values(
                            status=new_status,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                _ws.commit()

        def _validate_editor() -> bool:
            if not hook.strip():
                st.error("🎣 후킹(Hook)을 입력하세요.")
                return False
            if not body_lines and not comment_lines:
                st.error("📝 본문 또는 댓글 항목을 1개 이상 입력하세요.")
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

        auto_c, save_c, confirm_c = st.columns(3)
        with auto_c:
            if st.button(
                "⏩ AI 워커 전송", width="stretch",
                key=f"auto_gen_{selected_post_id}",
                help="편집 없이 AI 워커에 전송합니다 (APPROVED 상태로 전환)",
                disabled=_llm_running,
            ):
                _pid_auto = approved_posts[idx].id
                clear_llm_task(_pid_auto)
                clear_tts_task(_pid_auto)
                st.session_state["hidden_editor_ids"].add(_pid_auto)
                threading.Thread(
                    target=update_status,
                    args=(_pid_auto, PostStatus.APPROVED),
                    daemon=True,
                ).start()
                st.toast("⏩ AI 워커 대기열로 전송됨")
                st.session_state["editor_idx"] = max(0, idx - 1)
                _safe_rerun_fragment()
        with save_c:
            if st.button(
                "💾 저장", width="stretch",
                key=f"draft_save_{selected_post_id}",
                help="현재 편집 내용만 저장합니다 (상태 변경 없음, 편집실 유지)",
            ):
                if _validate_editor():
                    try:
                        _persist_script(new_status=None)
                        st.toast("✅ 저장 완료")
                    except Exception as exc:
                        st.error(f"저장 실패: {exc}")
        with confirm_c:
            if st.button(
                "✅ 확정 (저장+전송)", width="stretch", type="primary",
                key=f"confirm_{selected_post_id}",
                help="편집 내용을 저장하고 AI 워커 처리 대기열로 전송합니다",
            ):
                if _validate_editor():
                    try:
                        _persist_script(new_status=PostStatus.APPROVED)
                        st.toast("✅ 확정 완료 — AI 워커 대기열로 이동")
                        st.session_state["editor_idx"] = max(0, idx - 1)
                        _safe_rerun_fragment()
                    except Exception as exc:
                        st.error(f"확정 실패: {exc}")

    # ── 6. 비동기 작업 상태 모니터 (fragment — 2초마다 이 블록만 조용히 갱신) ────
    @st.fragment(run_every="10s")
    def _task_status_monitor(pid: int) -> None:
        """LLM / TTS 작업 완료를 10초 간격으로 감지.
        완료 시점에만 전체 rerun을 트리거하고, 그 전까지는 이 fragment만 갱신.
        """
        _l = get_llm_task(pid)
        _t = get_tts_task(pid)

        if _l:
            if _l["status"] == "done":
                _inject_ai_result(pid, _l["result"])
                clear_llm_task(pid)
                st.session_state.pop(f"_llm_gen_requested_{pid}", None)
                st.toast("✅ AI 대본 생성 완료!")
                st.rerun()
            elif _l["status"] == "error":
                st.error(f"❌ 대본 생성 실패: {_l.get('error', '알 수 없는 오류')}")
                clear_llm_task(pid)
                st.session_state.pop(f"_llm_gen_requested_{pid}", None)

        if _t:
            if _t["status"] == "done":
                st.session_state[f"tts_audio_{pid}"] = _t["path"]
                clear_tts_task(pid)
                st.toast("✅ TTS 미리듣기 완료!")
                st.rerun()
            elif _t["status"] == "error":
                st.error(f"❌ TTS 실패: {_t.get('error', '알 수 없는 오류')}")
                clear_tts_task(pid)

        _any_running = (
            (_l is not None and _l.get("status") == "running")
            or (_t is not None and _t.get("status") == "running")
        )
        if not _any_running:
            return  # 실행 중인 작업 없음

        if _any_running:
            _msgs = []
            if _l and _l["status"] == "running":
                _msgs.append("🤖 AI 대본")
            if _t and _t["status"] == "running":
                _msgs.append("🎙️ TTS")
            st.caption(f"{'·'.join(_msgs)} 생성 중... (자동 감지)")

    _task_status_monitor(_pid)
