"""DCInside 기존 크롤링 데이터의 플레이스홀더 이미지 URL을 실제 URL로 교체.

Docker 컨테이너 내부에서 실행:
    docker exec wagglebot-dashboard-1 python scripts/fix_dc_images.py

동작:
    1) DB에서 플레이스홀더 이미지가 포함된 DCInside 게시글을 조회
    2) 각 게시글의 원본 URL을 재방문하여 이미지를 다시 추출
    3) 새 이미지 목록으로 DB 업데이트
"""

import json
import logging
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

from db.session import SessionLocal
from db.models import Post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 상수 ──
_DC_PLACEHOLDERS = (
    "gallview_loading_ori.gif", "trans.gif", "img.gif",
    "loading_image.gif", "blank.gif",
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BASE_URL = "https://gall.dcinside.com"


def _has_placeholder(images: list[str]) -> bool:
    return any(any(ph in u for ph in _DC_PLACEHOLDERS) for u in images)


def _build_post_url(origin_id: str) -> str | None:
    """origin_id (예: 'dcbest_408445')에서 게시글 URL을 복원."""
    parts = origin_id.split("_", 1)
    if len(parts) != 2:
        return None
    gall_id, post_no = parts
    return f"{BASE_URL}/board/view/?id={gall_id}&no={post_no}"


def _extract_images(sess: requests.Session, url: str) -> list[str] | None:
    """게시글 URL에서 이미지 URL 목록을 추출. 실패 시 None."""
    try:
        resp = sess.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("  요청 실패: %s — %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    body_el = soup.select_one("div.writing_view_box")
    if not body_el:
        log.warning("  본문 영역 없음: %s", url)
        return None

    images: list[str] = []

    # 기본 추출 (data-original 우선)
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
            images.append(src)

    # 정규식 fallback
    seen = set(images)
    body_html = str(body_el)
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
            images.append(url_clean)
            seen.add(url_clean)

    return images if images else None


def main() -> None:
    log.info("=" * 60)
    log.info("DCInside 이미지 플레이스홀더 일괄 수정 시작")
    log.info("=" * 60)

    # ── 1) 수정 대상 조회 ──
    with SessionLocal() as db:
        posts = (
            db.query(Post)
            .filter(Post.site_code == "dcinside", Post.images.isnot(None))
            .all()
        )

        broken: list[Post] = []
        for p in posts:
            imgs = p.images if isinstance(p.images, list) else json.loads(p.images or "[]")
            if imgs and _has_placeholder(imgs):
                broken.append(p)

        log.info("총 DCInside 이미지 게시글: %d개", len(posts))
        log.info("플레이스홀더 포함 (수정 대상): %d개", len(broken))

        if not broken:
            log.info("수정할 게시글이 없습니다.")
            return

        # ── 2) 크롤링 세션 생성 ──
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.dcinside.com/",
        })

        # ── 3) 각 게시글 재크롤링 ──
        fixed = 0
        failed = 0
        deleted = 0  # 원본 삭제됨

        for i, post in enumerate(broken):
            url = _build_post_url(post.origin_id)
            if not url:
                log.warning("  [%d/%d] id=%d — origin_id 파싱 실패: %s", i + 1, len(broken), post.id, post.origin_id)
                failed += 1
                continue

            old_imgs = post.images if isinstance(post.images, list) else json.loads(post.images or "[]")
            old_count = len(old_imgs)
            old_valid = sum(1 for u in old_imgs if not any(ph in u for ph in _DC_PLACEHOLDERS))

            new_imgs = _extract_images(sess, url)

            if new_imgs is None:
                log.warning(
                    "  [%d/%d] id=%d ❌ 재크롤링 실패 (삭제된 게시글?): %s",
                    i + 1, len(broken), post.id, post.title[:30],
                )
                deleted += 1
            elif _has_placeholder(new_imgs):
                log.warning(
                    "  [%d/%d] id=%d ⚠️  여전히 플레이스홀더 포함 (기존 %d→%d장): %s",
                    i + 1, len(broken), post.id, old_count, len(new_imgs), post.title[:30],
                )
                failed += 1
            else:
                post.images = new_imgs
                db.flush()
                fixed += 1
                log.info(
                    "  [%d/%d] id=%d ✅ 수정 완료: %d장 → %d장 (유효 %d→%d): %s",
                    i + 1, len(broken), post.id,
                    old_count, len(new_imgs),
                    old_valid, len(new_imgs),
                    post.title[:30],
                )

            # 요청 간 딜레이
            time.sleep(0.5)

            # 50건마다 중간 커밋
            if fixed > 0 and fixed % 50 == 0:
                db.commit()
                log.info("  --- 중간 커밋 (%d건) ---", fixed)

        # 최종 커밋
        db.commit()

    # ── 결과 요약 ──
    log.info("\n" + "=" * 60)
    log.info("수정 완료")
    log.info("=" * 60)
    log.info("  수정 대상: %d개", len(broken))
    log.info("  성공:      %d개 ✅", fixed)
    log.info("  실패:      %d개 ❌ (플레이스홀더 잔존)", failed)
    log.info("  삭제됨:    %d개 🗑️  (원본 게시글 삭제)", deleted)

    if failed > 0:
        log.warning("  일부 게시글은 수동 확인이 필요합니다.")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
