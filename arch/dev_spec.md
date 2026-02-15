# WaggleBot 개발 명세서 (Development Specification)

## 1. 프로젝트 개요

### 1.1 목표
인기 커뮤니티 게시글을 자동으로 수집하여 쇼츠 영상(9:16 비율)으로 변환 후 유튜브 등에 자동 업로드하는 완전 자동화 파이프라인 구축.

### 1.2 하드웨어 환경
- **노드:** 단일 Windows PC (WSL Ubuntu 환경)
- **GPU:** NVIDIA RTX 3080 Ti (12GB VRAM)
- **역할:** 크롤링, DB, AI 추론, 영상 렌더링, 업로드 전체 담당
- **제약:** VRAM 부족으로 인한 순차 처리 필수

### 1.3 기술 스택
- **언어:** Python 3.12
- **DB:** MariaDB 11.x + SQLAlchemy ORM
- **웹 UI:** Streamlit
- **영상:** FFmpeg (NVENC 가속)
- **컨테이너:** Docker Compose with GPU support
- **LLM:** EEVE-Korean-10.8B / Llama-3.1-8B-Instruct-Ko (4-bit 양자화)
- **TTS:** Kokoro-82M / GPT-SoVITS / Edge-TTS

---

## 2. 시스템 아키텍처

### 2.1 전체 데이터 흐름

```
┌─────────────┐      ┌──────────────┐      ┌──────────────┐
│  Scheduler  │ 1hr  │   Crawler    │ DB   │  Dashboard   │
│  (Cron)     │─────>│ (수집/파싱)   │─────>│  (검수 UI)   │
└─────────────┘      └──────────────┘      └──────────────┘
                            │                      │
                            │ COLLECTED            │ APPROVED
                            ▼                      ▼
                     ┌─────────────────────────────────┐
                     │         MariaDB                 │
                     │  Posts / Comments / Contents    │
                     └─────────────────────────────────┘
                            │                      
                            │ 10초 Polling (APPROVED 감지)
                            ▼                      
                     ┌─────────────┐
                     │  AI Worker  │
                     │  (LLM/TTS/  │
                     │   Render)   │
                     └─────────────┘
                            │
                            │ RENDERED
                            ▼
                     ┌─────────────┐
                     │  Uploader   │
                     │  (YouTube)  │
                     └─────────────┘
```

### 2.2 상태 전이 (State Transition)

```
COLLECTED → APPROVED → PROCESSING → RENDERED → UPLOADED
    ↓
DECLINED (거절됨)
```

- **COLLECTED:** 크롤러가 DB에 저장 완료
- **APPROVED:** 대시보드에서 관리자 승인
- **PROCESSING:** AI 워커가 처리 중 (LLM/TTS/렌더링)
- **RENDERED:** 영상 생성 완료 (업로드 대기)
- **UPLOADED:** 최종 업로드 완료
- **DECLINED:** 관리자가 거절 (처리 안 함)

### 2.3 컨테이너 구성

```yaml
# docker-compose.yml 구조
services:
  db:          # MariaDB 11.x
  crawler:     # 1시간마다 자동 실행
  ai_worker:   # GPU 사용, DB 폴링
  dashboard:   # Streamlit UI (8501 포트)
  
volumes:
  - mariadb_data  # DB 영구 저장
  - ./media       # 영상/오디오 파일 공유
  - ./assets      # 배경 영상, 폰트 (읽기 전용)
```

---

## 3. 데이터베이스 스키마

### 3.1 테이블: posts

| 필드 | 타입 | 설명 |
|------|------|------|
| id | BIGINT (PK) | 자동 증가 ID |
| site_code | VARCHAR(32) | 사이트 코드 (예: nate_pann, nate_tok) |
| origin_id | VARCHAR(64) UNIQUE | 원본 사이트의 게시글 ID |
| title | VARCHAR(512) | 게시글 제목 |
| content | TEXT | 본문 (HTML 제거됨) |
| images | JSON | 이미지 URL 배열 `["url1", "url2"]` |
| stats | JSON | 통계 `{"views": 1234, "likes": 567}` |
| status | ENUM | COLLECTED/APPROVED/PROCESSING/RENDERED/UPLOADED/DECLINED |
| created_at | DATETIME | 최초 수집 시각 |
| updated_at | DATETIME | 마지막 수정 시각 |

**인덱스:**
- `site_code` (크롤러 필터링)
- `status` (AI 워커 폴링)
- `origin_id` UNIQUE (중복 방지)

### 3.2 테이블: comments

| 필드 | 타입 | 설명 |
|------|------|------|
| id | BIGINT (PK) | 자동 증가 ID |
| post_id | BIGINT (FK) | 게시글 ID |
| author | VARCHAR(128) | 작성자 닉네임 |
| content | TEXT | 댓글 내용 |
| content_hash | VARCHAR(64) | 중복 체크용 해시 |
| likes | INT | 추천수 |

