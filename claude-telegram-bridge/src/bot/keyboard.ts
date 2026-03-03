import TelegramBot from "node-telegram-bot-api";

type InlineButton = TelegramBot.InlineKeyboardButton;
type InlineKeyboard = TelegramBot.InlineKeyboardMarkup;

const PAGE_SIZE = 20;

export function buildKeyboard(rows: InlineButton[][]): InlineKeyboard {
  return { inline_keyboard: rows };
}

export function button(text: string, callbackData: string): InlineButton {
  return { text, callback_data: callbackData };
}

export const MainMenu = buildKeyboard([
  [button("📂 파일 탐색", "cmd:files"), button("📋 작업지시서", "cmd:request")],
  [button("📊 결과 보기", "cmd:result"), button("🔧 Git 상태", "cmd:git")],
  [button("📈 브리핑", "cmd:brief")],
]);

/**
 * 파일 목록 키보드 (페이지네이션 지원)
 * @param pagePrefix 페이지 콜백 prefix (예: "reqpage", "respage")
 */
export function fileListKeyboard(
  files: { name: string; path: string }[],
  prefix: string,
  page: number = 0,
  pagePrefix?: string,
): InlineKeyboard {
  const totalPages = Math.max(1, Math.ceil(files.length / PAGE_SIZE));
  const safePage = Math.max(0, Math.min(page, totalPages - 1));
  const start = safePage * PAGE_SIZE;
  const pageFiles = files.slice(start, start + PAGE_SIZE);

  const rows = pageFiles.map((f) => [button(f.name, `${prefix}:${f.path}`)]);

  // 페이지네이션 버튼 (2페이지 이상일 때만)
  if (totalPages > 1 && pagePrefix) {
    const navButtons: InlineButton[] = [];
    if (safePage > 0) {
      navButtons.push(button("◀ 이전", `${pagePrefix}:${safePage - 1}`));
    }
    navButtons.push(button(`${safePage + 1}/${totalPages}`, "noop"));
    if (safePage < totalPages - 1) {
      navButtons.push(button("다음 ▶", `${pagePrefix}:${safePage + 1}`));
    }
    rows.push(navButtons);
  }

  rows.push([button("◀ 뒤로", "cmd:back")]);
  return buildKeyboard(rows);
}

export function confirmKeyboard(actionId: string): InlineKeyboard {
  return buildKeyboard([
    [button("✅ 확인", `confirm:${actionId}`), button("❌ 취소", `cancel:${actionId}`)],
  ]);
}
