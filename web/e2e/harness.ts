// アプリ一式を立ち上げて片付ける（Python 側の harness を子プロセスとして使う）。

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type Running = {
  url: string;
  immich: string[];
  dataRoot: string;
  stop: () => void;
};

export async function start(password?: string, flags: string[] = []): Promise<Running> {
  const here = dirname(fileURLToPath(import.meta.url));
  const repo = resolve(here, "..", "..");
  const state = mkdtempSync(join(tmpdir(), "mediaferry-e2e-"));
  const args = ["run", "python", "-m", "tests.system.serve", state];
  if (password !== undefined) {
    args.push(password);
  }
  args.push(...flags);
  const child: ChildProcessWithoutNullStreams = spawn("uv", args, {
    cwd: repo,
    env: { ...process.env, PYTHONPATH: join(repo, "app") },
  });

  const line = await new Promise<string>((resolvePromise, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error(`起動を待ち切れなかった:\n${buffer}`)), 60_000);
    child.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
      const found = buffer.split("\n").find((candidate) => candidate.startsWith("{"));
      if (found !== undefined) {
        clearTimeout(timer);
        resolvePromise(found);
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`起動に失敗した（終了コード ${code}）:\n${buffer}`));
    });
  });

  const info = JSON.parse(line) as { url: string; immich: string[]; data_root: string };
  return {
    url: info.url,
    immich: info.immich,
    dataRoot: info.data_root,
    stop: () => {
      child.kill("SIGTERM");
      rmSync(state, { recursive: true, force: true });
    },
  };
}
