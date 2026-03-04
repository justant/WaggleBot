# ai_worker — PlantUML Diagrams

PlantUML `.puml` 파일 목록:

| 파일 | 설명 | 뷰어 |
|------|------|------|
| [01_module_overview.puml](01_module_overview.puml) | 모듈 의존성 전체도 | [PlantUML Server](https://www.plantuml.com/plantuml/uml/) |
| [02_pipeline_sequence.puml](02_pipeline_sequence.puml) | 8-Phase 시퀀스 다이어그램 | VS Code PlantUML Extension |
| [03_class_diagram.puml](03_class_diagram.puml) | 주요 클래스 관계도 | IntelliJ PlantUML Plugin |

## 렌더링 방법

```bash
# PlantUML JAR 사용
java -jar plantuml.jar arch/ai_worker/puml/*.puml -tsvg

# Docker 사용
docker run --rm -v $(pwd):/data plantuml/plantuml -tsvg /data/arch/ai_worker/puml/*.puml

# VS Code: PlantUML Extension (jebbs.plantuml) 설치 후 Alt+D로 미리보기
```
