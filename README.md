# WaggleBot 🤖

> 커뮤니티 인기 게시글을 자동으로 수집하여 유튜브 쇼츠 영상으로 변환하는 AI 파이프라인

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20RTX%203080%20Ti-76B900.svg)](https://www.nvidia.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 📌 프로젝트 개요

WaggleBot은 커뮤니티 게시글을 크롤링하고, LLM으로 요약한 뒤, TTS와 FFmpeg를 이용해 쇼츠 영상(9:16)을 자동 생성하는 완전 자동화 시스템입니다.

### 🎯 주요 기능

- 🕷️ **자동 크롤링**: 네이트판 등 커뮤니티 사이트에서 인기 게시글 수집
- 🧠 **AI 요약**: 로컬 LLM을 사용한 쇼츠 대본 생성 (200자 이내)
- 🎙️ **TTS 음성 합성**: Kokoro-82M, GPT-SoVITS, Edge-TTS 지원
- 🎬 **영상 렌더링**: FFmpeg + NVENC GPU 가속 (20배 빠른 인코딩)
- 📊 **관리 대시보드**: Streamlit 기반 웹 UI로 게시글 승인/거절
- 📤 **자동 업로드**: 유튜브 쇼츠 자동 업로드 (Phase 3)

### 🏗️ 시스템 플로우

```
커뮤니티 크롤링 → MariaDB 저장 → Streamlit 검수 → AI 워커(LLM/TTS) → FFmpeg 렌더링 → YouTube 업로드
```

### 🛠️ 기술 스택

| 분류 | 기술 |
|------|------|
| **언어** | Python 3.12 |
| **AI** | EEVE-Korean-10.8B (4-bit), Kokoro-82M TTS |
| **DB** | MariaDB 11.x + SQLAlchemy ORM |
| **영상** | FFmpeg (h264_nvenc 코덱) |
| **웹** | Streamlit Dashboard |
| **인프라** | Docker Compose (GPU 지원) |

---

## 💻 시스템 요구사항

### 필수 하드웨어
- **GPU**: NVIDIA RTX 3080 Ti (12GB VRAM) 이상
- **RAM**: 16GB 이상
- **저장공간**: SSD 50GB 이상

### 필수 소프트웨어
- **OS**: Windows 10/11
- **WSL2**: Ubuntu 22.04
- **Docker**: Docker Desktop for Windows (WSL2 백엔드)
- **GPU 드라이버**: NVIDIA 드라이버 525.xx 이상
- **기타**: Git, NVIDIA Container Toolkit

---

## 🚀 설치 가이드

### 1️⃣ WSL2 및 Ubuntu 설치

```powershell
# PowerShell 관리자 권한으로 실행

# WSL 활성화
wsl --install

# 재부팅 후 Ubuntu 22.04 설치
wsl --install -d Ubuntu-22.04

# 설치 확인
wsl -l -v
# 출력: Ubuntu-22.04  Running  2
```

### 2️⃣ Docker Desktop 설치

1. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop) 다운로드
2. 설치 후 실행
3. **Settings** → **General** → **Use WSL 2 based engine** 체크
4. **Settings** → **Resources** → **WSL Integration** → Ubuntu-22.04 활성화

### 3️⃣ NVIDIA GPU 드라이버 설치

```bash
# Windows에서 NVIDIA 드라이버 설치
# https://www.nvidia.com/Download/index.aspx

# WSL에서 확인 (CUDA Toolkit 설치 불필요)
nvidia-smi

# 출력 예시:
# +-----------------------------------------------------------------------------+
# | NVIDIA-SMI 525.xx.xx    Driver Version: 525.xx.xx    CUDA Version: 12.x    |
# +-----------------------------------------------------------------------------+
```

### 4️⃣ NVIDIA Container Toolkit 설치

```bash
# WSL Ubuntu 터미널에서 실행

# Docker GPG 키 및 저장소 추가
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 설치
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Docker 재시작
sudo systemctl restart docker

# GPU 접근 테스트
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### 5️⃣ 프로젝트 클론 및 설정

```bash
# WSL Ubuntu 터미널에서 실행
cd ~
git clone https://github.com/justant/WaggleBot.git
cd WaggleBot

