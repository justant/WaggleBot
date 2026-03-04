"""분석 (Analytics) 탭."""

import logging
import threading as _threading
from datetime import datetime, timezone, timedelta
from typing import Any as _Any

import streamlit as st
from sqlalchemy import func

from ai_worker.script.client import call_ollama_raw
from config.settings import load_pipeline_config, OLLAMA_MODEL
from db.models import Post, PostStatus, Content
from db.session import SessionLocal

from dashboard.components.status_utils import check_ollama_health

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 비동기 인사이트 작업 레지스트리 (period_days → task dict)
# ---------------------------------------------------------------------------
_insight_tasks: dict[int, dict[str, _Any]] = {}
_insight_lock = _threading.Lock()

# 피드백 반영 태스크 (단일 작업)
_feedback_task: dict[str, _Any] = {}
_feedback_lock = _threading.Lock()


def _submit_insight_task(
    period_days: int,
    total_collected: int,
    total_approved: int,
    total_uploaded: int,
    conversion_rate: float,
    ranked: list[dict],
    llm_model: str,
) -> bool:
    """AI 인사이트 생성을 백그라운드 스레드에 제출."""
    with _insight_lock:
        existing = _insight_tasks.get(period_days)
        if existing and existing["status"] == "running":
            return False
        _insight_tasks[period_days] = {"status": "running"}

    def _run() -> None:
        try:
            _data_summary = "\n".join(
                f"- {r['title'][:60]}: 조회수 {r['views']:,}, 좋아요 {r['likes']:,}"
                + (
                    f", 시청유지율 {r['analytics']['avg_watch_pct']:.1f}%"
                    if r["analytics"].get("avg_watch_pct")
                    else ""
                )
                for r in ranked[:10]
            )
            _prompt = f"""당신은 유튜브 쇼츠 채널 성과 분석 전문가입니다.
아래 최근 {period_days}일 업로드 영상 성과 데이터를 분석하고,
운영자에게 유용한 인사이트 3~5가지를 간결하게 한국어로 작성하세요.

## 성과 데이터
수집: {total_collected}건 → 승인: {total_approved}건 → 업로드: {total_uploaded}건 (전환율 {conversion_rate:.1f}%)
업로드 영상 목록:
{_data_summary}

## 인사이트 형식
- 어떤 주제/패턴이 잘 됐는지
- 개선이 필요한 부분
- 다음 {period_days}일 운영 전략 제안
각 항목은 "- " 로 시작하는 한 줄 문장으로 작성하세요."""

            _insight_text = call_ollama_raw(
                prompt=_prompt,
                model=llm_model,
                max_tokens=512,
                temperature=0.7,
            ).strip()
            with _insight_lock:
                _insight_tasks[period_days] = {"status": "done", "result": _insight_text}
        except Exception as _ex:
            with _insight_lock:
                _insight_tasks[period_days] = {"status": "error", "error": str(_ex)}

    _threading.Thread(target=_run, daemon=True).start()
    return True


def _submit_feedback_task(period_days: int, llm_model: str | None) -> bool:
    """구조화 인사이트 생성 및 피드백 반영을 백그라운드로 제출."""
    with _feedback_lock:
        if _feedback_task.get("status") == "running":
            return False
        _feedback_task.clear()
        _feedback_task["status"] = "running"

    def _run() -> None:
        try:
            from analytics.feedback import (
                load_feedback_config, generate_structured_insights,
                apply_feedback, build_performance_summary,
            )
            with SessionLocal() as _fb_s:
                _perf = build_performance_summary(_fb_s, days_back=period_days)
            if not _perf:
                with _feedback_lock:
                    _feedback_task.update({"status": "error", "error": "분석할 데이터 없음"})
                return
            _insights = generate_structured_insights(_perf, llm_model=llm_model)
            apply_feedback(_insights)
            with _feedback_lock:
                _feedback_task.update({"status": "done"})
        except Exception as _ex:
            with _feedback_lock:
                _feedback_task.update({"status": "error", "error": str(_ex)})

    _threading.Thread(target=_run, daemon=True).start()
    return True


