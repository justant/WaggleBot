# ai_worker — Pydeps Diagrams

`pydeps`로 자동 생성된 모듈 의존성 그래프.

| 파일 | 설명 |
|------|------|
| [ai_worker_deps.svg](ai_worker_deps.svg) | 전체 모듈 의존성 (상세) |
| [ai_worker_cluster.svg](ai_worker_cluster.svg) | 클러스터링된 패키지 뷰 |

## 재생성 방법

```bash
cd /home/justant/Data/WaggleBot
PYTHONPATH=. pydeps ai_worker --no-show --max-bacon=3 -o arch/ai_worker/pydeps/ai_worker_deps.svg -T svg
PYTHONPATH=. pydeps ai_worker --no-show --max-bacon=2 --cluster -o arch/ai_worker/pydeps/ai_worker_cluster.svg -T svg
```
