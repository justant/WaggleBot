"""
GPU Memory Manager

CUDA 메모리 관리 및 모니터링 시스템
"""

import gc
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logging.warning("PyTorch not available - GPU management disabled")

log = logging.getLogger(__name__)


class ModelType(Enum):
    """모델 타입"""
    LLM = "llm"
    TTS = "tts"
    VIDEO = "video"
    OTHER = "other"


@dataclass
class MemoryStats:
    """메모리 통계"""
    total_gb: float
    allocated_gb: float
    reserved_gb: float
    free_gb: float
    usage_percent: float


@dataclass
class ModelInfo:
    """모델 정보"""
    model_type: ModelType
    name: str
    estimated_vram_gb: float
    actual_vram_gb: Optional[float] = None
    loaded: bool = False


class GPUMemoryManager:
    """
    GPU 메모리 관리자

    모델 로딩/언로딩 및 메모리 모니터링
    """

    # 모델별 예상 VRAM 사용량 (GB)
    MODEL_VRAM_REQUIREMENTS = {
        ModelType.LLM: 4.5,   # LLM (4-bit 양자화)
        ModelType.TTS: 2.5,   # TTS
        ModelType.VIDEO: 6.0,  # LTX-Video (ComfyUI)
        ModelType.OTHER: 2.0,
    }

    def __init__(self):
        """초기화"""
        self.loaded_models: Dict[str, ModelInfo] = {}
        self.cuda_available = TORCH_AVAILABLE and torch.cuda.is_available()

        if self.cuda_available:
            self.device_count = torch.cuda.device_count()
            self.device_name = torch.cuda.get_device_name(0)
            log.info(
                "GPU detected: %s (devices: %d)",
                self.device_name,
                self.device_count
            )
        else:
            log.warning("CUDA not available - GPU features disabled")

    @contextmanager
    def managed_inference(self, model_type: ModelType, model_name: str = "default"):
        """
        GPU 메모리 자동 관리 컨텍스트 매니저

        Args:
            model_type: 모델 타입
            model_name: 모델 이름

        Example:
            with gpu_manager.managed_inference(ModelType.LLM):
                output = model.generate(input)

        Yields:
            None
        """
        if not self.cuda_available:
            log.debug("CUDA not available, skipping GPU management")
            yield
            return

        model_key = f"{model_type.value}_{model_name}"

        try:
            # === 진입: 메모리 확보 ===
            log.info(
                "[GPU] Starting inference: %s (type=%s)",
                model_name,
                model_type.value
            )

            # 현재 메모리 상태
            before_stats = self.get_memory_stats()
            log.debug(
                "[GPU] Memory before: %.2f GB / %.2f GB (%.1f%% used)",
                before_stats.allocated_gb,
                before_stats.total_gb,
                before_stats.usage_percent
            )

            # 필요한 VRAM 확인
            required_vram = self.MODEL_VRAM_REQUIREMENTS.get(model_type, 2.0)

            # 메모리 부족 시 다른 모델 언로드
            if not self.can_load_model(required_vram):
                log.warning(
                    "[GPU] Insufficient memory for %s (required: %.1f GB, available: %.1f GB)",
                    model_type.value,
                    required_vram,
                    before_stats.free_gb
                )
                self._free_memory_for_model(model_type, required_vram)

            # 모델 추적 시작
            self.loaded_models[model_key] = ModelInfo(
                model_type=model_type,
                name=model_name,
                estimated_vram_gb=required_vram,
                loaded=True
            )

            # 추론 실행
            yield

        finally:
            # === 종료: 메모리 정리 ===
            # 모델 추적 종료
            if model_key in self.loaded_models:
                self.loaded_models[model_key].loaded = False

            # 메모리 해제
            self.cleanup_memory()

            # 메모리 상태 로그
            after_stats = self.get_memory_stats()
            log.info(
                "[GPU] Inference complete: %s | Memory: %.2f GB → %.2f GB (freed: %.2f GB)",
                model_name,
                before_stats.allocated_gb,
                after_stats.allocated_gb,
                before_stats.allocated_gb - after_stats.allocated_gb
            )

    def get_memory_stats(self) -> MemoryStats:
        """
        현재 메모리 통계 조회

        Returns:
            메모리 통계
        """
        if not self.cuda_available:
            return MemoryStats(0, 0, 0, 0, 0)

        try:
            total = torch.cuda.get_device_properties(0).total_memory
            reserved = torch.cuda.memory_reserved(0)
            allocated = torch.cuda.memory_allocated(0)
            free = total - reserved

            total_gb = total / (1024 ** 3)
            reserved_gb = reserved / (1024 ** 3)
            allocated_gb = allocated / (1024 ** 3)
            free_gb = free / (1024 ** 3)
            usage_percent = (allocated / total * 100) if total > 0 else 0

            return MemoryStats(
                total_gb=total_gb,
                allocated_gb=allocated_gb,
                reserved_gb=reserved_gb,
                free_gb=free_gb,
                usage_percent=usage_percent
            )
        except Exception:
            log.exception("Failed to get memory stats")
            return MemoryStats(0, 0, 0, 0, 0)

    def get_available_vram(self) -> float:
        """
        사용 가능한 VRAM (GB) 조회

        Returns:
            사용 가능한 VRAM (GB)
        """
        if not self.cuda_available:
            return 0.0

        try:
            free, total = torch.cuda.mem_get_info(0)
            return free / (1024 ** 3)
        except Exception:
            log.exception("Failed to get available VRAM")
            return 0.0

    def can_load_model(self, required_vram_gb: float, safety_margin_gb: float = 1.0) -> bool:
        """
        모델 로드 가능 여부 확인

        Args:
            required_vram_gb: 필요한 VRAM (GB)
            safety_margin_gb: 안전 여유 (GB)

        Returns:
            로드 가능 여부
        """
        available = self.get_available_vram()
        required_with_margin = required_vram_gb + safety_margin_gb

        log.debug(
            "[GPU] Load check: required=%.1f GB + margin=%.1f GB = %.1f GB, available=%.1f GB",
            required_vram_gb,
            safety_margin_gb,
            required_with_margin,
            available
        )

        return available >= required_with_margin

    def cleanup_memory(self):
        """
        메모리 정리

        캐시를 비우고 가비지 컬렉션 실행
        """
        if not self.cuda_available:
            return

        try:
            # PyTorch 캐시 비우기
            torch.cuda.empty_cache()

            # Python 가비지 컬렉션
            gc.collect()

            log.debug("[GPU] Memory cleaned up")

        except Exception:
            log.exception("Failed to cleanup memory")

    def emergency_cleanup(self):
        """
        긴급 메모리 정리

        모든 모델 언로드 및 강제 정리
        """
        log.warning("[GPU] Emergency cleanup triggered")

        if not self.cuda_available:
            return

        try:
            # 모든 모델 언로드 마킹
            for model_info in self.loaded_models.values():
                model_info.loaded = False

            # 메모리 정리
            self.cleanup_memory()

            # 추가 정리 (더 강력함)
            if hasattr(torch.cuda, 'ipc_collect'):
                torch.cuda.ipc_collect()

            log.info("[GPU] Emergency cleanup complete")

        except Exception:
            log.exception("Emergency cleanup failed")

    def _free_memory_for_model(self, target_model_type: ModelType, required_vram_gb: float):
        """
        다른 모델을 언로드하여 메모리 확보

        Args:
            target_model_type: 로드할 모델 타입
            required_vram_gb: 필요한 VRAM (GB)
        """
        log.warning(
            "[GPU] Attempting to free memory for %s (required: %.1f GB)",
            target_model_type.value,
            required_vram_gb
        )

        # 다른 타입의 로드된 모델 찾기
        unload_candidates = [
            (key, info) for key, info in self.loaded_models.items()
            if info.loaded and info.model_type != target_model_type
        ]

        if unload_candidates:
            log.info("[GPU] Unloading %d models to free memory", len(unload_candidates))

            for key, info in unload_candidates:
                log.info(
                    "[GPU] Unloading model: %s (type=%s, estimated_vram=%.1f GB)",
                    info.name,
                    info.model_type.value,
                    info.estimated_vram_gb
                )
                info.loaded = False

            # 메모리 정리
            self.cleanup_memory()

            # 확보된 메모리 확인
            available = self.get_available_vram()
            log.info("[GPU] Memory freed: %.1f GB available", available)

        else:
            log.warning("[GPU] No models to unload")

            # 그래도 부족하면 긴급 정리
            if self.get_available_vram() < required_vram_gb:
                self.emergency_cleanup()

    def monitor_memory(self) -> Dict[str, Any]:
        """
        메모리 상태 모니터링

        Returns:
            모니터링 정보 딕셔너리
        """
        stats = self.get_memory_stats()

        # 로드된 모델 정보
        loaded_models_info = []
        for key, info in self.loaded_models.items():
            if info.loaded:
                loaded_models_info.append({
                    'name': info.name,
                    'type': info.model_type.value,
                    'vram_gb': info.estimated_vram_gb
                })

        return {
            'cuda_available': self.cuda_available,
            'memory_stats': {
                'total_gb': stats.total_gb,
                'allocated_gb': stats.allocated_gb,
                'free_gb': stats.free_gb,
                'usage_percent': stats.usage_percent
            },
            'loaded_models': loaded_models_info,
            'loaded_count': len(loaded_models_info)
        }

    def log_memory_status(self):
        """메모리 상태 로그 출력"""
        monitor_info = self.monitor_memory()

        if not monitor_info['cuda_available']:
            log.info("[GPU] CUDA not available")
            return

        mem_stats = monitor_info['memory_stats']
        log.info(
            "[GPU] Memory: %.2f / %.2f GB (%.1f%% used, %.2f GB free)",
            mem_stats['allocated_gb'],
            mem_stats['total_gb'],
            mem_stats['usage_percent'],
            mem_stats['free_gb']
        )

        if monitor_info['loaded_models']:
            log.info("[GPU] Loaded models: %d", monitor_info['loaded_count'])
            for model_info in monitor_info['loaded_models']:
                log.info(
                    "  - %s (%s): ~%.1f GB",
                    model_info['name'],
                    model_info['type'],
                    model_info['vram_gb']
                )
        else:
            log.info("[GPU] No models loaded")


# ===========================================================================
# 싱글톤 인스턴스
# ===========================================================================

_gpu_manager: Optional[GPUMemoryManager] = None


def get_gpu_manager() -> GPUMemoryManager:
    """
    GPU Manager 싱글톤 인스턴스 반환

    Returns:
        GPUMemoryManager 인스턴스
    """
    global _gpu_manager
    if _gpu_manager is None:
        _gpu_manager = GPUMemoryManager()
    return _gpu_manager
