"""
Monitoring System Test Script

모니터링 시스템을 테스트하는 간단한 스크립트
"""

import logging
from monitoring.alerting import AlertLevel, get_alert_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

log = logging.getLogger(__name__)


def test_alert_levels():
    """알림 레벨별 테스트"""
    log.info("=== Testing Alert Levels ===")

    manager = get_alert_manager()

    # INFO 레벨
    manager.send_alert(
        AlertLevel.INFO,
        "테스트 INFO 알림",
        "이것은 정보성 알림입니다."
    )

    # WARNING 레벨
    manager.send_alert(
        AlertLevel.WARNING,
        "테스트 WARNING 알림",
        "이것은 경고 알림입니다."
    )

    # CRITICAL 레벨 (실제 이메일/슬랙 전송 시도)
    manager.send_alert(
        AlertLevel.CRITICAL,
        "테스트 CRITICAL 알림",
        "이것은 심각한 알림입니다."
    )


def test_health_check():
    """헬스 체크 테스트"""
    log.info("=== Testing Health Check ===")

    manager = get_alert_manager()
    health_status = manager.check_health()

    log.info("Health Status:")
    log.info("  CPU: %.1f%%", health_status.get('cpu_percent') or 0)
    log.info("  Memory: %.1f%%", health_status.get('memory_percent') or 0)
    log.info("  Disk: %.1f%%", health_status.get('disk_percent') or 0)
    log.info("  GPU Temp: %s°C", health_status.get('gpu_temp') or 'N/A')
    log.info("  GPU Memory: %.1f%%", health_status.get('gpu_memory_percent') or 0)
    log.info("  DB Connected: %s", health_status.get('db_connected'))
    log.info("  Active Alerts: %d", len(health_status.get('alerts', [])))

    for alert in health_status.get('alerts', []):
        log.warning("  - %s", alert)


def test_individual_checks():
    """개별 체크 함수 테스트"""
    log.info("=== Testing Individual Checks ===")

    manager = get_alert_manager()

    gpu_temp = manager.get_gpu_temp()
    log.info("GPU Temperature: %s°C", gpu_temp if gpu_temp else 'N/A')

    disk_usage = manager.get_disk_usage()
    log.info("Disk Usage: %s%%", disk_usage if disk_usage else 'N/A')

    db_connected = manager.check_db_connection()
    log.info("DB Connection: %s", 'OK' if db_connected else 'FAIL')


if __name__ == '__main__':
    log.info("Starting monitoring system test...")
    log.info("")

    try:
        test_alert_levels()
        log.info("")

        test_individual_checks()
        log.info("")

        test_health_check()
        log.info("")

        log.info("All tests completed successfully!")

    except Exception:
        log.exception("Test failed")
