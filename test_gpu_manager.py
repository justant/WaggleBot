"""
GPU Memory Manager Test

GPU 메모리 관리 시스템 테스트
"""

import logging
import time

from ai_worker.gpu_manager import (
    GPUMemoryManager,
    ModelType,
    get_gpu_manager
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

log = logging.getLogger(__name__)


def test_gpu_detection():
    """GPU 감지 테스트"""
    log.info("=== GPU 감지 테스트 ===")

    manager = GPUMemoryManager()

    if manager.cuda_available:
        log.info("✓ CUDA 사용 가능")
        log.info("  디바이스 수: %d", manager.device_count)
        log.info("  디바이스 이름: %s", manager.device_name)
    else:
        log.warning("✗ CUDA 사용 불가 (CPU 모드)")

    return manager.cuda_available


def test_memory_stats():
    """메모리 통계 조회 테스트"""
    log.info("\n=== 메모리 통계 조회 테스트 ===")

    manager = get_gpu_manager()

    stats = manager.get_memory_stats()

    log.info("메모리 통계:")
    log.info("  총 메모리: %.2f GB", stats.total_gb)
    log.info("  할당됨: %.2f GB", stats.allocated_gb)
    log.info("  예약됨: %.2f GB", stats.reserved_gb)
    log.info("  여유: %.2f GB", stats.free_gb)
    log.info("  사용률: %.1f%%", stats.usage_percent)

    assert stats.total_gb >= 0, "총 메모리는 0 이상이어야 합니다"
    log.info("✓ 메모리 통계 조회 성공")


def test_available_vram():
    """사용 가능한 VRAM 조회 테스트"""
    log.info("\n=== 사용 가능한 VRAM 조회 테스트 ===")

    manager = get_gpu_manager()

    available = manager.get_available_vram()
    log.info("사용 가능한 VRAM: %.2f GB", available)

    assert available >= 0, "사용 가능한 VRAM은 0 이상이어야 합니다"
    log.info("✓ VRAM 조회 성공")


def test_can_load_model():
    """모델 로드 가능 여부 테스트"""
    log.info("\n=== 모델 로드 가능 여부 테스트 ===")

    manager = get_gpu_manager()

    # 작은 모델 (1GB)
    can_load_small = manager.can_load_model(1.0)
    log.info("1GB 모델 로드 가능: %s", can_load_small)

    # 큰 모델 (100GB - 불가능)
    can_load_large = manager.can_load_model(100.0)
    log.info("100GB 모델 로드 가능: %s", can_load_large)

    if manager.cuda_available:
        assert not can_load_large, "100GB 모델은 로드 불가능해야 합니다"

    log.info("✓ 모델 로드 체크 성공")


def test_managed_inference():
    """관리된 추론 컨텍스트 테스트"""
    log.info("\n=== 관리된 추론 컨텍스트 테스트 ===")

    manager = get_gpu_manager()

    # 추론 전 메모리
    before_stats = manager.get_memory_stats()
    log.info("추론 전 메모리: %.2f GB", before_stats.allocated_gb)

    # LLM 추론 시뮬레이션
    with manager.managed_inference(ModelType.LLM, "test_llm"):
        log.info("LLM 추론 중...")
        time.sleep(0.5)

    # 추론 후 메모리
    after_stats = manager.get_memory_stats()
    log.info("추론 후 메모리: %.2f GB", after_stats.allocated_gb)

    log.info("✓ 관리된 추론 성공")


def test_cleanup_memory():
    """메모리 정리 테스트"""
    log.info("\n=== 메모리 정리 테스트 ===")

    manager = get_gpu_manager()

    # 정리 전
    before_stats = manager.get_memory_stats()
    log.info("정리 전: %.2f GB allocated", before_stats.allocated_gb)

    # 메모리 정리
    manager.cleanup_memory()

    # 정리 후
    after_stats = manager.get_memory_stats()
    log.info("정리 후: %.2f GB allocated", after_stats.allocated_gb)

    log.info("✓ 메모리 정리 성공")


def test_monitor_memory():
    """메모리 모니터링 테스트"""
    log.info("\n=== 메모리 모니터링 테스트 ===")

    manager = get_gpu_manager()

    monitor_info = manager.monitor_memory()

    log.info("모니터링 정보:")
    log.info("  CUDA 사용 가능: %s", monitor_info['cuda_available'])

    if monitor_info['cuda_available']:
        mem_stats = monitor_info['memory_stats']
        log.info("  총 메모리: %.2f GB", mem_stats['total_gb'])
        log.info("  할당됨: %.2f GB", mem_stats['allocated_gb'])
        log.info("  여유: %.2f GB", mem_stats['free_gb'])
        log.info("  사용률: %.1f%%", mem_stats['usage_percent'])

    log.info("  로드된 모델 수: %d", monitor_info['loaded_count'])

    log.info("✓ 모니터링 성공")


def test_log_memory_status():
    """메모리 상태 로그 출력 테스트"""
    log.info("\n=== 메모리 상태 로그 출력 테스트 ===")

    manager = get_gpu_manager()
    manager.log_memory_status()

    log.info("✓ 메모리 상태 로그 출력 성공")


def test_multiple_models():
    """여러 모델 순차 로딩 테스트"""
    log.info("\n=== 여러 모델 순차 로딩 테스트 ===")

    manager = get_gpu_manager()

    # LLM 로딩
    with manager.managed_inference(ModelType.LLM, "model_1"):
        log.info("Model 1 (LLM) 로드 중...")
        time.sleep(0.2)

    # TTS 로딩 (LLM 자동 언로드)
    with manager.managed_inference(ModelType.TTS, "model_2"):
        log.info("Model 2 (TTS) 로드 중...")
        time.sleep(0.2)

    # 다시 LLM
    with manager.managed_inference(ModelType.LLM, "model_3"):
        log.info("Model 3 (LLM) 로드 중...")
        time.sleep(0.2)

    log.info("✓ 여러 모델 순차 로딩 성공")


def test_emergency_cleanup():
    """긴급 메모리 정리 테스트"""
    log.info("\n=== 긴급 메모리 정리 테스트 ===")

    manager = get_gpu_manager()

    # 긴급 정리
    manager.emergency_cleanup()

    log.info("✓ 긴급 정리 성공")


def run_all_tests():
    """모든 테스트 실행"""
    log.info("=" * 80)
    log.info("GPU 메모리 관리자 테스트 시작")
    log.info("=" * 80)

    try:
        cuda_available = test_gpu_detection()

        if not cuda_available:
            log.warning("CUDA를 사용할 수 없습니다. 일부 테스트는 스킵됩니다.")
            log.info("\n✅ 테스트 완료 (CPU 모드)")
            return

        test_memory_stats()
        test_available_vram()
        test_can_load_model()
        test_managed_inference()
        test_cleanup_memory()
        test_monitor_memory()
        test_log_memory_status()
        test_multiple_models()
        test_emergency_cleanup()

        log.info("\n" + "=" * 80)
        log.info("✅ 모든 테스트 통과!")
        log.info("=" * 80)

    except AssertionError as e:
        log.error("\n" + "=" * 80)
        log.error("❌ 테스트 실패: %s", e)
        log.error("=" * 80)
        raise
    except Exception:
        log.exception("\n❌ 테스트 중 예외 발생")
        raise


if __name__ == '__main__':
    run_all_tests()