# 환경 변수 파일 생성
cp .env.example .env

# .env 파일 편집 (nano 또는 다른 에디터 사용)
nano .env
```

**.env 파일 설정:**
```env
# Database
DB_ROOT_PASSWORD=your_secure_password_here
DB_USER=wagglebot
DB_PASSWORD=another_secure_password

# Hugging Face (LLM 모델 다운로드용)
HF_TOKEN=hf_your_token_here  # https://huggingface.co/settings/tokens

# YouTube API (Phase 3에서 사용)
YOUTUBE_API_KEY=your_youtube_api_key
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
```

**Hugging Face 토큰 발급 방법:**
1. https://huggingface.co/ 회원가입/로그인
2. Settings → Access Tokens → New token 생성
3. Token을 `.env` 파일의 `HF_TOKEN`에 입력

### 6️⃣ .gitignore 설정

```bash
# 민감한 파일을 Git 추적에서 제외
cat >> .gitignore << 'EOF'

# 환경 파일
.env

# Python 캐시
__pycache__/
*.pyc
*.pyo

# Docker 볼륨 데이터
media/
models_cache/

# Claude Code 개인 설정
.claude/settings.local.json
EOF
```

### 7️⃣ Docker 컨테이너 실행

```bash
# Docker 컨테이너 빌드 및 시작
docker-compose up -d

# 서비스 상태 확인
docker-compose ps

# 출력 예시:
#        Name                      State           Ports
# --------------------------------------------------------------
# wagglebot_db          Up      3306/tcp
# wagglebot_crawler     Up
# wagglebot_ai_worker   Up (healthy)
# wagglebot_dashboard   Up      0.0.0.0:8501->8501/tcp
```

### 8️⃣ 설치 확인

```bash
# 데이터베이스 연결 확인
docker exec wagglebot_db mysqladmin ping -h localhost

# AI 워커 로그 확인
docker-compose logs ai_worker | tail -20

# 대시보드 접속
# 브라우저에서 http://localhost:8501 열기
```

---

## 🎮 사용법

### 1. 크롤러 실행

```bash
# 1회 실행 (테스트)
docker exec wagglebot_crawler python main.py --once

# 스케줄 실행 (1시간마다 자동) - 이미 실행 중
# docker-compose.yml에 정의됨
```

### 2. 대시보드 사용

http://localhost:8501 접속

#### 📥 수신함 탭
- **필터링**: 사이트별, 이미지 유무, 정렬 기준
- **게시글 확인**: 제목, 본문 미리보기, 베스트 댓글
- **[승인]** 버튼: AI 워커가 자동으로 영상 생성 시작
- **[거절]** 버튼: 해당 게시글 제외

#### ⚙️ 진행 상태 탭
- **대기중**: APPROVED 상태 (AI 워커 처리 대기)
- **처리중**: PROCESSING 상태 (LLM 요약/TTS 생성 중)
- **렌더링 완료**: RENDERED 상태 (영상 생성 완료)
- **업로드 완료**: UPLOADED 상태 (YouTube 업로드 완료)

#### 🎬 갤러리 탭
- 완성된 영상 재생
- 다운로드 및 공유

### 3. 영상 생성 과정

```
승인 → LLM 요약(30초) → TTS 생성(20초) → 영상 렌더링(1-2분) → 완료
```

**예상 소요 시간**: 게시글 1개당 약 2-5분

### 4. 로그 모니터링

```bash
# 실시간 로그 확인
docker-compose logs -f ai_worker

# 특정 서비스 로그
docker-compose logs -f crawler
docker-compose logs -f dashboard
docker-compose logs -f db

# 에러만 필터링
docker-compose logs ai_worker | grep ERROR
```

---

## 🔧 문제 해결

### 문제 1: GPU가 Docker에서 인식되지 않음

**증상:**
```
RuntimeError: CUDA out of memory
또는
nvidia-smi: command not found
```

**해결:**
```bash
# 1. Windows에서 NVIDIA 드라이버 확인
# 제어판 → 프로그램 추가/제거 → NVIDIA Graphics Driver

