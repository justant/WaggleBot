import { config } from "./config.js";
import { logger } from "./utils/logger.js";
import { TelegramBotWrapper } from "./bot/telegram-bot.js";
import { CommandHandler } from "./bot/command-handler.js";
import { FileHandler } from "./bot/file-handler.js";
import { Notifier } from "./notification/notifier.js";
import { HookServer } from "./notification/hooks.js";
import { ProjectExplorer } from "./explorer/project-explorer.js";
import { GitCommands } from "./explorer/git-commands.js";
import { CronManager } from "./scheduler/cron-manager.js";
import { DailyBrief } from "./scheduler/daily-brief.js";

async function main(): Promise<void> {
  logger.info("=== WaggleBot Telegram Bridge starting ===");
  logger.info(`WaggleBot root: ${config.paths.wagglebotRoot}`);

  // Core
  const wrapper = new TelegramBotWrapper();
  const notifier = new Notifier(wrapper);

  // Feature modules
  const fileHandler = new FileHandler(wrapper);
  const explorer = new ProjectExplorer(wrapper);
  const git = new GitCommands(wrapper);
  const hookServer = new HookServer(notifier, fileHandler);
  const cronManager = new CronManager();
  const dailyBrief = new DailyBrief(wrapper);

  // Register commands
  const commandHandler = new CommandHandler(
    wrapper, fileHandler, explorer, git, notifier, dailyBrief,
  );
  commandHandler.register();

  // Start Telegram bot
  await wrapper.start();

  // Start hook server (Claude Code 이벤트 수신)
  hookServer.start();

  // Schedule daily briefing
  if (config.scheduler.dailyBriefEnabled) {
    cronManager.addJob("daily-brief", config.scheduler.dailyBriefCron, async () => {
      await dailyBrief.sendToAll();
    });
  }

  // Graceful shutdown
  const shutdown = async (signal: string) => {
    logger.info(`Received ${signal}, shutting down...`);
    cronManager.stopAll();
    hookServer.stop();
    await wrapper.stop();
    process.exit(0);
  };

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));

  logger.info("=== WaggleBot Telegram Bridge ready ===");

  // Notify users
  for (const userId of config.telegram.allowedUserIds) {
    try {
      await wrapper.bot.sendMessage(
        userId,
        "🐝 WaggleBot Bridge 시작됨\n파일 관리 · Git · 알림 전용 모드\n/help 로 사용법 확인",
      );
    } catch {
      // user might not have started the bot yet
    }
  }
}

main().catch((err) => {
  logger.error("Fatal error", { error: err });
  process.exit(1);
});
