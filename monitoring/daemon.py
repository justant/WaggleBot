"""
Monitoring Daemon

주기적으로 시스템 헬스 체크를 수행하는 백그라운드 프로세스
"""

import logging
import time
from datetime import datetime

from config import settings
from monitoring.alerting import get_alert_manager

log = logging.getLogger(__name__)


def run_monitoring_loop():
    """
    모니터링 메인 루프

    설정된 간격마다 헬스 체크를 실행하고 알림 전송
    """
    if not settings.MONITORING_ENABLED:
        log.warning("Monitoring is disabled in settings")
        return

    log.info(
        "Starting monitoring daemon (interval: %ds)",
        settings.HEALTH_CHECK_INTERVAL
    )

    alert_manager = get_alert_manager()

    while True:
        try:
            log.info("=== Health Check Started: %s ===", datetime.now().isoformat())
            health_status = alert_manager.check_health()

            # 헬스 상태 요약 출력
            log.info(
                "CPU: %.1f%% | MEM: %.1f%% | DISK: %.1f%% | GPU: %s°C | DB: %s",
                health_status.get('cpu_percent') or 0,
                health_status.get('memory_percent') or 0,
                health_status.get('disk_percent') or 0,
                health_status.get('gpu_temp') or 'N/A',
                'OK' if health_status.get('db_connected') else 'FAIL'
            )

            if health_status['alerts']:
                log.warning("Active alerts: %d", len(health_status['alerts']))
                for alert in health_status['alerts']:
                    log.warning("  - %s", alert)

            log.info("=== Health Check Completed ===")

        except Exception:
            log.exception("Health check failed")

        # 다음 체크까지 대기
        time.sleep(settings.HEALTH_CHECK_INTERVAL)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        run_monitoring_loop()
    except KeyboardInterrupt:
        log.info("Monitoring daemon stopped by user")
    except Exception:
        log.exception("Monitoring daemon crashed")
