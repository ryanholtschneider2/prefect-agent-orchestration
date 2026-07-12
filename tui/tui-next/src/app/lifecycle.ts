const ALT_ON = "\u001b[?1049h\u001b[?25l"; const ALT_OFF = "\u001b[?25h\u001b[?1049l";
export interface Lifecycle {restore(): void; dispose(): void}
export function installTerminalLifecycle(output: NodeJS.WriteStream, onResume: () => void): Lifecycle {
  let owned = false; let restored = false;
  const enter = () => { if (!output.isTTY) return; output.write(ALT_ON); owned = true; restored = false; };
  const restore = () => { if (owned && !restored) { output.write(ALT_OFF); restored = true; owned = false; } };
  const stop = () => { restore(); process.kill(process.pid, "SIGSTOP"); };
  const resume = () => { enter(); onResume(); };
  const term = () => { restore(); process.exitCode = 130; };
  enter(); process.on("SIGTSTP", stop); process.on("SIGCONT", resume); process.on("SIGINT", term); process.on("SIGTERM", term);
  return {restore, dispose: () => { restore(); process.off("SIGTSTP", stop); process.off("SIGCONT", resume); process.off("SIGINT", term); process.off("SIGTERM", term); }};
}
