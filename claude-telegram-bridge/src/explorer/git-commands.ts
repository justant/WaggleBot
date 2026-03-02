import simpleGit, { SimpleGit } from "simple-git";
import { TelegramBotWrapper } from "../bot/telegram-bot.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { isGitCommandSafe } from "../utils/sanitizer.js";
import { truncate } from "../utils/formatter.js";

export class GitCommands {
  private git: SimpleGit;

  constructor(private wrapper: TelegramBotWrapper) {
    this.git = simpleGit(config.paths.wagglebotRoot);
  }

  async execute(chatId: number, subCommand: string): Promise<void> {
    if (!isGitCommandSafe(subCommand)) {
      await this.wrapper.bot.sendMessage(
        chatId,
        "⛔ 허용되지 않는 Git 명령어입니다.\n사용 가능: status, log, diff, branch, show, remote, tag",
      );
      return;
    }

    try {
      const cmd = subCommand.trim().split(/\s+/);
      const mainCmd = cmd[0];
      const args = cmd.slice(1);

      let result: string;

      switch (mainCmd) {
        case "status":
          result = await this.git.status().then((s) => {
            const lines: string[] = [`🔧 Git Status — ${s.current || "unknown"}`];
            if (s.modified.length > 0) {
              const shown = s.modified.slice(0, 15);
              lines.push(`\n📝 수정됨 (${s.modified.length}):\n${shown.join("\n")}`);
              if (s.modified.length > 15) lines.push(`  ... 외 ${s.modified.length - 15}개`);
            }
            if (s.not_added.length > 0) {
              const shown = s.not_added.filter((f) => !f.startsWith(".venv/") && !f.startsWith("node_modules/")).slice(0, 10);
              lines.push(`\n❓ 추적안됨 (${s.not_added.length}개)`);
              if (shown.length > 0) lines.push(shown.join("\n"));
              if (s.not_added.length > shown.length) lines.push(`  ... 외 ${s.not_added.length - shown.length}개 (venv/node_modules 등 제외)`);
            }
            if (s.staged.length > 0) {
              const shown = s.staged.slice(0, 15);
              lines.push(`\n✅ 스테이징 (${s.staged.length}):\n${shown.join("\n")}`);
              if (s.staged.length > 15) lines.push(`  ... 외 ${s.staged.length - 15}개`);
            }
            if (s.ahead > 0) lines.push(`\n⬆ ${s.ahead} 커밋 ahead`);
            if (s.behind > 0) lines.push(`\n⬇ ${s.behind} 커밋 behind`);
            if (lines.length === 1) lines.push("\n✨ 깨끗한 워킹 트리");
            return lines.join("");
          });
          break;

        case "log":
          result = await this.git.log({ maxCount: 10, ...parseLogArgs(args) }).then((log) => {
            const lines = log.all.map((c) =>
              `• ${c.hash.slice(0, 7)} ${c.message.split("\n")[0]} (${c.author_name})`,
            );
            return `📜 최근 커밋:\n${lines.join("\n")}`;
          });
          break;

        case "diff":
          result = await this.git.diff(args).then((d) => {
            if (!d.trim()) return "📋 변경사항 없음";
            return `📋 Diff:\n\`\`\`\n${truncate(d, 3000)}\n\`\`\``;
          });
          break;

        case "branch":
          result = await this.git.branch().then((b) => {
            const lines = b.all.map((name) =>
              name === b.current ? `• *${name} (현재)` : `• ${name}`,
            );
            return `🌿 브랜치:\n${lines.join("\n")}`;
          });
          break;

        case "remote":
          result = await this.git.remote(["--verbose"]).then((r) => {
            return `🔗 Remote:\n${r || "(없음)"}`;
          });
          break;

        case "tag":
          result = await this.git.tags().then((t) => {
            if (t.all.length === 0) return "🏷 태그 없음";
            return `🏷 태그:\n${t.all.slice(-10).join("\n")}`;
          });
          break;

        default:
          result = "❌ 지원되지 않는 명령어";
      }

      await this.wrapper.sendLong(chatId, result);
    } catch (err) {
      logger.error("Git command failed", { subCommand, error: err });
      const errMsg = err instanceof Error ? err.message : String(err);
      try {
        await this.wrapper.sendLong(chatId, `❌ Git 오류: ${errMsg}`);
      } catch {
        // rate limit 등으로 에러 메시지도 전송 실패 시 무시
      }
    }
  }
}

function parseLogArgs(args: string[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "-n" && args[i + 1]) {
      result.maxCount = parseInt(args[i + 1], 10);
      i++;
    }
  }
  return result;
}
