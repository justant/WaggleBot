# Dashboard 수정 및 개선 작업 지시서

> **대상 파일:** `dashboard.py`
> **선행 조건:** Phase 1 + Phase 2 리팩토링 완료 상태
> **실행 순서:** Task 순번대로 실행 권장 (의존성 있음)

---

## 🔴 Part A: 리팩토링 정합성 수정 (Breaking Changes)

리팩토링에서 변경했지만 dashboard.py에 반영하지 못한 부분들.

---

### Task D-1: Ollama 직접 호출 2곳 → `call_ollama_raw()` 통합

**문제:**
Phase 2 Task 8에서 `analytics/feedback.py`의 Ollama 직접 호출을 `call_ollama_raw()`로 교체했으나,
dashboard.py에 **동일한 패턴의 직접 호출이 2곳** 남아 있음.
- GPU 매니저 우회 (VRAM 충돌 가능)
- LLM 로깅 누락 (LLM 이력 탭에 기록 안 됨)

**수정 위치 1: `run_ai_fit_analysis()` 함수 (약 라인 226~249)**

현재 코드:
```python
resp = _http.post(
    f"{get_ollama_host()}/api/generate",
    json={"model": model, "prompt": prompt, "stream": False},
    timeout=40,
)
resp.raise_for_status()
raw = resp.json().get("response", "")
```

수정:
```python
from ai_worker.llm import call_ollama_raw
raw = call_ollama_raw(prompt=prompt, model=model)
```
- `import requests as _http` 중 이 함수에서만 사용하는 부분 제거
- `get_ollama_host` import도 이 함수에서만 사용 시 제거
- 함수 시그니처에서 `model: str` 파라미터 유지
- JSON 파싱 로직(`re.search(r"\{.*?\}", raw, re.DOTALL)`)은 그대로 유지
- `call_ollama_raw`의 반환값이 문자열인지 확인 후 적용

**수정 위치 2: Analytics 탭 "AI 인사이트" 섹션 (약 라인 560~580)**

현재 코드:
```python
import requests as _req
_resp = _req.post(
    f"{get_ollama_host()}/api/generate",
    json={
        "model": load_pipeline_config().get("llm_model", OLLAMA_MODEL),
        "prompt": _prompt,
        "stream": False,
        "options": {"num_predict": 512, "temperature": 0.7},
    },
    timeout=120,
)
_resp.raise_for_status()
_insight_text = _resp.json().get("response", "").strip()
```

수정:
```python
from ai_worker.llm import call_ollama_raw
_insight_text = call_ollama_raw(
    prompt=_prompt,
    model=load_pipeline_config().get("llm_model", OLLAMA_MODEL),
).strip()
```
- `import requests as _req` 인라인 import 제거
- `call_ollama_raw`에 `options` 파라미터가 지원되지 않으면 무시 (기본값 사용)
- `call_ollama_raw`가 options(num_predict, temperature)를 지원하는지 확인
  - 지원하지 않으면 `call_ollama_raw` 함수에 `**kwargs`로 options 전달 추가 고려

**검증:**
```bash
# dashboard.py에서 직접 Ollama 호출이 남아있지 않은지 확인
grep -n "api/generate" dashboard.py
grep -n "get_ollama_host" dashboard.py
# 결과가 0건이어야 함 (import 라인 제외)
```

**import 정리:**
수정 후 dashboard.py 최상단에서 아래 항목이 더 이상 사용되지 않는지 확인:
- `get_ollama_host` — 다른 곳에서도 안 쓰이면 import에서 제거
- `OLLAMA_MODEL` — Analytics 인사이트에서 여전히 fallback으로 사용하므로 유지 가능
- `requests as _http` — `render_image_slider()`에서 여전히 사용하므로 유지

---

### Task D-2: ScriptData import 경로를 canonical location으로 변경

**문제:**
Phase 2 Task 6에서 `ScriptData`를 `db/models.py`로 이동하고 `ai_worker/llm.py`에서 re-export 유지.
dashboard.py에는 `from ai_worker.llm import ScriptData`가 **5곳**에 분산 (함수 내부 lazy import).
re-export 덕분에 당장은 동작하지만, canonical 위치에서 import하는 것이 원칙적으로 올바름.