**용도:** 베스트 댓글을 LLM 요약에 포함

### 3.3 테이블: contents

| 필드 | 타입 | 설명 |
|------|------|------|
| id | BIGINT (PK) | 자동 증가 ID |
| post_id | BIGINT UNIQUE (FK) | 게시글 ID (1:1 관계) |
| summary_text | TEXT | LLM 생성 요약 (200자 내외) |
| audio_path | VARCHAR(255) | TTS 생성 음성 파일 경로 |
| video_path | VARCHAR(255) | 최종 렌더링 영상 경로 |
| upload_meta | JSON | 업로드 결과 `{"youtube_id": "xxx"}` |
| created_at | DATETIME | 생성 시각 |

---

## 4. 모듈별 상세 명세

### 4.1 크롤러 (Crawler)

#### 4.1.1 설계 원칙: 확장성

**목표:** 100개 이상의 커뮤니티 사이트 지원  
**패턴:** BaseCrawler 추상 클래스 + 플러그인 레지스트리

#### 4.1.2 BaseCrawler 인터페이스

```python
# crawlers/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class BaseCrawler(ABC):
    """모든 크롤러가 상속받아야 하는 추상 클래스"""
    
    def __init__(self, site_code: str):
        self.site_code = site_code
        self.logger = logging.getLogger(f"crawler.{site_code}")
    
    @abstractmethod
    def fetch_listing(self, page: int = 1) -> List[Dict[str, str]]:
        """
        게시글 목록 페이지에서 URL 추출
        
        Returns:
            [{"url": "https://...", "title": "..."}, ...]
        """
        pass
    
    @abstractmethod
    def parse_post(self, url: str) -> Dict:
        """
        개별 게시글 파싱
        
        Returns:
            {
                "origin_id": "12345",
                "title": "제목",
                "content": "본문 텍스트",
                "images": ["url1", "url2"],
                "stats": {"views": 1234, "likes": 567},
                "best_comment": {"author": "...", "content": "..."}
            }
        """
        pass
    
    def save_to_db(self, data: Dict) -> bool:
        """
        DB에 Upsert (중복 시 통계만 업데이트)
        """
        with SessionLocal() as db:
            existing = db.query(Post).filter_by(
                site_code=self.site_code,
                origin_id=data['origin_id']
            ).first()
            
            if existing:
                # 기존 글: 통계만 업데이트
                existing.stats = data['stats']
                existing.updated_at = datetime.now()
                self.logger.info(f"Updated stats: {data['origin_id']}")
            else:
                # 신규 글: 전체 저장
                post = Post(
                    site_code=self.site_code,
                    origin_id=data['origin_id'],
                    title=data['title'],
                    content=data['content'],
                    images=json.dumps(data.get('images', [])),
                    stats=json.dumps(data['stats']),
                    status='COLLECTED'
                )
                db.add(post)
                self.logger.info(f"New post: {data['origin_id']}")
                
                # 베스트 댓글 저장
                if data.get('best_comment'):
                    comment = Comment(
                        post_id=post.id,
                        author=data['best_comment']['author'],
                        content=data['best_comment']['content'],
                        content_hash=hashlib.sha256(
                            data['best_comment']['content'].encode()
                        ).hexdigest()
                    )
                    db.add(comment)
            
            db.commit()
            return True
    
    def run(self, max_pages: int = 3):
        """크롤링 실행 (여러 페이지)"""
        for page in range(1, max_pages + 1):
            try:
                posts = self.fetch_listing(page)
                for post_meta in posts:
                    try:
                        data = self.parse_post(post_meta['url'])
                        self.save_to_db(data)
                        time.sleep(1)  # Rate limiting
                    except Exception as e:
                        self.logger.exception(f"Parse error: {post_meta['url']}")
            except Exception as e:
                self.logger.exception(f"Fetch error: page {page}")
```

#### 4.1.3 네이트판 구현 예시

```python
# crawlers/nate.py
from crawlers.base import BaseCrawler
from bs4 import BeautifulSoup
import requests

class NatePannCrawler(BaseCrawler):
    BASE_URL = "https://pann.nate.com"
    
    def fetch_listing(self, page: int = 1) -> List[Dict[str, str]]:
        url = f"{self.BASE_URL}/talk/ranking?page={page}"
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        posts = []
        for item in soup.select('.list_item'):
            posts.append({
                'url': self.BASE_URL + item.select_one('a')['href'],
                'title': item.select_one('.tit').text.strip()
            })
        return posts
    
    def parse_post(self, url: str) -> Dict:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Origin ID 추출 (URL에서)
        origin_id = url.split('/')[-1]
        
        # 본문
        content_div = soup.select_one('.article_content')
        content = content_div.get_text(strip=True)
        
        # 이미지 (본문 내 img 태그)
        images = [img['src'] for img in content_div.select('img') if img.get('src')]
        
        # 통계
        views = int(soup.select_one('.view_count').text.replace(',', ''))
        likes = int(soup.select_one('.like_count').text.replace(',', ''))
        
        # 베스트 댓글 (추천수 1위)
        comments = soup.select('.comment_item')
        best_comment = None
        if comments:
            sorted_comments = sorted(
                comments, 
                key=lambda c: int(c.select_one('.like_count').text or 0),
                reverse=True
            )
            best = sorted_comments[0]
            best_comment = {
                'author': best.select_one('.author').text.strip(),
                'content': best.select_one('.content').text.strip(),
                'likes': int(best.select_one('.like_count').text or 0)
            }
        
        return {
            'origin_id': origin_id,
            'title': soup.select_one('.article_title').text.strip(),
            'content': content,
            'images': images,
            'stats': {'views': views, 'likes': likes},
            'best_comment': best_comment
        }
```