# 2. WSL에서 nvidia-smi 확인
nvidia-smi

# 3. Docker GPU 테스트
docker run --rm --gpus all nvidia/cuda:12.1.0-base nvidia-smi

# 4. docker-compose.yml 확인
cat docker-compose.yml | grep -A 5 "deploy:"
# deploy.resources.reservations.devices가 있어야 함

# 5. Docker Desktop 재시작
```

### 문제 2: MariaDB 접속 실패

**증상:**
```
Can't connect to MySQL server on 'db'
```

**해결:**
```bash
# 1. DB 컨테이너 상태 확인
docker-compose ps db

# 2. 헬스체크 확인
docker inspect wagglebot_db | grep -A 10 Health

# 3. .env 파일 비밀번호 확인
cat .env | grep DB_PASSWORD

# 4. DB 컨테이너 재시작
docker-compose restart db

# 5. 초기화 (주의: 모든 데이터 삭제)
docker-compose down -v
docker-compose up -d
```

### 문제 3: OOM (Out of Memory) 에러

**증상:**
```
CUDA out of memory. Tried to allocate 2.00 GiB
```

**해결:**
```bash
# 1. GPU 메모리 사용량 확인
nvidia-smi

# 2. 다른 GPU 사용 프로그램 종료 (Chrome, 게임 등)

# 3. ai_worker 재시작
docker-compose restart ai_worker

# 4. VRAM 사용량 줄이기 (config/settings.py 수정)
# LLM_MAX_LENGTH = 512  # 기본값: 1024
# TTS_BATCH_SIZE = 1     # 기본값: 4
```

### 문제 4: FFmpeg 인코딩 실패

**증상:**
```
Unknown encoder 'h264_nvenc'
```

**해결:**
```bash
# 1. FFmpeg NVENC 지원 확인
docker exec wagglebot_ai_worker ffmpeg -encoders | grep nvenc

# 2. GPU 드라이버 업데이트 (Windows)

# 3. h264_nvenc → libx264로 임시 변경 (느림)
# ai_worker/renderer.py 수정:
# codec='libx264'  # h264_nvenc 대신
```

### 문제 5: 크롤러가 게시글을 수집하지 못함

**증상:**
- 대시보드 수신함이 비어있음

**해결:**
```bash
# 1. 크롤러 로그 확인
docker-compose logs crawler | tail -50

# 2. 네트워크 연결 확인
docker exec wagglebot_crawler ping -c 3 pann.nate.com

# 3. 수동 크롤링 테스트
docker exec wagglebot_crawler python main.py --once

# 4. 사이트 구조 변경 여부 확인 (crawlers/nate.py 수정 필요)
```

### 문제 6: 대시보드가 열리지 않음

**증상:**
- http://localhost:8501 접속 불가

**해결:**
```bash
# 1. 대시보드 컨테이너 상태 확인
docker-compose ps dashboard

# 2. 포트 충돌 확인
netstat -ano | findstr :8501

# 3. 대시보드 로그 확인
docker-compose logs dashboard

