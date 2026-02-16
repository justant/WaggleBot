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

## 3. 데이터베이스 스키마

### 3.1 테이블: posts

**인덱스:**
- `site_code` (크롤러 필터링)
- `status` (AI 워커 폴링)
- `origin_id` UNIQUE (중복 방지)

### 3.2 테이블: comments

**용도:** 베스트 댓글을 LLM 요약에 포함

### 3.3 테이블: contents

## 4. 모듈별 상세 명세

### 4.1 크롤러 (Crawler)

#### 4.1.1 설계 원칙: 확장성

**목표:** 100개 이상의 커뮤니티 사이트 지원  
**패턴:** BaseCrawler 추상 클래스 + 플러그인 레지스트리

#### 4.1.2 BaseCrawler 인터페이스


### 4.2 관리자 대시보드 (Streamlit)

#### 4.2.1 UI 구조

**3개 탭:**
1. **수신함 (Inbox):** COLLECTED 상태 게시글 승인/거절
2. **진행 상태 (Progress):** PROCESSING/RENDERED/UPLOADED 모니터링
3. **갤러리 (Gallery):** 완성된 영상 재생

#### 4.2.2 Tab 1: 수신함 구현

#### 4.2.3 Tab 2: 진행 상태

### 4.3 AI 워커 (LLM/TTS/Render)

#### 4.3.1 VRAM 관리 핵심 패턴

**문제점:**
- RTX 3080 Ti 12GB는 LLM(4GB) + TTS(2GB) + FFmpeg(2GB) 동시 로드 불가능
- OOM 발생 시 컨테이너 크래시 → 전체 파이프라인 중단

**해결책:**
1. **순차 처리:** LLM → TTS → 렌더링 단계별 실행
2. **명시적 메모리 해제:** 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()`
3. **모델 언로드:** 다음 모델 로드 전 이전 모델 완전 삭제

#### 4.3.2 AI 워커 메인 루프

#### 4.3.3 LLM 요약기

#### 4.3.4 TTS 생성기

#### 4.3.5 영상 렌더러

### 4.4 업로더 (YouTube)

#### 4.4.1 확장 가능한 업로더 패턴

#### 4.4.2 YouTube 업로더

## 5. Docker 구성

### 5.1 docker-compose.yml

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

### 6.2 에러 로깅

## 7. 테스트

### 7.1 단위 테스트

### 7.2 통합 테스트

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

### 9.2 프로메테우스 메트릭 (선택사항)

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
