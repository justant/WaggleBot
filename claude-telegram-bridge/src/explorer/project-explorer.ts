import fs from "fs";
import path from "path";
import { TelegramBotWrapper } from "../bot/telegram-bot.js";
import { buildKeyboard, button } from "../bot/keyboard.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { sanitizePath } from "../utils/sanitizer.js";
import { formatFileSize } from "../utils/formatter.js";

const IGNORED_DIRS = new Set([
  "node_modules", ".git", "__pycache__", "venv", ".venv",
  "dist", "build", ".cache", ".claude",
]);

const PAGE_SIZE = 20;

export class ProjectExplorer {
  constructor(private wrapper: TelegramBotWrapper) {}

  async browse(chatId: number, subPath: string, page: number = 0): Promise<void> {
    const resolved = sanitizePath(subPath);
    if (!resolved) {
      await this.wrapper.bot.sendMessage(chatId, "⛔ 접근할 수 없는 경로입니다.");
      return;
    }

    try {
      if (!fs.existsSync(resolved)) {
        await this.wrapper.bot.sendMessage(chatId, "❌ 경로를 찾을 수 없습니다.");
        return;
      }

      const stat = fs.statSync(resolved);
      if (!stat.isDirectory()) {
        // 파일이면 내용 미리보기
        await this.previewFile(chatId, resolved);
        return;
      }

      const entries = fs.readdirSync(resolved, { withFileTypes: true })
        .filter((e) => !IGNORED_DIRS.has(e.name))
        .sort((a, b) => {
          // 디렉토리 먼저
          if (a.isDirectory() && !b.isDirectory()) return -1;
          if (!a.isDirectory() && b.isDirectory()) return 1;
          return a.name.localeCompare(b.name);
        });

      if (entries.length === 0) {
        await this.wrapper.bot.sendMessage(chatId, `📂 ${this.relPath(resolved)} — 비어있음`);
        return;
      }

      const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
      const safePage = Math.max(0, Math.min(page, totalPages - 1));
      const start = safePage * PAGE_SIZE;
      const displayEntries = entries.slice(start, start + PAGE_SIZE);

      const rows = displayEntries.map((e) => {
        const icon = e.isDirectory() ? "📁" : getFileIcon(e.name);
        const entryPath = path.join(resolved, e.name);
        const cbPrefix = e.isDirectory() ? "dir" : "file";
        return [button(`${icon} ${e.name}`, `${cbPrefix}:${entryPath}`)];
      });

      // 페이지네이션 버튼
      if (totalPages > 1) {
        const navButtons = [
          ...(safePage > 0 ? [button("◀ 이전", `dirpage:${safePage - 1}:${resolved}`)] : []),
          button(`${safePage + 1}/${totalPages}`, "noop"),
          ...(safePage < totalPages - 1 ? [button("다음 ▶", `dirpage:${safePage + 1}:${resolved}`)] : []),
        ];
        rows.push(navButtons);
      }

      // 상위 디렉토리 버튼
      const parent = path.dirname(resolved);
      const parentSafe = sanitizePath(parent);
      if (parentSafe && parentSafe !== resolved) {
        rows.push([button("📁 ..", `dir:${parentSafe}`)]);
      }

      await this.wrapper.bot.sendMessage(
        chatId,
        `📂 ${this.relPath(resolved)} (${entries.length}개)`,
        { reply_markup: buildKeyboard(rows) },
      );
    } catch (err) {
      logger.error("Browse failed", { path: resolved, error: err });
      await this.wrapper.bot.sendMessage(chatId, "❌ 디렉토리 탐색 실패");
    }
  }

  private async previewFile(chatId: number, filePath: string): Promise<void> {
    const stat = fs.statSync(filePath);
    const ext = path.extname(filePath).toLowerCase();
    const baseName = path.basename(filePath).toLowerCase();
    const TEXT_EXTS = [".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js", ".sh", ".cfg", ".ini", ".env"];
    const TEXT_DOTFILES = [".env", ".env.local", ".env.production", ".gitignore", ".dockerignore", ".editorconfig"];
    const isText = TEXT_EXTS.includes(ext) || TEXT_DOTFILES.includes(baseName);

    let preview = `📄 ${this.relPath(filePath)}\n📦 ${formatFileSize(stat.size)}\n`;

    if (isText && stat.size < 3000) {
      const content = fs.readFileSync(filePath, "utf-8");
      preview += `\n\`\`\`\n${content.slice(0, 2500)}\n\`\`\``;
    } else if (isText) {
      const content = fs.readFileSync(filePath, "utf-8");
      const lines = content.split("\n").slice(0, 30).join("\n");
      preview += `\n(처음 30줄)\n\`\`\`\n${lines}\n\`\`\``;
    } else {
      preview += "\n(바이너리 파일 — /send로 다운로드)";
    }

    await this.wrapper.sendLong(chatId, preview);
  }

  private relPath(absPath: string): string {
    return path.relative(config.paths.wagglebotRoot, absPath) || ".";
  }
}

function getFileIcon(name: string): string {
  const ext = path.extname(name).toLowerCase();
  const icons: Record<string, string> = {
    ".py": "🐍", ".ts": "📘", ".js": "📒", ".json": "📋",
    ".md": "📝", ".yml": "⚙️", ".yaml": "⚙️", ".sh": "🔧",
    ".dockerfile": "🐳", ".sql": "🗃️", ".env": "🔒",
  };
  if (name === "Dockerfile") return "🐳";
  return icons[ext] || "📄";
}
