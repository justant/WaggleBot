# Claude Code 설정 가이드 (WaggleBot)

이 문서는 WaggleBot 프로젝트에서 Claude Code를 효율적으로 사용하기 위한 설정 방법을 안내합니다.

---

## 📋 파일 구조

```
WaggleBot/
├── CLAUDE.md                          # 프로젝트 전반 규칙 (이 파일을 프로젝트 루트에 배치)
└── .claude/
    ├── settings.local.json            # Git 워크플로우 권한 설정
    └── rules/                         # (선택사항) 세부 규칙 파일들
        ├── ai_worker.md               # AI 워커 관련 규칙
        ├── crawler.md                 # 크롤러 개발 규칙
        └── database.md                # DB 작업 규칙
```

---

## 🚀 설정 방법

### 1. CLAUDE.md 배치

생성된 `CLAUDE.md` 파일을 프로젝트 루트 디렉토리에 복사하세요:

```bash
# 현재 이 파일이 /tmp/CLAUDE.md에 있다면
cp /tmp/CLAUDE.md /path/to/WaggleBot/CLAUDE.md
```

### 2. .claude 디렉토리 생성

```bash
cd /path/to/WaggleBot
mkdir -p .claude
```

### 3. settings.local.json 배치

```bash
cp /tmp/settings.local.json /path/to/WaggleBot/.claude/settings.local.json
```

### 4. .gitignore 업데이트

`.claude/settings.local.json`은 개인 설정이므로 버전 관리에서 제외:

```bash
echo ".claude/settings.local.json" >> .gitignore
```

---

## ⚙️ 권한 설정 설명

### `settings.local.json` 동작 방식

#### ✅ **allow** (자동 실행)
- `git add`, `git status`, `git diff` 등 읽기 전용 또는 안전한 명령
- Claude가 승인 없이 즉시 실행 가능

#### ❓ **ask** (승인 필요)
- `git commit`, `git push` 등 중요한 작업
- **Claude가 실행 전 반드시 사용자에게 승인 요청**
- 당신이 "OK"를 클릭해야만 실행됨

#### ❌ **deny** (차단)
- `git push --force` to main/master 등 위험한 작업
- Claude가 절대 실행할 수 없음

---

## 🔄 Git 워크플로우 예시

### 시나리오 1: 코드 수정 후 커밋

**당신:** "크롤러 코드 수정해서 커밋해줘"

**Claude:**
1. 코드 수정 완료
2. `git add` 실행 (자동 - allow 권한)
3. `git commit` 시도 → **승인 팝업 표시**
4. 당신이 "OK" 클릭
5. 커밋 완료

**중요:** `git push`는 자동으로 실행되지 **않습니다**. Claude는 멈춰서 대기합니다.

---

### 시나리오 2: 커밋 후 푸시

**당신:** "방금 커밋한 내용 푸시해줘"

**Claude:**
1. `git push` 시도 → **승인 팝업 표시**
2. 당신이 "OK" 클릭
3. 푸시 완료

---

### 시나리오 3: PR 생성

**당신:** "이 작업으로 PR 만들어줘"

**Claude:**
1. `git commit` → 승인 요청
2. 승인 후 커밋
3. `git push` → 승인 요청
4. 승인 후 푸시
5. `gh pr create` → 승인 요청
6. 승인 후 PR 생성

**모든 단계에서 당신의 승인이 필요합니다.**

---

## 🛡️ 안전 장치

### 차단되는 위험한 작업

```bash
# ❌ main 브랜치에 force push 시도
git push --force origin main
→ Claude가 실행할 수 없음 (deny 설정)

# ❌ main 브랜치 hard reset 시도
git reset --hard origin/main
→ Claude가 실행할 수 없음 (deny 설정)
```

### 승인이 필요한 작업

