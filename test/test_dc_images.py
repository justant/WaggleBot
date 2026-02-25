"""DCInside 이미지 5장 이상 크롤링 → 대시보드 이미지 로딩 E2E 테스트.

사용법:
    python -m test.test_dc_images
"""

import logging
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── 로깅 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 상수 ──
MIN_IMAGES = 5
MAX_POSTS_TO_SCAN = 30  # 최대 스캔할 게시글 수
TARGET_SECTIONS = [
    "https://gall.dcinside.com/board/lists/?id=dcbest",
    "https://gall.dcinside.com/board/lists/?id=hit",
]
_DC_PLACEHOLDERS = (
    "gallview_loading_ori.gif", "trans.gif", "img.gif",
    "loading_image.gif", "blank.gif",
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# =====================================================================
# Phase 1: 크롤러와 동일한 방식으로 이미지 URL 수집
# =====================================================================

def create_crawl_session() -> requests.Session:
    """크롤러(BaseCrawler)와 동일한 세션 생성."""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.dcinside.com/",
    })
    return sess


def fetch_listing(sess: requests.Session) -> list[dict]:
    """DCInside 실베/힛갤 목록에서 게시글 URL 수집."""
    posts: list[dict] = []
    seen: set[str] = set()

    for section_url in TARGET_SECTIONS:
        try:
            resp = sess.get(section_url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            log.warning("목록 페이지 요청 실패: %s — %s", section_url, e)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # 테이블 기반 레이아웃
        rows = soup.select("table.gall_list tbody tr.us-post")
        if not rows:
            rows = soup.select("tr.ub-content")

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
            if href in seen:
                continue
            seen.add(href)

            url = ("https://gall.dcinside.com" + href) if href.startswith("/") else href
            title = link.get_text(strip=True)[:60]
            posts.append({"url": url, "title": title})

        log.info("  섹션 %s → %d개 게시글", section_url.split("id=")[1], len(posts))
        time.sleep(0.5)

    return posts


def parse_images_from_post(sess: requests.Session, url: str) -> dict:
    """게시글 HTML에서 이미지 URL 추출 (크롤러 로직 재현).

    Returns:
        {
            "title": str,
            "img_tags_total": int,       # body 내 <img> 총 개수
            "img_tags_lazy": int,        # class="lazy" 인 태그 수
            "images_normal": list[str],  # data-original/src 방식 추출 결과
            "images_regex": list[str],   # 정규식 fallback 추가분
            "images_all": list[str],     # 최종 합산 결과
            "raw_attrs": list[dict],     # 디버깅용: 각 <img>의 주요 속성
        }
    """
    resp = sess.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = (
        soup.select_one("span.title_subject")
        or soup.select_one("h4.title span")
        or soup.select_one("h3.title")
    )
    title = title_el.get_text(strip=True) if title_el else "(제목 없음)"

    body_el = soup.select_one("div.writing_view_box")
    if not body_el:
        return {
            "title": title, "img_tags_total": 0, "img_tags_lazy": 0,
            "images_normal": [], "images_regex": [], "images_all": [],
            "raw_attrs": [],
        }

    # ── 1) 기본 추출 (크롤러 로직) ──
    all_imgs = body_el.select("img:not(.og-img)")
    lazy_count = sum(1 for img in all_imgs if "lazy" in (img.get("class") or []))

    raw_attrs: list[dict] = []
    images_normal: list[str] = []
    for img in all_imgs:
        attrs = {
            "src": img.get("src", "")[:100],
            "data-original": img.get("data-original", "")[:100],
            "data-lazy": img.get("data-lazy", "")[:100],
            "data-src": img.get("data-src", "")[:100],
            "class": img.get("class", []),
        }
        raw_attrs.append(attrs)

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
            images_normal.append(src)

    # ── 2) 정규식 fallback ──
    seen = set(images_normal)
    body_html = str(body_el)
    images_regex: list[str] = []
    for raw in re.findall(
        r'(?:https?:)?//(?:dcimg\d*|image)\.dcinside\.com/[^\s"\'<>]+',
        body_html,
    ):
        url_clean = "https:" + raw if raw.startswith("//") else raw
        if (
            url_clean not in seen
            and not any(ph in url_clean for ph in _DC_PLACEHOLDERS)
            and ("viewimage.php" in url_clean or re.search(r'\.(?:jpg|jpeg|png|gif|webp)', url_clean, re.IGNORECASE))
        ):
            images_regex.append(url_clean)
            seen.add(url_clean)

    images_all = images_normal + images_regex

    return {
        "title": title,
        "img_tags_total": len(all_imgs),
        "img_tags_lazy": lazy_count,
        "images_normal": images_normal,
        "images_regex": images_regex,
        "images_all": images_all,
        "raw_attrs": raw_attrs,
    }


# =====================================================================
# Phase 2: 대시보드 이미지 슬라이더와 동일한 방식으로 이미지 다운로드
# =====================================================================

def create_dashboard_session() -> requests.Session:
    """image_slider.py의 _get_dc_session() 재현."""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    # 쿠키 워밍업
    try:
        sess.get("https://www.dcinside.com/", timeout=10)
        log.info("  대시보드 세션 쿠키 워밍업 OK (cookies=%d)", len(sess.cookies))
    except Exception:
        log.warning("  대시보드 세션 쿠키 워밍업 실패")
    return sess


def fetch_image_like_dashboard(sess: requests.Session, url: str) -> dict:
    """image_slider.py의 _fetch_image() 로직 재현.

    Returns:
        {"ok": bool, "status": int, "size": int, "content_type": str, "error": str}
    """
    hostname = urlparse(url).hostname or ""
    is_dc = any(hostname.endswith(d) for d in ("dcinside.com", "dcinside.co.kr"))

    try:
        if is_dc:
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
                    "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
                    "User-Agent": _UA,
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
        resp.raise_for_status()
        data = resp.content
        ct = resp.headers.get("Content-Type", "")
        size = len(data)

        if size < 200:
            return {"ok": False, "status": resp.status_code, "size": size, "content_type": ct, "error": f"플레이스홀더 의심 ({size}B)"}

        return {"ok": True, "status": resp.status_code, "size": size, "content_type": ct, "error": ""}

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        return {"ok": False, "status": code, "size": 0, "content_type": "", "error": str(e)}
    except Exception as e:
        return {"ok": False, "status": 0, "size": 0, "content_type": "", "error": str(e)}


# =====================================================================
# Main
# =====================================================================

def main() -> None:
    log.info("=" * 60)
    log.info("DCInside 이미지 E2E 테스트 시작")
    log.info("  조건: 이미지 %d장 이상인 게시글 찾기", MIN_IMAGES)
    log.info("=" * 60)

    # ── Step 1: 게시글 목록 수집 ──
    crawl_sess = create_crawl_session()
    log.info("\n[Step 1] 게시글 목록 수집...")
    posts = fetch_listing(crawl_sess)
    if not posts:
        log.error("게시글을 찾을 수 없습니다.")
        sys.exit(1)
    log.info("  총 %d개 게시글 발견", len(posts))

    # ── Step 2: 이미지 5장 이상 게시글 찾기 ──
    log.info("\n[Step 2] 이미지 %d장 이상 게시글 검색 (최대 %d개 스캔)...", MIN_IMAGES, MAX_POSTS_TO_SCAN)
    target_post = None

    for i, post in enumerate(posts[:MAX_POSTS_TO_SCAN]):
        try:
            result = parse_images_from_post(crawl_sess, post["url"])
        except Exception as e:
            log.warning("  [%d] 파싱 실패: %s — %s", i + 1, post["title"][:30], e)
            time.sleep(0.5)
            continue

        img_count = len(result["images_all"])
        log.info(
            "  [%d/%d] %s — <img> %d개 (lazy=%d) → 유효 이미지 %d개 (기본=%d, regex=%d)",
            i + 1, min(len(posts), MAX_POSTS_TO_SCAN),
            result["title"][:30],
            result["img_tags_total"], result["img_tags_lazy"],
            img_count, len(result["images_normal"]), len(result["images_regex"]),
        )

        if img_count >= MIN_IMAGES:
            target_post = {**post, **result}
            log.info("  ✅ 대상 게시글 발견!")
            break

        time.sleep(0.5)

    if target_post is None:
        log.error("이미지 %d장 이상인 게시글을 찾지 못했습니다.", MIN_IMAGES)
        sys.exit(1)

    # ── Step 3: 크롤러 결과 상세 출력 ──
    images = target_post["images_all"]
    log.info("\n[Step 3] 크롤러 이미지 수집 결과 상세")
    log.info("  제목: %s", target_post["title"])
    log.info("  URL:  %s", target_post["url"])
    log.info("  <img> 태그: %d개 (lazy: %d개)", target_post["img_tags_total"], target_post["img_tags_lazy"])
    log.info("  기본 추출: %d장 / 정규식 추가: %d장 / 합계: %d장",
             len(target_post["images_normal"]), len(target_post["images_regex"]), len(images))

    log.info("\n  [<img> 태그 속성 디버깅]")
    for j, attrs in enumerate(target_post["raw_attrs"]):
        log.info("    img[%d] class=%s", j, attrs["class"])
        log.info("           src           = %s", attrs["src"] or "(없음)")
        log.info("           data-original = %s", attrs["data-original"] or "(없음)")
        log.info("           data-lazy     = %s", attrs["data-lazy"] or "(없음)")
        log.info("           data-src      = %s", attrs["data-src"] or "(없음)")

    log.info("\n  [최종 이미지 URL 목록]")
    for j, url in enumerate(images):
        log.info("    [%d] %s", j + 1, url[:120])

    # ── Step 4: 대시보드 방식으로 이미지 다운로드 테스트 ──
    log.info("\n[Step 4] 대시보드(image_slider) 방식 이미지 다운로드 테스트")
    dash_sess = create_dashboard_session()

    results: list[dict] = []
    all_ok = True
    for j, url in enumerate(images):
        r = fetch_image_like_dashboard(dash_sess, url)
        results.append(r)
        status_icon = "✅" if r["ok"] else "❌"
        log.info(
            "  %s [%d/%d] HTTP %s | %s | %s%s",
            status_icon, j + 1, len(images),
            r["status"] or "ERR",
            f"{r['size']:,}B" if r["size"] else "0B",
            r["content_type"][:30],
            f" — {r['error']}" if r["error"] else "",
        )
        if not r["ok"]:
            all_ok = False
        time.sleep(0.3)

    # ── 결과 요약 ──
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count

    log.info("\n" + "=" * 60)
    log.info("테스트 결과 요약")
    log.info("=" * 60)
    log.info("  게시글:     %s", target_post["title"])
    log.info("  총 이미지:  %d장", len(images))
    log.info("  성공:       %d장 ✅", ok_count)
    log.info("  실패:       %d장 ❌", fail_count)

    if fail_count > 0:
        log.info("\n  [실패 상세]")
        for j, (url, r) in enumerate(zip(images, results)):
            if not r["ok"]:
                log.info("    [%d] %s", j + 1, url[:100])
                log.info("         → %s", r["error"])

    if all_ok:
        log.info("\n🎉 모든 이미지 로드 성공! 크롤러 + 대시보드 이미지 파이프라인 정상.")
    else:
        log.info("\n⚠️  일부 이미지 로드 실패. 위 상세 내용을 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
