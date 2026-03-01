"""scene_director의 _download_and_cache_image DC 이미지 다운로드 테스트.

이 테스트는 DC Inside 이미지 403 문제 수정을 검증한다.
핵심: 쿠키 워밍업 세션 + Sec-Fetch-* 헤더로 DC 이미지 다운로드 성공 여부.

사용법:
    python -m test.test_scene_director_dc_download
"""
import logging
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_DC_PLACEHOLDERS = (
    "gallview_loading_ori.gif", "trans.gif", "img.gif",
    "loading_image.gif", "blank.gif",
)


def _find_dc_image_urls(max_posts: int = 10) -> list[str]:
    """DC Inside에서 테스트용 이미지 URL을 동적 수집한다.

    목록 페이지 접근이 차단될 경우 빈 리스트 반환 → 호출자가 fallback 처리.
    """
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    # 쿠키 워밍업
    try:
        sess.get("https://www.dcinside.com/", timeout=10)
    except Exception:
        pass

    listing_url = "https://gall.dcinside.com/board/lists/?id=dcbest"
    try:
        sess.headers["Referer"] = "https://www.dcinside.com/"
        resp = sess.get(listing_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("  DC 목록 페이지 접근 실패: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table.gall_list tbody tr.us-post") or soup.select("tr.ub-content")

    post_urls: list[str] = []
    for row in rows:
        if "notice" in " ".join(row.get("class", [])):
            continue
        link = (
            row.select_one("td.gall_tit a:first-child")
            or row.select_one("a.newtxt")
            or row.select_one("a[href*='/board/view/']")
        )
        if not link:
            continue
        href = link.get("href", "")
        url = ("https://gall.dcinside.com" + href) if href.startswith("/") else href
        post_urls.append(url)

    log.info("  DC 목록에서 %d개 게시글 발견", len(post_urls))

    image_urls: list[str] = []
    for post_url in post_urls[:max_posts]:
        try:
            sess.headers["Referer"] = listing_url
            resp = sess.get(post_url, timeout=15)
            resp.raise_for_status()
        except Exception:
            time.sleep(0.5)
            continue

        post_soup = BeautifulSoup(resp.text, "html.parser")
        body_el = post_soup.select_one("div.writing_view_box")
        if not body_el:
            time.sleep(0.5)
            continue

        for img in body_el.select("img:not(.og-img)"):
            src = (
                img.get("data-original")
                or img.get("data-lazy")
                or img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            )
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and not any(ph in src for ph in _DC_PLACEHOLDERS):
                image_urls.append(src)

        if len(image_urls) >= 3:
            break
        time.sleep(0.5)

    return image_urls


def test_scene_director_download() -> bool:
    """scene_director._download_and_cache_image를 사용한 DC 이미지 다운로드 테스트."""
    from ai_worker.pipeline.scene_director import _download_and_cache_image

    log.info("[Test 1] scene_director._download_and_cache_image DC 이미지 다운로드")

    image_urls = _find_dc_image_urls()

    if not image_urls:
        log.warning("  동적 크롤링 실패 — DC 메인 페이지 이미지로 fallback 테스트")
        # DC Inside 메인 페이지에서 이미지 URL을 추출하여 fallback
        try:
            sess = requests.Session()
            sess.headers.update({
                "User-Agent": _UA,
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            })
            resp = sess.get("https://gall.dcinside.com/board/lists/?id=dcbest", timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for img in soup.select("img[src*='dcimg']"):
                src = img.get("src", "")
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("http"):
                    image_urls.append(src)
                if len(image_urls) >= 3:
                    break
        except Exception as e:
            log.warning("  fallback 이미지 수집도 실패: %s", e)

    if not image_urls:
        log.error("  테스트용 DC 이미지 URL을 찾을 수 없음 — 네트워크 환경 확인 필요")
        return False

    log.info("  테스트 대상 이미지 %d장", len(image_urls))

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        success = 0
        fail = 0

        for i, url in enumerate(image_urls[:5]):
            result = _download_and_cache_image(url, cache_dir)
            if result and result.exists() and result.stat().st_size > 200:
                log.info("  [%d] OK — %s (%d bytes)", i + 1, result.name, result.stat().st_size)
                success += 1
            else:
                log.error("  [%d] FAIL — %s", i + 1, url[:80])
                fail += 1

    log.info("  결과: %d 성공, %d 실패", success, fail)
    return fail == 0 and success > 0


def test_dc_session_warmup() -> bool:
    """DC 세션 워밍업이 정상 동작하는지 테스트."""
    from ai_worker.pipeline.scene_director import _get_dc_session

    log.info("[Test 2] DC 세션 워밍업")
    sess = _get_dc_session()

    has_cookies = len(sess.cookies) > 0
    has_ua = "Chrome" in sess.headers.get("User-Agent", "")

    log.info("  쿠키 수: %d", len(sess.cookies))
    log.info("  User-Agent 설정: %s", "OK" if has_ua else "FAIL")

    if not has_ua:
        log.error("  User-Agent가 설정되지 않음")
        return False

    log.info("  결과: OK (쿠키=%d)", len(sess.cookies))
    return True


def test_is_dc_url() -> bool:
    """_is_dc_url 함수 검증."""
    from ai_worker.pipeline.scene_director import _is_dc_url

    log.info("[Test 3] _is_dc_url 판별")

    cases = [
        ("https://dcimg6.dcinside.co.kr/viewimage.php?id=abc", True),
        ("https://image.dcinside.com/img.jpg", True),
        ("https://dcimg1.dcinside.com/path", True),
        ("https://example.com/image.jpg", False),
        ("https://www.dcinside.com/page", True),
        ("https://cdn.other-site.com/dc.jpg", False),
    ]

    all_ok = True
    for url, expected in cases:
        result = _is_dc_url(url)
        ok = result == expected
        status = "OK" if ok else "FAIL"
        log.info("  [%s] _is_dc_url(%s) = %s (expected %s)", status, urlparse(url).hostname, result, expected)
        if not ok:
            all_ok = False

    return all_ok


def test_cache_hit() -> bool:
    """캐시된 이미지는 재다운로드하지 않는지 테스트."""
    from ai_worker.pipeline.scene_director import _download_and_cache_image

    log.info("[Test 4] 캐시 히트 테스트")

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)

        # 가짜 캐시 파일 생성
        import hashlib
        fake_url = "https://dcimg6.dcinside.co.kr/viewimage.php?id=test_cache"
        url_hash = hashlib.md5(fake_url.encode()).hexdigest()[:16]
        cache_path = cache_dir / f"vid_img_{url_hash}.jpg"
        cache_path.write_bytes(b"x" * 500)  # 200보다 큰 가짜 데이터

        result = _download_and_cache_image(fake_url, cache_dir)
        if result and result == cache_path:
            log.info("  캐시 히트 정상 — 네트워크 요청 건너뜀")
            return True
        else:
            log.error("  캐시 히트 실패 — 네트워크 재요청 발생")
            return False


def test_non_dc_download() -> bool:
    """DC 외 일반 이미지 다운로드도 정상인지 확인."""
    from ai_worker.pipeline.scene_director import _download_and_cache_image

    log.info("[Test 5] 비-DC 이미지 다운로드")

    test_url = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png"

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        result = _download_and_cache_image(test_url, cache_dir)

        if result and result.exists() and result.stat().st_size > 200:
            log.info("  OK — %s (%d bytes)", result.name, result.stat().st_size)
            return True
        else:
            log.warning("  비-DC 이미지 다운로드 실패 (네트워크 문제일 수 있음)")
            return False


def main() -> None:
    log.info("=" * 60)
    log.info("scene_director DC 이미지 다운로드 수정 검증 테스트")
    log.info("=" * 60)

    results: dict[str, bool] = {}

    results["dc_session_warmup"] = test_dc_session_warmup()
    results["is_dc_url"] = test_is_dc_url()
    results["cache_hit"] = test_cache_hit()
    results["non_dc_download"] = test_non_dc_download()
    results["scene_director_dc_download"] = test_scene_director_download()

    log.info("\n" + "=" * 60)
    log.info("결과 요약")
    log.info("=" * 60)

    all_pass = True
    for name, passed in results.items():
        icon = "PASS" if passed else "FAIL"
        log.info("  [%s] %s", icon, name)
        if not passed:
            all_pass = False

    if all_pass:
        log.info("\n모든 테스트 통과!")
    else:
        log.error("\n일부 테스트 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