#### 4.1.4 플러그인 레지스트리 (확장용)

```python
# crawlers/registry.py
from typing import Dict, Type
from crawlers.base import BaseCrawler

class CrawlerRegistry:
    _crawlers: Dict[str, Type[BaseCrawler]] = {}
    
    @classmethod
    def register(cls, site_code: str):
        def decorator(crawler_class):
            cls._crawlers[site_code] = crawler_class
            return crawler_class
        return decorator
    
    @classmethod
    def get(cls, site_code: str) -> BaseCrawler:
        if site_code not in cls._crawlers:
            raise ValueError(f"Unknown site: {site_code}")
        return cls._crawlers[site_code](site_code)

# 사용 예시
@CrawlerRegistry.register('nate_pann')
class NatePannCrawler(BaseCrawler):
    pass

# main.py
for site in ['nate_pann', 'nate_tok']:
    crawler = CrawlerRegistry.get(site)
    crawler.run(max_pages=3)
```

---

### 4.2 관리자 대시보드 (Streamlit)

#### 4.2.1 UI 구조

**3개 탭:**
1. **수신함 (Inbox):** COLLECTED 상태 게시글 승인/거절
2. **진행 상태 (Progress):** PROCESSING/RENDERED/UPLOADED 모니터링
3. **갤러리 (Gallery):** 완성된 영상 재생

#### 4.2.2 Tab 1: 수신함 구현

```python
# dashboard.py
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from db.session import SessionLocal
from db.models import Post, Comment

# 30초마다 자동 새로고침
st_autorefresh(interval=30000, key="refresh")

def render_inbox():
    st.header("📥 수신함 (Collected)")
    
    # 필터링 옵션
    col1, col2, col3 = st.columns(3)
    with col1:
        sites = st.multiselect("사이트", ["nate_pann", "nate_tok"], default=None)
    with col2:
        has_image = st.selectbox("이미지", ["전체", "있음", "없음"])
    with col3:
        sort_by = st.selectbox("정렬", ["최신순", "조회수순", "추천수순"])
    
    # 데이터 조회
    with SessionLocal() as db:
        query = db.query(Post).filter(Post.status == 'COLLECTED')
        
        # 필터 적용
        if sites:
            query = query.filter(Post.site_code.in_(sites))
        if has_image == "있음":
            query = query.filter(Post.images != '[]')
        elif has_image == "없음":
            query = query.filter(Post.images == '[]')
        
        # 정렬
        if sort_by == "조회수순":
            query = query.order_by(Post.stats['views'].desc())
        elif sort_by == "추천수순":
            query = query.order_by(Post.stats['likes'].desc())
        else:
            query = query.order_by(Post.created_at.desc())
        
        posts = query.limit(50).all()
    
    # 게시글 카드 렌더링
    for post in posts:
        with st.container():
            col1, col2 = st.columns([4, 1])
            
            with col1:
                st.markdown(f"### {post.title}")
                stats = json.loads(post.stats)
                st.caption(
                    f"🌐 {post.site_code} | "
                    f"👁️ {stats.get('views', 0):,} | "
                    f"👍 {stats.get('likes', 0):,}"
                )
                
                # 내용 미리보기
                with st.expander("내용 미리보기"):
                    st.write(post.content[:300] + "...")
                    
                    # 이미지 미리보기
                    images = json.loads(post.images)
                    if images:
                        st.image(images[0], width=200, caption="첫 번째 이미지")
                
                # 베스트 댓글
                comments = db.query(Comment).filter_by(post_id=post.id).all()
                if comments:
                    best = max(comments, key=lambda c: c.likes)
                    st.info(f"💬 **{best.author}:** {best.content[:100]}...")
            
            with col2:
                st.write("")  # 간격
                if st.button("✅ 승인", key=f"approve_{post.id}"):
                    with SessionLocal() as db:
                        db.query(Post).filter_by(id=post.id).update({
                            'status': 'APPROVED'
                        })
                        db.commit()
                    st.success("승인됨")
                    st.rerun()
                
                if st.button("❌ 거절", key=f"decline_{post.id}"):
                    with SessionLocal() as db:
                        db.query(Post).filter_by(id=post.id).update({
                            'status': 'DECLINED'
                        })
                        db.commit()
                    st.warning("거절됨")
                    st.rerun()
            
            st.divider()
```

