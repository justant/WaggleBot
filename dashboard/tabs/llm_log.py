"""LLM 호출 이력 (LLM Log) 탭.

3개 서브탭(대본/씬/비디오)으로 구성.
- 대본 LLM: 대본 생성/청킹 이력 + JSON 구문 강조
- 씬 LLM: 씬 디렉팅 이력 + 대본→씬 Diff 뷰
- 비디오 LLM: 비디오 프롬프트 이력 + 한글/영어 이중언어 뷰
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import func

from db.models import Post, Content, LLMLog
from db.session import SessionLocal

logger = logging.getLogger(__name__)

# ── 카테고리 정의 ────────────────────────────────────
_SCRIPT_TYPES = ("chunk", "generate_script", "generate_script_editor", "generate_script_auto")
_SCENE_TYPES = ("scene_director",)
_VIDEO_TYPES = ("video_prompt_t2v", "video_prompt_i2v", "video_prompt_simplify")
_ALL_TYPES = _SCRIPT_TYPES + _SCENE_TYPES + _VIDEO_TYPES

_CALL_TYPE_LABELS = {
    "generate_script": "생성",
    "generate_script_editor": "편집실",
    "generate_script_auto": "자동생성",
    "chunk": "청킹",
    "scene_director": "씬 디렉팅",
    "video_prompt_t2v": "T2V",
    "video_prompt_i2v": "I2V",
    "video_prompt_simplify": "단순화",
}

# ── 씬 Diff 색상 ────────────────────────────────────
_CLIP_COLORS = {
    "itv": ("#1e3a5f", "#4a9eff"),  # (배경, 테두리) 파란색
    "ttv": ("#1e4d2e", "#4aff7f"),  # 초록색
    "text_only": ("#2a2a2a", "#888"),  # 회색
    "image_text": ("#4d3a1e", "#ffc04a"),  # 노란색
}


# ══════════════════════════════════════════════════════
# 공통 유틸리티
# ══════════════════════════════════════════════════════

def _relative_time(dt: datetime) -> str:
    """datetime을 상대 시간 문자열로 변환한다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = datetime.now(timezone.utc) - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "방금"
    if seconds < 60:
        return f"{seconds}초 전"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}분 전"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 전"
    days = hours // 24
    return f"{days}일 전"


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
            copy
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
            btn.textContent = ok ? 'copied' : 'failed';
            setTimeout(function() {{ btn.textContent = 'copy'; }}, 2000);
        }};
    }})();
    </script>
    """,
        height=height,
        scrolling=True,
    )


def _extract_scene_desc(log) -> str:
    """비디오 LLM 프롬프트에서 씬 설명(한국어 텍스트) 15자 추출."""
    # parsed_result에 korean_text가 있으면 우선 사용
    if isinstance(log.parsed_result, dict):
        kt = log.parsed_result.get("korean_text", "")
        if kt:
            kt = re.sub(r"^\[제목:\s*[^\]]*\]\s*", "", kt)
            return kt[:15] + "…" if len(kt) > 15 else kt

    prompt = log.prompt_text or ""
    m = re.search(r"Korean source text:\s*(.+)", prompt, re.DOTALL)
    if not m:
        m = re.search(r"Korean context:\s*(.+?)(?:\n\nWrite)", prompt, re.DOTALL)
    if m:
        text = m.group(1).strip()
        text = re.sub(r"^\[제목:\s*[^\]]*\]\s*", "", text)
        return text[:15] + "…" if len(text) > 15 else text
    resp = log.raw_response or ""
    if resp:
        return resp[:15] + "…" if len(resp) > 15 else resp
    return "-"


def _query_logs_flat(
    db,
    call_types: tuple[str, ...],
    cutoff: datetime,
    filter_success: str,
    filter_post_id: int,
) -> tuple[list, dict[int, Post], dict[int, Content]]:
    """call_type별 로그를 created_at DESC 플랫 리스트로 조회한다.

    동일 post_id라도 각 로그가 독립 row로 표시된다.
    """
    q = db.query(LLMLog).filter(
        LLMLog.created_at >= cutoff,
        LLMLog.call_type.in_(call_types),
    )
    if filter_success == "성공":
        q = q.filter(LLMLog.success == True)  # noqa: E712
    elif filter_success == "실패":
        q = q.filter(LLMLog.success == False)  # noqa: E712
    if filter_post_id > 0:
        q = q.filter(LLMLog.post_id == filter_post_id)

    logs = q.order_by(LLMLog.created_at.desc()).limit(500).all()

    # Post / Content 일괄 조회
    post_ids = {lg.post_id for lg in logs if lg.post_id is not None}
    posts_map: dict[int, Post] = {}
    contents_map: dict[int, Content] = {}
    if post_ids:
        posts_map = {p.id: p for p in db.query(Post).filter(Post.id.in_(post_ids)).all()}
        contents_map = {c.post_id: c for c in db.query(Content).filter(Content.post_id.in_(post_ids)).all()}

    return logs, posts_map, contents_map


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
    c1.metric("총 호출", total)
    c2.metric("성공률", f"{(success_cnt / total * 100):.1f}%" if total else "N/A")
    c3.metric("평균 응답시간", f"{avg_dur:.0f}ms" if avg_dur else "N/A")


def _post_header(post_id, posts_map: dict, contents_map: dict) -> str:
    """post_id → expander 헤더 문자열."""
    post = posts_map.get(post_id) if post_id else None
    content = contents_map.get(post_id) if post_id else None
    cid = content.id if content else "?"
    pid = str(post_id) if post_id else "?"
    site = post.site_code if post else "-"
    title = (
        (post.title[:25] + "…") if post and len(post.title) > 25
        else (post.title if post else "-")
    )
    return f"#{cid} | {pid} | {site} | {title}"


# ══════════════════════════════════════════════════════
# 서브탭 1: 대본 LLM
# ══════════════════════════════════════════════════════

def _render_script_tab(db, cutoff: datetime, filter_success: str, filter_post_id: int) -> None:
    """대본 LLM 서브탭 전체 렌더링."""
    _render_stats(db, cutoff, _SCRIPT_TYPES)
    st.divider()

    logs, posts_map, contents_map = _query_logs_flat(
        db, _SCRIPT_TYPES, cutoff, filter_success, filter_post_id,
    )
    if not logs:
        st.info("조건에 맞는 대본 LLM 이력이 없습니다.")
        return

    st.caption(f"{len(logs)}건")

    for log in logs:
        icon = "O" if log.success else "X"
        label = _CALL_TYPE_LABELS.get(log.call_type, log.call_type)
        hdr = (
            f"[{icon}] {_post_header(log.post_id, posts_map, contents_map)} | "
            f"{label} | {log.model_name or '-'} | "
            f"{log.duration_ms or 0}ms | {log.content_length}자 | "
            f"{_relative_time(log.created_at)}"
        )

        with st.expander(hdr):
            if log.error_message:
                st.error(log.error_message)

            # 메타 정보
            st.markdown(
                f"**모델:** `{log.model_name or '-'}` | "
                f"**전략:** `{log.strategy or '-'}` | "
                f"**본문:** {log.content_length}자 | "
                f"**응답:** {log.duration_ms or 0}ms"
            )

            # 입력/출력 탭
            t_in, t_out = st.tabs(["입력", "출력"])

            with t_in:
                prompt = log.prompt_text or "(없음)"
                preview_lines = prompt.split("\n")[:10]
                st.code("\n".join(preview_lines), language="markdown")
                if len(prompt.split("\n")) > 10:
                    with st.expander("전체 프롬프트 보기"):
                        _copyable_code(prompt, key=f"sp_{log.id}")

            with t_out:
                if log.parsed_result:
                    st.json(log.parsed_result)
                elif log.raw_response:
                    _copyable_code(log.raw_response, key=f"sr_{log.id}")
                else:
                    st.caption("출력 없음")


# ══════════════════════════════════════════════════════
# 서브탭 2: 씬 LLM
# ══════════════════════════════════════════════════════

def _build_scene_diff(
    script_parsed: dict | None,
    scene_parsed: dict | None,
) -> dict | None:
    """대본 LLM 출력과 씬 LLM 출력을 diff 구조로 변환한다.

    Returns:
        {
            "before": [{"index": 0, "text": "...", "type": "body"}, ...],
            "after": [{"clip_type": "itv", "source_scenes": [0,1,2], ...}, ...],
            "mapping": {0: {"clip_index": 0, "clip_type": "itv"}, ...},
        }
    """
    if not scene_parsed:
        return None

    # Before: 씬 디렉터에 저장된 입력 씬 정보 사용
    scenes_input = scene_parsed.get("scenes_input", [])
    before = [
        {"index": s.get("index", i), "text": s.get("text", ""), "type": s.get("type", "body")}
        for i, s in enumerate(scenes_input)
    ]

    # 대본 LLM의 body에서 보충 (scenes_input이 없는 구버전)
    if not before and script_parsed:
        body = script_parsed.get("body", [])
        idx = 0
        for item in body:
            if isinstance(item, dict):
                for line in item.get("lines", []):
                    before.append({"index": idx, "text": line, "type": item.get("type", "body")})
                    idx += 1
            elif isinstance(item, str):
                before.append({"index": idx, "text": item, "type": "body"})
                idx += 1

    if not before:
        return None

    # After: 비디오 클립 + 정적 씬
    merge_groups_map = scene_parsed.get("merge_groups_map", {})
    video_clips = scene_parsed.get("video_clips", [])
    static_scenes = scene_parsed.get("static_scenes", [])

    after = []
    mapping: dict[int, dict] = {}
    clip_idx = 0

    for clip in video_clips:
        group_id = clip.get("group_id", "")
        source_scenes = merge_groups_map.get(group_id, [])
        clip_type = clip.get("type", "ttv")

        # 텍스트 병합
        merged_texts = []
        for si in source_scenes:
            for b in before:
                if b["index"] == si:
                    merged_texts.append(b["text"])
                    break

        after.append({
            "clip_type": clip_type,
            "source_scenes": source_scenes,
            "merged_text": " ".join(merged_texts),
            "group_id": group_id,
            "reason": clip.get("reason", ""),
            "image_id": clip.get("image_id"),
        })

        for si in source_scenes:
            mapping[si] = {"clip_index": clip_idx, "clip_type": clip_type}
        clip_idx += 1

    for static in static_scenes:
        source_scenes = static.get("scene_indices", [])
        clip_type = static.get("type", "text_only")

        merged_texts = []
        for si in source_scenes:
            for b in before:
                if b["index"] == si:
                    merged_texts.append(b["text"])
                    break

        after.append({
            "clip_type": clip_type,
            "source_scenes": source_scenes,
            "merged_text": " ".join(merged_texts),
            "reason": static.get("reason", ""),
            "image_id": static.get("image_id"),
        })

        for si in source_scenes:
            mapping[si] = {"clip_index": clip_idx, "clip_type": clip_type}
        clip_idx += 1

    return {"before": before, "after": after, "mapping": mapping}


def _render_scene_diff(diff_data: dict, key_prefix: str) -> None:
    """씬 Diff 뷰 렌더링 (좌우 2-컬럼 색상 매핑)."""
    before = diff_data["before"]
    after = diff_data["after"]
    mapping = diff_data["mapping"]

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**대본 LLM 출력 (원본 씬)**")
        for scene in before:
            idx = scene["index"]
            m = mapping.get(idx, {})
            clip_type = m.get("clip_type", "text_only")
            _, border_color = _CLIP_COLORS.get(clip_type, ("#2a2a2a", "#888"))
            clip_idx = m.get("clip_index", "?")

            text_preview = scene["text"][:30] + "…" if len(scene["text"]) > 30 else scene["text"]
            st.markdown(
                f'<div style="border-left: 3px solid {border_color}; '
                f'padding: 4px 8px; margin: 2px 0; font-size: 13px;">'
                f'<b>#{idx}</b> [{scene["type"]}] → 클립{clip_idx} '
                f'<span style="color: #aaa;">{text_preview}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with col_right:
        st.markdown("**씬 LLM 출력 (병합 후)**")
        for i, clip in enumerate(after):
            clip_type = clip["clip_type"]
            bg_color, border_color = _CLIP_COLORS.get(clip_type, ("#2a2a2a", "#888"))
            type_label = clip_type.upper()
            scenes_str = ", ".join(str(s) for s in clip["source_scenes"])
            text_preview = clip["merged_text"][:40] + "…" if len(clip["merged_text"]) > 40 else clip["merged_text"]

            extra = ""
            if clip.get("group_id"):
                extra = f' | {clip["group_id"]}'
            if clip.get("image_id") is not None:
                extra += f' | img#{clip["image_id"]}'

            st.markdown(
                f'<div style="background: {bg_color}; border: 1px solid {border_color}; '
                f'border-radius: 4px; padding: 6px 8px; margin: 3px 0; font-size: 13px;">'
                f'<b>[{type_label}] 클립 {i}</b>{extra}<br/>'
                f'<span style="color: #ccc;">씬 [{scenes_str}]</span><br/>'
                f'<span style="color: #aaa; font-size: 12px;">{clip["reason"]}</span><br/>'
                f'<span style="color: #ddd; font-size: 12px;">{text_preview}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_scene_tab(db, cutoff: datetime, filter_success: str, filter_post_id: int) -> None:
    """씬 LLM 서브탭 전체 렌더링."""
    _render_stats(db, cutoff, _SCENE_TYPES)
    st.divider()

    logs, posts_map, contents_map = _query_logs_flat(
        db, _SCENE_TYPES, cutoff, filter_success, filter_post_id,
    )
    if not logs:
        st.info("조건에 맞는 씬 LLM 이력이 없습니다.")
        return

    st.caption(f"{len(logs)}건")

    for log in logs:
        pr = log.parsed_result or {}
        scene_count = pr.get("total_scenes", "?")
        clip_count = pr.get("video_clip_count", "?")
        itv = pr.get("itv_count", 0)
        ttv = pr.get("ttv_count", 0)

        icon = "O" if log.success else "X"
        hdr = (
            f"[{icon}] {_post_header(log.post_id, posts_map, contents_map)} | "
            f"씬{scene_count} | 클립{clip_count} (ITV{itv}/TTV{ttv}) | "
            f"{log.duration_ms or 0}ms | {_relative_time(log.created_at)}"
        )

        with st.expander(hdr):
            if log.error_message:
                st.error(log.error_message)

            # 입력 / 출력 / Diff 탭
            t_in, t_out, t_diff = st.tabs(["입력", "출력", "Diff"])

            with t_in:
                _render_scene_input(log)

            with t_out:
                _render_scene_output(log)

            with t_diff:
                _render_scene_diff_tab(db, log)


def _render_scene_input(log) -> None:
    """씬 LLM 입력 3-섹션 렌더링."""
    prompt = log.prompt_text or ""
    if not prompt:
        st.caption("입력 프롬프트 없음")
        return

    # 프롬프트를 "---" 구분자로 시스템/사용자 분리
    parts = prompt.split("\n---\n", 1)
    system_part = parts[0] if parts else ""
    user_part = parts[1] if len(parts) > 1 else ""

    # 사용자 프롬프트에서 섹션 추출
    scene_table = ""
    merge_table = ""
    rest = user_part

    # 씬 목록 추출
    m = re.search(r"(## 씬 목록.*?)(?=## 병합|## 6초|$)", user_part, re.DOTALL)
    if m:
        scene_table = m.group(1).strip()

    # 병합 그룹 추출
    m = re.search(r"(## 병합 가능 그룹.*?)(?=## 6초|## ITV|## 제약|$)", user_part, re.DOTALL)
    if m:
        merge_table = m.group(1).strip()

    if scene_table:
        with st.expander("씬 목록 (대본 LLM 출력 기반)", expanded=True):
            st.code(scene_table, language="markdown")

    if merge_table:
        with st.expander("병합 그룹 후보 (사전 계산)"):
            st.code(merge_table, language="markdown")

    with st.expander("시스템 프롬프트"):
        st.code(system_part[:2000], language="markdown")

    with st.expander("전체 프롬프트 (원시)"):
        _copyable_code(prompt, key=f"sci_{log.id}")


def _render_scene_output(log) -> None:
    """씬 LLM 출력 렌더링 (JSON + 타입별 배지)."""
    pr = log.parsed_result
    if pr:
        # 요약
        st.markdown(
            f"**씬:** {pr.get('total_scenes', '?')}개 | "
            f"**비디오 클립:** {pr.get('video_clip_count', '?')}개 "
            f"(ITV {pr.get('itv_count', 0)} / TTV {pr.get('ttv_count', 0)}) | "
            f"**text_only:** {pr.get('text_only_count', '?')}개"
        )

        # video_clips 표시
        for i, clip in enumerate(pr.get("video_clips", [])):
            clip_type = clip.get("type", "?").upper()
            group_id = clip.get("group_id", "?")
            reason = clip.get("reason", "")
            color = "#4a9eff" if "itv" in clip_type.lower() else "#4aff7f"
            st.markdown(
                f'<span style="background: {color}20; color: {color}; '
                f'padding: 2px 6px; border-radius: 3px; font-size: 12px;">'
                f'{clip_type}</span> '
                f'{group_id} — {reason}',
                unsafe_allow_html=True,
            )

        # static_scenes 표시
        for static in pr.get("static_scenes", []):
            stype = static.get("type", "?")
            indices = static.get("scene_indices", [])
            reason = static.get("reason", "")
            st.markdown(
                f'<span style="background: #88888820; color: #888; '
                f'padding: 2px 6px; border-radius: 3px; font-size: 12px;">'
                f'{stype.upper()}</span> '
                f'씬 {indices} — {reason}',
                unsafe_allow_html=True,
            )

        with st.expander("전체 JSON"):
            st.json(pr)
    elif log.raw_response:
        _copyable_code(log.raw_response, key=f"sco_{log.id}")
    else:
        st.caption("출력 없음")


def _render_scene_diff_tab(db, scene_log) -> None:
    """씬 Diff 탭 렌더링 — 대본 LLM과 자동 매칭."""
    if not scene_log.parsed_result:
        st.warning("씬 LLM parsed_result가 없어 Diff를 생성할 수 없습니다.")
        return

    # 대응하는 대본 LLM 로그 찾기
    script_log = None
    if scene_log.post_id:
        script_log = (
            db.query(LLMLog)
            .filter(
                LLMLog.post_id == scene_log.post_id,
                LLMLog.call_type.in_(_SCRIPT_TYPES),
                LLMLog.success == True,  # noqa: E712
                LLMLog.created_at < scene_log.created_at,
            )
            .order_by(LLMLog.created_at.desc())
            .first()
        )

    script_parsed = script_log.parsed_result if script_log else None
    diff_data = _build_scene_diff(script_parsed, scene_log.parsed_result)

    if not diff_data:
        st.warning("Diff 데이터를 구성할 수 없습니다. (scenes_input 또는 대본 LLM 이력 없음)")
        return

    st.markdown("##### 대본 → 씬 변환 Diff")
    _render_scene_diff(diff_data, key_prefix=f"sd_{scene_log.id}")


# ══════════════════════════════════════════════════════
# 서브탭 3: 비디오 LLM
# ══════════════════════════════════════════════════════

# 워크플로우 파라미터 한글 매핑
_WORKFLOW_PARAM_KO = {
    "Resolution": "해상도",
    "Frames": "프레임 수",
    "Steps": "샘플링 스텝",
    "CFG": "CFG 스케일",
    "Mode": "모드",
    "Seed": "시드",
    "Negative prompt": "네거티브 프롬프트",
    "T2V (Full)": "Text-to-Video (풀)",
    "T2V (Distilled)": "Text-to-Video (경량)",
    "I2V (Full)": "Image-to-Video (풀)",
    "I2V (Distilled)": "Image-to-Video (경량)",
    "worst quality": "최악 품질",
    "inconsistent motion": "불일치 모션",
    "blurry": "흐림",
    "jittery": "떨림",
    "distorted": "왜곡",
    "watermarks": "워터마크",
}


def _parse_video_prompt_sections(prompt_text: str) -> dict:
    """비디오 LLM prompt_text에서 시스템 프롬프트와 한국어 원문을 분리한다.

    Returns:
        {
            "system_prompt": "You are a video prompt writer...",
            "korean_text": "이게 진짜 실화냐고...",
            "mood": "humor",
            "style_hint": "bright atmosphere...",
        }
    """
    result = {"system_prompt": "", "korean_text": "", "mood": "", "style_hint": ""}

    if not prompt_text:
        return result

    # T2V: "Korean source text: ..." (끝까지)
    m = re.search(r"Korean source text:\s*(.+)", prompt_text, re.DOTALL)
    if m:
        result["korean_text"] = m.group(1).strip()
        result["system_prompt"] = prompt_text[:m.start()].strip()
    else:
        # I2V: "Korean context: ..." ~ "Write the motion prompt:"
        m = re.search(r"Korean context:\s*(.+?)(?:\n\nWrite the motion prompt:)", prompt_text, re.DOTALL)
        if m:
            result["korean_text"] = m.group(1).strip()
            result["system_prompt"] = prompt_text[:m.start()].strip()
        else:
            result["system_prompt"] = prompt_text

    # Mood 추출
    m = re.search(r"Mood:\s*(\w+)", prompt_text)
    if m:
        result["mood"] = m.group(1)

    # Style hint 추출
    m = re.search(r"Visual style:\s*(.+?)(?:\n|$)", prompt_text)
    if m:
        result["style_hint"] = m.group(1).strip()

    return result


def _translate_video_prompt(log_id: int) -> str | None:
    """영어 비디오 프롬프트를 한글로 역번역한다 (Ollama 호출 + 캐싱)."""
    try:
        from ai_worker.script.client import call_ollama_raw

        with SessionLocal() as db:
            log = db.query(LLMLog).get(log_id)
            if not log or not log.raw_response:
                return None

            # 캐시 확인
            pr = log.parsed_result or {}
            if pr.get("korean_translation"):
                return pr["korean_translation"]

            prompt = (
                "다음 영어 비디오 프롬프트를 자연스러운 한국어로 번역하세요. "
                "비디오 촬영 용어는 한글로 옮기되, 카메라 앵글 등 전문용어는 "
                "원문을 병기하세요. 번역문만 출력하세요.\n\n"
                f"English: {log.raw_response}\n\n"
                "Korean:"
            )
            korean = call_ollama_raw(prompt, max_tokens=300, temperature=0.2)

            # 캐시 저장
            pr["korean_translation"] = korean.strip()
            log.parsed_result = pr
            # flag_modified 필요 (JSON 필드 변경 감지)
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(log, "parsed_result")
            db.commit()

            return korean.strip()
    except Exception as e:
        logger.warning("비디오 프롬프트 역번역 실패: %s", e)
        return None


def _render_video_tab(db, cutoff: datetime, filter_success: str, filter_post_id: int) -> None:
    """비디오 LLM 서브탭 전체 렌더링."""
    _render_stats(db, cutoff, _VIDEO_TYPES)
    st.divider()

    logs, posts_map, contents_map = _query_logs_flat(
        db, _VIDEO_TYPES, cutoff, filter_success, filter_post_id,
    )
    if not logs:
        st.info("조건에 맞는 비디오 LLM 이력이 없습니다.")
        return

    st.caption(f"{len(logs)}건")

    for log in logs:
        icon = "O" if log.success else "X"
        mode_label = _CALL_TYPE_LABELS.get(log.call_type, log.call_type)
        desc = _extract_scene_desc(log)
        pr = log.parsed_result or {}
        scene_idx = pr.get("scene_index")
        scene_num = f"#{scene_idx + 1}" if scene_idx is not None else "#?"
        hdr = (
            f"[{icon}] {_post_header(log.post_id, posts_map, contents_map)} | "
            f"{mode_label} {scene_num} | {desc} | "
            f"{log.duration_ms or 0}ms | {_relative_time(log.created_at)}"
        )

        with st.expander(hdr):
            if log.call_type == "video_prompt_simplify":
                # Simplified 프롬프트는 간단히 표시
                if log.error_message:
                    st.error(log.error_message)
                if log.raw_response:
                    st.markdown("**생성된 프롬프트**")
                    st.info(log.raw_response)
                with st.expander("프롬프트 원시 데이터"):
                    _copyable_code(log.prompt_text or "(없음)", key=f"vs_{log.id}")
            else:
                _render_video_detail(log)


def _render_video_detail(log) -> None:
    """비디오 LLM 상세 — 이중언어 뷰."""
    mode = "T2V" if log.call_type == "video_prompt_t2v" else "I2V"
    m_icon = "O" if log.success else "X"
    st.markdown(
        f"**[{m_icon}] {mode} 프롬프트** | "
        f"{log.duration_ms or 0}ms | {log.content_length}자"
    )
    if log.error_message:
        st.error(log.error_message)

    t_in, t_out, t_raw = st.tabs(["입력 (이중언어)", "출력 (이중언어)", "원시 데이터"])

    with t_in:
        _render_video_input_bilingual(log)

    with t_out:
        _render_video_output_bilingual(log)

    with t_raw:
        st.markdown("**LLM 입력**")
        _copyable_code(log.prompt_text or "(없음)", key=f"vim_{log.id}")
        st.markdown("**LLM 응답**")
        _copyable_code(log.raw_response or "(없음)", key=f"vir_{log.id}")


def _render_video_input_bilingual(log) -> None:
    """비디오 LLM 입력 — 한글 | 영어 2-컬럼."""
    sections = _parse_video_prompt_sections(log.prompt_text or "")
    pr = log.parsed_result or {}

    # 프롬프트 입력 (한글 원문 | 영어 프롬프트 내 포함 형태)
    korean_text = pr.get("korean_text", "") or sections.get("korean_text", "")
    mood = pr.get("mood", "") or sections.get("mood", "")
    style_hint = pr.get("style_hint", "") or sections.get("style_hint", "")

    st.markdown("##### 씬 대사 입력")
    col_ko, col_en = st.columns(2)
    with col_ko:
        st.markdown("**한글 (원본 대사)**")
        # 제목 접두사 분리
        clean_text = re.sub(r"^\[제목:\s*[^\]]*\]\s*", "", korean_text)
        st.text_area("한글 원문", value=clean_text or "(없음)", height=120,
                     disabled=True, key=f"viko_{log.id}", label_visibility="collapsed")
        if mood:
            st.markdown(f"**Mood:** `{mood}`")
        if style_hint:
            st.markdown(f"**스타일:** `{style_hint}`")

    with col_en:
        st.markdown("**영어 (프롬프트에 포함된 형태)**")
        # 프롬프트에서 Korean source text / Korean context 이후 부분 표시
        en_input = ""
        prompt = log.prompt_text or ""
        m = re.search(r"(Korean (?:source text|context):\s*.+)", prompt, re.DOTALL)
        if m:
            en_input = m.group(1)
        st.text_area("영어 입력", value=en_input or "(없음)", height=120,
                     disabled=True, key=f"vien_{log.id}", label_visibility="collapsed")

    # 워크플로우 (시스템 프롬프트)
    system_prompt = sections.get("system_prompt", "")
    if system_prompt:
        with st.expander("Workflow 지시사항"):
            st.code(system_prompt[:1500], language="text")


def _render_video_output_bilingual(log) -> None:
    """비디오 LLM 출력 — 한글 역번역 | 영어 원문 2-컬럼."""
    raw_response = log.raw_response or ""
    pr = log.parsed_result or {}

    if not raw_response:
        st.caption("출력 없음")
        return

    st.markdown("##### 비디오 프롬프트")
    col_ko, col_en = st.columns(2)

    with col_ko:
        st.markdown("**한글 (역번역)**")
        cached_translation = pr.get("korean_translation", "")
        if cached_translation:
            st.text_area("한글 번역", value=cached_translation, height=150,
                         disabled=True, key=f"votko_{log.id}", label_visibility="collapsed")
        else:
            st.caption("역번역 미완료")
            if st.button("역번역 실행", key=f"vtrans_{log.id}",
                         help="Ollama로 영어→한글 번역 (LLM 모델 로드 필요)"):
                with st.spinner("역번역 중..."):
                    result = _translate_video_prompt(log.id)
                if result:
                    st.text_area("한글 번역", value=result, height=150,
                                 disabled=True, key=f"votkr_{log.id}", label_visibility="collapsed")
                    st.success("역번역 완료 (캐시 저장됨)")
                else:
                    st.error("역번역 실패 — Ollama 접속 불가 또는 모델 미로드")

    with col_en:
        st.markdown("**영어 (LLM 원본 출력)**")
        st.text_area("영어 출력", value=raw_response, height=150,
                     disabled=True, key=f"voten_{log.id}", label_visibility="collapsed")


# ══════════════════════════════════════════════════════
# 메인 렌더 함수
# ══════════════════════════════════════════════════════

def render() -> None:
    """LLM 이력 탭 렌더링 — 3개 서브탭 구조."""

    _llm_hdr, _llm_ref = st.columns([5, 1])
    with _llm_hdr:
        st.header("LLM 호출 이력")
    with _llm_ref:
        if st.button("새로고침", key="llm_refresh_btn"):
            st.rerun()

    # ── 필터 컨트롤 (공통) ─────────────────────────────
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

    cutoff = datetime.now(timezone.utc) - timedelta(days=filter_days)

    # ── 3개 서브탭 ─────────────────────────────────────
    with SessionLocal() as db:
        # 각 탭 로그 수 미리 조회
        script_cnt = db.query(func.count(LLMLog.id)).filter(
            LLMLog.created_at >= cutoff, LLMLog.call_type.in_(_SCRIPT_TYPES),
        ).scalar() or 0
        scene_cnt = db.query(func.count(LLMLog.id)).filter(
            LLMLog.created_at >= cutoff, LLMLog.call_type.in_(_SCENE_TYPES),
        ).scalar() or 0
        video_cnt = db.query(func.count(LLMLog.id)).filter(
            LLMLog.created_at >= cutoff, LLMLog.call_type.in_(_VIDEO_TYPES),
        ).scalar() or 0

        tab_script, tab_scene, tab_video = st.tabs([
            f"대본 LLM ({script_cnt})",
            f"씬 LLM ({scene_cnt})",
            f"비디오 LLM ({video_cnt})",
        ])

        with tab_script:
            _render_script_tab(db, cutoff, filter_success, filter_post_id)

        with tab_scene:
            _render_scene_tab(db, cutoff, filter_success, filter_post_id)

        with tab_video:
            _render_video_tab(db, cutoff, filter_success, filter_post_id)
