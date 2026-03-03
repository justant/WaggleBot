"""LLM 호출 이력 (LLM Log) 탭.

콘텐츠별로 그룹화하여 대본/비디오 LLM 이력을 계층적으로 표시.
구조: 콘텐츠 리스트 → 대본/비디오 탭 → (비디오) 씬별 프롬프트
"""

import json
import re
from datetime import datetime, timezone, timedelta

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import func

from db.models import Post, Content, LLMLog
from db.session import SessionLocal

# ── 카테고리 정의 ────────────────────────────────────
_SCRIPT_TYPES = ("chunk", "generate_script", "generate_script_editor")
_VIDEO_TYPES = ("video_prompt_t2v", "video_prompt_i2v", "video_prompt_simplify")
_ALL_TYPES = _SCRIPT_TYPES + _VIDEO_TYPES


def _copyable_code(text: str, key: str) -> None:
    """HTTP 환경에서도 동작하는 복사 버튼 포함 코드 블록."""
    escaped = json.dumps(text)
    lines = text.count("\n") + 1
    height = min(lines * 20 + 70, 500)
    uid = key.replace("-", "_")

    components.html(
        f"""
    <style>
    .cc-wrap {{
        position: relative;
        background: #0e1117;
        border-radius: 0.5rem;
    }}
    .cc-pre {{
        margin: 0;
        padding: 1rem;
        padding-top: 2.2rem;
        color: #fafafa;
        font-family: 'Source Code Pro', 'Courier New', monospace;
        font-size: 13px;
        line-height: 1.5;
        white-space: pre-wrap;
        word-break: break-word;
        overflow: auto;
    }}
    .cc-btn {{
        position: absolute;
        top: 6px;
        right: 6px;
        background: #262730;
        color: #aaa;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 3px 10px;
        cursor: pointer;
        font-size: 12px;
        z-index: 2;
    }}
    .cc-btn:hover {{ background: #3a3a4a; color: #fff; }}
    </style>
    <div class="cc-wrap">
        <button class="cc-btn" id="btn_{uid}" onclick="doCopy_{uid}()">
            📋 복사
        </button>
        <pre class="cc-pre" id="pre_{uid}"></pre>
    </div>
    <script>
    (function() {{
        var text = {escaped};
        document.getElementById('pre_{uid}').textContent = text;
        window.doCopy_{uid} = function() {{
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            var ok = false;
            try {{ ok = document.execCommand('copy'); }} catch(e) {{}}
            document.body.removeChild(ta);
            if (!ok) {{
                try {{
                    navigator.clipboard.writeText(text);
                    ok = true;
                }} catch(e) {{}}
            }}
            var btn = document.getElementById('btn_{uid}');
            btn.textContent = ok ? '✅ 복사됨' : '❌ 실패';
            setTimeout(function() {{ btn.textContent = '📋 복사'; }}, 2000);
        }};
    }})();
    </script>
    """,
        height=height,
        scrolling=True,
    )


def _extract_scene_desc(log) -> str:
    """비디오 LLM 프롬프트에서 씬 설명(한국어 텍스트) 15자 추출."""
    prompt = log.prompt_text or ""
    # T2V: "Korean source text: ..."
    m = re.search(r"Korean source text:\s*(.+)", prompt, re.DOTALL)
    if not m:
        # I2V: "Korean context: ..." 다음 "Write the motion prompt:" 전까지
        m = re.search(r"Korean context:\s*(.+?)(?:\n\nWrite)", prompt, re.DOTALL)
    if m:
        text = m.group(1).strip()
        # 제목 접두사 제거: "[제목: ...] "
        text = re.sub(r"^\[제목:\s*[^\]]*\]\s*", "", text)
        return text[:15] + "…" if len(text) > 15 else text
    # Fallback: raw_response 앞부분
    resp = log.raw_response or ""
    if resp:
        return resp[:15] + "…" if len(resp) > 15 else resp
    return "-"


