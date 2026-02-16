# WaggleBot — CLAUDE.md

커뮤니티 게시글 → LLM 요약 → TTS → FFmpeg 렌더링 → YouTube 업로드 자동화 파이프라인.
상세 명세: @arch/dev_spec.md

---

## 현재 개발 상태

### 완료된 모듈 ✅
| 모듈 | 주요 파일 |
|------|-----------|
| 크롤러 | `crawlers/base.py`, `nate_pann.py`, `nate_tok.py`, `plugin_manager.py` |
| DB | `db/models.py` (Post/Comment/Content + PostStatus), `db/session.py` |
| AI 워커 | `ai_worker/processor.py`, `llm.py`, `video.py`, `gpu_manager.py` |
| TTS | `ai_worker/tts/` (edge_tts, kokoro, gptsovits) |
| 업로더 | `uploaders/base.py`, `youtube.py`, `uploader.py` |
| 대시보드 | `dashboard.py` (수신함/진행상태/갤러리 탭) |
| 모니터링 | `monitoring/alerting.py`, `monitoring/daemon.py` |
| 인프라 | `docker-compose.yml` (GPU), `docker-compose.galaxybook.yml` (No-GPU) |

### Phase 3 개발 예정
- **멀티 플랫폼 업로더**: TikTok, Instagram (`BaseUploader` 상속으로 추가)
- **고급 영상 효과**: Ken Burns, 단어 단위 자막 페이드인
- **분석 대시보드**: YouTube Analytics API 연동

---

## 상태 전이

```
COLLECTED → (대시보드 승인) → APPROVED → (AI워커) → PROCESSING → RENDERED → UPLOADED
                                                                              ↕
                                                                    DECLINED / FAILED
```

---

## 코딩 규칙 (필수)

```python
# DB — 항상 with 블록
with SessionLocal() as db:
    post = db.query(Post).filter_by(status=PostStatus.APPROVED).first()

# GPU — 반드시 컨텍스트 매니저 사용
with gpu_manager.managed_inference(ModelType.LLM, "name"):
    result = model.generate(text)  # 블록 종료 시 자동 해제
```

- **로깅**: `logging.getLogger(__name__)` — `print()` 금지
- **임포트**: 절대경로 (`from db.models import Post`)
- **경로**: `pathlib.Path` 필수 — `os.path` 금지
- **설정**: `config/settings.py` 경유 — 로직 파일 내 `os.getenv()` 금지
- **타입힌트**: 모든 함수 필수
- **가드절**: 조기 반환으로 중첩 최소화

---

## 하드 제약사항 (절대 위반 금지)

### VRAM (RTX 3080 Ti 12GB)
- 순차 처리 필수: LLM → TTS → 렌더링
- 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()`
- 동시 모델 로드 금지 / 4-bit 양자화 필수

### FFmpeg
- GPU 환경: `h264_nvenc` 필수
- `libx264` 수동 지정 금지 (VRAM 차단)
- No-GPU 환경(galaxybook): `libx264` 자동 폴백 허용

---

## 승인 없이 수정 금지

| 대상 | 이유 |
|------|------|
| `db/models.py` | 스키마 변경 시 DB 마이그레이션 필요 |
| `.env` | 시크릿 포함, 커밋 절대 금지 |
| `docker-compose.yml` | GPU 매핑 민감 |
| `requirements.txt` | 의존성 충돌 위험 |
| `h264_nvenc` → `libx264` 변경 | VRAM 차단 |
| `git push --force` to main | 이력 파괴 |
| `DROP TABLE` | 데이터 손실 |
| `/app/media/` 삭제 | 업로드 영상 손실 |

---

## Docker 우선 명령어

모든 서비스는 Docker 컨테이너에서 실행됨. 호스트에서 직접 실행 시 DB 연결 등 환경 차이 발생.

```bash
# 전체 서비스 시작/중지
docker compose up -d
docker compose down

# 서비스별 로그
docker compose logs -f ai_worker
docker compose logs -f crawler

# 크롤러 1회 실행 (테스트)
docker exec wagglebot-crawler-1 python main.py --once

# 테스트
docker exec wagglebot-crawler-1 pytest
docker exec wagglebot-crawler-1 pytest -k test_crawler

# DB 초기화
docker exec wagglebot-crawler-1 python -c "from db.session import init_db; init_db()"

# 코드 수정 후 재시작 (재빌드 불필요 — 볼륨 마운트됨)
docker restart wagglebot-ai_worker-1

# requirements.txt 변경 시에만 재빌드 필요
docker compose build ai_worker && docker compose up -d ai_worker

# GPU/Ollama 확인
nvidia-smi
docker compose exec ai_worker curl http://host.docker.internal:11434/api/tags
```

---

## 새 크롤러 추가

`crawlers/ADDING_CRAWLER.md` 참조. `BaseCrawler` 상속 후 `plugin_manager.py`에 자동 등록.

## 새 업로더 추가

`BaseUploader` 상속 → `platform` 클래스 변수 지정 → `config/pipeline.json`의 `upload_platforms`에 추가.

---

## Git 워크플로우

- **커밋**: 명시적 요청 시에만 / Conventional Commits (`feat:` `fix:` `docs:` `refactor:`)
- **브랜치**: `main`(프로덕션), `feature/*`, `fix/*`
- **Push**: 반드시 명시적 승인 필요

---

## 응답 스타일

- 추임새·예고 없이 바로 실행
- 변경된 부분만 출력 (전체 파일 반복 금지)
- 단순 작업은 계획 없이 즉시 실행
- 성공 시 불필요한 완료 보고 생략
- 쉘 명령어는 `&&`로 체이닝