#### 4.2.3 Tab 2: 진행 상태

```python
def render_progress():
    st.header("⚙️ 진행 상태")
    
    with SessionLocal() as db:
        counts = {
            'APPROVED': db.query(Post).filter_by(status='APPROVED').count(),
            'PROCESSING': db.query(Post).filter_by(status='PROCESSING').count(),
            'RENDERED': db.query(Post).filter_by(status='RENDERED').count(),
            'UPLOADED': db.query(Post).filter_by(status='UPLOADED').count(),
        }
    
    # 메트릭 표시
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("대기중", counts['APPROVED'])
    col2.metric("처리중", counts['PROCESSING'])
    col3.metric("렌더링 완료", counts['RENDERED'])
    col4.metric("업로드 완료", counts['UPLOADED'])
    
    # 처리 중인 항목 상세
    st.subheader("처리 중인 항목")
    with SessionLocal() as db:
        processing = db.query(Post).filter_by(status='PROCESSING').all()
        for post in processing:
            st.write(f"- {post.title} (ID: {post.id})")
```

#### 4.2.4 Tab 3: 갤러리

```python
def render_gallery():
    st.header("🎬 갤러리")
    
    with SessionLocal() as db:
        contents = db.query(Content).join(Post).filter(
            Post.status.in_(['RENDERED', 'UPLOADED'])
        ).order_by(Content.created_at.desc()).limit(20).all()
    
    # 3열 그리드
    cols = st.columns(3)
    for idx, content in enumerate(contents):
        with cols[idx % 3]:
            # 영상 재생
            video_path = f"/app/media/{content.video_path}"
            if os.path.exists(video_path):
                st.video(video_path)
                st.caption(content.post.title[:30] + "...")
                
                # 요약 텍스트
                with st.expander("요약"):
                    st.write(content.summary_text)
                
                # 업로드 버튼
                if content.post.status == 'RENDERED':
                    if st.button("📤 업로드", key=f"upload_{content.id}"):
                        # 업로더 트리거 (별도 구현)
                        trigger_upload(content.id)
```

---

### 4.3 AI 워커 (LLM/TTS/Render)

#### 4.3.1 VRAM 관리 핵심 패턴

**문제점:**
- RTX 3080 Ti 12GB는 LLM(4GB) + TTS(2GB) + FFmpeg(2GB) 동시 로드 불가능
- OOM 발생 시 컨테이너 크래시 → 전체 파이프라인 중단

**해결책:**
1. **순차 처리:** LLM → TTS → 렌더링 단계별 실행
2. **명시적 메모리 해제:** 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()`
3. **모델 언로드:** 다음 모델 로드 전 이전 모델 완전 삭제

```python
# ai_worker/gpu_manager.py
import torch
import gc
from contextlib import contextmanager
from typing import Literal

ModelType = Literal['llm', 'tts']

class GPUMemoryManager:
    def __init__(self):
        self.loaded_models = {}
        self.logger = logging.getLogger(__name__)
    
    @contextmanager
    def managed_inference(self, model_type: ModelType):
        """컨텍스트 매니저로 GPU 메모리 자동 관리"""
        try:
            # 사용 가능한 VRAM 확인
            available = self.get_available_vram()
            required = {'llm': 4.5, 'tts': 2.5}[model_type]
            
            if available < required:
                self.logger.warning(
                    f"Insufficient VRAM: {available:.1f}GB < {required}GB"
                )
                # 기존 모델 언로드
                self.unload_all()
            
            self.logger.info(f"Loading {model_type} model...")
            yield
            
        finally:
            # 추론 완료 후 즉시 메모리 해제
            torch.cuda.empty_cache()
            gc.collect()
            self.logger.info(f"Released {model_type} memory")
    
    def get_available_vram(self) -> float:
        """사용 가능한 VRAM (GB)"""
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return free / 1024**3
        return 0.0
    
    def unload_all(self):
        """모든 모델 언로드"""
        self.loaded_models.clear()
        torch.cuda.empty_cache()
        gc.collect()
        self.logger.info("Unloaded all models")
```

#### 4.3.2 AI 워커 메인 루프

```python
# ai_worker/main.py
import time
from ai_worker.gpu_manager import GPUMemoryManager
from ai_worker.llm import LLMSummarizer
from ai_worker.tts import TTSGenerator
from ai_worker.renderer import VideoRenderer

