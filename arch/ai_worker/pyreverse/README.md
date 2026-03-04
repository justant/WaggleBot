# ai_worker — Pyreverse Diagrams

`pylint.pyreverse`로 자동 생성된 클래스/패키지 다이어그램.

| 파일 | 설명 |
|------|------|
| [classes_ai_worker.svg](classes_ai_worker.svg) | 클래스 상속·연관 다이어그램 |
| [packages_ai_worker.svg](packages_ai_worker.svg) | 패키지 의존성 다이어그램 |

## 재생성 방법

```bash
cd /home/justant/Data/WaggleBot
PYTHONPATH=. pyreverse ai_worker -o svg -p ai_worker --output-directory arch/ai_worker/pyreverse/
```

`.dot` 원본 파일도 함께 포함되어 있어 [Graphviz Online](https://dreampuf.github.io/GraphvizOnline/) 등에서 커스터마이징 가능.
