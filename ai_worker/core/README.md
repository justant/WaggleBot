# ai_worker/core — 핵심 런타임

AI Worker의 진입점, 프로세서 루프, GPU 관리, 종료 핸들링을 담당하는 코어 모듈.

---

## 파일 구조

```
ai_worker/core/
├── __init__.py       # 패키지 마커
├── main.py           # 진입점 — 3-worker async 루프 (288줄)
├── processor.py      # RobustProcessor — 메인 처리 루프 (871줄)
├── gpu_manager.py    # GPUManager — VRAM 할당/해제 (480줄)
├── shutdown.py       # 안전 종료 핸들러 (34줄)
└── settings.yaml     # 도메인별 설정 (retry, worker 등)
```

---

## 모듈 상세

### main.py (288줄)

AI Worker 컨테이너 진입점. Docker `CMD`에서 실행.

**주요 기능:**
- 3개 비동기 워커 동시 실행 (crawl → process → upload)
- Fish Speech TTS 서버 기동 대기 (`wait_for_fish_speech`)
- Telegram 봇 핸들러 초기화
- 시그널 핸들링 (SIGTERM/SIGINT → graceful shutdown)

**핵심 함수:**

| 함수 | 역할 |
|------|------|
| `main()` | asyncio 이벤트 루프 진입점 |
| `_run_workers()` | 3-worker 동시 실행 (gather) |
| `_init_services()` | DB/TTS/Telegram 초기화 |

### processor.py (871줄)

콘텐츠 처리 메인 루프. DB에서 `APPROVED` 상태 게시글을 가져와 8-Phase 파이프라인 실행.

**핵심 클래스:**

```python
class RobustProcessor:
    async def run_loop(self, interval: float = 30.0) -> None: ...
    async def process_one(self, content_id: int) -> bool: ...
```

**처리 흐름:**

```
1. APPROVED 콘텐츠 조회 → PROCESSING으로 상태 전환
2. content_processor.process_content() 호출 (Phase 1~8)
3. 성공 → RENDERED, 실패 → FAILED + 에러 로깅
4. interval 간격으로 반복
```

**에러 처리:**
- Phase별 에러 캐치 + 상태 복원
- 연속 실패 카운터 → 임계치 초과 시 interval 증가
- 치명적 에러 (DB 연결 등) → 전체 루프 중단

### gpu_manager.py (480줄)

RTX 3090 24GB VRAM을 2막 구조로 관리하는 GPU 리소스 매니저.

**핵심 클래스:**

```python
class GPUManager:
    def managed_inference(self, model_type: ModelType, name: str): ...
    def get_available_vram(self) -> float: ...
    def emergency_cleanup(self) -> None: ...
```

**ModelType 열거형:**

| 타입 | VRAM | 용도 |
|------|------|------|
| `LLM` | ~14GB | Ollama qwen2.5:14b |
| `TTS` | ~5GB | Fish Speech |
| `VIDEO` | ~12GB | LTX-2 (ComfyUI) |

**2막 전략:**

```
1막: LLM 단독 (Phase 1~6)
    → torch.cuda.empty_cache() + gc.collect()
2막: TTS + VIDEO (Phase 5, 7~8)
    → MAX_COEXIST_VRAM_GB = 20GB 제한
```

### shutdown.py (34줄)

SIGTERM/SIGINT 시그널을 받아 graceful shutdown을 수행.

```python
def setup_shutdown_handler(loop: asyncio.AbstractEventLoop) -> None: ...
```

### settings.yaml

```yaml
retry:
  max_attempts: 3
  backoff_factor: 2.0
worker:
  process_interval: 30
  max_consecutive_failures: 5
```

`config/settings.py`의 `get_domain_setting('core', 'retry', 'max_attempts')` 형태로 접근.

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `docker-compose.yml` | `ai_worker.core.main` | 컨테이너 진입점 |
| `ai_worker/pipeline/content_processor.py` | `gpu_manager` | VRAM 관리 |
| `config/settings.py` | `settings.yaml` | 도메인별 설정 |
