export interface Theme {accent?: string; muted?: string; running?: string; success?: string; warning?: string; error?: string; border?: string}
export const theme = (noColor = Boolean(process.env.NO_COLOR)): Theme => noColor ? {} : {accent: "cyan", muted: "gray", running: "blue", success: "green", warning: "yellow", error: "red", border: "gray"};
export const stateGlyph = (state: string, ascii = false): string => {
  if (ascii) return state === "closed" || state === "COMPLETED" ? "[+]" : state === "failed" || state === "FAILED" ? "[x]" : state === "blocked" || state === "PAUSED" ? "[!]" : state === "in_progress" || state === "RUNNING" ? "[>]" : "[ ]";
  return state === "closed" || state === "COMPLETED" ? "●" : state === "failed" || state === "FAILED" ? "×" : state === "blocked" || state === "PAUSED" ? "!" : state === "in_progress" || state === "RUNNING" ? "◆" : "○";
};