# 4. 대시보드 재시작
docker-compose restart dashboard
```

---

## 📂 프로젝트 구조

```
WaggleBot/
├── 📄 CLAUDE.md                   # Claude Code 사용 시 개발 규칙
├── 📄 README.md                   # 이 파일
├── 📄 docker-compose.yml          # Docker 구성
├── 📄 requirements.txt            # Python 의존성
├── 📁 arch/                       # 아키텍처 문서
│   └── dev_spec.md                # 상세 기술 명세서
├── 📁 crawlers/                   # 크롤러 모듈
│   ├── base.py                    # BaseCrawler 추상 클래스
│   └── nate.py                    # 네이트판 크롤러
├── 📁 db/                         # 데이터베이스
│   ├── models.py                  # SQLAlchemy 모델
│   └── session.py                 # DB 세션 관리
├── 📁 ai_worker/                  # AI 워커
│   ├── main.py                    # DB 폴링 메인 루프
│   ├── llm.py                     # LLM 요약기
│   ├── tts.py                     # TTS 생성기
│   └── renderer.py                # FFmpeg 영상 렌더러
├── 📁 assets/                     # 정적 리소스
│   ├── backgrounds/               # 9:16 배경 영상
│   └── fonts/                     # 한글 폰트
├── 📁 config/                     # 설정
│   └── settings.py                # 중앙화된 설정
├── 📁 monitoring/                 # 모니터링 시스템
│   ├── alerting.py                # 알림 관리자
│   └── daemon.py                  # 헬스체크 데몬
├── 📄 main.py                     # 크롤러 진입점
├── 📄 scheduler.py                # Cron 스케줄러
└── 📄 dashboard.py                # Streamlit 대시보드
```

---

## 📊 운영 및 모니터링

### 모니터링 시스템

WaggleBot은 시스템 헬스를 자동으로 모니터링하고, 문제 발생 시 알림을 전송합니다.

#### 모니터링 항목

- **CPU/메모리 사용률**: 시스템 리소스 모니터링
- **디스크 공간**: 영상 저장 공간 확인
- **GPU 온도**: 과열 방지 (경고: 75°C, 위험: 80°C)
- **DB 연결**: 데이터베이스 상태 체크

#### 알림 설정

**.env 파일 설정:**

```bash
# 모니터링 활성화
MONITORING_ENABLED=true
HEALTH_CHECK_INTERVAL=300  # 5분마다 체크

# 임계값 설정
GPU_TEMP_WARNING=75
GPU_TEMP_CRITICAL=80
DISK_USAGE_WARNING=80
DISK_USAGE_CRITICAL=90

# 이메일 알림 (Gmail 예시)
EMAIL_ALERTS_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password  # Gmail 앱 비밀번호
ALERT_EMAIL_TO=admin@example.com,dev@example.com

# 슬랙 알림
SLACK_ALERTS_ENABLED=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

**Gmail 앱 비밀번호 생성:**
1. Google 계정 → 보안 → 2단계 인증 활성화
2. 앱 비밀번호 생성 → "메일" 선택
3. 생성된 16자 비밀번호를 `SMTP_PASSWORD`에 입력

#### 모니터링 서비스 시작

```bash
# Docker Compose로 시작
docker-compose up -d monitoring

# 로그 확인
docker-compose logs -f monitoring

# 수동 테스트
python test_monitoring.py
```

#### 모니터링 로그 예시

```
2025-02-15 12:00:00 - monitoring.alerting - INFO - Starting health check...
2025-02-15 12:00:01 - monitoring.alerting - INFO - CPU: 45.2% | MEM: 62.1% | DISK: 55.3% | GPU: 68°C | DB: OK
2025-02-15 12:00:01 - monitoring.alerting - INFO - Health check OK
```

**알림이 전송되는 경우:**
- ⚠️ **WARNING**: GPU 75°C 이상, 디스크 80% 이상 (로그만)
- 🚨 **CRITICAL**: GPU 80°C 이상, 디스크 90% 이상, DB 연결 실패 (이메일/슬랙 전송)

---

## 🛡️ 에러 핸들링 및 복구

### 견고한 에러 처리 시스템

WaggleBot은 AI 워커 처리 중 발생할 수 있는 다양한 에러를 자동으로 분류하고 복구합니다.

#### 에러 타입 분류

- **LLM_ERROR**: LLM 요약 실패 (재시도 불가 - 즉시 FAILED 처리)
- **TTS_ERROR**: TTS 음성 생성 실패 (재시도 가능)
- **RENDER_ERROR**: 영상 렌더링 실패 (재시도 가능)
- **NETWORK_ERROR**: 네트워크 오류 (재시도 가능)
- **RESOURCE_ERROR**: VRAM/디스크 부족 (재시도 가능)
- **UNKNOWN_ERROR**: 알 수 없는 오류 (재시도 가능)

