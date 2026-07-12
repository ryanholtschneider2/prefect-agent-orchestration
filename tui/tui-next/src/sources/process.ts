import {execFile} from "node:child_process";

export interface CommandResult {stdout: string; stderr: string; code: number}

export function run(command: string, args: string[], options: {cwd?: string; timeoutMs?: number; env?: NodeJS.ProcessEnv} = {}): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    execFile(command, args, {cwd: options.cwd, env: options.env, timeout: options.timeoutMs ?? 10_000, maxBuffer: 8 * 1024 * 1024}, (error, stdout, stderr) => {
      if (error && typeof (error as NodeJS.ErrnoException).code === "string") {
        reject(new Error(`${command}: ${(error as Error).message}`));
        return;
      }
      const code = error && "code" in error && typeof error.code === "number" ? error.code : 0;
      resolve({stdout, stderr, code});
    });
  });
}

export async function checked(command: string, args: string[], options?: {cwd?: string; timeoutMs?: number}): Promise<string> {
  const result = await run(command, args, options);
  if (result.code !== 0) throw new Error(`${command} ${args.join(" ")} exited ${result.code}: ${result.stderr.trim()}`);
  return result.stdout;
}
