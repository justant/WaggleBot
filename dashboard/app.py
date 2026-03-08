"""WaggleBot 관리자 대시보드 — Streamlit 진입점.

Usage:
    streamlit run dashboard/app.py --server.port=8501

성능 주의:
    각 탭을 @st.fragment로 감싸서, 한 탭 내 위젯 상호작용이
    다른 6개 탭의 불필요한 재렌더링을 유발하지 않도록 한다.
    (st.rerun() 호출 시에만 전체 재렌더링 발생)
"""

import json
import logging
import sys
import time as _perf_time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (streamlit run dashboard/app.py 실행 시 config/ 모듈 인식)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 대시보드 로깅 레벨 설정 — ai_worker/dashboard 모듈의 INFO 이상 표시
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

import streamlit as st

from config.settings import load_pipeline_config, get_pipeline_defaults, OLLAMA_MODEL

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 설정 탭 session_state 초기화 (세션 최초 1회 — 파일에서 로드)
# ---------------------------------------------------------------------------

def _apply_cfg_to_session(cfg: dict[str, str]) -> None:
    """dict 값을 설정 위젯 session_state 키에 적용한다.

    위젯이 아직 렌더링되기 전(스크립트 상단)에서만 호출해야 한다.
    위젯 렌더 후 동일 키를 수정하면 StreamlitAPIException이 발생한다.
    """
    _d = get_pipeline_defaults()
    st.session_state["set_tts_engine"]             = cfg.get("tts_engine", _d["tts_engine"])
    st.session_state["set_tts_voice"]              = cfg.get("tts_voice",  _d["tts_voice"])
    st.session_state["set_llm_model"]              = cfg.get("llm_model",  _d["llm_model"])
    st.session_state["set_upload_platforms"]       = json.loads(cfg.get("upload_platforms", '["youtube"]'))
    st.session_state["set_upload_privacy"]         = cfg.get("upload_privacy", "unlisted")
    st.session_state["set_auto_upload"]            = cfg.get("auto_upload", "false") == "true"
    st.session_state["set_auto_approve"]           = cfg.get("auto_approve_enabled", "false") == "true"
    st.session_state["set_auto_approve_threshold"] = int(cfg.get("auto_approve_threshold", "80"))
    st.session_state["set_use_content_processor"]  = cfg.get("use_content_processor", "false") == "true"


# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="WaggleBot 관리자",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🤖 WaggleBot 관리자 대시보드")

# 기본값 복원 요청이 있으면 위젯 렌더 전에 처리 (위젯 렌더 후 key 수정 불가)
if st.session_state.pop("_settings_reset_pending", False):
    _apply_cfg_to_session(get_pipeline_defaults())

# 세션 최초 1회 — 파일에서 로드
if "settings_initialized" not in st.session_state:
    _apply_cfg_to_session(load_pipeline_config())
    st.session_state["settings_initialized"] = True

# ---------------------------------------------------------------------------
# 탭 구성
# ---------------------------------------------------------------------------

from dashboard.tabs import inbox, editor, progress, gallery, analytics, llm_log  # noqa: E402
from dashboard.tabs import settings as settings_tab  # noqa: E402

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
    ["📥 수신함", "✏️ 편집실", "⚙️ 진행현황", "🎬 갤러리", "📊 분석", "🔬 LLM 이력", "⚙️ 설정"]
)

if st.session_state.pop("_auto_queued", False):
    st.toast("✅ AI 워커 처리 대기열에 추가됨")
    st.components.v1.html("""<script>
    setTimeout(function() {
        var tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
        if (tabs && tabs.length > 2) tabs[2].click();
    }, 300);
    </script>""", height=0)

# ---------------------------------------------------------------------------
# 각 탭을 @st.fragment로 래핑 — 위젯 상호작용이 해당 탭만 재실행
# (st.rerun() 호출 시에는 기존과 동일하게 전체 페이지 재실행)
# ---------------------------------------------------------------------------

_PERF_LOG = log.isEnabledFor(logging.DEBUG)


def _timed_render(name: str, fn) -> None:
    """탭 렌더 함수를 실행하고 소요 시간을 로깅한다."""
    _t0 = _perf_time.perf_counter()
    fn()
    _dur = (_perf_time.perf_counter() - _t0) * 1000
    if _dur > 200:  # 200ms 이상만 WARNING 레벨
        log.warning("[PERF] %s render: %.0fms (SLOW)", name, _dur)
    elif _PERF_LOG:
        log.debug("[PERF] %s render: %.0fms", name, _dur)


@st.fragment
def _tab_inbox():
    _timed_render("inbox", inbox.render)


@st.fragment
def _tab_editor():
    _timed_render("editor", editor.render)


@st.fragment
def _tab_progress():
    _timed_render("progress", progress.render)


@st.fragment
def _tab_gallery():
    _timed_render("gallery", gallery.render)


@st.fragment
def _tab_analytics():
    _timed_render("analytics", analytics.render)


@st.fragment
def _tab_llm_log():
    _timed_render("llm_log", llm_log.render)


@st.fragment
def _tab_settings():
    _timed_render("settings", settings_tab.render)


with tab1:
    _tab_inbox()

with tab2:
    _tab_editor()

with tab3:
    _tab_progress()

with tab4:
    _tab_gallery()

with tab5:
    _tab_analytics()

with tab6:
    _tab_llm_log()

with tab7:
    _tab_settings()
