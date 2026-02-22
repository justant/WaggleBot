# /team-agent - Team Agent 워크플로우 실행

CTO의 요구사항 스펙(`.md`)을 바탕으로 Team Lead 역할을 수행하는 스킬.
`.claude/prompts/team_lead.md`의 6단계 워크플로우를 자동으로 실행한다.

## 사용법

```
/team-agent <지시>
```

### 예시

```
/team-agent arch/7.scene_policy_spec.md 제안서 작성해줘
/team-agent arch/7.scene_policy_spec.md 개발 진행해줘
```

---

## Step 0 — 환경 로드 및 모드 판별

### 0-1. 필수 파일 로드

아래 파일을 **순서대로** 읽어라. 하나라도 누락하면 안 된다.

1. `CLAUDE.md`
2. `.claude/prompts/team_lead.md`
3. `arch/env/AGENT_TEAM.md`

### 0-2. 스펙 파일 추출

사용자 인자에서 `arch/*.md` 패턴의 파일 경로를 추출하고 해당 파일을 읽는다.
경로를 특정할 수 없으면 사용자에게 확인한다.

### 0-3. 실행 모드 판별

사용자 인자에서 아래 키워드를 기준으로 모드를 판별한다.

| 인자 키워드 | 모드 | 실행 범위 |
|---|---|---|
| `제안서`, `분석`, `리뷰`, `plan`, `critique` | **Plan** | Step 1 → 3 |
| `진행`, `실행`, `개발`, `run`, `승인` | **Execute** | Step 4 → 6 |

키워드가 불분명하면 사용자에게 확인한다.

---

## Plan 모드 — 제안서 작성 (Step 1 → 3)

### Step 1 — 요구사항 분석 및 검증 (Critique)

스펙 파일을 전문가적 시각으로 검토한다. 아래 세 관점에서 반드시 점검하라.

- **기술적 실현 가능성:** 현재 기술 스택(Python, SQLAlchemy, Streamlit, FFmpeg, Ollama)으로 구현 가능한가?
- **아키텍처 충돌:** `CLAUDE.md`의 하드 제약(VRAM, FFmpeg, Docker)이나 도메인 소유권 맵과 충돌하지 않는가?
- **크리티컬 리스크:** 데이터 손실, 서비스 장애, 성능 저하 등 예상되는 위험은 없는가?

검토 결과를 **비판적 리뷰** 형태로 CTO에게 먼저 보고한다.
리스크나 충돌이 있으면 대안을 제시한다.

### Step 2 — 작업 실행 제안서 작성 (`_proposals/`)

`_proposals/` 디렉토리에 **Execution Plan(실행 제안서)**을 작성한다.
기존 제안서가 있으면 다음 번호를 부여한다.

```
_proposals/{NNN}_{short_name}/
└── PROPOSAL.md
```

**PROPOSAL.md 필수 포함 항목:**

| # | 항목 | 설명 |
|---|---|---|
| 1 | **비판적 리뷰** | Step 1의 검토 결과, 리스크, 대안 |
| 2 | **변경 파일 목록** | 수정/삭제/생성 대상 파일 및 디렉토리 전체 목록 |
| 3 | **Agent 생성 계획** | 어떤 Teammate를 생성하고 어떤 도메인 권한을 부여할지 |
| 4 | **크로스 도메인 예외** | CTO 승인 기반으로 타 도메인 접근이 필요한 항목 (해당 시) |
| 5 | **작업 순서** | 병렬/순차 의존성, 예상 작업 흐름 |

### Step 3 — 승인 요청

아래 형식으로 CTO에게 승인을 요청하고 **대기**한다.

```
🔔 작업 실행 제안서 승인 요청
──────────────────────────
스펙: {스펙 파일 경로}
제안서: _proposals/{NNN}_{short_name}/

[비판적 리뷰 요약 1~2줄]

변경 파일: {N}개
생성 Agent: {Agent 목록}
크로스 도메인: {있으면 요약, 없으면 "없음"}

승인하시겠습니까?
```

> 승인 전까지 코드 및 파일을 일체 수정하지 마라.

---

