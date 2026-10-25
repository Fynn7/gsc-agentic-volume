#!/usr/bin/env node
// Copied into consumer scripts so bootstrap never depends on a node_modules link.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const target = process.cwd();
const name = "@fynn7/ui-design-core";
try {
  const prepare = process.argv.includes("--prepare");
  const pkg = JSON.parse(fs.readFileSync(path.join(target, "package.json"), "utf8").replace(/^\uFEFF/, ""));
  const spec = pkg.dependencies?.[name] ?? pkg.devDependencies?.[name];
  const local = typeof spec === "string" && spec.startsWith("file:");
  const core = local
    ? spec.startsWith("file://") ? fileURLToPath(spec) : path.resolve(target, spec.slice(5))
    : path.join(target, "node_modules", "@fynn7", "ui-design-core");
  if (!prepare && local && !fs.existsSync(core)) {
    const ref = process.env.FYNNS_UI_CORE_REF || "dev";
    const clone = spawnSync("git", ["clone", "--branch", ref, "--depth", "1",
      "https://github.com/Fynn7/fynns_ui_design_core.git", core], {
      cwd: target, stdio: "inherit", windowsHide: true,
    });
    if (clone.error || clone.status !== 0) throw new Error("Cannot clone UI core; check network/Git access and retry.");
  }
  let helper = path.join(core, "scripts", prepare ? "consumer-recovery.mjs" : "ensure-sibling-ui-core.mjs");
  // A pinned UI snapshot may predate recovery tooling. Use the canonical local
  // core's tools without changing or syncing the UI dependency it deliberately pins.
  if (prepare && !fs.existsSync(helper)) {
    for (let dir = target; dir !== path.dirname(dir); dir = path.dirname(dir)) {
      const candidate = path.join(dir, "fynns_ui_design_core", "scripts", "consumer-recovery.mjs");
      if (fs.existsSync(candidate)) { helper = candidate; break; }
    }
  }
  if (!fs.existsSync(helper)) throw new Error(`UI bootstrap helper missing: ${helper}. Restore/update the core checkout first.`);
  const result = spawnSync(process.execPath, [helper, "--target", target, ...(prepare ? ["--before-gate"] : ["--update"])], {
    cwd: target, stdio: "inherit", windowsHide: true,
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} catch (err) {
  console.error(`[fynns-ui] ${err.message}`);
  process.exitCode = 1;
}