class AIWorker:
    def __init__(self):
        self.gpu_manager = GPUMemoryManager()
        self.llm = None
        self.tts = None
        self.renderer = VideoRenderer()
        self.logger = logging.getLogger(__name__)
    
    def poll_and_process(self):
        """DB에서 APPROVED 상태 폴링 (10초 간격)"""
        while True:
            try:
                with SessionLocal() as db:
                    # Race condition 방지: SELECT FOR UPDATE SKIP LOCKED
                    post = db.query(Post).filter_by(status='APPROVED').with_for_update(skip_locked=True).first()
                    
                    if post:
                        # 상태를 즉시 PROCESSING으로 변경 (다른 워커 중복 방지)
                        post.status = 'PROCESSING'
                        db.commit()
                        post_id = post.id
                
                if post:
                    self.logger.info(f"Processing post: {post_id}")
                    success = self.process_post(post_id)
                    
                    if success:
                        self.update_status(post_id, 'RENDERED')
                    else:
                        self.update_status(post_id, 'FAILED')
                
                time.sleep(10)  # 10초 대기
                
            except Exception as e:
                self.logger.exception("Polling error")
                time.sleep(30)  # 에러 시 더 긴 대기
    
    def process_post(self, post_id: int) -> bool:
        """
        3단계 파이프라인: LLM → TTS → Render
        각 단계 후 GPU 메모리 명시적 해제
        """
        try:
            # Step 1: LLM 요약
            with self.gpu_manager.managed_inference('llm'):
                if not self.llm:
                    self.llm = LLMSummarizer()
                summary = self.llm.generate_summary(post_id)
                self.logger.info(f"Summary generated: {len(summary)} chars")
            
            # LLM 모델 언로드 (VRAM 확보)
            del self.llm
            self.llm = None
            torch.cuda.empty_cache()
            gc.collect()
            
            # Step 2: TTS 생성
            with self.gpu_manager.managed_inference('tts'):
                if not self.tts:
                    self.tts = TTSGenerator()
                audio_path = self.tts.generate_audio(summary, post_id)
                self.logger.info(f"Audio saved: {audio_path}")
            
            # TTS 모델 언로드
            del self.tts
            self.tts = None
            torch.cuda.empty_cache()
            gc.collect()
            
            # Step 3: 영상 렌더링 (FFmpeg, NVENC 사용)
            video_path = self.renderer.create_video(post_id, summary, audio_path)
            self.logger.info(f"Video rendered: {video_path}")
            
            # DB에 결과 저장
            self.save_content(post_id, summary, audio_path, video_path)
            return True
            
        except Exception as e:
            self.logger.exception(f"Processing failed: {post_id}")
            return False
    
    def save_content(self, post_id: int, summary: str, audio: str, video: str):
        """처리 결과를 contents 테이블에 저장"""
        with SessionLocal() as db:
            content = Content(
                post_id=post_id,
                summary_text=summary,
                audio_path=audio,
                video_path=video
            )
            db.add(content)
            db.commit()
    
    def update_status(self, post_id: int, status: str):
        """게시글 상태 업데이트"""
        with SessionLocal() as db:
            db.query(Post).filter_by(id=post_id).update({'status': status})
            db.commit()

if __name__ == '__main__':
    worker = AIWorker()
    worker.poll_and_process()
```

#### 4.3.3 LLM 요약기

```python
# ai_worker/llm.py
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import torch

class LLMSummarizer:
    def __init__(self, model_name: str = "yanolja/EEVE-Korean-10.8B-v1.0"):
        self.logger = logging.getLogger(__name__)
        
        # 4-bit 양자화 설정 (VRAM 절약)
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True
        )
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True
        )
        self.logger.info(f"LLM loaded: {model_name} (4-bit)")
    
    def generate_summary(self, post_id: int) -> str:
        """게시글 + 베스트 댓글을 200자 쇼츠 대본으로 요약"""
        with SessionLocal() as db:
            post = db.query(Post).filter_by(id=post_id).first()
            comments = db.query(Comment).filter_by(post_id=post_id).order_by(Comment.likes.desc()).limit(1).all()
        
        # 프롬프트 구성
        prompt = f"""다음 커뮤니티 게시글을 유튜브 쇼츠용 대본으로 요약해주세요.
조건:
- 200자 이내
- 구어체 사용
- 핵심 내용만 추출
- 베스트 댓글 반응 포함

제목: {post.title}

본문:
{post.content[:500]}

베스트 댓글:
{comments[0].content if comments else '없음'}

쇼츠 대본:"""
        
        # 추론
        inputs = self.tokenizer(prompt, return_tensors="pt").to("cuda")
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.7,
                do_sample=True,
                top_p=0.9
            )
        
        summary = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # 프롬프트 부분 제거
        summary = summary.split("쇼츠 대본:")[-1].strip()
        
        # 200자 제한
        return summary[:200]
```

#### 4.3.4 TTS 생성기

```python
# ai_worker/tts.py
from pathlib import Path
import edge_tts
import asyncio

