# ai_worker 아키텍처 시각화

## 1. 작업 배경 및 목적 (Context & Objective)
- **요구사항:** ai_worker/ 디렉토리의 구조를 5가지 시각화 도구로 분석하여 arch/ai_worker/에 저장
- **작업 목적:** 모듈 간 의존성, 파이프라인 흐름, 클래스 구조를 다양한 형식으로 시각화하여 팀 이해도 향상

## 2. 핵심 작업 결과 (Core Achievements)
- 5가지 도구(Mermaid, draw.io, Pyreverse, Pydeps, PlantUML)로 ai_worker 전체 구조 시각화 완료
- Pyreverse/Pydeps는 코드에서 자동 추출한 실제 의존성 기반 SVG 생성
- Mermaid/draw.io/PlantUML은 README 분석 기반 수작업 다이어그램 (모듈 전체도 + 파이프라인 흐름 + 클래스 다이어그램)

## 3. 상세 수정 내용 (Detailed Modifications)

### 생성 파일 목록 (18개)

| 도구 | 파일 | 형식 | 설명 |
|------|------|------|------|
| **Mermaid** | `mermaid/README.md` | MD (GitHub 렌더링) | 3개 다이어그램 인라인 |
| | `mermaid/01_module_overview.mmd` | Mermaid source | 모듈 의존성 전체도 |
| | `mermaid/02_pipeline_flow.mmd` | Mermaid source | VRAM 2막 파이프라인 |
| | `mermaid/03_class_diagram.mmd` | Mermaid source | 주요 클래스 관계도 |
| **draw.io** | `drawio/ai_worker_architecture.drawio` | XML (2페이지) | Module Overview + Pipeline Flow |
| | `drawio/README.md` | MD | 열기 방법 안내 |
| **Pyreverse** | `pyreverse/classes_ai_worker.svg` | SVG (자동생성) | 클래스 상속·연관 |
| | `pyreverse/packages_ai_worker.svg` | SVG (자동생성) | 패키지 의존성 |
| | `pyreverse/classes_ai_worker.dot` | DOT source | Graphviz 원본 |
| | `pyreverse/packages_ai_worker.dot` | DOT source | Graphviz 원본 |
| | `pyreverse/README.md` | MD | 재생성 방법 |
| **Pydeps** | `pydeps/ai_worker_deps.svg` | SVG (자동생성) | 전체 모듈 의존성 (상세) |
| | `pydeps/ai_worker_cluster.svg` | SVG (자동생성) | 클러스터링 패키지 뷰 |
| | `pydeps/README.md` | MD | 재생성 방법 |
| **PlantUML** | `puml/01_module_overview.puml` | PUML source | 모듈 의존성 전체도 |
| | `puml/02_pipeline_sequence.puml` | PUML source | 8-Phase 시퀀스 다이어그램 |
| | `puml/03_class_diagram.puml` | PUML source | 주요 클래스 관계도 |
| | `puml/README.md` | MD | 렌더링 방법 안내 |

### 도구별 특징

- **Mermaid**: GitHub에서 README.md 열면 바로 렌더링 (별도 뷰어 불필요)
- **draw.io**: app.diagrams.net 또는 VS Code 확장으로 열기. 2페이지 (전체도 + 파이프라인)
- **Pyreverse**: `pylint.pyreverse`가 코드에서 자동 추출. 37 모듈, 68 import 관계 분석
- **Pydeps**: 실제 import 추적 기반. 상세 버전 + 클러스터링 버전 2종
- **PlantUML**: 컴포넌트도 + 시퀀스도 + 클래스도 3종. VS Code 또는 PlantUML Server에서 렌더링

## 4. 하드 제약 및 시스템 영향도 (Constraints & System Impact)
- **VRAM 제약:** 해당 없음 (문서 생성 작업)
- **DB 마이그레이션 필요 여부:** X
- **환경 변수 (.env) 변경:** X
- **의존성 (requirements.txt) 변경:** X (pylint/pydeps는 --user로 설치, 프로젝트 의존성 아님)

## 5. 엣지 케이스 및 예외 처리 (Edge Cases & Fallbacks)
- mmdc (Mermaid CLI)는 headless Chrome 필요 → SVG 렌더링 불가 → README.md에 인라인 mermaid 코드블록으로 대체 (GitHub 네이티브 렌더링)
- PlantUML은 Java 필요 → .puml 소스만 생성, 렌더링은 외부 도구에 위임
- graphviz(dot)는 apt 불가 → 소스에서 빌드하여 ~/.local/bin에 설치

## 6. 테스트 및 검증 (Test & Validation)
- **테스트 결과물 저장 위치:** `arch/ai_worker/` 하위 5개 디렉토리
- **수동 확인 방법:**
    1. Mermaid: GitHub에서 `arch/ai_worker/mermaid/README.md` 열기 → 다이어그램 렌더링 확인
    2. draw.io: `arch/ai_worker/drawio/ai_worker_architecture.drawio`를 app.diagrams.net에서 열기
    3. Pyreverse: `arch/ai_worker/pyreverse/classes_ai_worker.svg`를 브라우저에서 열기
    4. Pydeps: `arch/ai_worker/pydeps/ai_worker_deps.svg`를 브라우저에서 열기
    5. PlantUML: `arch/ai_worker/puml/*.puml`을 VS Code PlantUML Extension으로 미리보기 (Alt+D)

## 7. 알려진 문제 및 향후 과제 (Known Issues & TODOs)
- Mermaid SVG 직접 파일 생성 불가 (WSL2 환경에서 libnss3 없음) → GitHub 마크다운 렌더링으로 대체
- PlantUML SVG 직접 생성 불가 (Java 미설치) → Docker 또는 온라인 서버로 렌더링 가능
- Pyreverse classes 다이어그램이 복잡할 수 있음 → 서브모듈별 분리 생성 고려

## 8. 추천 커밋 메시지 (한글로 작성)
```text
docs: ai_worker 아키텍처 시각화 5종 (Mermaid/draw.io/Pyreverse/Pydeps/PlantUML)

- arch/ai_worker/ 하위에 5가지 도구별 다이어그램 생성
- Mermaid: 모듈 전체도 + 파이프라인 흐름 + 클래스도 (GitHub 렌더링)
- draw.io: 2페이지 아키텍처 다이어그램 (Module Overview + Pipeline)
- Pyreverse: 자동 추출 클래스/패키지 SVG (37모듈, 68 import)
- Pydeps: 모듈 의존성 SVG (상세 + 클러스터링)
- PlantUML: 컴포넌트도 + 시퀀스도 + 클래스도 소스
```