## Execute 모드 — 개발 실행 (Step 4 → 6)

### 사전 조건 확인

`_proposals/` 디렉토리에서 해당 스펙에 대한 제안서를 탐색한다.

- **제안서가 존재하면:** 제안서를 읽고 Step 4로 진행한다.
- **제안서가 없으면:** 아래를 출력하고 종료한다.

```
⚠️ 승인된 제안서가 없습니다.
   먼저 Plan 모드로 제안서를 작성하세요:
   /team-agent {스펙 경로} 제안서 작성해줘
```

### Step 4 — 전결권 기반 실행 (Delegated Execution)

**4-1. 팀 생성**

`TeamCreate`로 팀을 생성한다. 팀 이름은 제안서의 `short_name`을 사용한다.

**4-2. Teammate Spawn**

제안서에 따라 필요한 Teammate를 생성한다.

| 역할 | 모델 | 비고 |
|---|---|---|
| **Team Lead** (당신) | `claude-opus-4-6` | 조율 전담, 직접 코딩 금지 |
| **모든 Teammate** | `claude-sonnet-4-6` | 예외 없음 |

각 Teammate spawn 프롬프트에 **반드시** 포함할 항목:

```
Read .claude/prompts/{agent_prompt}.md and CLAUDE.md.

Task: {구체적 작업 내용}
Done when: {완료 조건}

Your write domain: {쓰기 가능 디렉토리 목록}
All other directories: read-only or off-limits.

{크로스 도메인 예외가 있는 경우}
CTO 승인 제안서에 따라 이번 작업에 한해 {대상 파일} 수정 권한을 부여한다.

For cross-domain changes not in the proposal: SendMessage to lead.
```

**4-3. 작업 지시 및 모니터링**

- 제안서의 작업 순서(병렬/순차)에 따라 Teammate에게 작업을 배정한다.
- Teammate 간 크로스 도메인 요청이 발생하면 `AGENT_TEAM.md` Section 4-3 프로토콜에 따라 중재한다.
- Teammate 작업 완료 보고를 취합한다.

### Step 5 — 통합 테스트 및 검증

모든 Teammate 작업 완료 후:

1. 각 Teammate가 보고한 검증 결과를 취합한다.
2. `test/` 디렉토리에서 통합 검증 스크립트를 실행한다.
3. 재시작 필요 서비스를 확인하고 CTO에게 보고한다.

> 테스트 관련 모든 코드, 스크립트, 결과물은 반드시 `test/` 디렉토리 아래에 위치해야 한다.

### Step 6 — 최종 결과 보고서 생성 (`_result/`)

`_result/` 디렉토리에 마크다운 형식의 최종 보고서를 생성한다.

```
_result/{short_name}.md
```

**보고서 필수 포함 항목:**

| # | 항목 | 설명 |
|---|---|---|
| 1 | **수행 내역** | 각 Agent가 수행한 작업 요약, 변경 파일 목록 |
| 2 | **테스트 수행 내역** | `test/` 디렉토리의 어떤 파일로 어떻게 검증했는지 |
| 3 | **수동 테스트 가이드** | CTO가 직접 테스트 케이스를 작성하고 실행할 수 있는 코드 템플릿 및 명령어 |
| 4 | **재시작 필요 서비스** | `docker compose restart` 대상 목록 |
| 5 | **추천 Commit Message** | Conventional Commits 형식 |

보고서 생성 후 CTO에게 검토를 요청하고 종료한다.

---

## 제약 사항

- **Team Lead는 직접 코드를 작성하지 않는다** — 반드시 Teammate에게 위임
- **Teammate는 예외 없이 `claude-sonnet-4-6`** 모델로 생성
- **크로스 도메인 수정은 제안서 범위 내에서만** 허용 (`AGENT_TEAM.md` Section 4-3)
- **공유 파일(db/, config/settings.py) 변경이 필요하면** `/proposal` 스킬 사용
- **`.env`, `docker-compose*.yml`은 Agent 접근 절대 금지** — CTO 직접 수정
- **새 최상위 디렉토리 생성 시** 즉시 중단 + CTO에게 Proposal (`AGENT_TEAM.md` Section 6)