class TTSGenerator:
    def __init__(self, engine: str = "edge-tts"):
        self.engine = engine
        self.logger = logging.getLogger(__name__)
    
    def generate_audio(self, text: str, post_id: int) -> str:
        """텍스트를 음성으로 변환"""
        output_path = Path(f"/app/media/audio/post_{post_id}.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self.engine == "edge-tts":
            asyncio.run(self._edge_tts(text, output_path))
        elif self.engine == "kokoro":
            self._kokoro_tts(text, output_path)
        else:
            raise ValueError(f"Unknown TTS engine: {self.engine}")
        
        self.logger.info(f"TTS generated: {output_path}")
        return str(output_path)
    
    async def _edge_tts(self, text: str, output_path: Path):
        """Edge-TTS (무료, 빠름)"""
        communicate = edge_tts.Communicate(text, "ko-KR-SunHiNeural")
        await communicate.save(str(output_path))
    
    def _kokoro_tts(self, text: str, output_path: Path):
        """Kokoro-82M (로컬, 고품질)"""
        # TODO: Kokoro 모델 로드 및 추론
        pass
```

#### 4.3.5 영상 렌더러

```python
# ai_worker/renderer.py
from moviepy.editor import (
    VideoFileClip, ImageClip, AudioFileClip, TextClip, CompositeVideoClip
)
from pathlib import Path
import json

class VideoRenderer:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.backgrounds = list(Path("/app/assets/backgrounds").glob("*.mp4"))
    
    def create_video(self, post_id: int, summary: str, audio_path: str) -> str:
        """쇼츠 영상 생성 (9:16 비율)"""
        # 오디오 길이 측정
        audio = AudioFileClip(audio_path)
        duration = audio.duration
        
        # 게시글 이미지 확인
        with SessionLocal() as db:
            post = db.query(Post).filter_by(id=post_id).first()
            images = json.loads(post.images)
        
        if images:
            # 이미지 기반 슬라이드 쇼
            video = self._create_slideshow(images, duration)
        else:
            # 배경 영상 사용
            video = self._create_background_video(duration)
        
        # 자막 추가
        video_with_text = self._add_subtitles(video, summary)
        
        # 오디오 합성 (TTS + BGM)
        final_video = video_with_text.set_audio(audio)
        
        # NVENC 인코딩 (GPU 가속)
        output_path = Path(f"/app/media/videos/post_{post_id}.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        final_video.write_videofile(
            str(output_path),
            codec='h264_nvenc',  # 필수: GPU 가속
            audio_codec='aac',
            fps=30,
            preset='fast',
            ffmpeg_params=['-gpu', '0']  # RTX 3080 Ti 지정
        )
        
        self.logger.info(f"Video rendered: {output_path}")
        return str(output_path)
    
    def _create_slideshow(self, image_urls: list, duration: float) -> VideoFileClip:
        """이미지 슬라이드 쇼 (Ken Burns 효과)"""
        clips = []
        per_image_duration = duration / len(image_urls)
        
        for url in image_urls:
            # 이미지 다운로드 및 9:16 크롭
            img_path = self._download_and_crop(url)
            
            # Ken Burns 효과 (줌인)
            clip = (ImageClip(img_path, duration=per_image_duration)
                    .resize(height=1920)
                    .set_position('center')
                    .crossfadein(0.5))
            clips.append(clip)
        
        return concatenate_videoclips(clips, method="compose")
    
    def _create_background_video(self, duration: float) -> VideoFileClip:
        """배경 영상 반복/자름"""
        bg = VideoFileClip(str(self.backgrounds[0]))
        
        if bg.duration < duration:
            # 반복
            loops = int(duration / bg.duration) + 1
            bg = concatenate_videoclips([bg] * loops)
        
        # 길이 자름
        return bg.subclip(0, duration)
    
    def _add_subtitles(self, video: VideoFileClip, text: str) -> CompositeVideoClip:
        """자막 추가 (화면 중앙)"""
        txt_clip = (TextClip(
            text,
            fontsize=60,
            color='white',
            font='NanumGothic-Bold',  # 한글 폰트
            size=(1080, None),
            method='caption',
            align='center'
        ).set_position('center')
          .set_duration(video.duration))
        
        return CompositeVideoClip([video, txt_clip])
    
    def _download_and_crop(self, url: str) -> str:
        """이미지 다운로드 및 9:16 크롭"""
        # TODO: requests로 다운로드 후 PIL로 크롭
        pass
```

---

### 4.4 업로더 (YouTube)

#### 4.4.1 확장 가능한 업로더 패턴

```python
# uploaders/base.py
from abc import ABC, abstractmethod

class BaseUploader(ABC):
    @abstractmethod
    def upload(self, video_path: str, metadata: dict) -> dict:
        """
        영상 업로드
        
        Args:
            video_path: 영상 파일 경로
            metadata: {title, description, tags, privacy}
        
        Returns:
            {platform_id, url}
        """
        pass
```

#### 4.4.2 YouTube 업로더

```python
# uploaders/youtube.py
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

class YouTubeUploader(BaseUploader):
    def __init__(self, credentials_path: str):
        self.creds = Credentials.from_authorized_user_file(credentials_path)
        self.youtube = build('youtube', 'v3', credentials=self.creds)
    
    def upload(self, video_path: str, metadata: dict) -> dict:
        """YouTube Shorts 업로드"""
        body = {
            'snippet': {
                'title': metadata['title'][:100],  # 100자 제한
                'description': metadata['description'],
                'tags': metadata.get('tags', []),
                'categoryId': '22'  # People & Blogs
            },
            'status': {
                'privacyStatus': metadata.get('privacy', 'unlisted'),
                'selfDeclaredMadeForKids': False
            }
        }
        
        media = MediaFileUpload(
            video_path,
            chunksize=-1,
            resumable=True,
            mimetype='video/mp4'
        )
        
        request = self.youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )
        
        response = request.execute()
        
        return {
            'platform_id': response['id'],
            'url': f"https://youtube.com/shorts/{response['id']}"
        }
