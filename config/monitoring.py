"""모니터링 및 알림 설정."""
import os

from dotenv import load_dotenv

load_dotenv()

MONITORING_ENABLED: bool = os.getenv("MONITORING_ENABLED", "true").lower() == "true"
HEALTH_CHECK_INTERVAL: int = int(os.getenv("HEALTH_CHECK_INTERVAL", "300"))  # 5분

# 임계값
GPU_TEMP_WARNING: int = int(os.getenv("GPU_TEMP_WARNING", "75"))
GPU_TEMP_CRITICAL: int = int(os.getenv("GPU_TEMP_CRITICAL", "80"))
DISK_USAGE_WARNING: int = int(os.getenv("DISK_USAGE_WARNING", "80"))
DISK_USAGE_CRITICAL: int = int(os.getenv("DISK_USAGE_CRITICAL", "90"))
MEMORY_USAGE_WARNING: int = int(os.getenv("MEMORY_USAGE_WARNING", "85"))
MEMORY_USAGE_CRITICAL: int = int(os.getenv("MEMORY_USAGE_CRITICAL", "95"))

# 이메일 알림
EMAIL_ALERTS_ENABLED: bool = os.getenv("EMAIL_ALERTS_ENABLED", "false").lower() == "true"
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO: list[str] = (
    os.getenv("ALERT_EMAIL_TO", "").split(",") if os.getenv("ALERT_EMAIL_TO") else []
)

# 슬랙 알림
SLACK_ALERTS_ENABLED: bool = os.getenv("SLACK_ALERTS_ENABLED", "false").lower() == "true"
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
