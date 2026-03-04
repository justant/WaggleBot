import path from "path";
import { config } from "../config.js";

const BLOCKED_PATTERNS = [
  /credentials\.json$/i,
  /token\.json$/i,
  /secrets\//i,
  /\.ssh\//i,
  /\.gnupg\//i,
];

/**
 * 경로가 허용된 sandbox 내부인지 검증
 */
export function isPathSafe(targetPath: string): boolean {
  const resolved = path.resolve(targetPath);
  const root = path.resolve(config.paths.wagglebotRoot);
  const bridge = path.resolve(config.paths.bridgeRoot);

  // sandbox roots: WaggleBot 루트 및 bridge 디렉토리
  if (!resolved.startsWith(root) && !resolved.startsWith(bridge)) {
    return false;
  }

  // 민감한 파일 패턴 차단
  for (const pattern of BLOCKED_PATTERNS) {
    if (pattern.test(resolved)) {
      return false;
    }
  }

  return true;
}

/**
 * 경로 정규화 및 탈출 방지
 */
export function sanitizePath(rawPath: string, basePath?: string): string | null {
  const base = basePath || config.paths.wagglebotRoot;

  // null bytes 제거
  const cleaned = rawPath.replace(/\0/g, "");

  // 절대 경로면 그대로, 상대 경로면 base 기준
  const resolved = path.isAbsolute(cleaned)
    ? path.resolve(cleaned)
    : path.resolve(base, cleaned);

  if (!isPathSafe(resolved)) {
    return null;
  }

  return resolved;
}

/**
 * 사용자 입력 텍스트 검증
 */
export function sanitizeInput(input: string, maxLength = 2000): string {
  return input
    .replace(/\0/g, "")
    .trim()
    .slice(0, maxLength);
}

/**
 * Git 명령어가 읽기전용인지 검증
 */
const ALLOWED_GIT_COMMANDS = new Set([
  "status",
  "log",
  "diff",
  "branch",
  "show",
  "remote",
  "tag",
]);

export function isGitCommandSafe(subCommand: string): boolean {
  const cmd = subCommand.trim().split(/\s+/)[0];
  return ALLOWED_GIT_COMMANDS.has(cmd);
}