```

---

## 5. Docker 구성

### 5.1 docker-compose.yml

```yaml
version: '3.8'

services:
  db:
    image: mariadb:11
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
      MYSQL_DATABASE: wagglebot
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASSWORD}
    volumes:
      - mariadb_data:/var/lib/mysql
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  crawler:
    build: .
    command: python scheduler.py
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./config:/app/config:ro
      - ./crawlers:/app/crawlers
    environment:
      DB_HOST: db
      DB_USER: ${DB_USER}
      DB_PASSWORD: ${DB_PASSWORD}
    restart: unless-stopped

  ai_worker:
    build: .
    command: python ai_worker/main.py
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./media:/app/media
      - ./assets:/app/assets:ro
      - ./config:/app/config:ro
      - models_cache:/root/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      CUDA_VISIBLE_DEVICES: 0
      DB_HOST: db
    restart: unless-stopped

  dashboard:
    build: .
    command: streamlit run dashboard.py --server.port=8501 --server.address=0.0.0.0
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8501:8501"
    volumes:
      - ./media:/app/media:ro
    environment:
      DB_HOST: db
    restart: unless-stopped

volumes:
  mariadb_data:
  models_cache:
```

### 5.2 Dockerfile

```dockerfile
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

# Python 3.12 설치
RUN apt-get update && apt-get install -y \
    python3.12 python3-pip \
    ffmpeg \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 복사
COPY . .

CMD ["python3", "-u", "main.py"]
```

---

## 6. 에러 핸들링 및 복구

### 6.1 재시도 로직

```python
# utils/retry.py
import time
import logging
from functools import wraps

def retry(max_attempts=3, backoff_factor=2, exceptions=(Exception,)):
    """재시도 데코레이터"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__)
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(f"Final attempt failed: {func.__name__}")
                        raise
                    
                    wait_time = backoff_factor ** attempt
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
        
        return wrapper
    return decorator

# 사용 예시
@retry(max_attempts=3, exceptions=(requests.RequestException,))
def fetch_post(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text
```

### 6.2 에러 로깅

```python
# utils/logger.py
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name: str, log_file: str = None):
    """구조화된 로거 생성"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # 포맷
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 파일 핸들러 (10MB 로테이션)
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger
```

---

## 7. 테스트

### 7.1 단위 테스트

```python
# tests/test_crawler.py
import pytest
from crawlers.nate import NatePannCrawler

@pytest.fixture
def crawler():
    return NatePannCrawler(site_code='nate_pann')

def test_fetch_listing(crawler):
    posts = crawler.fetch_listing(page=1)
    assert len(posts) > 0
    assert 'url' in posts[0]
    assert 'title' in posts[0]

def test_parse_post(crawler):
    # 실제 게시글 URL (테스트 시점에 유효한 것)
    url = "https://pann.nate.com/talk/123456"
    data = crawler.parse_post(url)
    
    assert 'origin_id' in data
    assert 'title' in data
    assert 'content' in data
    assert isinstance(data['images'], list)
```

### 7.2 통합 테스트

```python
# tests/test_pipeline.py
def test_end_to_end_pipeline(db_session):
    """크롤링 → 승인 → AI 처리 → 업로드 전체 파이프라인 테스트"""
    
    # 1. 크롤러 실행
    crawler = NatePannCrawler('nate_pann')
    crawler.run(max_pages=1)
    
    # 2. DB 확인
    post = db_session.query(Post).filter_by(status='COLLECTED').first()
    assert post is not None
    
    # 3. 승인
    post.status = 'APPROVED'
    db_session.commit()
    
    # 4. AI 워커 실행 (모킹)
    worker = AIWorker()
    success = worker.process_post(post.id)
    
    assert success
    assert post.status == 'RENDERED'
    
    # 5. 결과 확인
    content = db_session.query(Content).filter_by(post_id=post.id).first()
    assert content is not None
    assert content.video_path is not None