**수정:**
dashboard.py에서 `from ai_worker.llm import ScriptData` → `from db.models import ScriptData`

해당 위치들 (lazy import — 함수 내부):
1. `_run_hd_render()` 내부 (약 라인 39)
2. `_gallery_action_btn()` 내부 — 직접 import 없지만 `_run_hd_render` 경유
3. 편집실 탭 "기존 Content / ScriptData 로드" (약 라인 356)
4. 편집실 탭 "대본 재생성" 버튼 핸들러 (약 라인 393)
5. 편집실 탭 "저장 & 확정" 버튼 핸들러 (약 라인 476)
6. 갤러리 탭 "대본" expander (약 라인 530)

**권장 방식:**
- 파일 최상단에 `from db.models import ..., ScriptData` 추가 (기존 `from db.models import Post, PostStatus, Comment, Content, LLMLog` 라인에 추가)
- 함수 내부의 모든 lazy `from ai_worker.llm import ScriptData` 제거
- `from ai_worker.llm import generate_script`는 그대로 유지 (generate_script의 canonical 위치는 ai_worker/llm.py)

**검증:**
```bash
grep -n "from ai_worker.llm import ScriptData" dashboard.py
# 결과가 0건이어야 함
grep -n "ScriptData" dashboard.py
# 모든 참조가 정상 동작하는지 확인
```

---

### Task D-3: 사이트 필터 하드코딩 → CrawlerRegistry 동적 조회

**문제:**
수신함 탭의 사이트 필터가 `["nate_pann", "bobaedream", "dcinside", "fmkorea"]`로 하드코딩.
Phase 1 Task 1에서 nate_tok.py를 삭제했고, 향후 크롤러 추가/삭제 시 dashboard 수정 필요.

**수정:**
```python
# 현재 (하드코딩)
site_filter = st.multiselect(
    "사이트 필터", ["nate_pann", "bobaedream", "dcinside", "fmkorea"], ...
)

# 변경 (동적)
from crawlers.plugin_manager import list_crawlers
_available_sites = list(list_crawlers().keys())

site_filter = st.multiselect(
    "사이트 필터", _available_sites, default=[], placeholder="전체"
)
```

**주의:**
- `list_crawlers()`는 `{site_code: CrawlerClass}` dict 반환
- Phase 2 Task 9에서 `plugin_manager.py` 단순화 후에도 `list_crawlers()`는 유지됨
- import는 파일 최상단에 배치

**검증:**
```bash
grep -n "nate_pann.*bobaedream.*dcinside.*fmkorea" dashboard.py
# 하드코딩 목록이 0건이어야 함
```

---

### Task D-4: `upload_post` import 경로 확인 (UploaderRegistry 반영)

**문제:**
Phase 2 Task 11에서 `UploaderRegistry` 도입 후 `uploaders/uploader.py`를 레지스트리 기반 디스패치로 교체.
갤러리 탭에서 `from uploaders.uploader import upload_post` lazy import가 정상 동작하는지 확인 필요.

**확인사항:**
- `uploaders/uploader.py`에 `upload_post()` 함수가 여전히 존재하는지 확인
- 함수 시그니처 `upload_post(post, content, session)` → 변경 여부 확인
- 변경되었다면 갤러리 탭의 호출부 수정

**검증:**
```bash
grep -n "def upload_post" uploaders/uploader.py
# 함수가 존재하고 시그니처가 동일한지 확인
```

---

## 🟡 Part B: 운영자 편의성 개선

---

### Task D-5: 진행현황 탭 자동 갱신 (Auto-Refresh)

**문제:**
진행현황 탭은 AI 워커의 처리 상태를 모니터링하는 핵심 화면이지만,
상태 변화를 보려면 매번 "새로고침" 버튼을 수동 클릭해야 함.
갤러리의 `_gallery_action_btn`에는 이미 `@st.fragment(run_every="3s")` 적용되어 있음.

**수정:**
진행현황 탭의 **메트릭 카드 영역**을 `@st.fragment(run_every="5s")`로 분리.

