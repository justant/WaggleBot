import TelegramBot from "node-telegram-bot-api";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";

export class TelegramBotWrapper {
  readonly bot: TelegramBot;
  private started = false;

  constructor() {
    this.bot = new TelegramBot(config.telegram.botToken, { polling: true });
  }

  isAuthorized(userId: number): boolean {
    return config.telegram.allowedUserIds.includes(userId);
  }

  withAuth(
    handler: (msg: TelegramBot.Message, match?: RegExpExecArray | null) => Promise<void>,
  ) {
    return async (msg: TelegramBot.Message, match?: RegExpExecArray | null) => {
      const userId = msg.from?.id;
      if (!userId || !this.isAuthorized(userId)) {
        logger.warn("Unauthorized access attempt", { userId, username: msg.from?.username });
        await this.bot.sendMessage(msg.chat.id, "⛔ 인증되지 않은 사용자입니다.");
        return;
      }
      try {
        await handler(msg, match);
      } catch (err) {
        logger.error("Command handler error", { error: err });
        try {
          await this.sendWithRetry(msg.chat.id, "❌ 명령 처리 중 오류가 발생했습니다.");
        } catch {
          // rate limit 등으로 에러 메시지도 전송 실패 시 무시
        }
      }
    };
  }

  withCallbackAuth(
    handler: (query: TelegramBot.CallbackQuery) => Promise<void>,
  ) {
    return async (query: TelegramBot.CallbackQuery) => {
      const userId = query.from.id;
      if (!this.isAuthorized(userId)) {
        logger.warn("Unauthorized callback attempt", { userId });
        await this.bot.answerCallbackQuery(query.id, { text: "⛔ 인증되지 않은 사용자입니다." });
        return;
      }
      try {
        await handler(query);
      } catch (err) {
        logger.error("Callback handler error", { error: err });
        await this.bot.answerCallbackQuery(query.id, { text: "❌ 오류 발생" });
      }
    };
  }

  async sendLong(chatId: number, text: string, options?: TelegramBot.SendMessageOptions): Promise<void> {
    const maxLen = config.limits.maxMessageLength;
    if (text.length <= maxLen) {
      await this.sendWithRetry(chatId, text, options);
      return;
    }
    const chunks = splitMessage(text, maxLen);
    for (let i = 0; i < chunks.length; i++) {
      if (i > 0) await sleep(1000); // 청크 간 1초 딜레이 (rate limit 방지)
      await this.sendWithRetry(chatId, chunks[i], options);
    }
  }

  /**
   * 429 rate limit 시 retry-after 만큼 대기 후 1회 재시도
   */
  private async sendWithRetry(
    chatId: number,
    text: string,
    options?: TelegramBot.SendMessageOptions,
  ): Promise<TelegramBot.Message> {
    try {
      return await this.bot.sendMessage(chatId, text, options);
    } catch (err: unknown) {
      const error = err as { response?: { body?: { retry_after?: number } } };
      const retryAfter = error?.response?.body?.retry_after;
      if (retryAfter && retryAfter > 0) {
        logger.warn(`Rate limited, waiting ${retryAfter}s before retry`);
        await sleep(retryAfter * 1000);
        return await this.bot.sendMessage(chatId, text, options);
      }
      throw err;
    }
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    this.bot.on("polling_error", (err) => {
      logger.error("Telegram polling error", { error: err.message });
    });

    const me = await this.bot.getMe();
    logger.info(`Telegram bot connected: @${me.username}`);
  }

  async stop(): Promise<void> {
    if (!this.started) return;
    await this.bot.stopPolling();
    this.started = false;
    logger.info("Telegram bot stopped");
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function splitMessage(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }
    let splitAt = remaining.lastIndexOf("\n", maxLen);
    if (splitAt === -1 || splitAt < maxLen * 0.3) {
      splitAt = maxLen;
    }
    chunks.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt);
  }
  return chunks;
}
