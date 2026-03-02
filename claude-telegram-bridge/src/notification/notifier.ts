import { TelegramBotWrapper } from "../bot/telegram-bot.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { truncate } from "../utils/formatter.js";

export type NotificationType = "info" | "success" | "warning" | "error";

interface Notification {
  type: NotificationType;
  title: string;
  body: string;
  timestamp: number;
}

const ICONS: Record<NotificationType, string> = {
  info: "ℹ️",
  success: "✅",
  warning: "⚠️",
  error: "❌",
};

export class Notifier {
  private throttleMap = new Map<string, number>();
  private throttleMs = 5000; // 동일 키 5초 내 중복 방지

  constructor(private wrapper: TelegramBotWrapper) {}

  async notify(
    type: NotificationType,
    title: string,
    body: string,
    throttleKey?: string,
  ): Promise<void> {
    // 스로틀링
    if (throttleKey) {
      const lastSent = this.throttleMap.get(throttleKey) || 0;
      if (Date.now() - lastSent < this.throttleMs) {
        logger.debug("Notification throttled", { throttleKey });
        return;
      }
      this.throttleMap.set(throttleKey, Date.now());
    }

    const icon = ICONS[type];
    const message = `${icon} ${title}\n\n${truncate(body, 3500)}`;

    for (const userId of config.telegram.allowedUserIds) {
      try {
        await this.wrapper.sendLong(userId, message);
      } catch (err) {
        logger.error("Failed to send notification", { userId, error: err });
      }
    }
  }

  async notifyTaskComplete(taskName: string, resultPath?: string): Promise<void> {
    let body = `작업 "${taskName}" 이 완료되었습니다.`;
    if (resultPath) {
      body += `\n\n📄 결과: ${resultPath}`;
    }
    await this.notify("success", "작업 완료", body);
  }

  async notifyError(source: string, error: string): Promise<void> {
    await this.notify("error", `오류 발생 — ${source}`, error, `error:${source}`);
  }

  async notifyFileCreated(filePath: string): Promise<void> {
    await this.notify("info", "새 파일 생성", filePath, `file:${filePath}`);
  }
}