```python
@st.fragment(run_every="5s")
def _progress_metrics():
    """진행현황 메트릭 자동 갱신 (5초 간격)."""
    with SessionLocal() as session:
        counts = dict(
            session.query(Post.status, func.count(Post.id))
            .filter(Post.status.in_(progress_statuses))
            .group_by(Post.status)
            .all()
        )
        metric_cols = st.columns(len(progress_statuses))
        for col, status in zip(metric_cols, progress_statuses):
            emoji = STATUS_EMOJI.get(status, "")
            col.metric(f"{emoji} {status.value}", counts.get(status, 0))
```

- 메트릭 아래의 상세 목록은 fragment 밖에 유지 (성능 고려)
- 전체 페이지 rerun 없이 메트릭만 갱신됨

---

### Task D-6: 수신함 전체 선택/해제 토글

**문제:**
수신함에 게시글이 많을 때 하나씩 체크박스 클릭해야 함.
일괄 승인/거절 버튼은 있지만 "전체 선택" 기능 없음.

**수정:**
글로벌 배치 액션 바에 전체 선택/해제 버튼 추가:

```python
bc0, bc1, bc2 = st.columns([1, 1, 1])
with bc0:
    if st.button("☑️ 전체 선택", width="stretch"):
        st.session_state["selected_posts"] = {p.id for p in posts}
        st.rerun()
    if st.button("⬜ 전체 해제", width="stretch"):
        st.session_state["selected_posts"] = set()
        st.rerun()
```

- 기존 `bc1, bc2 = st.columns(2)` → `bc0, bc1, bc2 = st.columns([1, 1, 1])`로 변경
- 또는 별도 행으로 분리

---

### Task D-7: 갤러리 상태별 필터 추가

**문제:**
갤러리에 `PREVIEW_RENDERED`, `RENDERED`, `UPLOADED` 3개 상태가 혼재.
특정 상태만 보고 싶을 때 필터가 없음.

**수정:**
갤러리 헤더 아래에 필터 추가:

```python
_gal_filter = st.multiselect(
    "상태 필터",
    ["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
    default=["PREVIEW_RENDERED", "RENDERED", "UPLOADED"],
    key="gallery_status_filter",
    label_visibility="collapsed",
)
_gal_statuses = [PostStatus(s) for s in _gal_filter] if _gal_filter else [
    PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED
]
```

- 쿼리의 `.filter(Post.status.in_([...]))` 부분을 `_gal_statuses`로 교체

---

### Task D-8: 편집실 대본 저장 전 유효성 검증

**문제:**
hook, body, closer가 모두 비어있어도 "저장 & 확정" 가능.
빈 대본이 AI 워커로 전달되면 렌더링 실패.

**수정:**
"저장 & 확정" 버튼 핸들러 시작 부분에 유효성 검증 추가:

```python
if st.button("💾 저장 & 확정", ...):
    # 유효성 검증
    if not hook.strip():
        st.error("🎣 후킹(Hook)을 입력하세요.")
        st.stop()
    if not body_lines:
        st.error("📝 본문 항목을 1개 이상 입력하세요.")
        st.stop()
    if not closer.strip():
        st.error("🔚 마무리(Closer)를 입력하세요.")
        st.stop()
    if est_seconds < 15:
        st.error("⏱️ 대본이 너무 짧습니다 (최소 15초 이상).")
        st.stop()
    # 기존 저장 로직...
```

---

### Task D-9: LLM 이력 탭에 Post ID 검색 추가

**문제:**
LLM 이력 탭에서 특정 게시글의 LLM 호출만 보고 싶을 때 필터가 없음.
디버깅 시 특정 post_id의 LLM 처리 과정 추적 불가.

**수정:**
기존 필터 행에 4번째 컬럼 추가:

```python
col_f1, col_f2, col_f3, col_f4 = st.columns(4)
# ... 기존 필터들 ...
with col_f4:
    filter_post_id = st.number_input(
        "Post ID",
        min_value=0,
        value=0,
        step=1,
        key="llm_filter_post_id",
        help="0이면 전체 표시",
    )

# 쿼리에 추가
if filter_post_id > 0:
    _fq = _fq.filter(LLMLog.post_id == filter_post_id)
```

