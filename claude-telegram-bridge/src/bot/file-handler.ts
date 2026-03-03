import fs from "fs";
import path from "path";
import TelegramBot from "node-telegram-bot-api";
import { TelegramBotWrapper } from "./telegram-bot.js";
import { fileListKeyboard, confirmKeyboard } from "./keyboard.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { sanitizePath } from "../utils/sanitizer.js";
import { formatFileSize } from "../utils/formatter.js";

export class FileHandler {
  constructor(private wrapper: TelegramBotWrapper) {}

  /**
   * 파일 업로드 → _request/ 에 저장 (모든 파일 타입)
   */
  async handleUpload(msg: TelegramBot.Message): Promise<void> {
    const doc = msg.document;
    if (!doc) return;

    const fileName = doc.file_name || "unknown";

    if (doc.file_size && doc.file_size > config.limits.maxFileSize) {
      await this.wrapper.bot.sendMessage(
        msg.chat.id,
        `❌ 파일이 너무 큽니다 (${formatFileSize(doc.file_size)}). 최대: ${formatFileSize(config.limits.maxFileSize)}`,
      );
      return;
    }

    try {
      const requestDir = path.join(config.paths.wagglebotRoot, "_request");
      if (!fs.existsSync(requestDir)) {
        fs.mkdirSync(requestDir, { recursive: true });
      }

      const fileLink = await this.wrapper.bot.getFileLink(doc.file_id);
      const response = await fetch(fileLink);
      const buffer = Buffer.from(await response.arrayBuffer());

      const targetPath = path.join(requestDir, fileName);
      fs.writeFileSync(targetPath, buffer);

      logger.info("File uploaded to _request/", { fileName, size: buffer.length });

      await this.wrapper.bot.sendMessage(
        msg.chat.id,
        `✅ 저장 완료: _request/${fileName}\n📦 크기: ${formatFileSize(buffer.length)}`,
      );
    } catch (err) {
      logger.error("File upload failed", { error: err });
      await this.wrapper.bot.sendMessage(msg.chat.id, "❌ 파일 저장 실패");
    }
  }

