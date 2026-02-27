"""설정 (Settings) 탭."""

import json
import logging
import shutil

import pandas as pd
import requests as _http
import streamlit as st

from config.settings import (
    TTS_VOICES, MEDIA_DIR, PLATFORM_CREDENTIAL_FIELDS,
    load_pipeline_config, save_pipeline_config, get_pipeline_defaults,
    load_credentials_config, save_credentials_config, OLLAMA_MODEL,
)

from dashboard.components.status_utils import check_ollama_health
from dashboard.components.style_presets import load_style_presets, save_style_presets

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 탭 전용 헬퍼
# ---------------------------------------------------------------------------

def _write_youtube_token(token_json_str: str) -> str | None:
    """credentials.json의 token_json을 youtube_token.json 파일로 동기화.

    Returns:
        None on success, error message string on failure.
    """
    from config.settings import _PROJECT_ROOT
    token_path = _PROJECT_ROOT / "config" / "youtube_token.json"
    try:
        json.loads(token_json_str)  # JSON 유효성 검사
        token_path.write_text(token_json_str, encoding="utf-8")
        log.info("youtube_token.json 갱신 완료")
        return None
    except json.JSONDecodeError as e:
        return f"JSON 파싱 오류 (위치: {e.lineno}줄 {e.colno}열): {e.msg}"