#### 재시도 정책 (Exponential Backoff)

```python
# 기본 설정 (.env)
MAX_RETRY_COUNT=3          # 최대 3회 재시도
BACKOFF_FACTOR=2.0         # 2배씩 증가
INITIAL_DELAY=5.0          # 첫 재시도 5초 후

# 재시도 타임라인 예시
# 1차 시도 실패 → 5초 대기
# 2차 시도 실패 → 10초 대기
# 3차 시도 실패 → 20초 대기
# → FAILED 상태로 전환
```

#### 처리 흐름

```
APPROVED → PROCESSING
    ↓
[Step 1] LLM 요약 생성
    ├─ 성공 → Step 2
    └─ 실패 → 즉시 FAILED (재시도 불가)
    ↓
[Step 2] TTS 음성 생성
    ├─ 성공 → Step 3
    └─ 실패 → Backoff 후 재시도
    ↓
[Step 3] 영상 렌더링
    ├─ 성공 → RENDERED
    └─ 실패 → Backoff 후 재시도
    ↓
최대 재시도 초과 → FAILED
```

#### 에러 로그 확인

**failures.log 파일:**
```bash
# 에러 로그 위치
media/logs/failures.log

# 실시간 모니터링
tail -f media/logs/failures.log

# 예시
2025-02-15T12:00:00 | post_id=123 | failure_type=tts_error | attempt=1 | error=TTS synthesis failed
2025-02-15T12:00:10 | post_id=123 | failure_type=tts_error | attempt=2 | error=TTS synthesis failed
```

**대시보드에서 확인:**
- **진행현황 탭**: FAILED 상태 게시글 확인
- **재시도 버튼**: 실패한 게시글을 APPROVED로 되돌려 재처리

#### 테스트

```bash
# 에러 핸들링 테스트 실행
python test_error_handling.py

# 예상 출력
✓ LLM 에러 분류 성공
✓ TTS 에러 분류 성공
✓ Backoff 계산 성공
✓ LLM 에러 시 즉시 중단 확인
✓ TTS 에러 재시도 로직 확인
```

#### 수동 복구

**실패한 게시글 재처리:**
```python
# Python 스크립트
from db.session import SessionLocal
from db.models import Post, PostStatus

with SessionLocal() as session:
    # 실패한 게시글 조회
    failed_post = session.query(Post).filter_by(status=PostStatus.FAILED).first()

    # APPROVED로 변경하여 재시도 큐에 추가
    failed_post.status = PostStatus.APPROVED
    failed_post.retry_count = 0  # 재시도 카운트 초기화
    session.commit()
```

**또는 대시보드에서:**
1. 진행현황 탭 이동
2. FAILED 섹션에서 게시글 확인
3. "🔄 재시도" 버튼 클릭

---

## 👨‍💻 개발 가이드

### 기본 개발 환경

```bash
# Python 의존성 설치 (로컬 개발 시)
pip install -r requirements.txt

# 데이터베이스 초기화
python -c "from db.session import init_db; init_db()"

# 크롤러 테스트
python main.py --once

# 대시보드 실행 (로컬)
streamlit run dashboard.py
```

### 새 크롤러 추가하기

```python
# crawlers/reddit.py
from crawlers.base import BaseCrawler

class RedditCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(site_code='reddit')
    
    def fetch_listing(self, page: int = 1) -> list[dict]:
        """게시글 목록 가져오기"""
        # Reddit API 호출
        pass
    
    def parse_post(self, url: str) -> dict:
        """개별 게시글 파싱"""
        # 제목, 본문, 이미지, 통계 추출
        return {
            'origin_id': '...',
            'title': '...',
            'content': '...',
            'images': [...],
            'stats': {'views': 0, 'likes': 0}
        }

# main.py에 등록
from crawlers.reddit import RedditCrawler
crawler = RedditCrawler()
crawler.run(max_pages=3)
```

자세한 내용은 [arch/dev_spec.md](arch/dev_spec.md) 참조.

### (선택) Claude Code로 개발하기

