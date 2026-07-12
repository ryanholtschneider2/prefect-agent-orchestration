const ALT_ON = "\u001b[?1049h\u001b[?25l"; const ALT_OFF = "\u001b[?25h\u001b[?1049l";
export interface Lifecycle {restore(): void; dispose(): void}
export function installTerminalLifecycle(output: NodeJS.WriteStream, onResume: () => void): Lifecycle {
  let owned = false; let restored = false; let stopInstalled = true;
  const setRaw = (value: boolean) => { if (process.stdin.isTTY && typeof process.stdin.setRawMode === "function") process.stdin.setRawMode(value); };
  const enter = () => { if (!output.isTTY) return; output.write(ALT_ON); owned = true; restored = false; };
  const restore = () => { if (owned && !restored) { setRaw(false); output.write(ALT_OFF); restored = true; owned = false; } };
  const stop = () => { restore(); process.off("SIGTSTP", stop); stopInstalled = false; process.kill(0, "SIGTSTP"); };
  const resume = () => { if (!stopInstalled) { process.on("SIGTSTP", stop); stopInstalled = true; } enter(); setRaw(true); onResume(); };
  const interrupt = () => { restore(); process.exit(130); };
  const terminate = () => { restore(); process.exit(143); };
  enter(); process.on("SIGTSTP", stop); process.on("SIGCONT", resume); process.on("SIGINT", interrupt); process.on("SIGTERM", terminate);
  return {restore, dispose: () => { restore(); if (stopInstalled) process.off("SIGTSTP", stop); process.off("SIGCONT", resume); process.off("SIGINT", interrupt); process.off("SIGTERM", terminate); }};
}