def _write_tiktok_token(creds: dict) -> str | None:
    """credentials.json의 TikTok 필드를 tiktok_token.json 파일로 동기화.

    Returns:
        None on success, error message string on failure.
    """
    from config.settings import _PROJECT_ROOT
    token_path = _PROJECT_ROOT / "config" / "tiktok_token.json"
    try:
        # client_key/secret + access_token → tiktok_token.json
        token_data = {
            "client_key": creds.get("client_key", ""),
            "client_secret": creds.get("client_secret", ""),
            "access_token": creds.get("access_token", ""),
        }
        token_path.write_text(
            json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("tiktok_token.json 갱신 완료")
        return None
    except Exception as e:
        return f"TikTok 토큰 동기화 오류: {e}"


# ---------------------------------------------------------------------------
# 탭 렌더
# ---------------------------------------------------------------------------

def render() -> None:
    """설정 탭 렌더링."""

    _set_hdr, _set_ref = st.columns([5, 1])
    with _set_hdr:
        st.header("⚙️ 파이프라인 설정")
    with _set_ref:
        if st.button("🔄 새로고침", key="settings_refresh_btn", width="stretch"):
            st.rerun()

    # TTS 설정
    st.subheader("🎙️ TTS 설정")

    engine_list = list(TTS_VOICES.keys())
    _stored_engine = st.session_state.get("set_tts_engine", engine_list[0])
    engine_idx = engine_list.index(_stored_engine) if _stored_engine in engine_list else 0
    selected_engine = st.selectbox("TTS 엔진", engine_list, index=engine_idx, key="set_tts_engine")

    voices = TTS_VOICES[selected_engine]
    voice_ids = [v["id"] for v in voices]
    voice_labels = [f'{v["name"]} ({v["id"]})' for v in voices]
    _stored_voice = st.session_state.get("set_tts_voice", voice_ids[0] if voice_ids else "")
    voice_idx = voice_ids.index(_stored_voice) if _stored_voice in voice_ids else 0
    selected_voice_label = st.selectbox("TTS 목소리", voice_labels, index=voice_idx, key="set_tts_voice_label")
    selected_voice = voice_ids[voice_labels.index(selected_voice_label)] if selected_voice_label in voice_labels else voice_ids[0]

    # 댓글 낭독자 설정
    st.subheader("💬 댓글 낭독자 설정")
    st.caption("댓글을 읽어주는 씬에서 랜덤으로 선택될 목소리입니다. 최대 5명까지 설정 가능합니다.")

    # 현재 설정 로드
    _stored_comment_voices_raw = load_pipeline_config().get("comment_voices", "[]")
    try:
        import json as _j
        _stored_comment_voices: list[str] = _j.loads(_stored_comment_voices_raw)
    except Exception:
        _stored_comment_voices = []

    # "사용 안 함" + 현재 엔진의 목소리 목록
    _comment_voice_options = ["사용 안 함"] + voice_ids
    _comment_voice_labels = ["사용 안 함"] + voice_labels

    _comment_voice_cols = st.columns(5)
    _selected_comment_voices = []
    for _ci in range(5):
        with _comment_voice_cols[_ci]:
            _cv_stored = _stored_comment_voices[_ci] if _ci < len(_stored_comment_voices) else None
            _cv_idx = voice_ids.index(_cv_stored) + 1 if (_cv_stored and _cv_stored in voice_ids) else 0
            _cv_selected = st.selectbox(
                f"낭독자 {_ci + 1}",
                _comment_voice_labels,
                index=_cv_idx,
                key=f"set_comment_voice_{_ci + 1}",
            )
            if _cv_selected != "사용 안 함":
                _cv_id = voice_ids[_comment_voice_labels.index(_cv_selected) - 1]
                _selected_comment_voices.append(_cv_id)

    st.divider()

    # 스타일 프리셋 관리
    st.subheader("✍️ 스타일 프리셋 관리")
    st.caption("편집실의 '스타일 프리셋' 드롭다운에 표시되는 항목을 조회·수정·추가·삭제할 수 있습니다.")
    _cur_presets = load_style_presets()
    _presets_df = pd.DataFrame(_cur_presets, columns=["name", "prompt"])
    _edited_presets_df = st.data_editor(
        _presets_df,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "name":   st.column_config.TextColumn("프리셋 이름", width="medium", max_chars=40),
            "prompt": st.column_config.TextColumn("지시사항 (비워두면 기본 스타일)", width="large", max_chars=300),
        },
        key="set_style_presets_editor",
        height=220,
    )
    if st.button("💾 프리셋 저장", key="save_presets_btn", width="content"):
        _new_presets = [
            {"name": str(r.get("name", "")).strip(), "prompt": str(r.get("prompt", "") or "")}
            for _, r in _edited_presets_df.iterrows()
            if str(r.get("name", "")).strip()
        ]
        if _new_presets:
            save_style_presets(_new_presets)
            st.success("✅ 스타일 프리셋이 저장되었습니다.")
        else:
            st.error("프리셋 이름이 비어 있습니다. 이름을 입력하세요.")

    st.divider()

    # LLM 설정
    st.subheader("🧠 LLM 설정")
    llm_model = st.text_input("LLM 모델 (Ollama)", key="set_llm_model")
    if st.button("🔍 연결 확인", key="check_ollama", width="content"):
        from config.settings import get_ollama_host
        try:
            _r = _http.get(f"{get_ollama_host()}/api/tags", timeout=5)
            _r.raise_for_status()
            _models = [m["name"] for m in _r.json().get("models", [])]
            if llm_model in _models:
                st.success(f"✅ Ollama 연결 정상 — `{llm_model}` 모델 사용 가능")
            else:
                st.warning(
                    f"⚠️ Ollama 연결 정상, 모델 `{llm_model}` 미발견.\n"
                    f"사용 가능: {', '.join(_models[:10])}"
                )
        except Exception as _e:
            st.error(f"❌ Ollama 서버 연결 실패: {_e}")

    st.divider()

    # 업로드 설정
    st.subheader("📤 업로드 설정")

    available_platforms = ["youtube", "tiktok"]
    selected_platforms = st.multiselect(
        "업로드 플랫폼",
        available_platforms,
        key="set_upload_platforms",
    )

    privacy_options = ["unlisted", "private", "public"]
    _stored_privacy = st.session_state.get("set_upload_privacy", "unlisted")
    privacy_idx = privacy_options.index(_stored_privacy) if _stored_privacy in privacy_options else 0
    selected_privacy = st.selectbox("공개 설정", privacy_options, index=privacy_idx, key="set_upload_privacy")

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
                    if st.button(btn_label, key=f"edit_btn_{platform}", width="stretch"):
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
                    if st.button("💾 저장", key=f"save_{platform}", type="primary", width="stretch"):
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
                                _token_err = _write_youtube_token(merged["token_json"])
                                if _token_err:
                                    st.error(f"token_json 오류: {_token_err}")
                                    st.stop()

                            # TikTok: credentials → tiktok_token.json 동기화
                            if platform == "tiktok":
                                _tk_err = _write_tiktok_token(merged)
                                if _tk_err:
                                    st.error(_tk_err)
                                    st.stop()

                            st.session_state[edit_key] = False
                            st.success(f"{platform.upper()} 인증 정보가 저장되었습니다.")
                            st.rerun()

                with cancel_col:
                    if st.button("취소", key=f"cancel_{platform}", width="stretch"):
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

    # 자동 업로드 설정
    st.subheader("📤 자동 업로드")
    st.caption("RENDERED 상태 영상을 AI 워커가 자동으로 업로드합니다. 비활성화 시 갤러리의 '업로드' 버튼으로만 업로드합니다.")

    auto_upload_on = st.checkbox(
        "자동 업로드 활성화",
        key="set_auto_upload",
        help="활성화 시 고화질 렌더링 완료 즉시 자동으로 플랫폼에 업로드됩니다.",
    )

    st.divider()

    # 자동 승인 설정
    st.subheader("🤖 자동 승인")
    st.caption("점수 임계값 이상의 게시글을 수신함 진입 즉시 자동으로 승인합니다.")

    auto_approve_on = st.checkbox(
        "자동 승인 활성화",
        key="set_auto_approve",
        help="활성화 시 수신함 로드마다 임계값 이상 게시글이 자동 승인됩니다.",
    )
    if auto_approve_on:
        st.info(
            "ℹ️ 자동 승인은 수신함 탭 로드 시에만 실행됩니다. "
            "백그라운드 자동 승인이 필요하면 AI 워커에 자동 승인 로직 추가를 고려하세요."
        )
    auto_approve_thresh = st.number_input(
        "자동 승인 임계값 (Engagement Score)",
        min_value=0,
        max_value=100,
        step=5,
        key="set_auto_approve_threshold",
        help="이 점수 이상인 게시글이 자동 승인됩니다. 80점 권장.",
    )

    st.divider()

    # LLM 파이프라인 설정
    st.subheader("🔬 LLM 파이프라인")
    st.caption(
        "활성화 시 resource_analyzer → llm_chunker → text_validator → scene_director "
        "5-Phase 파이프라인으로 대본을 생성합니다."
    )
    use_content_processor = st.checkbox(
        "content_processor 사용 (5-Phase 파이프라인)",
        key="set_use_content_processor",
        help="비활성화 시 기존 generate_script() 경로(레거시)를 사용합니다.",
    )

    st.divider()

    # 저장 / 기본값 복원 버튼
    # TTS 엔진 변경 시 댓글 낭독자 음성 리셋 경고
    _prev_engine = load_pipeline_config().get("tts_engine")
    if _prev_engine and _prev_engine != selected_engine and _selected_comment_voices:
        st.warning(
            f"⚠️ TTS 엔진이 `{_prev_engine}` → `{selected_engine}`(으)로 변경되었습니다. "
            "댓글 낭독자 음성이 새 엔진의 목소리로 재설정됩니다."
        )

    _save_col, _reset_col = st.columns(2)
    with _save_col:
        if st.button("💾 설정 저장", type="primary", key="save_settings_btn", width="stretch"):
            _new_cfg = {
                "tts_engine": selected_engine,
                "tts_voice": selected_voice,
                "llm_model": st.session_state.get("set_llm_model", OLLAMA_MODEL),
                "upload_platforms": json.dumps(st.session_state.get("set_upload_platforms", ["youtube"])),
                "upload_privacy": st.session_state.get("set_upload_privacy", "unlisted"),
                "auto_upload": "true" if st.session_state.get("set_auto_upload") else "false",
                "auto_approve_enabled": "true" if st.session_state.get("set_auto_approve") else "false",
                "auto_approve_threshold": str(st.session_state.get("set_auto_approve_threshold", 80)),
                "use_content_processor": "true" if st.session_state.get("set_use_content_processor") else "false",
                "comment_voices": json.dumps(_selected_comment_voices),
            }
            # tts_voice는 label selectbox에서 추출한 값을 session_state에 동기화
            st.session_state["set_tts_voice"] = selected_voice
            save_pipeline_config(_new_cfg)
            st.success("✅ 설정이 저장되었습니다.")
    with _reset_col:
        if st.button("↩️ 기본값 복원", key="restore_defaults_btn", width="stretch"):
            save_pipeline_config(get_pipeline_defaults())
            st.session_state["_settings_reset_pending"] = True
            st.rerun()

    st.divider()

    # 시스템 정리
    st.subheader("🧹 시스템 정리")
    _tmp_dir = MEDIA_DIR / "tmp"
    if _tmp_dir.exists():
        _preview_files = list(_tmp_dir.glob("preview_*.mp3"))
        _cache_root = _tmp_dir / "tts_scene_cache"
        _cache_dirs = list(_cache_root.glob("*")) if _cache_root.exists() else []
        st.caption(f"TTS 미리듣기 파일: {len(_preview_files)}개 | TTS 씬 캐시: {len(_cache_dirs)}개")
        if st.button("🗑️ 임시 파일 정리", key="cleanup_tmp"):
            for _f in _preview_files:
                _f.unlink(missing_ok=True)
            for _d in _cache_dirs:
                shutil.rmtree(_d, ignore_errors=True)
            st.success(f"✅ {len(_preview_files)}개 파일 + {len(_cache_dirs)}개 캐시 삭제 완료")
            st.rerun()
    else:
        st.caption("임시 파일 디렉토리가 없습니다.")

    st.divider()

    # 현재 설정 표시
    with st.expander("🔍 현재 저장된 설정 보기"):
        st.json(load_pipeline_config())