**주의:** `LLMLog` 모델에 `post_id` 컬럼이 있는지 확인. 없으면 이 Task는 스킵.

---

### Task D-10: 설정 탭 Ollama 연결 상태 표시

**문제:**
LLM 모델명을 설정해도 Ollama 서버가 다운이면 알 수 없음.
설정 저장 후 다른 탭에서 사용 시에야 에러 발생.

**수정:**
LLM 설정 섹션에 연결 상태 확인 버튼 추가:

```python
st.subheader("🧠 LLM 설정")
llm_model = st.text_input("LLM 모델 (Ollama)", value=cfg.get("llm_model", "qwen2.5:14b"))

if st.button("🔍 연결 확인", key="check_ollama", width="content"):
    try:
        import requests as _req
        _r = _req.get(f"{get_ollama_host()}/api/tags", timeout=5)
        _r.raise_for_status()
        _models = [m["name"] for m in _r.json().get("models", [])]
        if llm_model in _models:
            st.success(f"✅ Ollama 연결 정상 — `{llm_model}` 모델 사용 가능")
        else:
            st.warning(
                f"⚠️ Ollama 연결 정상, 모델 `{llm_model}` 미발견.\n"
                f"사용 가능: {', '.join(_models[:10])}"
            )
    except Exception as _e:
        st.error(f"❌ Ollama 서버 연결 실패: {_e}")
```

---

### Task D-11: 갤러리 삭제 확인 UX 개선

**문제:**
현재 삭제 버튼 2번 클릭 방식의 UX 결함:
- `st.session_state[f"confirm_delete_{content.id}"]`가 설정된 후 **다른 버튼 클릭 시 rerun**되면서 확인 상태가 유지됨
- 사용자가 의도치 않게 다음 rerun에서 삭제가 실행될 수 있음
- 확인 상태가 영구적으로 남음 (초기화 타이밍 없음)

**수정:**
`st.popover` 또는 명시적 확인 체크박스 패턴으로 변경:

```python
with btn_col2:
    with st.popover("🗑️ 삭제", use_container_width=True):
        st.warning(f"**{post.title[:30]}** 게시글과 영상이 영구 삭제됩니다.")
        if st.button(
            "⚠️ 삭제 확인",
            key=f"confirm_del_{content.id}",
            type="primary",
        ):
            delete_post(post.id)
            st.success("삭제됨")
            st.rerun()
```

**참고:** Streamlit 1.31+ 기준 `st.popover` 지원. 미지원 버전이면 `st.expander`로 대체.

```bash
# Streamlit 버전 확인
pip show streamlit | grep Version
```

---

### Task D-12: 수신함 키보드 단축키 안내 및 빠른 처리 UX

**문제:**
대량의 게시글을 검토할 때 마우스 클릭만으로 처리해야 하며,
현재 게시글 수와 처리 속도를 체감하기 어려움.

**수정:**
수신함 헤더 영역에 처리 현황 progress bar 추가:

```python
# 전체 수집 대비 처리 완료 비율
with SessionLocal() as _sess:
    _total_ever = _sess.query(func.count(Post.id)).scalar() or 0
    _total_decided = _sess.query(func.count(Post.id)).filter(
        Post.status.notin_([PostStatus.COLLECTED])
    ).scalar() or 0

if _total_ever:
    _pct = _total_decided / _total_ever
    st.progress(_pct, text=f"전체 처리율: {_total_decided}/{_total_ever} ({_pct*100:.1f}%)")
```

---

## 🟢 Part C: 운영 안정성 개선

---

### Task D-13: Ollama 서버 다운 시 Graceful Degradation

**문제:**
Ollama 서버가 다운되면:
- 수신함의 "AI 적합도 분석" 클릭 시 40초 타임아웃 후 에러
- Analytics 인사이트 생성 시 120초 타임아웃
- 전체 탭이 먹통 (spinner 상태로 대기)

**수정:**
`run_ai_fit_analysis()` 함수에 빠른 실패 로직 추가:

