#!/usr/bin/env node
// `npm run dev` entry: start Vite alongside the Python harness (src/app.py)
// so the dev proxy (/api, /artifact -> 127.0.0.1:8765) works without a
// second manual terminal. Reuses an already-listening backend instead of
// failing on port reuse. No new npm dependencies.
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";

// Anchor on this file, not process.cwd(): `npm run dev` from the repository
// root and `node scripts/dev-with-backend.mjs` inside frontend/ must both work.
const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root = path.resolve(frontendDir, "..");
const port = Number(process.env.GAV_PORT ?? 8765) || 8765;
// 前端独占 5177（GSC 5173 / sandbox 5174 / mermaid 5176 / agentic-drive 5178 …）。
// 被占用时直接报错退出，禁止让 Vite 静默顺延到别的端口。
const vitePort = Number(process.env.GAV_VITE_PORT ?? 5177) || 5177;
const backendOnly = process.argv.includes("--backend-only");

function pickPython() {
  if (process.env.GAV_PYTHON) return process.env.GAV_PYTHON;
  const bundled = path.join(
    os.homedir(),
    ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe",
  );
  try {
    if (fs.existsSync(bundled)) return bundled;
  } catch { /* fall through to PATH python */ }
  return "python";
}

function portInUse(targetPort) {
  return new Promise((resolve) => {
    const socket = net.connect(targetPort, "127.0.0.1");
    socket.once("connect", () => socket.end(() => resolve(true)));
    socket.once("error", () => resolve(false));
  });
}

function waitForBackend(child, timeoutMs = 30000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      if (child.exitCode !== null) {
        clearInterval(timer);
        reject(new Error(`Backend exited before becoming ready (code ${child.exitCode}).`));
        return;
      }
      if (await portInUse(port)) {
        clearInterval(timer);
        resolve();
        return;
      }
      if (Date.now() - start > timeoutMs) {
        clearInterval(timer);
        reject(new Error(`Backend did not listen on 127.0.0.1:${port} within ${timeoutMs / 1000}s.`));
      }
    }, 300);
  });
}

const children = new Set();
let stopping = false;

/**
 * Heal the parent terminal before this process dies.
 *
 * A crashed/killed batch layer (cmd /c npm.CMD …) used to leave the tab with
 * raw-ish input: Ctrl+C only echoed "^C", Backspace and every other key did
 * nothing until the tab was closed. Leaving the alternate screen and resetting
 * the Win32 input mode in place keeps PowerShell/PSReadLine usable, the same
 * repair Start-VolumeTUI.ps1 applies after a TUI crash.
 */
function restoreConsole() {
  try {
    process.stdout.write("\x1b[?1049l\x1b[?25h");
  } catch {
    /* not a TTY */
  }
  if (process.platform !== "win32" || !process.stdin.isTTY) return;
  try {
    const script =
      "$s='[DllImport(\"kernel32.dll\")] public static extern bool GetConsoleMode(IntPtr h, ref uint m);" +
      '[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);' +
      '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n);";' +
      "$k=Add-Type -MemberDefinition $s -Name GavDevConsoleFix -Namespace Win32 -PassThru;" +
      "$h=$k::GetStdHandle(-10);[uint32]$m=0;if($k::GetConsoleMode($h,[ref]$m)){$null=$k::SetConsoleMode($h,0x1F7)}";
    spawnSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
      { timeout: 3000, windowsHide: true, stdio: "ignore" },
    );
  } catch {
    /* best effort */
  }
}

function stopAll(signal = "SIGINT") {
  // On Windows Node cannot deliver POSIX signals: kill("SIGINT") is an
  // unconditional TerminateProcess, which is exactly what skips a child's own
  // console/terminal cleanup. Ctrl+C already reaches every process attached to
  // the console, so let the children exit on their own and only force-kill
  // stragglers after a grace period.
  for (const child of children) {
    try {
      child.kill(signal);
    } catch {
      /* already gone */
    }
  }
}

function shutdown(signal = "SIGINT") {
  if (stopping) return;
  stopping = true;
  if (process.platform === "win32") {
    // Children share this console and receive the same Ctrl+C; give them time
    // to restore their own state before anything is terminated.
    setTimeout(() => {
      for (const child of children) {
        try {
          child.kill("SIGKILL");
        } catch {
          /* already gone */
        }
      }
      restoreConsole();
    }, 3000).unref();
    return;
  }
  stopAll(signal);
  restoreConsole();
}

for (const event of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(event, () => shutdown(event));
process.on("exit", () => {
  if (process.platform !== "win32") stopAll("SIGKILL");
  restoreConsole();
});

const python = pickPython();
// 前端端口预检：被占用直接报错，不启动后端也不让 Vite 顺延（掩盖冲突）。
if (!backendOnly && (await portInUse(vitePort))) {
  console.error(
    `[dev] Vite port 127.0.0.1:${vitePort} is already in use (another app?). ` +
      `Free it or set GAV_VITE_PORT to another value. Refusing to hop ports.`,
  );
  process.exit(1);
}
const backendArgs = ["-B", path.join(root, "src", "app.py"), "--port", String(port)];
let backend = null;
if (await portInUse(port)) {
  console.log(`[dev] Backend already listening on 127.0.0.1:${port}; reusing it.`);
} else {
  backend = spawn(python, backendArgs, { cwd: root, stdio: "inherit", windowsHide: false });
  children.add(backend);
  backend.on("exit", (code) => {
    children.delete(backend);
    if (code !== 0 && code !== null) {
      console.error(`[dev] Backend exited with code ${code}.`);
      stopAll("SIGKILL");
      process.exitCode = code;
    }
  });
  try {
    await waitForBackend(backend);
    console.log(`[dev] Backend ready on http://127.0.0.1:${port}.`);
  } catch (err) {
    console.error(`[dev] ${err.message}`);
    stopAll("SIGKILL");
    process.exit(1);
  }
}

if (backendOnly) {
  if (backend) await new Promise(() => {});
  process.exit(0);
}

// Spawn Vite as a plain node child: no cmd.exe / npm.CMD batch layer in
// between. A batch layer swallows Ctrl+C with its "Terminate batch job (Y/N)?"
// prompt, which froze this terminal until the tab was closed (see 65dc5d7).
const viteBin = path.join(frontendDir, "node_modules", "vite", "bin", "vite.js");
const vite = fs.existsSync(viteBin)
  ? spawn(process.execPath, [viteBin], { cwd: frontendDir, stdio: "inherit" })
  : spawn(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "dev:vite"], {
      cwd: frontendDir,
      stdio: "inherit",
      shell: process.platform === "win32",
    });
children.add(vite);
vite.on("exit", (code) => {
  children.delete(vite);
  stopAll("SIGKILL");
  process.exitCode = code ?? 1;
  if (code !== 0 && code !== null) console.error(`[dev] Vite exited with code ${code}.`);
});

await new Promise(() => {});