  /**
   * _request/ 디렉토리 파일 목록
   */
  async listRequestFiles(chatId: number, page: number = 0): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_request");
    await this.listDirectory(chatId, dir, "_request", "file", page, "reqpage");
  }

  /**
   * _result/ 디렉토리 파일 목록
   */
  async listResultFiles(chatId: number, page: number = 0): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_result");
    await this.listDirectory(chatId, dir, "_result", "file", page, "respage");
  }

  /**
   * _request/ 디렉토리 전체 파일 삭제
   */
  async deleteRequestFiles(chatId: number): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_request");
    await this.deleteDirectoryFiles(chatId, dir, "_request");
  }

  /**
   * _result/ 디렉토리 파일 삭제 (sample/ 하위 디렉토리 보존)
   */
  async deleteResultFiles(chatId: number): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_result");
    await this.deleteDirectoryFiles(chatId, dir, "_result", ["sample"]);
  }

  private async listDirectory(
    chatId: number,
    dir: string,
    label: string,
    prefix: string,
    page: number = 0,
    pagePrefix?: string,
  ): Promise<void> {
    try {
      if (!fs.existsSync(dir)) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 디렉토리가 비어있습니다.`);
        return;
      }

      const files = fs.readdirSync(dir)
        .filter((f) => {
          const fullPath = path.join(dir, f);
          return fs.statSync(fullPath).isFile();
        })
        .sort()
        .map((name) => ({
          name,
          path: path.join(dir, name),
        }));

      if (files.length === 0) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 에 파일이 없습니다.`);
        return;
      }

      const keyboard = fileListKeyboard(files, prefix, page, pagePrefix);
      await this.wrapper.bot.sendMessage(
        chatId,
        `📂 ${label}/ (${files.length}개 파일)`,
        { reply_markup: keyboard },
      );
    } catch (err) {
      logger.error("List directory failed", { dir, error: err });
      await this.wrapper.bot.sendMessage(chatId, `❌ ${label}/ 조회 실패`);
    }
  }

  private async deleteDirectoryFiles(
    chatId: number,
    dir: string,
    label: string,
    preserveDirs: string[] = [],
  ): Promise<void> {
    try {
      if (!fs.existsSync(dir)) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 디렉토리가 비어있습니다.`);
        return;
      }

      const entries = fs.readdirSync(dir);
      let deletedCount = 0;

      for (const entry of entries) {
        const fullPath = path.join(dir, entry);
        const stat = fs.statSync(fullPath);

        if (stat.isFile()) {
          fs.unlinkSync(fullPath);
          deletedCount++;
        } else if (stat.isDirectory() && !preserveDirs.includes(entry)) {
          fs.rmSync(fullPath, { recursive: true });
          deletedCount++;
        }
      }

      if (deletedCount === 0) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 에 삭제할 파일이 없습니다.`);
      } else {
        logger.info(`Deleted ${deletedCount} items from ${label}/`);
        await this.wrapper.bot.sendMessage(
          chatId,
          `🗑 ${label}/ 에서 ${deletedCount}개 항목을 삭제했습니다.`,
        );
      }
    } catch (err) {
      logger.error("Delete directory files failed", { dir, error: err });
      await this.wrapper.bot.sendMessage(chatId, `❌ ${label}/ 삭제 실패`);
    }
  }

  /**
   * 파일을 Telegram으로 전송
   * @param forceDocument true이면 항상 다운로드 가능한 문서로 전송
   */
  async sendFile(chatId: number, filePath: string, forceDocument = false): Promise<void> {
    const resolved = sanitizePath(filePath);
    if (!resolved) {
      await this.wrapper.bot.sendMessage(chatId, "⛔ 접근할 수 없는 경로입니다.");
      return;
    }

    try {
      if (!fs.existsSync(resolved)) {
        await this.wrapper.bot.sendMessage(chatId, "❌ 파일을 찾을 수 없습니다.");
        return;
      }

      const stat = fs.statSync(resolved);
      if (stat.isDirectory()) {
        await this.wrapper.bot.sendMessage(chatId, "❌ 디렉토리는 전송할 수 없습니다. /f 명령어를 사용하세요.");
        return;
      }

      if (stat.size > config.limits.maxFileSize) {
        await this.wrapper.bot.sendMessage(
          chatId,
          `❌ 파일이 너무 큽니다 (${formatFileSize(stat.size)})`,
        );
        return;
      }

      const ext = path.extname(resolved).toLowerCase();
      const relPath = path.relative(config.paths.wagglebotRoot, resolved);
      const isText = [".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js"].includes(ext);
      const isPhoto = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"].includes(ext);
      const isVideo = [".mp4", ".mov", ".avi", ".mkv", ".webm"].includes(ext);

      if (forceDocument) {
        await this.wrapper.bot.sendDocument(chatId, resolved, {
          caption: `📄 ${relPath}`,
        });
      } else if (isText && stat.size < 4000) {
        const content = fs.readFileSync(resolved, "utf-8");
        const fileName = path.basename(resolved);
        await this.wrapper.sendLong(chatId, `📄 ${fileName}\n\`\`\`\n${content}\n\`\`\``);
      } else if (isPhoto) {
        await this.wrapper.bot.sendPhoto(chatId, resolved, {
          caption: `🖼 ${relPath}`,
        });
      } else if (isVideo) {
        await this.wrapper.bot.sendVideo(chatId, resolved, {
          caption: `🎬 ${relPath}`,
        });
      } else {
        await this.wrapper.bot.sendDocument(chatId, resolved, {
          caption: `📄 ${relPath}`,
        });
      }

      logger.info("File sent", { path: resolved, size: stat.size });
    } catch (err) {
      logger.error("Send file failed", { filePath: resolved, error: err });
      await this.wrapper.bot.sendMessage(chatId, "❌ 파일 전송 실패");
    }
  }
}