```

---

## 8. 성능 최적화

### 8.1 DB 인덱스

```sql
-- 크롤러 필터링
CREATE INDEX idx_site_status ON posts(site_code, status);

-- AI 워커 폴링
CREATE INDEX idx_status_created ON posts(status, created_at);

-- 대시보드 정렬
CREATE INDEX idx_stats_views ON posts((stats->>'$.views'));
CREATE INDEX idx_stats_likes ON posts((stats->>'$.likes'));
```

### 8.2 캐싱 전략

```python
# utils/cache.py
from functools import lru_cache
from typing import List

@lru_cache(maxsize=100)
def get_background_videos() -> List[str]:
    """배경 영상 목록 캐싱 (파일 시스템 I/O 감소)"""
    return list(Path("/app/assets/backgrounds").glob("*.mp4"))
```

---

## 9. 모니터링 및 알림

### 9.1 헬스체크

```python
# utils/health.py
import psutil
import GPUtil

def check_system_health() -> dict:
    """시스템 상태 체크"""
    gpus = GPUtil.getGPUs()
    
    return {
        'cpu_percent': psutil.cpu_percent(interval=1),
        'memory_percent': psutil.virtual_memory().percent,
        'disk_percent': psutil.disk_usage('/').percent,
        'gpu_temp': gpus[0].temperature if gpus else None,
        'gpu_memory_used': gpus[0].memoryUsed if gpus else None,
        'gpu_memory_total': gpus[0].memoryTotal if gpus else None,
    }

def send_alert_if_needed(health: dict):
    """임계값 초과 시 알림"""
    if health['gpu_temp'] > 80:
        logging.critical(f"GPU 과열: {health['gpu_temp']}°C")
    
    if health['disk_percent'] > 90:
        logging.critical(f"디스크 부족: {health['disk_percent']}%")
```

### 9.2 프로메테우스 메트릭 (선택사항)

```python
# utils/metrics.py
from prometheus_client import Counter, Gauge

posts_crawled = Counter('posts_crawled_total', 'Total posts crawled')
posts_processed = Counter('posts_processed_total', 'Total posts processed')
gpu_memory_usage = Gauge('gpu_memory_usage_bytes', 'GPU memory usage')
processing_time = Gauge('processing_time_seconds', 'Time to process one post')
```

---

## 10. 배포 및 운영

### 10.1 초기 설정

```bash
# 1. 환경 변수 설정
cp .env.example .env
# .env 파일 편집 (DB 비밀번호 등)

# 2. Docker 빌드 및 실행
docker-compose build
docker-compose up -d

# 3. DB 초기화 확인
docker-compose logs db | grep "ready for connections"

# 4. 대시보드 접속
open http://localhost:8501
```

### 10.2 백업 스크립트

```bash
#!/bin/bash
# scripts/backup.sh

BACKUP_DIR="/mnt/backup/wagglebot"
DATE=$(date +%Y%m%d_%H%M%S)

# DB 백업
docker exec wagglebot_db mysqldump -u root -p${DB_ROOT_PASSWORD} wagglebot \
  > ${BACKUP_DIR}/db_${DATE}.sql

# 미디어 파일 백업
rsync -av --progress ./media/ ${BACKUP_DIR}/media_${DATE}/

# 7일 이상 된 백업 삭제
find ${BACKUP_DIR} -type f -mtime +7 -delete

echo "Backup completed: ${DATE}"
```

---

## 11. 확장 로드맵

### Phase 1 (완료)
- ✅ 크롤러 인프라 (BaseCrawler)
- ✅ DB 스키마
- ✅ Streamlit 대시보드

### Phase 2 (진행 중)
- 🚧 AI 워커 (LLM/TTS)
- 🚧 영상 렌더링
- 🚧 VRAM 관리

### Phase 3 (계획)
- 📋 YouTube 업로더
- 📋 멀티 플랫폼 (TikTok, Instagram)
- 📋 고급 영상 효과 (Ken Burns, 전환)
- 📋 분석 대시보드

---

## 12. 트러블슈팅

### 12.1 OOM (Out of Memory)
**증상:** CUDA out of memory 에러  
**해결:** `gpu_manager.unload_all()` 호출, 양자화 확인

### 12.2 FFmpeg 인코딩 실패
**증상:** h264_nvenc 코덱 사용 불가  
**해결:** `nvidia-smi` 확인, Docker GPU 매핑 재시작

### 12.3 DB 커넥션 풀 고갈
**증상:** Too many connections  
**해결:** `with SessionLocal()` 패턴 준수 확인

---

## 부록: 참조 자료

- **SQLAlchemy ORM:** https://docs.sqlalchemy.org/
- **FFmpeg NVENC:** https://docs.nvidia.com/video-technologies/
- **Transformers 양자화:** https://huggingface.co/docs/transformers/quantization
- **MoviePy:** https://zulko.github.io/moviepy/