def _render_stats(db, cutoff: datetime) -> None:
    """통계 카드 렌더링."""
    base_q = db.query(LLMLog).filter(
        LLMLog.created_at >= cutoff,
        LLMLog.call_type.in_(_ALL_TYPES),
    )
    total = base_q.count()
    success_cnt = base_q.filter(LLMLog.success == True).count()  # noqa: E712
    avg_dur = (
        db.query(func.avg(LLMLog.duration_ms))
        .filter(LLMLog.created_at >= cutoff, LLMLog.call_type.in_(_ALL_TYPES))
        .scalar()
        or 0
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("총 호출 (기간)", total)
    c2.metric(
        "성공률",
        f"{(success_cnt / total * 100):.1f}%" if total else "N/A",
    )
    c3.metric("평균 응답시간", f"{avg_dur:.0f}ms" if avg_dur else "N/A")


def _render_script_section(script_logs: list, post_id) -> None:
    """대본 LLM 섹션 렌더링 (콘텐츠 expander 내부)."""
    if not script_logs:
        st.caption("대본 LLM 이력 없음")
        return

    for slog in script_logs:
        is_editor = slog.call_type == "generate_script_editor"
        icon = "🔵" if is_editor else ("✅" if slog.success else "❌")
        label = "편집실 재생성" if is_editor else slog.call_type
        sub_hdr = f"{icon} {label} | {slog.duration_ms or 0}ms | {slog.content_length}자"

        with st.expander(sub_hdr):
            mc, rc = st.columns(2)
            with mc:
                st.markdown(
                    f"**모델:** `{slog.model_name or '-'}`  \n"
                    f"**본문 길이:** {slog.content_length}자  \n"
                    f"**응답시간:** {slog.duration_ms or 0}ms"
                )
                if slog.error_message:
                    st.error(slog.error_message)
            with rc:
                if slog.parsed_result:
                    st.markdown("**파싱 결과**")
                    st.json(slog.parsed_result)

            st.markdown("**프롬프트**")
            _copyable_code(slog.prompt_text or "(없음)", key=f"sp_{slog.id}")
            st.markdown("**LLM 응답**")
            _copyable_code(slog.raw_response or "(없음)", key=f"sr_{slog.id}")


def _render_video_section(video_logs: list, post_id) -> None:
    """비디오 LLM 섹션 렌더링 — 씬별 그룹화."""
    if not video_logs:
        st.caption("비디오 LLM 이력 없음")
        return

    # 씬별 그룹화
    scene_groups: dict[int | str, list] = {}
    for vlog in video_logs:
        idx: int | str = "?"
        if isinstance(vlog.parsed_result, dict):
            si = vlog.parsed_result.get("scene_index")
            if si is not None:
                idx = int(si)
        if idx not in scene_groups:
            scene_groups[idx] = []
        scene_groups[idx].append(vlog)

    # 씬 번호순 정렬
    sorted_scenes = sorted(
        scene_groups.items(),
        key=lambda x: (isinstance(x[0], str), x[0] if isinstance(x[0], int) else 999),
    )

    for scene_idx, scene_logs in sorted_scenes:
        # 메인 로그 (T2V 또는 I2V)
        main_log = next(
            (l for l in scene_logs if l.call_type in ("video_prompt_t2v", "video_prompt_i2v")),
            None,
        )
        simplify_logs = [l for l in scene_logs if l.call_type == "video_prompt_simplify"]

        # 씬 설명 추출
        desc = _extract_scene_desc(main_log) if main_log else "-"
        all_success = all(l.success for l in scene_logs)
        icon = "✅" if all_success else "❌"
        # 0-indexed → 1-indexed 표시
        display_num = scene_idx + 1 if isinstance(scene_idx, int) else "?"

        with st.expander(f"{icon} 씬 #{display_num} | {desc}"):
            # ── T2V / I2V 프롬프트 ──
            if main_log:
                mode = "T2V" if main_log.call_type == "video_prompt_t2v" else "I2V"
                m_icon = "✅" if main_log.success else "❌"
                st.markdown(f"#### {m_icon} {mode} 프롬프트")
                st.markdown(
                    f"**응답시간:** {main_log.duration_ms or 0}ms | "
                    f"**입력 길이:** {main_log.content_length}자"
                )
                if main_log.error_message:
                    st.error(main_log.error_message)
                if main_log.raw_response:
                    st.markdown("**생성된 프롬프트**")
                    st.info(main_log.raw_response)
                st.markdown("**LLM 입력**")
                _copyable_code(main_log.prompt_text or "(없음)", key=f"vm_{main_log.id}")
                st.markdown("**LLM 응답 (원시)**")
                _copyable_code(main_log.raw_response or "(없음)", key=f"vr_{main_log.id}")
            else:
                st.caption("T2V/I2V 프롬프트 이력 없음")

            # ── Simplified 프롬프트 ──
            if simplify_logs:
                st.divider()
                for i, slog in enumerate(simplify_logs):
                    s_icon = "✅" if slog.success else "❌"
                    suffix = f" #{i + 1}" if len(simplify_logs) > 1 else ""
                    st.markdown(f"#### {s_icon} Simplified 프롬프트{suffix}")
                    st.markdown(
                        f"**응답시간:** {slog.duration_ms or 0}ms | "
                        f"**입력 길이:** {slog.content_length}자"
                    )
                    if slog.error_message:
                        st.error(slog.error_message)
                    if slog.raw_response:
                        st.markdown("**생성된 프롬프트**")
                        st.info(slog.raw_response)
                    st.markdown("**LLM 입력**")
                    _copyable_code(slog.prompt_text or "(없음)", key=f"vs_{slog.id}")
                    st.markdown("**LLM 응답 (원시)**")
                    _copyable_code(slog.raw_response or "(없음)", key=f"vsr_{slog.id}")


def render() -> None:
    """LLM 이력 탭 렌더링 — 콘텐츠별 계층 구조."""

    _llm_hdr, _llm_ref = st.columns([5, 1])
    with _llm_hdr:
        st.header("🔬 LLM 호출 이력")
    with _llm_ref:
        if st.button("🔄 새로고침", key="llm_refresh_btn", width="stretch"):
            st.rerun()

    # ── 필터 컨트롤 ─────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_success = st.selectbox(
            "성공 여부", ["전체", "성공", "실패"], key="llm_filter_success"
        )
    with col_f2:
        filter_days = st.selectbox(
            "기간",
            [7, 30, 90],
            format_func=lambda d: f"최근 {d}일",
            key="llm_filter_days",
        )
    with col_f3:
        filter_post_id = st.number_input(
            "Post ID",
            min_value=0,
            value=0,
            step=1,
            key="llm_filter_post_id",
            help="0이면 전체 표시",
        )

    with SessionLocal() as _db:
        _cutoff = datetime.now(timezone.utc) - timedelta(days=filter_days)

        # 통계
        _render_stats(_db, _cutoff)
        st.divider()

        # 로그 조회
        _fq = _db.query(LLMLog).filter(
            LLMLog.created_at >= _cutoff,
            LLMLog.call_type.in_(_ALL_TYPES),
        )
        if filter_success == "성공":
            _fq = _fq.filter(LLMLog.success == True)  # noqa: E712
        elif filter_success == "실패":
            _fq = _fq.filter(LLMLog.success == False)  # noqa: E712
        if filter_post_id > 0:
            _fq = _fq.filter(LLMLog.post_id == filter_post_id)

        _logs = _fq.order_by(LLMLog.created_at.desc()).limit(500).all()

        if not _logs:
            st.info("조건에 맞는 이력이 없습니다.")
            return

        # Post / Content 일괄 조회
        _post_ids = {lg.post_id for lg in _logs if lg.post_id is not None}
        _posts_map: dict[int, Post] = {}
        _contents_map: dict[int, Content] = {}  # post_id → Content
        if _post_ids:
            _posts_map = {
                p.id: p
                for p in _db.query(Post).filter(Post.id.in_(_post_ids)).all()
            }
            _contents_map = {
                c.post_id: c
                for c in _db.query(Content).filter(Content.post_id.in_(_post_ids)).all()
            }

        # post_id별 그룹화 (최신 로그 순서 유지)
        groups: dict[int | None, list] = {}
        for log in _logs:
            pid = log.post_id
            if pid not in groups:
                groups[pid] = []
            groups[pid].append(log)

    # ── 콘텐츠별 렌더링 ─────────────────────────────
    st.caption(f"최근 {filter_days}일 이력 — {len(groups)}개 콘텐츠")

    for post_id, logs in groups.items():
        post = _posts_map.get(post_id) if post_id else None
        content = _contents_map.get(post_id) if post_id else None

        # 헤더 구성: #{content.id} | {post_id} | site | title | 이미지 N장
        content_id = content.id if content else "?"
        pid_str = str(post_id) if post_id else "?"
        site = post.site_code if post else "-"
        title = (
            (post.title[:25] + "…") if post and len(post.title) > 25
            else (post.title if post else "-")
        )
        img_count = len(post.images) if post and isinstance(post.images, list) else 0

        # 대본/비디오 로그 분류
        script_logs = [l for l in logs if l.call_type in _SCRIPT_TYPES]
        video_logs = [l for l in logs if l.call_type in _VIDEO_TYPES]

        hdr = f"#{content_id} | {pid_str} | {site} | {title} | 이미지 {img_count}장"

        with st.expander(hdr):
            tab_labels = [
                f"📝 대본 LLM ({len(script_logs)})",
                f"🎬 비디오 LLM ({len(video_logs)})",
            ]
            tab_script, tab_video = st.tabs(tab_labels)

            with tab_script:
                _render_script_section(script_logs, post_id)

            with tab_video:
                _render_video_section(video_logs, post_id)
