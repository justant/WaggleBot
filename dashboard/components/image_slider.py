"""이미지 슬라이더 컴포넌트."""

import json
import logging
from urllib.parse import urlparse

import requests
import streamlit as st

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 핫링크 방지 사이트별 Referer 매핑
_REFERER_MAP: dict[str, str] = {
    "dcinside.co.kr": "https://gall.dcinside.com/",
    "dcinside.com": "https://gall.dcinside.com/",
}

# DCInside 전용 세션 (쿠키 워밍업 포함)
_dc_session: requests.Session | None = None


def _get_referer(url: str) -> str:
    """이미지 URL의 도메인에 맞는 Referer를 반환한다."""
    hostname = urlparse(url).hostname or ""
    for domain, referer in _REFERER_MAP.items():
        if hostname.endswith(domain):
            return referer
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _is_dc_url(url: str) -> bool:
    """DCInside CDN URL 여부 확인."""
    hostname = urlparse(url).hostname or ""
    return any(hostname.endswith(d) for d in ("dcinside.com", "dcinside.co.kr"))


def _get_dc_session() -> requests.Session:
    """DCInside 이미지 다운로드용 세션 (쿠키 워밍업 포함).

    DCInside CDN은 쿠키 없이 요청하면 403/이미지 차단하는 경우가 있으므로
    메인 페이지에서 세션 쿠키를 먼저 획득한다.
    """
    global _dc_session
    if _dc_session is not None:
        return _dc_session
    _dc_session = requests.Session()
    _dc_session.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    try:
        _dc_session.get("https://www.dcinside.com/", timeout=10)
        log.debug(
            "DCInside 이미지 세션 워밍업 OK (cookies=%d)",
            len(_dc_session.cookies),
        )
    except Exception:
        log.debug("DCInside 이미지 세션 워밍업 실패 — 쿠키 없이 시도")
    return _dc_session


def _safe_rerun_fragment() -> None:
    """fragment rerun 컨텍스트에서만 scope='fragment' 사용, 아니면 전체 rerun."""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_image(url: str) -> bytes | None:
    """이미지를 캐시하여 반복 요청 방지 (5분 TTL).

    DCInside 이미지는 전용 세션(쿠키 + Referer + Sec-Fetch 헤더)을 사용하여
    핫링크 차단 및 봇 차단을 우회한다.
    """
    try:
        if _is_dc_url(url):
            sess = _get_dc_session()
            resp = sess.get(
                url,
                timeout=(5, 15),
                headers={
                    "Referer": "https://gall.dcinside.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Sec-Fetch-Dest": "image",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
        else:
            resp = requests.get(
                url,
                timeout=(5, 10),
                headers={
                    "Referer": _get_referer(url),
                    "User-Agent": _UA,
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
        resp.raise_for_status()
        data = resp.content
        # 200바이트 미만은 플레이스홀더 GIF일 가능성 높음 (1×1 GIF ≈ 43B)
        if len(data) < 200:
            log.warning("이미지 크기 의심 (%d bytes, 플레이스홀더?): %s", len(data), url)
            return None
        return data
    except Exception as e:
        log.warning("이미지 로드 실패: %s — %s", url, e)
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
                _safe_rerun_fragment()
        with nav_mid:
            st.caption(f"{cur + 1} / {len(imgs)}")
        with nav_r:
            if st.button("▶", key=f"img_next_{key_prefix}", disabled=(cur == len(imgs) - 1)):
                st.session_state[slide_key] = cur + 1
                _safe_rerun_fragment()

    img_data = _fetch_image(imgs[cur])
    if img_data:
        try:
            st.image(img_data, width=width)
        except Exception:
            st.caption(f"이미지 로드 실패: {imgs[cur]}")
    else:
        st.caption(f"이미지 로드 실패: {imgs[cur]}")