```python
def _check_ollama_health() -> bool:
    """Ollama 서버 응답 여부를 빠르게 확인 (2초 타임아웃)."""
    try:
        _http.get(f"{get_ollama_host()}/api/tags", timeout=2)
        return True
    except Exception:
        return False
```

AI 분석/인사이트 버튼 핸들러에서:
```python
if st.button("🔍 AI 적합도 분석", ...):
    if not _check_ollama_health():
        st.error("❌ LLM 서버에 연결할 수 없습니다. 설정 탭에서 Ollama 상태를 확인하세요.")
    else:
        with st.spinner("LLM 분석 중..."):
            ...
```

**적용 위치:**
- 수신함 "AI 적합도 분석" 버튼 (약 라인 330)
- 편집실 "대본 재생성" 버튼 (약 라인 393)
- Analytics "인사이트 생성" 버튼 (약 라인 560)
- Analytics "피드백 반영" 버튼 (약 라인 600)

---

### Task D-14: HD 렌더 큐 중복 요청 방지

**문제:**
`_enqueue_hd_render(post_id)`가 동일 post_id에 대해 중복 호출 가능.
`_hd_render_pending` set으로 UI 버튼만 비활성화하지만,
큐에는 같은 post_id가 여러 번 들어갈 수 있음.

**수정:**
`_enqueue_hd_render()`에 중복 방지 로직 추가:

```python
def _enqueue_hd_render(post_id: int) -> None:
    if post_id in _hd_render_pending:
        log.warning("HD 렌더 요청 중복 무시: post_id=%d", post_id)
        return
    _hd_render_pending.add(post_id)
    _hd_render_queue.put(post_id)
    # ... 워커 시작 로직 동일
```

---

### Task D-15: 업로드 실패 시 상태 롤백

**문제:**
갤러리의 업로드 버튼 핸들러에서:
```python
ok = upload_post(_up, _uc, upload_session)
if ok:
    _up.status = PostStatus.UPLOADED
    upload_session.commit()
```
`upload_post()`가 예외를 발생시키면 except 블록으로 가지만,
부분 업로드(multi-platform) 실패 시 `ok=False`인 경우 상태가 `RENDERED`로 남아
사용자에게 어떤 플랫폼이 실패했는지 정보 없음.

**수정:**
```python
ok = upload_post(_up, _uc, upload_session)
if ok:
    _up.status = PostStatus.UPLOADED
    upload_session.commit()
    st.success("업로드 완료!")
    st.rerun()
else:
    # upload_meta에 실패 정보가 있으면 표시
    upload_session.refresh(_uc)
    _meta = _uc.upload_meta or {}
    _fail_info = {
        k: v.get("error", "알 수 없는 오류")
        for k, v in _meta.items()
        if isinstance(v, dict) and v.get("error")
    }
    if _fail_info:
        for plat, err in _fail_info.items():
            st.error(f"❌ {plat}: {err}")
    else:
        st.error("일부 플랫폼 업로드 실패. 로그를 확인하세요.")
```

---

### Task D-16: DB 세션 범위 안정성 확보

**문제:**
편집실 탭에서 `with SessionLocal() as session:` 블록이 탭 전체를 감싸고 있어,
그 안에서 여러 `st.button` 핸들러가 실행됨.
Streamlit의 rerun 메커니즘 상 세션이 예기치 않게 오래 유지될 수 있음.

또한 편집실의 `session` 내에서 `update_status()`를 호출하면
별도 세션이 열려 동일 레코드를 동시 수정 — 잠재적 충돌.

**수정 방향:**
- 편집실 "건너뛰기" 버튼에서 호출하는 `update_status()`가
  이미 바깥 세션이 해당 Post를 로드한 상태 → 독립적인 세션이므로 현재는 안전하지만,
  SQLAlchemy의 identity map 혼동 가능성 있음
- 읽기 전용 로드 후 세션 닫고, 수정 작업은 별도 세션에서 수행하는 패턴 권장

**구체적 수정:**
편집실 탭의 메인 `with SessionLocal() as session:` 블록을:
1. **읽기 전용 영역** — 게시글 목록/내용 조회
2. **쓰기 영역** — "저장 & 확정" 등 상태 변경

