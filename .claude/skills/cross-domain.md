# /cross-domain - 크로스 도메인 요청 메시지 생성

Agent가 타 Agent의 소유 도메인 변경이 필요할 때 Team Lead에게 보내는
올바른 형식의 SendMessage를 생성하는 스킬.

`arch/env/AGENT_TEAM.md` Section 4-3 프로토콜을 따른다.

## 사용법

```
/cross-domain
```

대화에서 필요한 변경 내용을 설명하면 메시지를 완성한다.

---

## 수행 절차

### Step 1 — 5개 필수 필드 수집

아래 정보가 대화에서 명확하지 않으면 사용자에게 물어본다:

| 필드 | 설명 | 예시 |
|---|---|---|
| **대상** | 어느 Agent의 어떤 파일/디렉토리 | `Agent B 도메인 (config/layout.json)` |
| **내용** | 구체적으로 무엇을 변경해야 하는지 | `tts_enabled boolean 필드 추가` |
| **이유** | 왜 필요한지 (맥락) | `씬별 TTS 활성화 여부 판별` |
| **긴급도** | 블로킹 여부 | `현재 작업 블로킹됨` 또는 `나중에 해도 됨` |
| **요청 작업** | 대상 Agent가 수행할 구체적 작업 | `layout.json의 각 레이아웃에 "tts_enabled": true 추가` |

### Step 2 — 대상 Agent 및 도메인 확인

요청 대상 파일이 어느 Agent 소유인지 아래 테이블로 확인:

| 파일/디렉토리 | 소유 Agent | 권한 유형 |
|---|---|---|
| `ai_worker/` pipeline 계열 (llm, tts, scene 등) | Agent A | Write |
| `ai_worker/` render 계열 (layout, video, gpu 등) | Agent B | Write |
| `assets/` | Agent B | Write |
| `crawlers/`, `config/crawler.py` | Agent C | Write |
| `dashboard.py`, `analytics/`, `uploaders/`, `monitoring/` | Agent D | Write |
| `db/`, `config/settings.py`, `main.py` | Team Lead | **Proposal** |
| `.env`, `docker-compose*.yml` | CEO | **직접 수정만** |

**공유 파일(db/, config/settings.py 등)이 대상이면** 크로스 도메인 요청이 아닌 `/proposal` 스킬을 사용해야 함을 안내한다.

### Step 3 — SendMessage 출력

아래 형식 그대로 출력한다. Agent는 이 텍스트를 그대로 Team Lead에게 SendMessage한다:

```
SendMessage to lead:
"크로스 도메인 요청.

대상: {소유 Agent} 도메인 ({파일 또는 디렉토리 경로})
내용: {구체적으로 무엇을 변경할 것인지}
이유: {왜 필요한지}
긴급도: {블로킹 여부 — "현재 작업 블로킹됨 — 이 변경 없이 진행 불가" 또는 "블로킹 아님 — 병렬 진행 가능"}
요청 작업: {대상 Agent가 수행할 구체적 작업 1~3줄}"
```

### Step 4 — 대기 안내 출력

```
⏳ Team Lead의 응답을 기다리세요.
   - Team Lead가 대상 Agent에게 Sub-task를 할당합니다.
   - 완료 통보 전까지 해당 파일을 직접 수정하지 마세요.
   - 완료 통보 후 변경된 파일을 읽기 전용으로 참조하여 작업을 재개하세요.
```

---

## 금지 사항 (이 스킬 사용 시 항상 표시)

```
❌ 타 Agent 도메인 파일을 "급하다"는 이유로 직접 수정하면 안 됩니다.
❌ Team Lead가 타 도메인 임시 쓰기 권한을 부여하더라도 사용하지 마세요.
❌ Team Lead가 직접 코드를 작성하여 중재해서는 안 됩니다 (조율만 수행).
```

---

## 참고: 도메인 소유권 판별 3단계

1. 해당 파일이 소유권 테이블의 디렉토리에 속하는가? → YES: 해당 디렉토리 소유자
2. `ai_worker/` 내부라면 파일명 키워드가 매칭되는가? → YES: 매칭 Agent
3. 위에 해당 없으면 → Team Lead에게 소유권 판정 요청
