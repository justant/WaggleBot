/**
 * Telegram MarkdownV2 포맷팅 유틸리티
 */

const SPECIAL_CHARS = /([_*\[\]()~`>#+\-=|{}.!\\])/g;

export function escapeMarkdownV2(text: string): string {
  return text.replace(SPECIAL_CHARS, "\\$1");
}

export function bold(text: string): string {
  return `*${escapeMarkdownV2(text)}*`;
}

export function italic(text: string): string {
  return `_${escapeMarkdownV2(text)}_`;
}

export function code(text: string): string {
  return `\`${text.replace(/`/g, "\\`")}\``;
}

export function codeBlock(text: string, lang = ""): string {
  const escaped = text.replace(/```/g, "\\`\\`\\`");
  return `\`\`\`${lang}\n${escaped}\n\`\`\``;
}

export function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen - 3) + "...";
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

/**
 * Claude Code 응답의 마크다운을 Telegram에 적합하게 변환
 * 복잡한 변환 대신, 일반 텍스트로 정리
 */
export function formatClaudeResponse(text: string): string {
  // Telegram은 MarkdownV2 파싱이 까다롭기 때문에
  // 코드블록만 보존하고 나머지는 일반 텍스트로 처리
  return text;
}