def render() -> None:
    """분석 탭 렌더링."""

    st.header("📊 분석")

    # ---------------------------------------------------------------------------
    # 기간 선택
    # ---------------------------------------------------------------------------
    hdr_c1, hdr_c2 = st.columns([4, 1])
    with hdr_c1:
        period_days = st.selectbox(
            "분석 기간",
            [7, 14, 30],
            format_func=lambda d: f"최근 {d}일",
        )
    with hdr_c2:
        if st.button("🔄 새로고침", key="analytics_refresh", width="stretch"):
            st.rerun()

    since_dt = datetime.now(timezone.utc) - timedelta(days=period_days)

    # ---------------------------------------------------------------------------
    # DB 집계
    # ---------------------------------------------------------------------------
    with SessionLocal() as _db:
        _total_collected = (
            _db.query(func.count(Post.id))
            .filter(Post.created_at >= since_dt)
            .scalar() or 0
        )
        _total_approved = (
            _db.query(func.count(Post.id))
            .filter(
                Post.created_at >= since_dt,
                Post.status.in_([
                    PostStatus.APPROVED, PostStatus.PROCESSING,
                    PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED,
                    PostStatus.UPLOADED,
                ]),
            )
            .scalar() or 0
        )
        _total_rendered = (
            _db.query(func.count(Post.id))
            .filter(
                Post.created_at >= since_dt,
                Post.status.in_([PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED]),
            )
            .scalar() or 0
        )
        _total_uploaded = (
            _db.query(func.count(Post.id))
            .filter(Post.created_at >= since_dt, Post.status == PostStatus.UPLOADED)
            .scalar() or 0
        )
        # 업로드된 컨텐츠 목록 (analytics 데이터 포함)
        _uploaded_contents: list[tuple[Post, Content]] = (
            _db.query(Post, Content)
            .join(Content, Content.post_id == Post.id)
            .filter(Post.status == PostStatus.UPLOADED)
            .order_by(Post.updated_at.desc())
            .all()
        )

    _conversion_rate = (_total_uploaded / _total_collected * 100) if _total_collected else 0.0

    # ---------------------------------------------------------------------------
    # 📈 주간 생산성
    # ---------------------------------------------------------------------------
    st.subheader("📈 파이프라인 생산성")
    with st.container(border=True):
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("수집", f"{_total_collected:,}건")
        m2.metric("승인", f"{_total_approved:,}건")
        m3.metric("렌더링", f"{_total_rendered:,}건")
        m4.metric("업로드", f"{_total_uploaded:,}건")
        m5.metric("전환율", f"{_conversion_rate:.1f}%")

        # 퍼널 프로그레스바
        if _total_collected:
            st.markdown("**수집 → 업로드 전환 퍼널**")
            stages = [
                ("수집", _total_collected, "#4e8cff"),
                ("승인", _total_approved, "#48bb78"),
                ("렌더링", _total_rendered, "#ed8936"),
                ("업로드", _total_uploaded, "#e53e3e"),
            ]
            for label, count, color in stages:
                pct = count / _total_collected if _total_collected else 0
                st.markdown(
                    f"""<div style="margin:4px 0">
<span style="display:inline-block;width:60px;font-size:0.8rem">{label}</span>
<span style="display:inline-block;height:18px;width:{int(pct*400)}px;
background:{color};border-radius:3px;vertical-align:middle"></span>
<span style="margin-left:8px;font-size:0.85rem">{count:,}건 ({pct*100:.1f}%)</span>
</div>""",
                    unsafe_allow_html=True,
                )

    # ---------------------------------------------------------------------------
    # 🏆 Top 5 영상
    # ---------------------------------------------------------------------------
    st.subheader("🏆 Top 5 영상 (조회수 기준)")

    # upload_meta 또는 post.stats 에서 조회수 수집
    _ranked: list[dict] = []
    for _post, _cnt in _uploaded_contents:
        _meta = _cnt.upload_meta or {}
        # YouTube Analytics에서 수집된 최신 analytics 우선, 없으면 post.stats
        _yt = _meta.get("youtube", {})
        _analytics = _yt.get("analytics", {})
        _views = _analytics.get("views") or (_post.stats or {}).get("views", 0)
        _likes = _analytics.get("likes") or (_post.stats or {}).get("likes", 0)
        _yt_url = _yt.get("url", "")
        _ranked.append({
            "title": _post.title,
            "views": int(_views),
            "likes": int(_likes),
            "url": _yt_url,
            "post_id": _post.id,
            "analytics": _analytics,
        })

    _ranked.sort(key=lambda x: x["views"], reverse=True)

    if _ranked:
        with st.container(border=True):
            for rank, item in enumerate(_ranked[:5], 1):
                rc1, rc2, rc3 = st.columns([6, 2, 2])
                with rc1:
                    _title_str = item["title"][:55] + "..." if len(item["title"]) > 55 else item["title"]
                    if item["url"]:
                        st.markdown(f"**{rank}.** [{_title_str}]({item['url']})")
                    else:
                        st.markdown(f"**{rank}.** {_title_str}")
                with rc2:
                    st.markdown(f"👁️ **{item['views']:,}**회")
                with rc3:
                    st.markdown(f"👍 {item['likes']:,}")
                if item["analytics"].get("avg_watch_pct"):
                    st.caption(
                        f"   평균 시청률 {item['analytics']['avg_watch_pct']:.1f}% · "
                        f"수집일: {item['analytics'].get('collected_at', '?')[:10]}"
                    )
    else:
        st.info("업로드된 영상이 없습니다.")

    # ---------------------------------------------------------------------------
    # 📉 성과 분석
    # ---------------------------------------------------------------------------
    st.subheader("📉 성과 분석")
    with st.container(border=True):
        if _ranked:
            _all_views = [r["views"] for r in _ranked]
            _all_likes = [r["likes"] for r in _ranked]
            _analytics_items = [r["analytics"] for r in _ranked if r["analytics"]]

            avg_views = sum(_all_views) / len(_all_views) if _all_views else 0
            avg_likes = sum(_all_likes) / len(_all_likes) if _all_likes else 0
            avg_watch = (
                sum(a["avg_watch_pct"] for a in _analytics_items if "avg_watch_pct" in a)
                / len([a for a in _analytics_items if "avg_watch_pct" in a])
                if any("avg_watch_pct" in a for a in _analytics_items) else None
            )
            sub_conv = (
                sum(a.get("subscriber_gained", 0) for a in _analytics_items)
            )

            pa1, pa2, pa3, pa4 = st.columns(4)
            pa1.metric("평균 조회수", f"{avg_views:,.0f}회")
            pa2.metric("평균 좋아요", f"{avg_likes:,.0f}")
            pa3.metric(
                "평균 시청 유지율",
                f"{avg_watch:.1f}%" if avg_watch is not None else "데이터 없음"
            )
            pa4.metric("구독 전환 합계", f"{sub_conv:,}명")
        else:
            st.caption("업로드 후 YouTube Analytics 수집 시 성과 지표가 표시됩니다.")

        # YouTube Analytics 수집 버튼
        st.divider()
        st.markdown("**YouTube Analytics 수동 수집**")
        st.caption("업로드된 영상의 조회수·좋아요·시청 유지율을 YouTube Analytics API에서 가져옵니다.")
        if st.button("📡 Analytics 수집", key="fetch_analytics", width="content"):
            _fetched, _errors = 0, 0
            with st.spinner("YouTube Analytics 수집 중..."):
                for _post, _cnt in _uploaded_contents:
                    _meta = dict(_cnt.upload_meta or {})
                    _yt = _meta.get("youtube", {})
                    _video_id = _yt.get("video_id")
                    if not _video_id:
                        continue
                    try:
                        from uploaders.youtube import YouTubeUploader
                        _uploader = YouTubeUploader()
                        _stats = _uploader.fetch_analytics(_video_id)
                        if _stats:
                            _yt["analytics"] = {
                                **_stats,
                                "collected_at": datetime.now(timezone.utc).isoformat(),
                            }
                            _meta["youtube"] = _yt
                            with SessionLocal() as _s:
                                _c = _s.query(Content).filter_by(post_id=_post.id).first()
                                if _c:
                                    _c.upload_meta = _meta
                                    _s.commit()
                            _fetched += 1
                    except Exception as _ex:
                        log.warning("Analytics 수집 실패 post_id=%d: %s", _post.id, _ex)
                        _errors += 1
            if _fetched:
                st.success(f"✅ {_fetched}건 수집 완료" + (f" ({_errors}건 실패)" if _errors else ""))
                st.rerun()
            else:
                st.warning("수집된 데이터가 없습니다. YouTube 인증 정보를 확인하세요.")

    # ---------------------------------------------------------------------------
    # 🎯 AI 인사이트
    # ---------------------------------------------------------------------------
    st.subheader("🎯 AI 인사이트")

    _insight_key = f"analytics_insight_{period_days}"
    with st.container(border=True):
        # 완료된 task가 있으면 session_state에 저장 후 정리
        _itask = _insight_tasks.get(period_days)
        if _itask:
            if _itask["status"] == "done":
                st.session_state[_insight_key] = _itask["result"]
                with _insight_lock:
                    _insight_tasks.pop(period_days, None)
            elif _itask["status"] == "error":
                _ie = _itask.get("error", "알 수 없는 오류")
                _is_timeout = "timeout" in _ie.lower() or "timed out" in _ie.lower()
                if _is_timeout:
                    st.warning(
                        f"⏱️ 인사이트 생성 시간 초과: {_ie}\n\n"
                        "LLM 서버 부하가 높을 수 있습니다. 잠시 후 다시 시도하세요."
                    )
                else:
                    st.error(
                        f"❌ 인사이트 생성 실패: {_ie}\n\n"
                        "설정 탭에서 Ollama 연결 상태를 확인하세요."
                    )
                with _insight_lock:
                    _insight_tasks.pop(period_days, None)

        _saved_insight = st.session_state.get(_insight_key)
        _itask_running = _insight_tasks.get(period_days, {}).get("status") == "running"

        if _itask_running:
            @st.fragment(run_every="10s")
            def _insight_poller() -> None:
                _t = _insight_tasks.get(period_days)
                if _t and _t["status"] in ("done", "error"):
                    st.rerun()  # 완료 시 전체 재렌더링
                else:
                    st.info("🤖 LLM 인사이트 생성 중... (자동 갱신)")
            _insight_poller()

        elif _saved_insight:
            st.markdown(_saved_insight)
            if st.button("✨ 인사이트 재생성", key="gen_insight", width="content"):
                if not _ranked:
                    st.warning("업로드된 영상 데이터가 없습니다.")
                elif not check_ollama_health():
                    st.error("❌ LLM 서버에 연결할 수 없습니다.")
                else:
                    st.session_state.pop(_insight_key, None)
                    _submit_insight_task(
                        period_days, _total_collected, _total_approved,
                        _total_uploaded, _conversion_rate, _ranked,
                        load_pipeline_config().get("llm_model", OLLAMA_MODEL),
                    )
                    st.rerun()

        else:
            if st.button("✨ 인사이트 생성", key="gen_insight", width="content", type="primary"):
                if not _ranked:
                    st.warning("업로드된 영상 데이터가 없습니다.")
                elif not check_ollama_health():
                    st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
                else:
                    _submit_insight_task(
                        period_days, _total_collected, _total_approved,
                        _total_uploaded, _conversion_rate, _ranked,
                        load_pipeline_config().get("llm_model", OLLAMA_MODEL),
                    )
                    st.rerun()
            st.caption("'인사이트 생성' 버튼을 눌러 LLM 분석을 시작하세요.")

    # ---------------------------------------------------------------------------
    # 🎯 피드백 파이프라인 반영
    # ---------------------------------------------------------------------------
    st.subheader("🎯 피드백 파이프라인 반영")

    with st.container(border=True):
        from analytics.feedback import load_feedback_config

        _fb_cfg = load_feedback_config()
        _fb_updated = _fb_cfg.get("updated_at")
        if _fb_updated:
            st.caption(f"마지막 반영: {_fb_updated[:19].replace('T', ' ')} UTC")

        # 피드백 태스크 완료 처리
        _ftask = dict(_feedback_task)
        if _ftask.get("status") == "done":
            st.success("✅ 피드백이 파이프라인에 반영되었습니다.")
            with _feedback_lock:
                _feedback_task.clear()
            st.rerun()
        elif _ftask.get("status") == "error":
            _fb_err = _ftask.get("error", "알 수 없는 오류")
            st.error(
                f"❌ 피드백 반영 실패: {_fb_err}\n\n"
                "설정 탭에서 Ollama 연결 상태를 확인한 후 다시 시도하세요."
            )
            with _feedback_lock:
                _feedback_task.clear()

        _col_fb1, _col_fb2 = st.columns([1, 1])
        with _col_fb1:
            _fb_running = _feedback_task.get("status") == "running"
            if _fb_running:
                st.button(
                    "🔄 분석 중...",
                    key="apply_feedback_btn",
                    width="stretch",
                    disabled=True,
                )
                @st.fragment(run_every="5s")
                def _fb_poller() -> None:
                    if _feedback_task.get("status") in ("done", "error"):
                        st.rerun()
                    else:
                        st.caption("LLM 인사이트 생성 중... (자동 감지)")
                _fb_poller()
            elif st.button(
                "🔄 구조화 인사이트 생성 후 반영",
                key="apply_feedback_btn",
                width="stretch",
                type="primary",
                help="LLM이 성과 데이터를 분석해 대본 프롬프트·mood 가중치를 자동 업데이트합니다.",
            ):
                if not check_ollama_health():
                    st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
                else:
                    _submit_feedback_task(
                        period_days,
                        load_pipeline_config().get("llm_model"),
                    )
                    st.rerun()

        with _col_fb2:
            if st.button(
                "🗑️ 피드백 초기화",
                key="reset_feedback_btn",
                width="stretch",
                help="feedback_config.json을 기본값으로 초기화합니다.",
            ):
                from config.settings import FEEDBACK_CONFIG_PATH
                FEEDBACK_CONFIG_PATH.unlink(missing_ok=True)
                st.success("✅ 피드백 설정이 초기화되었습니다.")
                st.rerun()

        # 현재 피드백 설정 표시
        _extra = _fb_cfg.get("extra_instructions", "")
        _weights = _fb_cfg.get("mood_weights", {})
        if _extra:
            st.info(f"**현재 대본 지시사항:** {_extra[:200]}")
        if any(v != 1.0 for v in _weights.values()):
            _w_lines = " | ".join(f"{k}: ×{v:.1f}" for k, v in _weights.items() if v != 1.0)
            st.caption(f"Mood 가중치 조정: {_w_lines}")

    # ---------------------------------------------------------------------------
    # 🧪 A/B 테스트
    # ---------------------------------------------------------------------------
    st.subheader("🧪 A/B 테스트")

    from analytics.ab_test import (
        list_tests, create_test, cancel_test,
        evaluate_group, apply_winner, VARIANT_PRESETS,
    )

    _ab_tests = list_tests()
    _active_tests  = [t for t in _ab_tests if t.status == "active"]
    _done_tests    = [t for t in _ab_tests if t.status == "completed"]

    # ── 진행 중인 테스트 ──────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**진행 중인 테스트**")
        if not _active_tests:
            st.caption("활성 A/B 테스트 없음")
        else:
            for _t in _active_tests:
                _tc1, _tc2, _tc3 = st.columns([4, 2, 2])
                with _tc1:
                    st.markdown(
                        f"🟢 **{_t.name}**  \n"
                        f"`{_t.group_id}` · "
                        f"A: {_t.config_a.get('label', _t.config_a.get('preset_key','?'))} / "
                        f"B: {_t.config_b.get('label', _t.config_b.get('preset_key','?'))}"
                    )
                with _tc2:
                    if st.button("📊 결과 평가", key=f"eval_{_t.group_id}", width="stretch"):
                        with SessionLocal() as _es:
                            _w = evaluate_group(_t.group_id, _es)
                        if _w:
                            st.success(f"승자: Variant {_w}")
                        else:
                            st.warning("데이터 부족 (최소 3건/변형 필요)")
                        st.rerun()
                with _tc3:
                    if st.button("❌ 취소", key=f"cancel_{_t.group_id}", width="stretch"):
                        cancel_test(_t.group_id)
                        st.rerun()

    # ── 완료된 테스트 ──────────────────────────────────────────────────────────
    if _done_tests:
        with st.container(border=True):
            st.markdown("**완료된 테스트**")
            for _t in _done_tests:
                _dc1, _dc2 = st.columns([5, 2])
                with _dc1:
                    _a_avg = _t.stats.get("A", {}).get("avg_views", 0)
                    _b_avg = _t.stats.get("B", {}).get("avg_views", 0)
                    _a_n   = _t.stats.get("A", {}).get("posts", 0)
                    _b_n   = _t.stats.get("B", {}).get("posts", 0)
                    _winner_badge = f"🏆 승자: {_t.winner}" if _t.winner else "판정 없음"
                    st.markdown(
                        f"✅ **{_t.name}**  \n"
                        f"A: {_a_avg:,.0f}회/{_a_n}건 | B: {_b_avg:,.0f}회/{_b_n}건  \n"
                        f"{_winner_badge}"
                        + (" ✔ 적용됨" if _t.winner_applied else "")
                    )
                with _dc2:
                    if _t.winner and not _t.winner_applied:
                        if st.button(
                            f"✨ 승자({_t.winner}) 반영",
                            key=f"apply_winner_{_t.group_id}",
                            width="stretch",
                            type="primary",
                        ):
                            if apply_winner(_t.group_id):
                                st.success(f"Variant {_t.winner} 설정이 파이프라인에 반영되었습니다.")
                            else:
                                st.error("반영 실패")
                            st.rerun()

    # ── 새 테스트 생성 ──────────────────────────────────────────────────────────
    with st.expander("➕ 새 A/B 테스트 생성", expanded=False):
        _preset_options = list(VARIANT_PRESETS.keys())
        _preset_labels  = {k: v["label"] for k, v in VARIANT_PRESETS.items()}

        _new_name = st.text_input("테스트 이름", placeholder="예: hook 스타일 테스트 2026-02")
        _col_a, _col_b = st.columns(2)
        with _col_a:
            _preset_a = st.selectbox(
                "Variant A",
                _preset_options,
                format_func=lambda k: f"{k} — {_preset_labels[k]}",
                key="ab_preset_a",
            )
        with _col_b:
            _preset_b = st.selectbox(
                "Variant B",
                _preset_options,
                index=1,
                format_func=lambda k: f"{k} — {_preset_labels[k]}",
                key="ab_preset_b",
            )

        if _preset_a == _preset_b:
            st.warning("Variant A와 B가 동일합니다. 다른 프리셋을 선택하세요.")
        elif st.button("테스트 시작", key="create_ab_test", type="primary", width="content"):
            if not _new_name.strip():
                st.error("테스트 이름을 입력하세요.")
            else:
                _new_test = create_test(_new_name.strip(), _preset_a, _preset_b)
                st.success(
                    f"✅ A/B 테스트 생성 완료! (group_id: `{_new_test.group_id}`)  \n"
                    f"이후 APPROVED 포스트는 자동으로 A/B 변형이 배정됩니다."
                )
                st.rerun()