> **Claude Code**는 AI 페어 프로그래밍 도구입니다. 사용은 선택사항이지만, 자동 커밋 메시지 생성, 코드 리뷰, 버그 수정 등의 기능을 제공합니다.

#### Claude Code 설치

1. [Claude Code 다운로드](https://code.claude.com)
2. 설치 후 Anthropic 계정 로그인
3. Settings → WSL → Enable 활성화

#### Claude Code 설정

```bash
# .claude 디렉토리 생성
mkdir -p .claude

# settings.local.json 생성
cat > .claude/settings.local.json << 'EOF'
{
  "permissions": {
    "allow": [
      "Bash(git add:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(docker ps:*)",
      "Bash(pytest:*)"
    ],
    "ask": [
      "Bash(git commit:*)",
      "Bash(git push:*)",
      "Bash(docker-compose up:*)"
    ],
    "deny": [
      "Bash(git push --force:main)",
      "Bash(DROP TABLE:*)"
    ]
  }
}
EOF

# .gitignore에 추가
echo ".claude/settings.local.json" >> .gitignore
```

#### Claude Code 사용법

```bash
# 프로젝트 열기
cd ~/WaggleBot
claude-code .

# Claude에게 요청 예시:
# "네이트판 크롤러에 이미지 다운로드 기능 추가해줘"
# "AI 워커 로그를 확인해줘"
# "OOM 에러를 해결해줘"
```

**Claude Code 장점:**
- ✅ `CLAUDE.md` 규칙 자동 준수
- ✅ Conventional Commits 자동 생성
- ✅ 코드 리뷰 및 버그 제안
- ✅ git push는 사용자 승인 필요 (안전)

**자세한 내용:** [CLAUDE.md](CLAUDE.md) 참조

---

## 🎯 개발 로드맵

### ✅ Phase 1 (완료)
- [x] 크롤러 인프라 (BaseCrawler 패턴)
- [x] MariaDB 스키마 설계
- [x] Streamlit 대시보드 (수신함/갤러리)

### 🚧 Phase 2 (진행 중)
- [x] LLM 요약 (EEVE-Korean-10.8B)
- [x] TTS 생성 (Kokoro-82M)
- [ ] FFmpeg 영상 렌더링 (NVENC)
- [ ] VRAM 관리 최적화
- [ ] 에러 복구 메커니즘

### 📋 Phase 3 (계획)
- [ ] 유튜브 쇼츠 자동 업로드
- [ ] TikTok, 인스타그램 릴스 지원
- [ ] 고급 영상 효과 (Ken Burns, 전환)
- [ ] 분석 대시보드 (조회수, 참여율)

---

## 🤝 기여하기

기여는 언제나 환영합니다!

### 기여 절차

```bash
# 1. Fork & Clone
git clone https://github.com/your-username/WaggleBot.git
cd WaggleBot

# 2. 브랜치 생성
git checkout -b feature/your-feature-name

# 3. 개발 및 테스트
pytest tests/

# 4. 커밋
git add .
git commit -m "feat: add new feature"

# 5. Push
git push origin feature/your-feature-name

# 6. GitHub에서 Pull Request 생성
```

### 커밋 메시지 규칙

```
feat: 새 기능 추가
fix: 버그 수정
docs: 문서 수정
refactor: 코드 리팩토링
test: 테스트 추가/수정
chore: 빌드/설정 변경
```

---

## 🔍 FAQ

<details>
<summary><b>Q1: GPU가 없으면 실행할 수 없나요?</b></summary>

**A:** 현재 버전은 NVIDIA GPU가 필수입니다. CPU만으로 실행하려면:
- LLM: Ollama CPU 모드
- TTS: Edge-TTS (CPU)
- 영상: libx264 코덱 (CPU, 매우 느림)

하지만 권장하지 않습니다. RTX 3080 Ti 기준 영상 1개당 2-5분이지만, CPU는 30분 이상 소요됩니다.
</details>

<details>
<summary><b>Q2: WSL 없이 Windows에서 직접 실행 가능한가요?</b></summary>

**A:** 가능하지만 비권장입니다. 이유:
- FFmpeg NVENC는 Linux에서 더 안정적
- Python 경로 처리가 WSL에서 더 간단
- Docker GPU 지원이 WSL에 최적화됨

직접 실행 시: Python 3.12 설치 → 의존성 설치 → MariaDB 별도 설치 → `.env` 설정 → 각 모듈 개별 실행
</details>

<details>
<summary><b>Q3: 다른 커뮤니티 사이트 추가는?</b></summary>

**A:** `BaseCrawler`를 상속하여 구현:

```python
# crawlers/yoursite.py
from crawlers.base import BaseCrawler

class YourSiteCrawler(BaseCrawler):
    def fetch_listing(self, page: int):
        # API 호출 또는 스크래핑
        pass
    
    def parse_post(self, url: str):
        # 파싱 로직
        pass
```

자세한 내용: [arch/dev_spec.md#41-크롤러](arch/dev_spec.md#41-크롤러-확장성-패턴)
</details>

<details>
<summary><b>Q4: Claude Code 없이 개발 가능한가요?</b></summary>

**A:** 네, 가능합니다. Claude Code는 선택사항입니다. 일반 IDE(VS Code 등)로도 개발할 수 있지만, 다음 기능을 놓치게 됩니다:
- 자동 커밋 메시지 생성
- 프로젝트 규칙 자동 준수
- AI 페어 프로그래밍

Claude Code 사용을 권장하지만, 필수는 아닙니다.
</details>

<details>
<summary><b>Q5: 영상 퀄리티를 높이려면?</b></summary>

**A:** `ai_worker/renderer.py` 수정:

```python
final_video.write_videofile(
    str(output_path),
    codec='h264_nvenc',
    bitrate='8000k',      # 기본 5000k → 8000k
    fps=60,               # 기본 30 → 60
    preset='slow'         # 기본 fast → slow
)
```

단, 렌더링 시간이 2배 이상 증가합니다.
</details>

---

## 📚 추가 문서

- **[CLAUDE.md](CLAUDE.md)**: Claude Code 사용 시 개발 규칙
- **[arch/dev_spec.md](arch/dev_spec.md)**: 상세 기술 명세서 (1,400+ 줄)
    - 크롤러 구현 가이드
    - AI 워커 VRAM 관리
    - DB 스키마 상세
    - 에러 핸들링
    - 테스트 작성법

---

## 🐛 버그 리포트 & 기능 제안

- **버그 리포트**: [GitHub Issues](https://github.com/justant/WaggleBot/issues) → `bug` 라벨
- **기능 제안**: [GitHub Issues](https://github.com/justant/WaggleBot/issues) → `enhancement` 라벨

**버그 리포트 시 포함사항:**
1. 환경 정보 (OS, GPU, Docker 버전)
2. 재현 단계
3. 에러 로그 (`docker-compose logs` 출력)
4. 예상 동작 vs 실제 동작

---

## 📜 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE) 하에 배포됩니다.

**요약:**
- ✅ 상업적 사용 가능
- ✅ 수정 및 배포 가능
- ⚠️ 라이선스 및 저작권 고지 필수
- ❌ 무보증 (AS-IS)

---

## 🙏 감사의 말

이 프로젝트는 다음 오픈소스 프로젝트들을 사용합니다:

- [EEVE-Korean](https://huggingface.co/yanolja/EEVE-Korean-10.8B-v1.0) - LLM
- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) - TTS
- [FFmpeg](https://ffmpeg.org/) - 영상 처리
- [Streamlit](https://streamlit.io/) - 대시보드
- [SQLAlchemy](https://www.sqlalchemy.org/) - ORM

---

## 📞 연락처

- **프로젝트 메인테이너**: [@justant](https://github.com/justant)
- **GitHub Issues**: https://github.com/justant/WaggleBot/issues

---

<div align="center">

**WaggleBot**을 사용해주셔서 감사합니다! ⭐

Made with ❤️ by [@justant](https://github.com/justant)

</div>
