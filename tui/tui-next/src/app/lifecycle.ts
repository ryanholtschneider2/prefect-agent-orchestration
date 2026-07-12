const ALT_ON = "\u001b[?1049h\u001b[?25l"; const ALT_OFF = "\u001b[?25h\u001b[?1049l";
export interface Lifecycle {restore(): void; dispose(): void}
export function installTerminalLifecycle(output: NodeJS.WriteStream, onResume: () => void): Lifecycle {
  let owned = false; let restored = false;
  const enter = () => { if (!output.isTTY) return; output.write(ALT_ON); owned = true; restored = false; };
  const restore = () => { if (owned && !restored) { output.write(ALT_OFF); restored = true; owned = false; } };
  const stop = () => { restore(); process.kill(process.pid, "SIGSTOP"); };
  const resume = () => { enter(); onResume(); };
  const interrupt = () => { restore(); process.exit(130); };
  const terminate = () => { restore(); process.exit(143); };
  enter(); process.on("SIGTSTP", stop); process.on("SIGCONT", resume); process.on("SIGINT", interrupt); process.on("SIGTERM", terminate);
  return {restore, dispose: () => { restore(); process.off("SIGTSTP", stop); process.off("SIGCONT", resume); process.off("SIGINT", interrupt); process.off("SIGTERM", terminate); }};
}
