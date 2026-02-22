"""test_llm.py — LLM 프롬프트 튜닝 테스트

DB에서 다음 3가지 케이스별로 최신 게시글 1개씩(최대 3개)을 조회해
generate_script()를 실행하고, LLM 입력 프롬프트와 원시 응답을
test_llm_output/ 에 txt로 저장합니다.

케이스:
  1) 본문 500자 이상 (긴 서사 — 분량 유지 검증)
  2) 이미지 8개 이상 (이미지 다수 — 이미지+텍스트 배분 검증)
  3) 댓글 5개 이상 (comment 타입 분리 검증)

사용법:
    python test/test_llm.py
"""

import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from db.models import Comment, LLMLog, Post
from db.session import SessionLocal
from ai_worker.llm.client import generate_script, _SCRIPT_PROMPT_V2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "test_llm_output"


def _safe_filename(text: str, max_len: int = 40) -> str:
    """제목을 파일명으로 안전하게 변환."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in text[:max_len])
    return safe.strip("_ ")


def _write_result(
    filepath: Path,
    post: Post,
    prompt: str,
    raw_response: str,
    script,
    success: bool,
    error_msg: str | None,
    duration_ms: int | None,
) -> None:
    SEP = "=" * 70
    SEP2 = "-" * 70

    # 좋아요 순 정렬 댓글
    best_comments = sorted(post.comments, key=lambda c: c.likes, reverse=True)[:5]

    with open(filepath, "w", encoding="utf-8") as f:
        # ── 헤더 ──────────────────────────────────────────────────────────────
        f.write(f"{SEP}\n")
        f.write(f"Post ID : {post.id}\n")
        f.write(f"사이트   : {post.site_code}\n")
        f.write(f"수집일   : {post.created_at}\n")
        f.write(f"상태     : {post.status.value if post.status else '없음'}\n")
        f.write(f"{SEP}\n\n")

        # ── 원본 글 ───────────────────────────────────────────────────────────
        f.write("## 원본 글\n")
        f.write(f"제목: {post.title}\n\n")
        f.write("본문:\n")
        f.write(post.content or "(본문 없음)")
        f.write("\n\n")

        # ── 댓글 ──────────────────────────────────────────────────────────────
        f.write(f"## 댓글 (전체 {len(post.comments)}개 / 상위 {len(best_comments)}개)\n")
        if best_comments:
            for rank, c in enumerate(best_comments, 1):
                f.write(f"  [{rank}] 좋아요 {c.likes:>4}  {c.author}\n")
                f.write(f"       {c.content}\n")
        else:
            f.write("  (댓글 없음)\n")
        f.write("\n")

        # ── LLM 입력 (프롬프트) ────────────────────────────────────────────────
        f.write(f"{SEP2}\n")
        f.write("## LLM 입력 (프롬프트)\n")
        f.write(f"{SEP2}\n")
        f.write(prompt if prompt else "(프롬프트 캡처 실패 — LLMLog 없음)")
        f.write("\n\n")

        # ── LLM 출력 (Raw Response) ────────────────────────────────────────────
        f.write(f"{SEP2}\n")
        f.write("## LLM 출력 (Raw Response)\n")
        f.write(f"{SEP2}\n")
        f.write(raw_response if raw_response else "(응답 없음)")
        f.write("\n\n")

        # ── 파싱 결과 (ScriptData) ─────────────────────────────────────────────
        f.write(f"{SEP2}\n")
        f.write("## 파싱 결과 (ScriptData)\n")
        f.write(f"{SEP2}\n")
        if duration_ms is not None:
            f.write(f"처리 시간 : {duration_ms}ms\n")
        f.write(f"성공 여부 : {'성공' if success else '실패'}\n\n")

        if success and script:
            f.write(f"mood   : {script.mood}\n")
            f.write(f"hook   : {script.hook}\n")
            f.write(f"closer : {script.closer}\n")
            f.write(f"title  : {script.title_suggestion}\n")
            f.write(f"tags   : {script.tags}\n\n")

            # 검증 카운터
            body_count = sum(1 for b in script.body if b.get("type", "body") == "body")
            comment_count = sum(1 for b in script.body if b.get("type") == "comment")
            f.write(f"body 블록: {body_count}개 / comment 블록: {comment_count}개\n\n")

            f.write("body:\n")
            for idx, block in enumerate(script.body):
                block_type = block.get("type", "body")
                author = block.get("author", "")
                lines = block.get("lines", [])
                line_count = block.get("line_count", len(lines))

                if block_type == "comment":
                    f.write(f"  [{idx}] type=comment  author={author}  line_count={line_count}\n")
                else:
                    f.write(f"  [{idx}] type=body  line_count={line_count}\n")

                for line in lines:
                    char_count = len(line)
                    warn = " ⚠️ >20자" if char_count > 20 else ""
                    f.write(f"       - ({char_count:>2}자) {line}{warn}\n")
        else:
            f.write(f"오류: {error_msg}\n")


def _fetch_test_posts(db) -> list[tuple[str, Post]]:
    """3가지 케이스별로 게시글을 1개씩 조회한다. 중복 post_id는 제거."""
    cases: list[tuple[str, Post | None]] = []

    # 케이스 1: 본문 500자 이상 (긴 서사 — 분량 유지 검증)
    c1 = (
        db.query(Post)
        .options(selectinload(Post.comments))
        .filter(func.char_length(Post.content) >= 500)
        .order_by(Post.created_at.desc())
        .first()
    )
    cases.append(("case1_본문500자이상", c1))

    # 케이스 2: 이미지 8개 이상
    c2 = (
        db.query(Post)
        .options(selectinload(Post.comments))
        .filter(func.json_length(Post.images) >= 8)
        .order_by(Post.created_at.desc())
        .first()
    )
    cases.append(("case2_이미지8개이상", c2))

    # 케이스 3: 댓글 5개 이상 (comment 타입 분리 검증)
    from sqlalchemy import select
    comment_count_sub = (
        select(Comment.post_id, func.count(Comment.id).label("cnt"))
        .group_by(Comment.post_id)
        .having(func.count(Comment.id) >= 5)
        .subquery()
    )
    c3 = (
        db.query(Post)
        .options(selectinload(Post.comments))
        .join(comment_count_sub, Post.id == comment_count_sub.c.post_id)
        .order_by(Post.created_at.desc())
        .first()
    )
    cases.append(("case3_댓글5개이상", c3))

    # 중복 제거 (같은 post_id가 여러 케이스에 해당할 경우 첫 케이스만 유지)
    seen: set[int] = set()
    result: list[tuple[str, Post]] = []
    for label, post in cases:
        if post is None:
            logger.warning("  [%s] 해당 조건의 게시글 없음", label)
            continue
        if post.id in seen:
            logger.warning("  [%s] Post %d 이미 다른 케이스에서 선택됨, 건너뜀", label, post.id)
            continue
        seen.add(post.id)
        result.append((label, post))

    return result


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("출력 디렉토리: %s", OUTPUT_DIR)

    with SessionLocal() as db:
        tagged_posts = _fetch_test_posts(db)

        if not tagged_posts:
            logger.warning("DB에 해당하는 게시글이 없습니다.")
            return

        logger.info("대상 게시글 %d개 로드 완료", len(tagged_posts))

        for i, (label, post) in enumerate(tagged_posts, 1):
            logger.info("[%d/%d] [%s] Post %d: %s", i, len(tagged_posts), label, post.id, post.title[:50])

            # 댓글 준비 (좋아요 순 상위 5개)
            best_comments = sorted(post.comments, key=lambda c: c.likes, reverse=True)[:5]
            comment_texts = [f"{c.author}: {c.content[:100]}" for c in best_comments]

            # 프롬프트 직접 빌드 (LLMLog 의존 없이 캡처)
            body_str = post.content or ""
            prompt_text = _SCRIPT_PROMPT_V2.format(
                title=post.title,
                body=body_str[:3000],
                comments="\n".join(f"- {c}" for c in comment_texts),
            )

            # LLM 호출
            script = None
            success = True
            error_msg: str | None = None

            try:
                script = generate_script(
                    title=post.title,
                    body=body_str,
                    comments=comment_texts,
                    post_id=post.id,
                    call_type="test_llm",
                )
            except Exception as exc:
                success = False
                error_msg = str(exc)
                logger.error("  LLM 호출 실패: %s", exc)

            # LLMLog에서 raw_response 회수 (generate_script가 별도 세션에 커밋)
            log: LLMLog | None = None
            with SessionLocal() as log_db:
                log = (
                    log_db.query(LLMLog)
                    .filter(
                        LLMLog.post_id == post.id,
                        LLMLog.call_type == "test_llm",
                    )
                    .order_by(LLMLog.created_at.desc())
                    .first()
                )
                raw_response = log.raw_response if log else ""
                duration_ms = log.duration_ms if log else None

            # 출력 파일 저장
            filename = OUTPUT_DIR / f"{i:02d}_{label}_post{post.id}_{_safe_filename(post.title)}.txt"
            _write_result(
                filepath=filename,
                post=post,
                prompt=prompt_text,
                raw_response=raw_response,
                script=script,
                success=success,
                error_msg=error_msg,
                duration_ms=duration_ms,
            )

            status_mark = "OK" if success else "FAIL"
            logger.info("  [%s] %s", status_mark, filename.name)

    logger.info("완료. 결과 위치: %s", OUTPUT_DIR)


if __name__ == "__main__":
    run()