두 영역으로 분리. 읽기 결과를 변수에 저장 후 세션 닫고, 쓰기 시 새 세션 사용.

```python
# 읽기
with SessionLocal() as session:
    approved_posts = session.query(Post).filter(...).all()
    # 필요한 데이터를 변수에 추출
    _post_data = [{
        "id": p.id, "title": p.title, "content": p.content,
        "stats": p.stats, "images": p.images, ...
    } for p in approved_posts]

# UI 렌더링 (세션 외부)
for data in _post_data:
    ...

# 쓰기 (버튼 핸들러)
if st.button("💾 저장 & 확정"):
    with SessionLocal() as write_session:
        ...
```

**참고:** 이 수정은 범위가 크므로 신중하게 적용. 현재 동작에 문제가 없다면 우선순위를 낮춰도 됨.

---

### Task D-17: 이미지 프록시 캐싱 및 에러 제한

**문제:**
`render_image_slider()`가 매번 rerun 시 이미지를 다시 fetch.
- 원본 사이트의 핫링크 차단으로 실패 빈번
- 실패 시 "이미지 로드 실패" 텍스트만 표시 — 네트워크 요청 낭비
- rerun마다 동일 이미지를 반복 요청

**수정:**
`@st.cache_data` 데코레이터로 이미지 캐싱:

```python
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_image(url: str) -> bytes | None:
    """이미지를 캐시하여 반복 요청 방지 (5분 TTL)."""
    try:
        resp = _http.get(
            url, timeout=8,
            headers={"Referer": url, "User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None
```

`render_image_slider()`에서:
```python
img_data = _fetch_image(imgs[cur])
if img_data:
    st.image(img_data, width=width)
else:
    st.caption(f"이미지 로드 실패: {imgs[cur]}")
```

---

### Task D-18: 자동 승인이 탭 로드 시에만 동작하는 문제 경고 표시

**문제:**
자동 승인 로직이 수신함 탭 로드 시에만 실행됨.
운영자가 다른 탭에 있으면 새 게시글이 수집되어도 자동 승인 안 됨.
이 한계를 운영자가 인지하지 못할 수 있음.

**수정:**
자동 승인 설정 섹션에 경고 표시:

```python
# 설정 탭의 자동 승인 섹션
auto_approve_on = st.checkbox("자동 승인 활성화", ...)
if auto_approve_on:
    st.info(
        "ℹ️ 자동 승인은 수신함 탭 로드 시에만 실행됩니다. "
        "백그라운드 자동 승인이 필요하면 AI 워커에 자동 승인 로직 추가를 고려하세요."
    )
```

---

### Task D-19: 편집실 TTS 미리듣기 임시 파일 정리

**문제:**
TTS 미리듣기 생성 시 `MEDIA_DIR / "tmp" / f"preview_{post_id}.mp3"` 파일 생성.
게시글 처리 완료 후에도 임시 파일이 남아 디스크 누적.

**수정:**
설정 탭에 임시 파일 정리 버튼 추가:

```python
st.subheader("🧹 시스템 정리")
_tmp_dir = MEDIA_DIR / "tmp"
if _tmp_dir.exists():
    _preview_files = list(_tmp_dir.glob("preview_*.mp3"))
    _cache_dirs = list((_tmp_dir / "tts_scene_cache").glob("*")) if (_tmp_dir / "tts_scene_cache").exists() else []
    st.caption(f"TTS 미리듣기 파일: {len(_preview_files)}개 | TTS 씬 캐시: {len(_cache_dirs)}개")
    if st.button("🗑️ 임시 파일 정리", key="cleanup_tmp"):
        import shutil
        for f in _preview_files:
            f.unlink(missing_ok=True)
        for d in _cache_dirs:
            shutil.rmtree(d, ignore_errors=True)
        st.success(f"✅ {len(_preview_files)}개 파일 + {len(_cache_dirs)}개 캐시 삭제 완료")
        st.rerun()
```

---

### Task D-20: 진행현황 FAILED 상태 상세 정보 표시

**문제:**
FAILED 상태 게시글에 "재시도" 버튼만 있고 실패 원인 표시 없음.
운영자가 재시도해도 같은 이유로 계속 실패할 수 있음.

