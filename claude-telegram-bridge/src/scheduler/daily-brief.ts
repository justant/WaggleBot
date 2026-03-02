import fs from "fs";
import path from "path";
import simpleGit from "simple-git";
import { TelegramBotWrapper } from "../bot/telegram-bot.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";

export class DailyBrief {
  constructor(private wrapper: TelegramBotWrapper) {}

  async sendBrief(chatId: number): Promise<void> {
    try {
      const brief = await this.generateBrief();
      await this.wrapper.sendLong(chatId, brief);
    } catch (err) {
      logger.error("Daily brief generation failed", { error: err });
      await this.wrapper.bot.sendMessage(chatId, "❌ 브리핑 생성 실패");
    }
  }

  async sendToAll(): Promise<void> {
    const brief = await this.generateBrief();
    for (const userId of config.telegram.allowedUserIds) {
      try {
        await this.wrapper.sendLong(userId, brief);
      } catch (err) {
        logger.error("Failed to send daily brief", { userId, error: err });
      }
    }
  }

  private async generateBrief(): Promise<string> {
    const root = config.paths.wagglebotRoot;
    const sections: string[] = ["📊 WaggleBot 일일 브리핑\n"];

    // Git 상태
    try {
      const git = simpleGit(root);
      const status = await git.status();
      const log = await git.log({ maxCount: 5 });

      sections.push("🔧 Git");
      sections.push(`브랜치: ${status.current}`);
      if (status.modified.length > 0) {
        sections.push(`수정됨: ${status.modified.length}개 파일`);
      }
      if (log.all.length > 0) {
        sections.push("\n최근 커밋:");
        for (const c of log.all.slice(0, 3)) {
          sections.push(`  • ${c.hash.slice(0, 7)} ${c.message.split("\n")[0]}`);
        }
      }
    } catch {
      sections.push("🔧 Git: 정보 없음");
    }

    // _request/ 현황
    const requestDir = path.join(root, "_request");
    if (fs.existsSync(requestDir)) {
      const files = fs.readdirSync(requestDir).filter((f) => f.endsWith(".md"));
      sections.push(`\n📋 작업지시서: ${files.length}개`);
      for (const f of files.slice(0, 5)) {
        sections.push(`  • ${f}`);
      }
    }

    // _result/ 현황
    const resultDir = path.join(root, "_result");
    if (fs.existsSync(resultDir)) {
      const files = fs.readdirSync(resultDir).filter((f) => f.endsWith(".md"));
      sections.push(`\n📊 결과보고서: ${files.length}개`);
      for (const f of files.slice(0, 5)) {
        sections.push(`  • ${f}`);
      }
    }

    // 디스크 사용량 (media/)
    const mediaDir = path.join(root, "media");
    if (fs.existsSync(mediaDir)) {
      try {
        const size = await getDirSize(mediaDir);
        const gb = (size / (1024 * 1024 * 1024)).toFixed(2);
        sections.push(`\n💾 미디어: ${gb}GB`);
      } catch {
        // skip
      }
    }

    sections.push(`\n⏰ ${new Date().toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}`);

    return sections.join("\n");
  }
}

async function getDirSize(dirPath: string): Promise<number> {
  let total = 0;
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name);
      if (entry.isFile()) {
        total += fs.statSync(fullPath).size;
      } else if (entry.isDirectory()) {
        total += await getDirSize(fullPath);
      }
    }
  } catch {
    // permission error etc
  }
  return total;
}