```bash
# ❓ 일반 push (승인 필요)
git push origin feature/my-branch
→ 승인 팝업 → OK 클릭 → 실행

# ❓ 리베이스 (승인 필요)
git rebase main
→ 승인 팝업 → OK 클릭 → 실행
```

---

## 🎯 커밋 메시지 규칙

Claude는 다음 형식으로 커밋 메시지를 작성합니다:

```
feat: 네이트판 크롤러에 이미지 수집 기능 추가

- 게시글의 모든 이미지 URL을 JSON 배열로 저장
- has_image 플래그 자동 설정
- 이미지 유효성 검증 로직 추가

🤖 AI-assisted development

Co-Authored-By: Claude <claude@anthropic.com>
```

### 커밋 타입 (Conventional Commits)
- `feat:` — 새 기능 추가
- `fix:` — 버그 수정
- `docs:` — 문서 수정
- `refactor:` — 코드 리팩토링
- `test:` — 테스트 추가/수정
- `chore:` — 빌드/설정 변경

---

## 📚 추가 규칙 파일 (선택사항)

더 세부적인 규칙이 필요하면 `.claude/rules/` 디렉토리를 활용하세요:

### ai_worker.md (AI 워커 전용 규칙)
```markdown
---
paths:
  - "ai_worker/**/*.py"
---

# AI Worker Development Rules

## VRAM Management
- ALWAYS call `torch.cuda.empty_cache()` after model inference
- Use sequential processing (LLM → TTS → Render)
- NEVER load multiple models simultaneously

## Error Handling
- Wrap all GPU operations in try/finally blocks
- Implement retry logic with exponential backoff
- Update post status to 'FAILED' on permanent errors
```

### crawler.md (크롤러 전용 규칙)
```markdown
---
paths:
  - "crawlers/**/*.py"
---

# Crawler Development Rules

## Base Pattern
- MUST inherit from `BaseCrawler`
- Implement `fetch_listing()` and `parse_post()`

## Error Handling
- Use `logging.exception()` for network errors
- Implement rate limiting (1 request/second)
- Validate JSON schema before DB insert
```

---

## 🔍 문제 해결

### Claude가 승인 없이 push를 실행한다면?

1. `.claude/settings.local.json` 파일이 올바른 위치에 있는지 확인
2. Claude Code 재시작
3. 여전히 문제가 있다면 명시적으로 지시:

**당신:** "CLAUDE.md를 읽고, git push는 내가 승인해야만 실행하도록 해줘"

### CLAUDE.md가 너무 길다면?

현재 CLAUDE.md는 약 250줄로 권장 범위(300줄 이하) 내에 있습니다.
만약 더 추가할 내용이 있다면 `.claude/rules/` 디렉토리로 분리하세요.

### Claude가 CLAUDE.md 규칙을 무시한다면?

1. 규칙을 더 명확하고 구체적으로 작성
2. 강조가 필요한 부분에 **IMPORTANT** 또는 **CRITICAL** 키워드 사용
3. 예시 코드와 함께 "Do NOT" 패턴 명시

---

## 📖 참고 문서

- [Claude Code 공식 문서](https://code.claude.com/docs)
- [CLAUDE.md 작성 가이드](https://www.builder.io/blog/claude-md-guide)
- [Git 워크플로우 설정](https://claudefa.st/blog/guide/development/git-integration)

---

## ✅ 체크리스트

설정이 완료되었는지 확인하세요:

- [ ] `CLAUDE.md` 파일이 프로젝트 루트에 있음
- [ ] `.claude/settings.local.json` 파일이 올바른 위치에 있음
- [ ] `.gitignore`에 `.claude/settings.local.json` 추가됨
- [ ] Claude Code에서 프로젝트 열었을 때 CLAUDE.md가 자동 로드됨
- [ ] `git push` 시도 시 승인 팝업이 뜨는지 테스트

---

이제 WaggleBot 프로젝트에서 Claude Code를 안전하고 효율적으로 사용할 준비가 완료되었습니다! 🎉
