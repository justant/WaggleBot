"""WaggleBot 관리자 대시보드 — Streamlit 진입점.

Usage:
    streamlit run dashboard/app.py --server.port=8501
"""

import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (streamlit run dashboard/app.py 실행 시 config/ 모듈 인식)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

with tab1:
    inbox.render()

with tab2:
    editor.render()

with tab3:
    progress.render()

with tab4:
    gallery.render()

with tab5:
    analytics.render()

with tab6:
    llm_log.render()

with tab7:
    settings_tab.render()
