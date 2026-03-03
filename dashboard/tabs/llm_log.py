"""LLM 호출 이력 (LLM Log) 탭.

두 가지 서브뷰로 구분:
- 대본 LLM: chunk, generate_script, generate_script_editor
- 비디오 LLM: video_prompt_t2v, video_prompt_i2v, video_prompt_simplify
"""

import json
from datetime import datetime, timezone, timedelta

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import func

from db.models import Post, LLMLog
from db.session import SessionLocal

# ── 카테고리 정의 ────────────────────────────────────
_SCRIPT_TYPES = ("chunk", "generate_script", "generate_script_editor")
_VIDEO_TYPES = ("video_prompt_t2v", "video_prompt_i2v", "video_prompt_simplify")

_VIDEO_MODE_LABELS = {
    "video_prompt_t2v": "T2V",
    "video_prompt_i2v": "I2V",
    "video_prompt_simplify": "Simplify",
}


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


def _render_stats(db, cutoff: datetime, call_types: tuple[str, ...]) -> None:
    """통계 카드 렌더링."""
    base_q = db.query(LLMLog).filter(
        LLMLog.created_at >= cutoff,
        LLMLog.call_type.in_(call_types),
    )
    total = base_q.count()
    success_cnt = base_q.filter(LLMLog.success == True).count()  # noqa: E712
    avg_dur = (
        db.query(func.avg(LLMLog.duration_ms))
        .filter(LLMLog.created_at >= cutoff, LLMLog.call_type.in_(call_types))
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


def _render_script_log(log, posts_map: dict[int, Post]) -> None:
    """대본 LLM 로그 항목 하나를 expander로 렌더링."""
    is_editor = log.call_type == "generate_script_editor"
    if log.success:
        icon = "🔵" if is_editor else "✅"
    else:
        icon = "❌"
    post = posts_map.get(log.post_id) if log.post_id else None
    site = post.site_code if post else "-"
    title = (post.title[:30] + "…") if post and len(post.title) > 30 else (post.title if post else "-")
    img_count = len(post.images) if post and isinstance(post.images, list) else 0
    hdr = f"{icon} #{log.id} {site} | {title} | 이미지 {img_count}장"

    with st.expander(hdr):
        mc, rc = st.columns(2)
        with mc:
            st.markdown(
                f"**모델:** `{log.model_name or '-'}`  \n"
                f"**본문 길이:** {log.content_length}자"
            )
            if log.error_message:
                st.error(log.error_message)
        with rc:
            if log.parsed_result:
                st.markdown("**파싱 결과**")
                st.json(log.parsed_result)

        st.markdown("**프롬프트**")
        _copyable_code(log.prompt_text or "(없음)", key=f"prompt_{log.id}")
        st.markdown("**LLM 응답**")
        _copyable_code(log.raw_response or "(없음)", key=f"response_{log.id}")


def _render_video_log(log, posts_map: dict[int, Post]) -> None:
    """비디오 LLM 로그 항목 하나를 expander로 렌더링."""
    icon = "✅" if log.success else "❌"
    mode_label = _VIDEO_MODE_LABELS.get(log.call_type, log.call_type)

    # parsed_result에서 씬 인덱스 추출
    scene_idx = "-"
    video_mode = mode_label
    if isinstance(log.parsed_result, dict):
        si = log.parsed_result.get("scene_index")
        if si is not None:
            scene_idx = str(si)
        vm = log.parsed_result.get("video_mode")
        if vm:
            video_mode = vm.upper()

    post = posts_map.get(log.post_id) if log.post_id else None
    site = post.site_code if post else "-"
    title = (post.title[:30] + "…") if post and len(post.title) > 30 else (post.title if post else "-")

    hdr = f"{icon} #{log.id} [{mode_label}] 씬 {scene_idx} | {site} | {title}"

    with st.expander(hdr):
        mc, rc = st.columns(2)
        with mc:
            st.markdown(
                f"**유형:** `{mode_label}` ({video_mode})  \n"
                f"**씬 번호:** {scene_idx}  \n"
                f"**응답시간:** {log.duration_ms or 0}ms  \n"
                f"**입력 길이:** {log.content_length}자"
            )
            if log.error_message:
                st.error(log.error_message)
        with rc:
            if log.raw_response:
                st.markdown("**생성된 프롬프트**")
                st.info(log.raw_response)

        st.markdown("**LLM 입력 (시스템 프롬프트)**")
        _copyable_code(log.prompt_text or "(없음)", key=f"vprompt_{log.id}")
        st.markdown("**LLM 응답 (원시)**")
        _copyable_code(log.raw_response or "(없음)", key=f"vresponse_{log.id}")


def render() -> None:
    """LLM 이력 탭 렌더링."""

    _llm_hdr, _llm_ref = st.columns([5, 1])
    with _llm_hdr:
        st.header("🔬 LLM 호출 이력")
    with _llm_ref:
        if st.button("🔄 새로고침", key="llm_refresh_btn", width="stretch"):
            st.rerun()

    # ── 서브뷰 선택 ─────────────────────────────────
    sub_view = st.radio(
        "카테고리",
        ["📝 대본 LLM (TTS·씬)", "🎬 비디오 LLM (씬별 프롬프트)"],
        horizontal=True,
        key="llm_sub_view",
    )
    is_video_view = sub_view.startswith("🎬")

    # ── 필터 컨트롤 ─────────────────────────────────
    if is_video_view:
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            filter_call_type = st.selectbox(
                "프롬프트 유형",
                ["전체", "video_prompt_t2v", "video_prompt_i2v", "video_prompt_simplify"],
                format_func=lambda v: {
                    "전체": "전체",
                    "video_prompt_t2v": "T2V (텍스트→비디오)",
                    "video_prompt_i2v": "I2V (이미지→비디오)",
                    "video_prompt_simplify": "Simplify (재시도용)",
                }.get(v, v),
                key="llm_vfilter_type",
            )
        with col_f2:
            filter_success = st.selectbox(
                "성공 여부", ["전체", "성공", "실패"], key="llm_vfilter_success"
            )
        with col_f3:
            filter_days = st.selectbox(
                "기간",
                [7, 30, 90],
                format_func=lambda d: f"최근 {d}일",
                key="llm_vfilter_days",
            )
        with col_f4:
            filter_post_id = st.number_input(
                "Post ID",
                min_value=0,
                value=0,
                step=1,
                key="llm_vfilter_post_id",
                help="0이면 전체 표시",
            )
        active_types = _VIDEO_TYPES
    else:
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            filter_call_type = st.selectbox(
                "호출 유형",
                ["전체", "chunk", "generate_script", "generate_script_editor"],
                key="llm_filter_type",
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
        with col_f4:
            filter_post_id = st.number_input(
                "Post ID",
                min_value=0,
                value=0,
                step=1,
                key="llm_filter_post_id",
                help="0이면 전체 표시",
            )
        active_types = _SCRIPT_TYPES

    with SessionLocal() as _db:
        _cutoff = datetime.now(timezone.utc) - timedelta(days=filter_days)

        # 통계
        _render_stats(_db, _cutoff, active_types)

        st.divider()

        # 필터 적용 로그 조회
        _fq = _db.query(LLMLog).filter(LLMLog.created_at >= _cutoff)

        if filter_call_type != "전체":
            _fq = _fq.filter(LLMLog.call_type == filter_call_type)
        else:
            _fq = _fq.filter(LLMLog.call_type.in_(active_types))

        if filter_success == "성공":
            _fq = _fq.filter(LLMLog.success == True)  # noqa: E712
        elif filter_success == "실패":
            _fq = _fq.filter(LLMLog.success == False)  # noqa: E712
        if filter_post_id > 0:
            _fq = _fq.filter(LLMLog.post_id == filter_post_id)

        _logs = _fq.order_by(LLMLog.created_at.desc()).limit(200).all()

        # Post 일괄 조회
        _post_ids = {lg.post_id for lg in _logs if lg.post_id is not None}
        _posts_map: dict[int, Post] = {}
        if _post_ids:
            _posts_map = {
                p.id: p
                for p in _db.query(Post).filter(Post.id.in_(_post_ids)).all()
            }

    if not _logs:
        st.info("조건에 맞는 이력이 없습니다.")
    else:
        st.caption(f"최근 {filter_days}일 이력 (최대 200건 표시)")
        for _log in _logs:
            if is_video_view:
                _render_video_log(_log, _posts_map)
            else:
                _render_script_log(_log, _posts_map)
