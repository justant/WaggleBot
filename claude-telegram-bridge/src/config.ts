import { config as dotenvConfig } from "dotenv";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// bridge 디렉토리 .env → WaggleBot 루트 .env 순서로 탐색
dotenvConfig({ path: path.resolve(__dirname, "..", ".env") });
dotenvConfig({ path: path.resolve(__dirname, "..", "..", ".env") });

function requireEnv(key: string): string {
  const value = process.env[key];
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
  return value;
}

function optionalEnv(key: string, defaultValue: string): string {
  return process.env[key] || defaultValue;
}

function parseUserIds(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n));
}

export const config = {
  telegram: {
    botToken: requireEnv("TELEGRAM_BOT_TOKEN"),
    allowedUserIds: parseUserIds(requireEnv("ALLOWED_USER_IDS")),
  },
  paths: {
    wagglebotRoot: optionalEnv(
      "WAGGLEBOT_ROOT",
      path.resolve(__dirname, "..", ".."),
    ),
    bridgeRoot: path.resolve(__dirname, ".."),
    dataDir: path.resolve(__dirname, "..", "data"),
    logsDir: path.resolve(__dirname, "..", "data", "logs"),
  },
  scheduler: {
    dailyBriefCron: optionalEnv("DAILY_BRIEF_CRON", "0 9 * * *"),
    dailyBriefEnabled:
      optionalEnv("DAILY_BRIEF_ENABLED", "false") === "true",
  },
  hooks: {
    port: parseInt(optionalEnv("HOOKS_PORT", "3847"), 10),
  },
  logging: {
    level: optionalEnv("LOG_LEVEL", "info"),
  },
  limits: {
    maxFileSize: 50 * 1024 * 1024, // 50MB
    maxMessageLength: 4096, // Telegram limit
  },
} as const;

export type Config = typeof config;