**수정:**
FAILED 게시글 카드에 에러 정보 표시:

```python
if status == PostStatus.FAILED:
    # Content에 에러 정보가 있으면 표시
    _fail_content = session.query(Content).filter_by(post_id=post.id).first()
    _fail_meta = (_fail_content.upload_meta or {}) if _fail_content else {}
    _fail_error = _fail_meta.get("error") or _fail_meta.get("last_error")
    if _fail_error:
        st.caption(f"❌ 실패 원인: {str(_fail_error)[:200]}")

    col_retry, col_del = st.columns(2)
    with col_retry:
        if st.button("🔄 재시도", key=f"retry_{post.id}"):
            update_status(post.id, PostStatus.APPROVED)
            st.rerun()
    with col_del:
        if st.button("🗑️ 삭제", key=f"del_failed_{post.id}"):
            delete_post(post.id)
            st.rerun()
```

---

## 📋 실행 순서 요약

| 순서 | Task | 긴급도 | 설명 |
|------|------|--------|------|
| 1 | D-1 | 🔴 | Ollama 직접 호출 → call_ollama_raw() |
| 2 | D-2 | 🔴 | ScriptData import 경로 정리 |
| 3 | D-3 | 🔴 | 사이트 필터 동적 조회 |
| 4 | D-4 | 🔴 | upload_post import 경로 확인 |
| 5 | D-14 | 🟡 | HD 렌더 큐 중복 방지 |
| 6 | D-13 | 🟡 | Ollama 다운 시 graceful degradation |
| 7 | D-8 | 🟡 | 대본 저장 전 유효성 검증 |
| 8 | D-17 | 🟡 | 이미지 프록시 캐싱 |
| 9 | D-5 | 🟡 | 진행현황 자동 갱신 |
| 10 | D-15 | 🟡 | 업로드 실패 시 상세 표시 |
| 11 | D-20 | 🟡 | FAILED 상태 에러 원인 표시 |
| 12 | D-11 | 🟡 | 갤러리 삭제 확인 UX 개선 |
| 13 | D-6 | 🟢 | 전체 선택/해제 토글 |
| 14 | D-7 | 🟢 | 갤러리 상태별 필터 |
| 15 | D-9 | 🟢 | LLM 이력 Post ID 검색 |
| 16 | D-10 | 🟢 | Ollama 연결 상태 확인 |
| 17 | D-12 | 🟢 | 수신함 처리 현황 progress bar |
| 18 | D-18 | 🟢 | 자동 승인 한계 안내 |
| 19 | D-19 | 🟢 | 임시 파일 정리 버튼 |
| 20 | D-16 | 🟢 | DB 세션 범위 분리 (대규모) |

---

## 커밋 메시지 (전체 통합)

```
fix: dashboard.py 리팩토링 정합성 수정 + 운영 편의성/안정성 개선

리팩토링 정합성 수정 (Part A)
- Ollama 직접 호출 2곳 → call_ollama_raw() 통합 (Task 8 미반영분)
- ScriptData import 경로를 db.models (canonical) 로 변경
- 사이트 필터를 CrawlerRegistry 동적 조회로 변경
- upload_post import 경로 UploaderRegistry 반영 확인

운영자 편의성 개선 (Part B)
- 진행현황 탭 메트릭 자동 갱신 (@st.fragment 5초)
- 수신함 전체 선택/해제 토글 버튼
- 갤러리 상태별 필터
- 편집실 대본 저장 전 유효성 검증
- LLM 이력 Post ID 검색 필터
- 설정 탭 Ollama 연결 상태 확인
- 갤러리 삭제 확인 UX 개선 (popover)
- 수신함 처리 현황 progress bar

운영 안정성 개선 (Part C)
- Ollama 서버 다운 시 graceful degradation (빠른 실패)
- HD 렌더 큐 중복 요청 방지
- 업로드 실패 시 플랫폼별 에러 상세 표시
- 이미지 프록시 캐싱 (@st.cache_data)
- FAILED 상태 에러 원인 표시 + 삭제 버튼
- TTS 임시 파일 정리 버튼
- 자동 승인 동작 한계 안내
```
