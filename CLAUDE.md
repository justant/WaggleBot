# WaggleBot Project Memory

## 1. Project Overview
- **Goal:** Automated Shorts Video Factory (Crawl -> Summarize -> TTS -> Video).
- **Architecture:** Single Node Windows PC (RTX 3080 Ti, 12GB VRAM). No distributed worker nodes.
- **Current Phase:** Phase 1 (Crawler/Dashboard) Done. Working on Phase 2 (AI/Video).
- **Core Stack:** Python 3.12, Streamlit, SQLAlchemy(MariaDB), FFmpeg(MoviePy).

## 2. Commonly Used Commands
- **Install Deps:** `pip install -r requirements.txt`
- **Run Crawler (Once):** `python main.py --once`
- **Run Scheduler:** `python main.py`
- **Run Dashboard:** `streamlit run dashboard.py`
- **Run Tests:** `pytest` (create tests/ if missing)
- **Database Init:** `python -c "from db.session import init_db; init_db()"`

## 3. Coding Standards & Patterns
- **Database:** Always use `db.session.SessionLocal` in a `with` block or `try/finally`.
- **Crawlers:** Must inherit `crawlers.base.BaseCrawler`. Implement `fetch_listing` & `parse_post`.
- **Logging:** Use `logging.getLogger(__name__)`. DO NOT use `print()`.
- **Type Hints:** Mandatory for all function signatures (e.g., `def func(x: int) -> str:`).
- **Imports:** Absolute imports preferred (e.g., `from db.models import Post`).

## 4. Phase 2 Constraints (AI & Video)
- **Hardware:** RTX 3080 Ti (12GB VRAM). VRAM is scarce.
- **LLM:** Use 4-bit quantization models via Ollama or local GGUF. No external APIs (OpenAI/Claude).
- **TTS:** Use local engines (Kokoro-82M, GPT-SoVITS) to save cost/latency.
- **Video:** Must use `h264_nvenc` codec for FFmpeg rendering.

## 5. Token Saving & Communication Rules
- **Be Concise:** Output ONLY code or essential explanations. No conversational filler ("Here is the code", "I updated the file").
- **Diffs Only:** When editing, show only the changed functions/lines, not the full file.
- **No Repetition:** Do not repeat context from `arch/dev_spec.md`. Assume I know it.
- **Proactive:** If a file is missing (e.g., `ai_worker.py`), propose creating it based on `dev_spec.md`.