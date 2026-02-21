"""LLM 호출 이력 (LLM Log) 탭."""

from datetime import datetime, timezone, timedelta

import streamlit as st
from sqlalchemy import func

from db.models import Post, LLMLog
from db.session import SessionLocal


def render() -> None:
    """LLM 이력 탭 렌더링."""

    _llm_hdr, _llm_ref = st.columns([5, 1])
    with _llm_hdr:
        st.header("🔬 LLM 호출 이력")
    with _llm_ref:
        if st.button("🔄 새로고침", key="llm_refresh_btn", width="stretch"):
            st.rerun()

    # 필터 컨트롤
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
        if filter_post_id > 0:
            _fq = _fq.filter(LLMLog.post_id == filter_post_id)

        _logs = _fq.order_by(LLMLog.created_at.desc()).limit(200).all()

        # 로그에 연결된 Post 일괄 조회 (헤더 표시용)
        _post_ids = {_l.post_id for _l in _logs if _l.post_id is not None}
        _posts_map: dict[int, Post] = {}
        if _post_ids:
            _posts_map = {
                p.id: p
                for p in _db.query(Post).filter(Post.id.in_(_post_ids)).all()
            }

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
            _is_editor = _log.call_type == "generate_script_editor"
            if _log.success:
                _icon = "🔵" if _is_editor else "✅"
            else:
                _icon = "❌"
            _post = _posts_map.get(_log.post_id) if _log.post_id else None
            _site = _post.site_code if _post else "-"
            _title = (_post.title[:30] + "…") if _post and len(_post.title) > 30 else (_post.title if _post else "-")
            _img_count = len(_post.images) if _post and isinstance(_post.images, list) else 0
            _hdr = (
                f"{_icon} #{_log.id} "
                f"{_site} | {_title} | 이미지 {_img_count}장"
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
