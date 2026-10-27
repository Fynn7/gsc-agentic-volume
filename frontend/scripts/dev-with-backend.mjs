#!/usr/bin/env node
// `npm run dev` entry: start Vite alongside the Python harness (src/app.py)
// so the dev proxy (/api, /artifact -> 127.0.0.1:8765) works without a
// second manual terminal. Reuses an already-listening backend instead of
// failing on port reuse. No new npm dependencies.
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const frontendDir = process.cwd();
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
function stopAll(signal = "SIGINT") {
  for (const child of children) {
    try {
      child.kill(signal);
    } catch { /* already gone */ }
  }
}
for (const event of ["SIGINT", "SIGTERM"]) process.on(event, () => stopAll(event));
process.on("exit", () => stopAll("SIGKILL"));

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

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
// NOTE: spawning npm.cmd on Windows requires a shell (Node >=22 throws EINVAL otherwise).
const vite = spawn(npm, ["run", "dev:vite"], { cwd: frontendDir, stdio: "inherit", shell: process.platform === "win32" });
children.add(vite);
vite.on("exit", (code) => {
  children.delete(vite);
  stopAll("SIGKILL");
  process.exitCode = code ?? 1;
  if (code !== 0 && code !== null) console.error(`[dev] Vite exited with code ${code}.`);
});

await new Promise(() => {});
