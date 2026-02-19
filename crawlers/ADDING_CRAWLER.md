# 크롤러 추가 가이드

WaggleBot의 크롤러는 `BaseCrawler`를 상속하는 플러그인 방식으로 동작합니다.
`crawlers/` 디렉토리에 파일을 추가하면 자동으로 등록됩니다.

---

## BaseCrawler 인터페이스

```python
# crawlers/base.py
class BaseCrawler(ABC):
    site_code: str = ""          # 고유 식별자 (DB 저장 키)

    @abstractmethod
    def fetch_listing(self) -> list[dict]:
        """목록 페이지 파싱. 최소 {origin_id, title, url} 포함."""

    @abstractmethod
    def parse_post(self, url: str) -> dict:
        """상세 페이지 파싱. 반환 형식:
        {
            title: str,
            content: str,
            stats: {views, likes, comments_count},
            comments: [{author, content, likes}],
            images: [url, ...]   # 선택
        }
        """
```

`run()`, `_upsert()`, `_sync_comments()`는 `BaseCrawler`가 처리하므로 구현 불필요.

---

## 구현 단계

### 1. 크롤러 파일 생성

`crawlers/yoursite.py` 파일을 생성합니다.

```python
import logging
import re

import requests
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler

log = logging.getLogger(__name__)

BASE_URL = "https://yoursite.com"


class YourSiteCrawler(BaseCrawler):
    site_code = "yoursite"
    SECTIONS = [
        {"name": "인기글", "url": f"{BASE_URL}/popular"},
    ]

    def fetch_listing(self) -> list[dict]:
        """인기글 목록 반환."""
        results = []
        for section in self.SECTIONS:
            try:
                resp = self._get(section["url"])
            except requests.RequestException:
                log.exception("Failed to fetch listing: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
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
        """상세 페이지 파싱."""
        resp = self._get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        comments = []
        for c in soup.select("li.comment"):
            author = c.select_one("span.author")
            content = c.select_one("p.content")
            if author and content:
                comments.append({
                    "author": self._text(author),
                    "content": self._text(content),
                    "likes": self._parse_int(self._text(c.select_one("span.likes"))),
                })

        page_text = soup.get_text()
        return {
            "title": self._text(soup.select_one("h1.post-title")),
            "content": self._text(soup.select_one("div.post-content")),
            "stats": {
                "views": self._parse_stat(page_text, r"조회\s*([\d,]+)"),
                "likes": self._parse_stat(page_text, r"추천\s*([\d,]+)"),
                "comments_count": len(comments),
            },
            "comments": comments,
        }
```

### 2. .env에 활성화

```bash
# .env
ENABLED_CRAWLERS=nate_pann,yoursite
```

### 3. 동작 확인

```bash
# 등록 확인
python main.py --list

# 1회 테스트 실행
python main.py --once
```

---

## 네이밍 규칙

| 항목 | 규칙 | 예시 |
|------|------|------|
| 파일명 | `crawlers/{site_code}.py` | `crawlers/nate_pann.py` |
| 클래스명 | `{SiteName}Crawler` | `NatePannCrawler` |
| `site_code` | snake_case, 짧게 | `"nate_pann"` |

---

## 주의사항

- `fetch_listing()` / `parse_post()`에서 발생한 예외는 `BaseCrawler.run()`이 잡아서 로깅합니다. 개별 게시글 실패가 전체 크롤링을 중단하지 않습니다.
- Rate Limiting: 요청 사이에 `time.sleep()` 추가를 권장합니다.
- `origin_id`는 사이트 내 고유 식별자여야 합니다. 중복 시 stats만 업데이트됩니다.
- 이미지 URL 목록은 `images` 키에 `list[str]`으로 반환합니다 (선택).

---

## 기존 크롤러 참고

- [crawlers/nate_pann.py](nate_pann.py) — 네이트판 크롤러 (실제 구현 예시)
- [crawlers/base.py](base.py) — BaseCrawler 전체 코드
