import { TelegramBotWrapper } from "./telegram-bot.js";
import { FileHandler } from "./file-handler.js";
import { ProjectExplorer } from "../explorer/project-explorer.js";
import { GitCommands } from "../explorer/git-commands.js";
import { Notifier } from "../notification/notifier.js";
import { DailyBrief } from "../scheduler/daily-brief.js";
import { MainMenu } from "./keyboard.js";
import { logger } from "../utils/logger.js";

export class CommandHandler {
  constructor(
    private wrapper: TelegramBotWrapper,
    private fileHandler: FileHandler,
    private explorer: ProjectExplorer,
    private git: GitCommands,
    private notifier: Notifier,
    private dailyBrief: DailyBrief,
  ) {}

  register(): void {
    const bot = this.wrapper.bot;
    const auth = this.wrapper.withAuth.bind(this.wrapper);

    // === Basic Commands ===
    bot.onText(/\/start/, auth(async (msg) => {
      await bot.sendMessage(
        msg.chat.id,
        "🐝 *WaggleBot Telegram Bridge*\n\n" +
        "파일 관리 · Git · 알림 전용 모드\n" +
        "Claude Code 명령은 Termius에서 직접 수행하세요\\.\n\n" +
        "아래 메뉴에서 원하는 기능을 선택하세요\\.",
        { parse_mode: "MarkdownV2", reply_markup: MainMenu },
      );
    }));

    bot.onText(/\/h(?:elp)?$/, auth(async (msg) => {
      await bot.sendMessage(msg.chat.id, HELP_TEXT);
    }));

    bot.onText(/\/menu/, auth(async (msg) => {
      await bot.sendMessage(msg.chat.id, "메뉴를 선택하세요:", { reply_markup: MainMenu });
    }));

    // === File Commands ===
    bot.onText(/\/rq(?:\s+(.+))?/, auth(async (msg, match) => {
      const arg = match?.[1]?.trim();
      if (arg === "-d") {
        await this.fileHandler.deleteRequestFiles(msg.chat.id);
        return;
      }
      await this.fileHandler.listRequestFiles(msg.chat.id);
    }));

    bot.onText(/\/rs(?:\s+(.+))?/, auth(async (msg, match) => {
      const arg = match?.[1]?.trim();
      if (arg === "-d") {
        await this.fileHandler.deleteResultFiles(msg.chat.id);
        return;
      }
      await this.fileHandler.listResultFiles(msg.chat.id);
    }));

    bot.onText(/\/f(?:\s+(.+))?/, auth(async (msg, match) => {
      const subPath = match?.[1]?.trim() || ".";
      await this.explorer.browse(msg.chat.id, subPath);
    }));

    // === Git Commands ===
    bot.onText(/\/git(?:\s+(.+))?/, auth(async (msg, match) => {
      const subCmd = match?.[1]?.trim() || "status";
      await this.git.execute(msg.chat.id, subCmd);
    }));

    // === Briefing ===
    bot.onText(/\/brief/, auth(async (msg) => {
      await this.dailyBrief.sendBrief(msg.chat.id);
    }));

    // === File Upload (document handler) ===
    bot.on("document", async (msg) => {
      const userId = msg.from?.id;
      if (!userId || !this.wrapper.isAuthorized(userId)) return;
      try {
        await this.fileHandler.handleUpload(msg);
      } catch (err) {
        logger.error("Document handler error", { error: err });
      }
    });

    // === Callback queries (inline keyboard) ===
    bot.on("callback_query", this.wrapper.withCallbackAuth(async (query) => {
      await this.handleCallback(query);
    }));

    logger.info("Command handlers registered");
  }

  private async handleCallback(query: import("node-telegram-bot-api").CallbackQuery): Promise<void> {
    const data = query.data;
    const chatId = query.message?.chat.id;
    if (!data || !chatId) return;

    await this.wrapper.bot.answerCallbackQuery(query.id);

    const [prefix, ...rest] = data.split(":");
    const value = rest.join(":");

    switch (prefix) {
      case "cmd":
        await this.handleMenuCommand(chatId, value);
        break;
      case "file":
        await this.fileHandler.sendFile(chatId, value);
        break;
      case "dir":
        await this.explorer.browse(chatId, value);
        break;
      case "reqpage":
        await this.fileHandler.listRequestFiles(chatId, parseInt(value, 10) || 0);
        break;
      case "respage":
        await this.fileHandler.listResultFiles(chatId, parseInt(value, 10) || 0);
        break;
      case "noop":
        break;
      default:
        logger.warn("Unknown callback", { prefix, value });
    }
  }

  private async handleMenuCommand(chatId: number, cmd: string): Promise<void> {
    switch (cmd) {
      case "files":
        await this.explorer.browse(chatId, ".");
        break;
      case "request":
        await this.fileHandler.listRequestFiles(chatId);
        break;
      case "result":
        await this.fileHandler.listResultFiles(chatId);
        break;
      case "git":
        await this.git.execute(chatId, "status");
        break;
      case "brief":
        await this.dailyBrief.sendBrief(chatId);
        break;
      case "back":
        await this.wrapper.bot.sendMessage(chatId, "메뉴:", { reply_markup: MainMenu });
        break;
    }
  }
}

const HELP_TEXT = `🐝 WaggleBot Telegram Bridge

📌 파일 관리 · Git · 알림 전용 모드
Claude Code 명령은 Termius + tmux에서 직접 수행

📂 파일 관리
/rq — 작업지시서 목록 (_request/)
/rq -d — 작업지시서 전체 삭제
/rs — 결과 보고서 목록 (_result/)
/rs -d — 결과 보고서 전체 삭제
/f [경로] — 디렉토리 탐색

🔧 Git (읽기전용)
/git [명령] — status, log, diff, branch

📊 기타
/brief — 프로젝트 브리핑
/menu — 메인 메뉴
/h — 이 도움말

📎 파일 업로드
파일을 전송하면 _request/에 저장됩니다.

🔔 자동 알림
• Claude Code 작업 완료 시 결과 파일 전송
• Claude Code 권한 요청 시 알림
• 일일 브리핑 (설정 시)`;
