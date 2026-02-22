"""이미지 슬라이더 컴포넌트."""

import json

import requests as _http
import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_image(url: str) -> bytes | None:
    """이미지를 캐시하여 반복 요청 방지 (5분 TTL)."""
    try:
        resp = _http.get(
            url, timeout=3,
            headers={"Referer": url, "User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


@st.fragment
def render_image_slider(images_raw: "str | list | None", key_prefix: str, width: int = 320) -> None:
    """이미지 URL 목록을 슬라이드로 렌더링한다.

    @st.fragment로 감싸서 이미지 ◀/▶ 네비게이션 시
    부모(editor 탭 전체)가 재실행되지 않도록 한다.

    - 서버에서 이미지를 프록시로 가져와 핫링크 차단 우회
    - 여러 장이면 ◀ / ▶ 버튼으로 슬라이드 이동
    """
    if not images_raw or images_raw == "[]":
        return
    try:
        imgs: list[str] = (
            json.loads(images_raw) if isinstance(images_raw, str) else list(images_raw)
        )
    except Exception:
        return
    if not imgs:
        return

    slide_key = f"slide_{key_prefix}"
    if slide_key not in st.session_state:
        st.session_state[slide_key] = 0
    cur = max(0, min(st.session_state[slide_key], len(imgs) - 1))

    if len(imgs) > 1:
        nav_l, nav_mid, nav_r = st.columns([1, 6, 1])
        with nav_l:
            if st.button("◀", key=f"img_prev_{key_prefix}", disabled=(cur == 0)):
                st.session_state[slide_key] = cur - 1
                st.rerun(scope="fragment")
        with nav_mid:
            st.caption(f"{cur + 1} / {len(imgs)}")
        with nav_r:
            if st.button("▶", key=f"img_next_{key_prefix}", disabled=(cur == len(imgs) - 1)):
                st.session_state[slide_key] = cur + 1
                st.rerun(scope="fragment")

    img_data = _fetch_image(imgs[cur])
    if img_data:
        try:
            st.image(img_data, width=width)
        except Exception:
            st.caption(f"이미지 로드 실패: {imgs[cur]}")
    else:
        st.caption(f"이미지 로드 실패: {imgs[cur]}")
