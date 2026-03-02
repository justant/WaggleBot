import fs from "fs";
import path from "path";
import TelegramBot from "node-telegram-bot-api";
import { TelegramBotWrapper } from "./telegram-bot.js";
import { fileListKeyboard } from "./keyboard.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { sanitizePath } from "../utils/sanitizer.js";
import { formatFileSize } from "../utils/formatter.js";

export class FileHandler {
  constructor(private wrapper: TelegramBotWrapper) {}

  /**
   * .md 파일 업로드 → _request/ 에 저장
   */
  async handleUpload(msg: TelegramBot.Message): Promise<void> {
    const doc = msg.document;
    if (!doc) return;

    const fileName = doc.file_name || "unknown";
    if (!fileName.endsWith(".md")) {
      await this.wrapper.bot.sendMessage(
        msg.chat.id,
        "📎 .md 파일만 업로드할 수 있습니다.",
      );
      return;
    }

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
  async listRequestFiles(chatId: number): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_request");
    await this.listDirectory(chatId, dir, "_request", "request");
  }

  /**
   * _result/ 디렉토리 파일 목록
   */
  async listResultFiles(chatId: number): Promise<void> {
    const dir = path.join(config.paths.wagglebotRoot, "_result");
    await this.listDirectory(chatId, dir, "_result", "result");
  }

  private async listDirectory(
    chatId: number,
    dir: string,
    label: string,
    prefix: string,
  ): Promise<void> {
    try {
      if (!fs.existsSync(dir)) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 디렉토리가 비어있습니다.`);
        return;
      }

      const files = fs.readdirSync(dir)
        .filter((f) => f.endsWith(".md"))
        .map((name) => ({
          name,
          path: path.join(dir, name),
        }));

      if (files.length === 0) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${label}/ 에 .md 파일이 없습니다.`);
        return;
      }

      const keyboard = fileListKeyboard(files, "file");
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
        await this.wrapper.bot.sendMessage(chatId, "❌ 디렉토리는 전송할 수 없습니다. /files 명령어를 사용하세요.");
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
        // 다운로드 가능한 문서로 전송
        await this.wrapper.bot.sendDocument(chatId, resolved, {
          caption: `📄 ${relPath}`,
        });
      } else if (isText && stat.size < 4000) {
        // 작은 텍스트 파일은 메시지로 인라인 전송
        const content = fs.readFileSync(resolved, "utf-8");
        const fileName = path.basename(resolved);
        await this.wrapper.sendLong(chatId, `📄 ${fileName}\n\`\`\`\n${content}\n\`\`\``);
      } else if (isPhoto) {
        // 사진: 미리보기 + 파일 다운로드
        await this.wrapper.bot.sendPhoto(chatId, resolved, {
          caption: `🖼 ${relPath}`,
        });
      } else if (isVideo) {
        // 영상: Telegram 비디오 플레이어로 전송
        await this.wrapper.bot.sendVideo(chatId, resolved, {
          caption: `🎬 ${relPath}`,
        });
      } else {
        // 그 외: document로 전송
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
