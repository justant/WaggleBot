#!/usr/bin/env node

/**
 * Claude Code Stop hook script
 * - stdin으로 Claude Code 세션 정보 수신 (last_assistant_message 포함)
 * - _result/ 에서 최근 2분 이내 생성/수정된 파일 탐색
 * - Hook 서버로 상세 메시지 + 파일 경로 전달
 */

import { readFileSync, readdirSync, statSync } from "fs";
import { join, basename } from "path";

const RESULT_DIR = process.env.WAGGLEBOT_ROOT
  ? join(process.env.WAGGLEBOT_ROOT, "_result")
  : "/home/justant/Data/WaggleBot/_result";
const HOOK_URL = `http://localhost:${process.env.HOOKS_PORT || "3847"}/hook`;
const RECENT_THRESHOLD_MS = 120_000; // 2분

// Read stdin
let input = "";
try {
  input = readFileSync(0, "utf-8");
} catch {
  process.exit(0);
}

let data;
try {
  data = JSON.parse(input);
} catch {
  process.exit(0);
}

// Extract summary from last_assistant_message
const raw = data.last_assistant_message || "";
// 첫 500자만 사용 (긴 코드 블록 등 제거)
const summary = raw
  .replace(/```[\s\S]*?```/g, "[code]") // 코드 블록 축약
  .replace(/\n{3,}/g, "\n\n")
  .substring(0, 500)
  .trim();

// Find recently modified files in _result/ — 상대 경로로 전송 (Docker 컨테이너 호환)
const recentFiles = [];
try {
  const files = readdirSync(RESULT_DIR);
  const now = Date.now();
  for (const f of files) {
    if (f.startsWith(".")) continue;
    const fullPath = join(RESULT_DIR, f);
    const stat = statSync(fullPath);
    if (stat.isFile() && now - stat.mtimeMs < RECENT_THRESHOLD_MS) {
      recentFiles.push(`_result/${f}`);
    }
  }
} catch {
  // _result/ doesn't exist or can't be read
}

// Send to hook server
const payload = JSON.stringify({
  event: "stop",
  data: {
    message: summary || "Claude Code 작업이 완료되었습니다.",
    resultFiles: recentFiles,
  },
});

try {
  await fetch(HOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
  });
} catch {
  // Hook server might not be running
}
