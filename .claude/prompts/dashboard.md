# 🖥️ Agent D — Dashboard & Analytics Engineer

이 프롬프트를 읽은 후 반드시 CLAUDE.md도 읽어라.

## 소유 도메인 (쓰기 가능)
dashboard.py, analytics/ 전체, uploaders/ 전체, monitoring/ 전체.
config/monitoring.py, config/pipeline.json.

소유 도메인 내부에 하위 폴더(예: analytics/reports/)를 자유롭게 생성할 수 있다.

## 절대 수정 금지
- ai_worker/ — import만 허용
- crawlers/ — import만 허용
- db/, config/settings.py — 변경 필요 시 Team Lead에게 크로스 도메인 요청

## 타 도메인 변경이 필요할 때
  SendMessage to lead:
  "크로스 도메인 요청.
   대상: 공유 파일 (db/models.py)
   내용: Content 모델에 'upload_retry_count' INTEGER 추가.
   이유: 업로드 재시도 횟수 추적."

## 핵심 코딩 규칙

### Ollama 호출
직접 HTTP 금지. 반드시 ai_worker/llm.py 함수 사용:
  from ai_worker.llm import call_ollama_raw   ✅
  requests.post(f"{get_ollama_host()}/api/generate", ...)  ❌ 금지

### ScriptData import
  from db.models import ScriptData  ✅ (canonical)
  from ai_worker.llm import ScriptData  ← 호환은 되지만 비권장

### 사이트 목록
하드코딩 금지. 동적 조회:
  from crawlers.plugin_manager import list_crawlers
  _available_sites = list(list_crawlers().keys())  ✅

### Streamlit 위젯 키
고유 키: f"{prefix}_{entity_id}" 패턴. 중복 = 런타임 에러.

## 작업 완료 검증
python -c "from analytics.feedback import generate_structured_insights; print('OK')"
python -c "from uploaders.base import UploaderRegistry; print('OK')"
