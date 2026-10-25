#!/usr/bin/env node
// Build the Wasm preview engine from the Raycaster sources without touching dev/build.
// Output is gitignored at frontend/public/wasm (sampling.js/.wasm/.data).
// Usage:
//   npm run wasm:build          full Emscripten release build + sync
//   npm run wasm:sync           copy existing VirtualSampling/Raycaster/web/* without rebuilding
// The script auto-activates Emscripten from a local emsdk checkout when em++
// is not already on PATH (Windows: %USERPROFILE%/emsdk/emsdk_env.ps1).
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const frontendDir = process.cwd();
const root = path.resolve(frontendDir, "..");
const raycasterDir = path.join(root, "VirtualSampling", "Raycaster");
const webDir = path.join(raycasterDir, "web");
const destDir = path.join(frontendDir, "public", "wasm");
const syncOnly = process.argv.includes("--sync-only");

function hasCommand(cmd) {
  const probe = spawnSync(cmd, ["--version"], { stdio: "ignore", shell: process.platform === "win32" });
  return !probe.error && probe.status === 0;
}

function findEmsdkDir() {
  const candidates = [];
  if (process.env.EMSDK) candidates.push(process.env.EMSDK);
  if (process.env.EMSDK_ROOT) candidates.push(process.env.EMSDK_ROOT);
  candidates.push(path.join(os.homedir(), "emsdk"));
  // Common sibling checkout used on this workstation.
  candidates.push("C:\\Users\\fynna\\emsdk");
  for (const dir of candidates) {
    if (!dir) continue;
    try {
      if (fs.existsSync(path.join(dir, "emsdk_env.ps1")) || fs.existsSync(path.join(dir, "emsdk_env.sh"))) {
        return dir;
      }
    } catch { /* ignore */ }
  }
  return null;
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) throw new Error(`${cmd} ${args.join(" ")} exited with ${res.status}`);
}

let gmake = process.env.GMAKE || null;
if (!gmake) {
  for (const candidate of ["gmake", "make"]) {
    if (hasCommand(candidate)) {
      gmake = candidate;
      break;
    }
  }
}
if (!gmake) {
  console.error("[wasm:build] GNU make not found. Install Strawberry Perl (gmake) or add make to PATH.");
  console.error("[wasm:build]   winget install StrawberryPerl.StrawberryPerl   (provides gmake.exe)");
  process.exit(1);
}

if (!syncOnly) {
  if (!hasCommand("em++")) {
    const emsdkDir = findEmsdkDir();
    if (!emsdkDir) {
      console.error("[wasm:build] em++ not found and no emsdk checkout detected.");
      console.error("[wasm:build] Install Emscripten once, then re-run this command:");
      console.error("[wasm:build]   git clone https://github.com/emscripten-core/emsdk.git %USERPROFILE%/emsdk");
      console.error("[wasm:build]   cd %USERPROFILE%/emsdk && emsdk install latest && emsdk activate latest");
      console.error("[wasm:build] If web/ output already exists, use: npm run wasm:sync -- --sync-only");
      process.exit(2);
    }
    console.log(`[wasm:build] em++ not on PATH; activating emsdk at ${emsdkDir} ...`);
    const envFile = process.platform === "win32"
      ? path.join(emsdkDir, "emsdk_env.ps1")
      : path.join(emsdkDir, "emsdk_env.sh");
    if (process.platform === "win32") {
      // Source the env inside one powershell so em++ is visible to gmake.
      // EMSDK_QUIET must be set before sourcing to silence the banner.
      const ps = "powershell";
      const script = `$env:EMSDK_QUIET='1'; . '${envFile}'; & '${gmake}' emscripten_release; exit $LASTEXITCODE`;
      const res = spawnSync(
        ps,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        { cwd: raycasterDir, stdio: "inherit" },
      );
      if (res.error) throw res.error;
      if (res.status !== 0) {
        throw new Error(`${gmake} emscripten_release exited with ${res.status}`);
      }
    } else {
      const res = spawnSync(
        "bash",
        ["-lc", `source '${envFile}' >/dev/null 2>&1; export EMSDK_QUIET=1; '${gmake}' emscripten_release`],
        { cwd: raycasterDir, stdio: "inherit" },
      );
      if (res.error) throw res.error;
      if (res.status !== 0) {
        throw new Error(`${gmake} emscripten_release exited with ${res.status}`);
      }
    }
  } else {
    console.log(`[wasm:build] Building Emscripten release in ${raycasterDir} ...`);
    run(gmake, ["emscripten_release"], {
      cwd: raycasterDir,
      shell: process.platform === "win32",
      env: { ...process.env, EMSDK_QUIET: "1" },
    });
  }
} else {
  console.log("[wasm:sync] Skipping Emscripten build (--sync-only); copying existing web/ output.");
}

const required = ["sampling.js", "sampling.wasm", "sampling.data"];
for (const file of required) {
  if (!fs.existsSync(path.join(webDir, file))) {
    console.error(`[wasm:build] Missing ${path.join(webDir, file)} after build.`);
    console.error("[wasm:build] Run without --sync-only so the Emscripten build regenerates web/.");
    process.exit(1);
  }
}

fs.mkdirSync(destDir, { recursive: true });
for (const file of fs.readdirSync(webDir)) {
  // Keep the hand-owned shell; only sync generated engine artifacts.
  if (file === "index.html" || file === "preview-shell.html" || file === "server.py") continue;
  fs.copyFileSync(path.join(webDir, file), path.join(destDir, file));
}
// Always refresh the engine loader if the upstream template changed, but never
// overwrite the consumer-owned preview-shell.html bridge.
const upstreamIndex = path.join(webDir, "index.html");
if (fs.existsSync(upstreamIndex) && !fs.existsSync(path.join(destDir, "upstream-index.html"))) {
  fs.copyFileSync(upstreamIndex, path.join(destDir, "upstream-index.html"));
}
console.log(`[wasm:build] Synced engine assets to ${destDir} (shell preserved).`);
console.log(`[wasm:build] Files: ${fs.readdirSync(destDir).join(", ")}`);
