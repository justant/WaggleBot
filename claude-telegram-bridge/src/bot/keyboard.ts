import TelegramBot from "node-telegram-bot-api";

type InlineButton = TelegramBot.InlineKeyboardButton;
type InlineKeyboard = TelegramBot.InlineKeyboardMarkup;

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

export function fileListKeyboard(
  files: { name: string; path: string }[],
  prefix: string,
): InlineKeyboard {
  const rows = files.map((f) => [button(f.name, `${prefix}:${f.path}`)]);
  rows.push([button("◀ 뒤로", "cmd:back")]);
  return buildKeyboard(rows);
}

export function confirmKeyboard(actionId: string): InlineKeyboard {
  return buildKeyboard([
    [button("✅ 확인", `confirm:${actionId}`), button("❌ 취소", `cancel:${actionId}`)],
  ]);
}

export function paginationKeyboard(
  currentPage: number,
  totalPages: number,
  prefix: string,
): InlineKeyboard {
  const buttons: InlineButton[] = [];
  if (currentPage > 0) {
    buttons.push(button("◀ 이전", `${prefix}:page:${currentPage - 1}`));
  }
  buttons.push(button(`${currentPage + 1}/${totalPages}`, "noop"));
  if (currentPage < totalPages - 1) {
    buttons.push(button("다음 ▶", `${prefix}:page:${currentPage + 1}`));
  }
  return buildKeyboard([buttons]);
}
