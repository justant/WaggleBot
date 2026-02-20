# /add-crawler — 새 크롤러 스캐폴딩

Agent C가 새 커뮤니티 사이트 크롤러를 추가할 때 사용하는 스킬.
`crawlers/ADDING_CRAWLER.md`의 절차를 따라 올바른 골격 파일을 생성한다.

## 사용법

```
/add-crawler <site_code> <SiteName> <base_url>
```

예시:
```
/add-crawler theqoo Theqoo https://theqoo.net
/add-crawler mlbpark MLBPark https://mlbpark.donga.com
```

---

## 수행 절차

### Step 1 — 파라미터 확인

| 파라미터 | 설명 | 규칙 |
|---|---|---|
| `site_code` | DB 저장 키 | snake_case, 짧게 (예: `nate_pann`) |
| `SiteName` | 클래스 이름 접두어 | PascalCase (예: `NatePann`) |
| `base_url` | 사이트 루트 URL | https:// 포함 |

파라미터가 없으면 사용자에게 물어본다.

### Step 2 — 크롤러 파일 생성

`crawlers/{site_code}.py` 파일을 아래 템플릿으로 생성:

```python
import logging
import re

import requests
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler

log = logging.getLogger(__name__)

BASE_URL = "{base_url}"


class {SiteName}Crawler(BaseCrawler):
    site_code = "{site_code}"
    SECTIONS = [
        {"name": "인기글", "url": f"{BASE_URL}/popular"},
        # TODO: 실제 섹션 URL로 교체
    ]

    def fetch_listing(self) -> list[dict]:
        """인기글 목록 반환. 최소 {origin_id, title, url} 포함 필수."""
        results = []
        for section in self.SECTIONS:
            try:
                resp = self._get(section["url"])
            except requests.RequestException:
                log.exception("fetch_listing failed: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            # TODO: 실제 CSS 셀렉터로 교체
            for item in soup.select("ul.post-list li"):
                link = item.select_one("a.post-link")
                if not link:
                    continue
                results.append({
                    "origin_id": link["href"].split("/")[-1],
                    "title": self._text(link),
                    "url": BASE_URL + link["href"],
                })
        return results

    def parse_post(self, url: str) -> dict:
        """상세 페이지 파싱.
        반환 필수 키: title, content, stats(views/likes/comments_count), comments
        선택 키: images (list[str])
        """
        resp = self._get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        comments = []
        # TODO: 실제 댓글 셀렉터로 교체
        for c in soup.select("li.comment"):
            author = c.select_one("span.author")
            content_el = c.select_one("p.content")
            if author and content_el:
                comments.append({
                    "author": self._text(author),
                    "content": self._text(content_el),
                    "likes": self._parse_int(self._text(c.select_one("span.likes"))),
                })

        page_text = soup.get_text()
        return {
            "title": self._text(soup.select_one("h1.post-title")),   # TODO: 교체
            "content": self._text(soup.select_one("div.post-content")),  # TODO: 교체
            "stats": {
                "views": self._parse_stat(page_text, r"조회\s*([\d,]+)"),
                "likes": self._parse_stat(page_text, r"추천\s*([\d,]+)"),
                "comments_count": len(comments),
            },
            "comments": comments,
        }
```

### Step 3 — 등록 확인

파일 생성 후 아래 명령으로 import가 정상인지 확인한다:

```bash
python -c "from crawlers.{site_code} import {SiteName}Crawler; print('OK')"
python -c "from crawlers.plugin_manager import CrawlerRegistry; print(CrawlerRegistry.list_crawlers())"
```

### Step 4 — 완료 안내 출력

```
✅ 크롤러 스캐폴딩 완료
────────────────────────
파일: crawlers/{site_code}.py
클래스: {SiteName}Crawler
site_code: "{site_code}"

다음 TODO를 구현하세요:
1. SECTIONS — 실제 섹션 URL 교체
2. fetch_listing() — 실제 CSS 셀렉터 적용
3. parse_post() — 제목/본문/댓글 셀렉터 적용

.env에 크롤러 활성화:
  ENABLED_CRAWLERS=...기존...,{site_code}

테스트:
  python main.py --list          # 등록 확인
  python main.py --once          # 1회 실행
```

---

## 주의사항

- `BaseCrawler`의 `run()`, `_upsert()`, `_sync_comments()`는 이미 구현되어 있으므로 재구현 금지.
- `origin_id`는 사이트 내 전역 고유값이어야 함. 중복 시 stats만 업데이트됨.
- Rate Limiting: `fetch_listing()` 루프 내에 `time.sleep(1)` 권장.
- `settings.py`에 새 항목 추가 금지 — `SECTIONS` 클래스 변수로 URL 직접 정의.
- 이미지 URL이 있으면 `images: list[str]` 키를 `parse_post()` 반환값에 추가 (선택).
- DB 스키마 변경이 필요하면 `/proposal` 스킬을 통해 Team Lead에게 Proposal 요청.
