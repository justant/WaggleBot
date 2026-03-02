import http from "http";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { Notifier } from "./notifier.js";
import { FileHandler } from "../bot/file-handler.js";

interface HookPayload {
  event: string;
  data?: Record<string, unknown>;
}

/**
 * HTTP 훅 서버 — Claude Code 이벤트를 수신하여 Telegram으로 전달
 *
 * Stop hook: scripts/stop-notify.mjs 가 last_assistant_message + _result/ 파일 목록 전송
 * Notification hook: permission_prompt 시 Termius 확인 알림
 */
export class HookServer {
  private server: http.Server | null = null;

  constructor(
    private notifier: Notifier,
    private fileHandler: FileHandler,
  ) {}

  start(): void {
    this.server = http.createServer(async (req, res) => {
      if (req.method !== "POST" || req.url !== "/hook") {
        res.writeHead(404);
        res.end("Not Found");
        return;
      }

      try {
        const body = await readBody(req);
        const payload = JSON.parse(body) as HookPayload;

        await this.handleHook(payload);

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      } catch (err) {
        logger.error("Hook processing error", { error: err });
        res.writeHead(400);
        res.end("Bad Request");
      }
    });

    this.server.listen(config.hooks.port, () => {
      logger.info(`Hook server listening on port ${config.hooks.port}`);
    });
  }

  private async handleHook(payload: HookPayload): Promise<void> {
    logger.info("Hook received", { event: payload.event });

    switch (payload.event) {
      // Claude Code 작업 완료 — 상세 메시지 + 결과 파일 전송
      case "stop":
      case "task_complete": {
        const message = String(payload.data?.message || "Claude Code 작업이 완료되었습니다.");
        const resultFiles = (payload.data?.resultFiles as string[]) || [];

        // 상세 알림 메시지 전송
        let body = message;
        if (resultFiles.length > 0) {
          const fileNames = resultFiles.map((f) => {
            const name = f.split("/").pop() || f;
            return `  - ${name}`;
          });
          body += `\n\n📎 결과 파일 (${resultFiles.length}개):\n${fileNames.join("\n")}`;
        }
        await this.notifier.notify("success", "작업 완료", body);

        // 결과 파일들 Telegram으로 전송
        for (const userId of config.telegram.allowedUserIds) {
          for (const filePath of resultFiles) {
            try {
              await this.fileHandler.sendFile(userId, filePath);
            } catch (err) {
              logger.error("Failed to send result file", { filePath, error: err });
            }
          }
        }
        break;
      }

      // 에러 발생
      case "error":
        await this.notifier.notifyError(
          String(payload.data?.source || "Claude Code"),
          String(payload.data?.message || "알 수 없는 오류"),
        );
        break;

      // 새 파일 생성
      case "file_created":
        await this.notifier.notifyFileCreated(
          String(payload.data?.path || "unknown"),
        );
        break;

      // Claude Code가 사용자 입력/승인 대기 중
      case "permission_request":
      case "user_input_needed":
        await this.notifier.notify(
          "warning",
          "Termius 확인 필요",
          `Claude Code가 응답을 기다리고 있습니다.\n${String(payload.data?.description || payload.data?.message || "tmux 세션을 확인하세요.")}`,
        );
        break;

      // 일반 알림
      case "notification":
        await this.notifier.notify(
          "info",
          String(payload.data?.title || "알림"),
          String(payload.data?.message || ""),
        );
        break;

      default:
        logger.warn("Unknown hook event", { event: payload.event });
        await this.notifier.notify(
          "info",
          `Hook: ${payload.event}`,
          JSON.stringify(payload.data || {}).slice(0, 500),
        );
    }
  }

  stop(): void {
    this.server?.close();
    this.server = null;
    logger.info("Hook server stopped");
  }
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}
