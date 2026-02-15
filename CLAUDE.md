# WaggleBot 프로젝트 메모리

## 1. 프로젝트 개요
- **목표:** 쇼츠 영상 자동화 팩토리 (수집 -> 요약 -> TTS -> 영상 생성).
- **아키텍처:** 단일 노드 Windows PC (RTX 3080 Ti, 12GB VRAM). 분산 워커 노드 없음.
- **현재 단계:** Phase 1 (크롤러/대시보드) 완료. Phase 2 (AI/영상) 작업 중.
- **핵심 스택:** Python 3.12, Streamlit, SQLAlchemy(MariaDB), FFmpeg(MoviePy).

## 2. 자주 사용하는 명령어
- **의존성 설치:** `pip install -r requirements.txt`
- **크롤러 실행 (1회):** `python main.py --once`
- **스케줄러 실행:** `python main.py`
- **대시보드 실행:** `streamlit run dashboard.py`
- **테스트 실행:** `pytest` (폴더가 없다면 tests/ 생성)
- **DB 초기화:** `python -c "from db.session import init_db; init_db()"`

## 3. 코딩 표준 및 패턴
- **데이터베이스:** 항상 `with` 블록이나 `try/finally` 안에서 `db.session.SessionLocal`을 사용할 것.
- **크롤러:** 반드시 `crawlers.base.BaseCrawler`를 상속받아야 함. `fetch_listing` 및 `parse_post` 구현 필수.
- **로깅:** `logging.getLogger(__name__)` 사용. `print()` 사용 금지.
- **타입 힌트:** 모든 함수 시그니처에 필수 적용 (예: `def func(x: int) -> str:`). 복잡한 데이터 구조에는 `TypedDict`나 `dataclasses` 사용.
- **임포트:** 절대 경로 임포트 권장 (예: `from db.models import Post`).
- **파일 경로:** `os.path` 대신 `pathlib.Path` 사용 (WSL/Windows 크로스 플랫폼 호환성에 필수).
- **설정:** 모든 설정값은 `config/settings.py`에 중앙화할 것. 로직 파일 내 `os.getenv` 사용 금지.
- **스타일:** 들여쓰기를 줄이기 위해 가드 클로즈(Early Return) 패턴 사용.
- 
## 4. Phase 2 제약 사항 (AI 및 영상)
- **하드웨어:** RTX 3080 Ti (12GB VRAM). VRAM 용량이 부족함.
- **LLM:** Ollama 또는 로컬 GGUF를 통한 4-bit 양자화 모델 사용. 외부 API(OpenAI/Claude) 사용 금지.
- **TTS:** 비용/지연 시간 절약을 위해 로컬 엔진(Kokoro-82M, GPT-SoVITS) 사용.
- **영상:** FFmpeg 렌더링 시 반드시 `h264_nvenc` 코덱 사용.

## 5. 토큰 절약 및 소통 규칙
- **간결하게:** 코드나 핵심 설명만 출력. 대화형 추임새 금지.
- **변경 사항만:** 파일 수정 시 전체가 아닌 변경된 라인(Diff)만 출력.
- **반복 금지:** `dev_spec.md` 내용 반복 금지.
- **주도적 제안:** 누락된 파일은 명세서 기반으로 생성 제안.
- **단순 작업 계획 생략:** 간단한 수정은 계획 설명 없이 즉시 실행.
- **도구 중계 금지:** "ls를 실행하겠습니다" 같은 예고 금지. 그냥 실행할 것.
- **명령어 체이닝:** 쉘 명령어는 가능하면 `&&`로 묶어서 한 번에 실행.
- **성공 시 침묵:** 단계가 성공하면 "성공했습니다" 보고 없이 즉시 다음 단계로 진행.