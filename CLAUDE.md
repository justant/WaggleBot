# WaggleBot — 커뮤니티 쇼츠 팩토리

**AI 기반 쇼츠 영상 자동 생성 파이프라인**  
커뮤니티 게시글 → LLM 요약 → TTS → 영상 렌더링 → 업로드

---

## 프로젝트 구조

```
WaggleBot/
├── crawlers/          # 웹 스크래핑 (BaseCrawler 패턴)
├── db/                # DB 모델 & 세션 관리
├── ai_worker/         # LLM, TTS, 영상 렌더링
├── assets/            # 배경 영상, 폰트 등
├── config/            # 중앙화된 설정 (settings.py)
├── main.py            # 크롤러 진입점
├── scheduler.py       # Cron 트리거
└── dashboard.py       # Streamlit 관리 UI
```

**세부 사항:** @arch/dev_spec.md 참조

---

## 코딩 표준 (필수 준수)

### 데이터베이스
```python
# ✅ 올바름 - 항상 with 블록 사용
with SessionLocal() as db:
    post = db.query(Post).filter_by(status='APPROVED').first()

# ❌ 틀림 - 커넥션 누수 위험
db = SessionLocal()
post = db.query(Post).first()
db.close()
```

### 타입 힌트 (필수)
```python
from typing import Optional
from pathlib import Path

def process_image(image_path: str) -> Optional[Path]:
    """모든 함수에 타입 힌트 필수"""
    if not image_path:
        return None
    return Path(f"/app/media/images/{hash(image_path)}.jpg")
```

### 기타 규칙
- **로깅:** `logging.getLogger(__name__)` 사용, `print()` 금지
- **임포트:** 절대 경로 (`from db.models import Post`)
- **경로:** `pathlib.Path` 사용 (WSL/Windows 호환)
- **설정:** `config/settings.py`에 중앙화, 로직 파일 내 `os.getenv()` 금지
- **가드 클로즈:** 조기 반환으로 중첩 최소화

---

## Git 워크플로우

### Commit 규칙
- **자동 커밋:** 명시적 요청 시에만
- **자동 Push 금지:** 반드시 사용자 승인 필요
- **메시지 형식:** Conventional Commits (feat:/fix:/docs:/refactor:)

### 브랜치 전략
- `main` — 프로덕션
- `feature/*` — 새 기능
- `fix/*` — 버그 수정

**Push 방법:**  
명시적으로 *"origin에 푸시해줘"* 또는 *"PR 만들어줘"* 요청 필요

---

## 토큰 절약 규칙 (엄수)

1. **간결하게:** 코드/핵심만, 대화 추임새 금지
2. **변경만 출력:** 전체 파일 말고 Diff만
3. **반복 금지:** `@arch/dev_spec.md` 내용 반복 안 함
4. **계획 생략:** 단순 작업은 즉시 실행
5. **예고 금지:** "실행하겠습니다" 말고 바로 실행
6. **체이닝:** 쉘 명령어 `&&`로 묶기
7. **성공 침묵:** 완료 보고 없이 다음 단계 진행

---

## 자주 사용하는 명령어

```bash
# 데이터베이스 초기화
python -c "from db.session import init_db; init_db()"

# 크롤링
python main.py --once              # 1회 실행 (테스트)
python main.py                      # 스케줄 실행

# AI 워커
python ai_worker/main.py

# 대시보드
streamlit run dashboard.py

# 테스트
pytest                              # 전체
pytest -k test_crawler              # 특정 테스트
pytest --cov=crawlers               # 커버리지

# Docker
docker-compose up -d
docker-compose logs -f ai_worker
docker-compose down
```

---

## 중요 제약사항

### VRAM 관리 (RTX 3080 Ti 12GB)
- LLM/TTS는 **4-bit 양자화 필수**
- **순차 처리:** LLM → TTS → 렌더링
- 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()` 필수
- 동시 모델 로드 절대 금지

### FFmpeg 인코딩
- **필수 코덱:** `h264_nvenc` (GPU 가속)
- **금지 코덱:** `libx264` (CPU 사용, VRAM 차단)

### 경로 처리
- **필수:** `pathlib.Path` (WSL/Windows 호환)
- **금지:** `os.path` (크로스 플랫폼 문제)

---

## 절대 수정 금지 (명시적 승인 필수)

**파일:**
- `db/models.py` — 스키마 변경 시 마이그레이션 필요
- `.env` — 시크릿 포함 (커밋 금지)
- `docker-compose.yml` — GPU 매핑 민감
- `requirements.txt` — 의존성 충돌 위험

**작업:**
- `git push --force` to main
- `DROP TABLE` 쿼리
- `/app/media/` 삭제 (업로드 영상)
- `h264_nvenc` → `libx264` 변경

---

## 참조 문서

- **프로젝트 명세:** @arch/dev_spec.md (필독)
- **크롤러 패턴:** @crawlers/base.py
- **DB 모델:** @db/models.py
- **설정:** @config/settings.py
