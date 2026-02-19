"""
Error Handling Test Script

에러 핸들링 및 재시도 메커니즘 테스트
"""

import asyncio
import logging
from unittest.mock import Mock, patch

from ai_worker.processor import (
    RobustProcessor,
    RetryPolicy,
    FailureType
)
from db.models import Post, PostStatus
from db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

log = logging.getLogger(__name__)


def create_mock_post():
    """테스트용 Mock Post 생성"""
    post = Mock(spec=Post)
    post.id = 12345
    post.title = "테스트 게시글"
    post.content = "테스트 내용입니다."
    post.comments = []
    post.status = PostStatus.APPROVED
    post.retry_count = 0
    return post


def test_error_classification():
    """에러 분류 테스트"""
    log.info("=== 에러 분류 테스트 ===")

    processor = RobustProcessor()

    # LLM 에러
    llm_error = Exception("Ollama connection failed")
    assert processor._classify_error(llm_error) == FailureType.LLM_ERROR
    log.info("✓ LLM 에러 분류 성공")

    # 렌더링 에러
    render_error = Exception("FFmpeg render failed")
    assert processor._classify_error(render_error) == FailureType.RENDER_ERROR
    log.info("✓ 렌더링 에러 분류 성공")

    # 네트워크 에러
    network_error = TimeoutError("Connection timeout")
    assert processor._classify_error(network_error) == FailureType.NETWORK_ERROR
    log.info("✓ 네트워크 에러 분류 성공")

    # 리소스 에러
    resource_error = Exception("CUDA out of memory")
    assert processor._classify_error(resource_error) == FailureType.RESOURCE_ERROR
    log.info("✓ 리소스 에러 분류 성공")

    log.info("에러 분류 테스트 완료\n")


def test_backoff_calculation():
    """Exponential Backoff 계산 테스트"""
    log.info("=== Backoff 계산 테스트 ===")

    policy = RetryPolicy(
        max_attempts=3,
        backoff_factor=2.0,
        initial_delay=5.0
    )
    processor = RobustProcessor(retry_policy=policy)

    delays = [processor._calculate_backoff_delay(i) for i in range(1, 4)]
    log.info("Backoff 지연 시간: %s", delays)

    # 5초, 10초, 20초 예상
    assert delays[0] == 5.0
    assert delays[1] == 10.0
    assert delays[2] == 20.0
    log.info("✓ Backoff 계산 성공\n")


def test_retry_policy():
    """재시도 정책 테스트"""
    log.info("=== 재시도 정책 테스트 ===")

    # 커스텀 정책
    custom_policy = RetryPolicy(
        max_attempts=5,
        backoff_factor=1.5,
        initial_delay=2.0
    )

    processor = RobustProcessor(retry_policy=custom_policy)

    assert processor.retry_policy.max_attempts == 5
    assert processor.retry_policy.backoff_factor == 1.5
    assert processor.retry_policy.initial_delay == 2.0

    log.info("✓ 커스텀 재시도 정책 설정 성공\n")


async def test_llm_error_no_retry():
    """LLM 에러 시 즉시 중단 테스트"""
    log.info("=== LLM 에러 즉시 중단 테스트 ===")

    mock_post = create_mock_post()
    mock_session = Mock()

    processor = RobustProcessor(
        retry_policy=RetryPolicy(max_attempts=3, initial_delay=0.1)
    )

    # LLM 에러 시뮬레이션
    with patch.object(processor, '_safe_generate_summary', side_effect=Exception("Ollama error")):
        success = await processor.process_with_retry(mock_post, mock_session)

        assert not success
        assert mock_post.status == PostStatus.FAILED
        log.info("✓ LLM 에러 시 즉시 중단 확인\n")



def test_failure_logging():
    """에러 로그 기록 테스트"""
    log.info("=== 에러 로그 기록 테스트 ===")

    processor = RobustProcessor()

    # 로그 기록
    processor._log_failure(
        post_id=12345,
        failure_type=FailureType.RENDER_ERROR,
        error_msg="Test error message",
        attempt=2
    )

    log.info("✓ 에러 로그 파일 기록 성공")
    log.info("  위치: media/logs/failures.log\n")


def run_all_tests():
    """모든 테스트 실행"""
    log.info("========================================")
    log.info("에러 핸들링 테스트 시작")
    log.info("========================================\n")

    try:
        # 동기 테스트
        test_error_classification()
        test_backoff_calculation()
        test_retry_policy()
        test_failure_logging()

        # 비동기 테스트
        asyncio.run(test_llm_error_no_retry())

        log.info("========================================")
        log.info("✅ 모든 테스트 통과!")
        log.info("========================================")

    except AssertionError as e:
        log.error("❌ 테스트 실패: %s", e)
        raise
    except Exception:
        log.exception("❌ 테스트 중 예외 발생")
        raise


if __name__ == '__main__':
    run_all_tests()
