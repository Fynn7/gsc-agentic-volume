# 12 状态报告 — 2026-10-29 晚（tonight-brief 基础设施）

> 结论先说：**P1、P2、P3 完成；P4 的 T4 见证解与 T1 阈值扫描完成；P5/P6 的脚本与文档骨架完成，但 35B 批量没有在本机跑**。本机（RTX 4060 8 GB / 32 GB RAM）装不下 `qwen3.6:35b`，一次 35B 烟雾 run 在第一次工具调用后 Ollama 连接被重置（内存不足）。批量必须在显存/内存足够的机器（实验室 2×RTX PRO 5000）上运行。

## 0 前置检查（已完成，01:40 左右）

| 项 | 结果 |
|---|---|
| 渲染后端 8765 | 活着：`{"service":"gav-native-renderer","toolContractVersion":1}` |
| Ollama 11434 | 活着；`qwen3.6:35b` 已安装且报告 `vision`；`qwen3.5:9b` 也可用 |
| 5173 / 5174 | 5173 空闲；5174 是 `fynns_ui_design_core` 的 Vite dev server（PID 33436），与今晚任务无关 |
| GPU / RAM / CPU | RTX 4060 8 GB（560.94）/ 32 GB / i5-12400F；`qwen3.6:35b` 权重 23.9 GB、加载后约 27 GB，**放不下** |
| 上次教堂 run | `runtime/archive/run-20261024-200023-a026f1`（run a）与 `runtime/archive/run-20261024-193758-90e262`（run b）；日志/最终图见 `12-pilot-v0.md §1` |

## 1 做了什么（逐项对照 tonight-brief）

### P1 T7 复用旧 run — 完成

- 两次 run 的工具调用数、墙钟、最终消息、最终图路径、声明类别（按语义推断）都写进 `docs/improvement/12-pilot-v0.md §1`。
- 最终图复制到 `docs/improvement/figures/t7-reuse-run-a-final.png` / `t7-reuse-run-b-final.png`（`runtime/` 被 git 忽略，文档里需要稳定副本）。
- run a：40 次调用（22 render / 10 slice / 7 inspect / 1 finish），21.9 min，预算 30/30，2 次预算耗尽错误；最终消息是“Here's the result…”，判 **DONE（按语义推断）**。
- run b：22 次调用（18 render / 3 inspect / 1 finish），12.4 min，预算 18/30；最终消息 “Done! … completely hidden.”，判 **DONE（按语义推断）**。
- 未评分。两张图都只有黑底亮斑，没有可辨认教堂；这一事实已记录，评分留给用户。

### P2 工具审计 — 完成

`docs/improvement/11-tool-audit.md`：

- 开头 5 条摘要（缺 `reset_state`、`wait_until_rendered` 只有隐式、参数语义不全、表达力缺口、预算语义不一致）。
- BorgVR 14 行逐项对照，每条带 `文件:行号`。
- 反向表（`inspect_scene` / `render_scene` / `inspect_slice` / `finish_task`）。
- 四个可靠性原语逐项判定；参数语义逐项核对（含 `translation`、`crop` 顺序、`tf` 默认值、`frameRevision`）。
- 结论：今晚不改工具；修复清单留给 v1。

### P3 prompt / 指令 / 配置 / 脚本 — 完成

- `eval/tasks/T1..T7/prompt.txt`：逐字；T7 用旧 run 原文（含 `chaple` 拼写），理由写在 `eval/CONFIG.md §8`。
- `eval/HARNESS_INSTRUCTION.txt`：与 brief 逐字一致。
- `eval/CONFIG.md`：模型、采样（`temperature=0`、`think=true`、`contextSize=16384`，与两次教堂 run 一致）、上限、干净起点、条件、已知环境、任务→数据集映射、今晚不做的事。
- 脚本：
  - `eval/scripts/run_pilot.py`：单次 run；拼接 instruction+prompt；调现有 launcher；强制墙钟；落 `meta.json` / `claim.json` / `trace.jsonl` / `final.png` / `final_message.txt` / `launcher.log`。退出码 0/2/3。
  - `eval/scripts/run_batch.py`：轮次顺序、续跑、infra 重试一次、连续 3 次 infra 中止。
  - `eval/scripts/run_witness.py`：固定调用序列生成见证解（不调模型）。
  - `eval/scripts/sweep_tf.py`：tfStart 扫描 + 联系表（不调模型）。
  - `eval/scripts/collect_pilot.py`：汇总表 + CSV + 拼图；保留人工填的评分/归因两列。
  - `eval/scripts/pilot_common.py`：常量、prompt 读取、claim 解析、HTTP 小工具。

### P4 烟雾测试与见证解 — 部分完成

- **T4 见证解：完成。** `eval/tasks/T4/witness.png` 能看到清晰的血管树；`witness.gsc` 是原生脚本；`witness_calls.json` 记录 `inspect_scene` + `finish_task(frameRevision=0)` 的完整参数、返回摘要、PNG sha256 校验。走的是后端 `/api/tools/open` + `/api/tools/call`（与 `scripts/renderer_mcp.py` 同一工具契约，不调模型）。
- **T1 阈值扫描：完成。** 14 张（0.08–0.60，步长 0.04，`tfWidth=0.08`），`eval/tasks/T1/sweep_contact_sheet.png` 带参数标注。观察：0.20–0.28 皮肤基本消失、骨骼可见；0.32 以上开始碎裂；0.08–0.16 皮肤仍遮骨。**注意**：默认视角下脚是斜的，脚踝/脚跟触及体数据边界，T1 的 C3（整只脚完整）能否满足需要你明早确认；不要为了通过而改验收条件。
- **T4 烟雾 run（35B）：没做成，原因是本机内存。** 用临时目录跑了一次 240 s 上限的 T4：模型在 65.9 s 返回第一次推理，调用 `inspect_scene` 一次；第二次推理时 Ollama 连接被重置（`WinError 10054`），当时可用 RAM 只剩约 0.5 GB。该 run **不在** `eval/runs/`，不是评测数据。wrapper 的 meta/claim/trace 都正常落盘（`status: NONE`、`toolCalls: 1`）。
- **9B 烟雾 run**：用 `qwen3.5:9b` 验证 wrapper 的完整路径（不是评测数据，结论见 §3）。

### P5 批量 — 脚本完成，未运行

- 命令已准备好（§4）。本机跑 35B 会 OOM，不能过夜跑。
- 云端条件未接通：launcher 只支持本地 Ollama，`validate_config` 拒绝非本地模型（`scripts/run_lifecycle.py:246-251`），并清掉 `OPENAI_API_KEY`（`scripts/run_local_agent.py:280-282`）。因此 `cloud_v0` 跳过，原因写进 `CONFIG.md §6`。

### P6 汇总 — 脚本与文档骨架完成

- `eval/scripts/collect_pilot.py` 已用假 run fixture 验证：表格、CSV、拼图、人工列保留都正常。
- `12-pilot-v0.md` 的 `RUNS` / `ISSUES` 标记已就位；有 run 后重复执行即可。
- 本报告即 P6.3。

## 2 验证记录（实际跑过的命令/结果）

| 检查 | 结果 |
|---|---|
| `python -m compileall -q eval/scripts` | OK |
| 所有脚本 `--help` / `--dry-run` | OK（T4 dry-run 打印完整命令与 task_text） |
| `run_witness.py --task T4` | 成功；`imageVerified: true`；witness.png 90,064 B；见证会话 `2fdd35e2-…` |
| `sweep_tf.py --task T1` | 14/14 帧成功；联系表生成；见证会话 `2bda2b96-…` |
| `collect_pilot.py`（无 run） | 成功；表格占位、CSV、拼图占位 |
| `collect_pilot.py`（fixture） | 成功；1 行表格、CSV、拼图；手填评分/归因在重跑后保留 |
| `claim` 解析单测 | DONE / PARTIAL（含 `**` 粗体）/ CANNOT / 缺失→NONE 均正确 |
| 35B 烟雾 | 失败于 Ollama 传输中断（内存），不是 wrapper 崩溃 |
| 9B 烟雾 | 见 §3 |

## 3 9B 烟雾的结论（不是评测数据）

用 `qwen3.5:9b` 在临时目录 `C:\Users\fynna\AppData\Local\Temp\opencode\pilot-smoke9b` 跑了一次 T4，`--timeout 300`：

- 前 35 s 完成第一次推理并 `inspect_scene`（revision 0）。
- 随后连续两次 `render_scene` 传 `expectedRevision: 1`，都收到 `Stale scene revision`；模型自己推理“应该用当前 revision 0”，第三次成功（frame 1，tf 0.3/0.12）。
- 第四次 render 用 `expectedRevision: 1` 成功（frame 2，tf 0.45/0.12）。
- 第 6 次推理还没返回时到达 300 s 墙钟，`run_pilot` 写 stop 文件并终止：`meta.exitReason=wall_clock_timeout`、`status=NONE`、`toolCalls=6`（inspect 2 / render 4）、`observationsUsed=2`、`finalImage=null`。

**这次 run 证明的东西**：wrapper 的启动、run 目录发现、事件/HTTP 记录、meta/claim/trace 落盘、墙钟停止都正常；`perTool` 与预算计数和 session 事件一致。**它同时是 `expectedRevision` 语义缺失的实测旁证**（见 `11-tool-audit.md` §0.3），但 9B 不是被测模型，不能计入 pilot 数据，也没有放进 `eval/runs/`。

## 4 你接下来要跑什么

> 先换到显存/内存足够的机器（实验室 2×RTX PRO 5000，或任何能容纳 ~27 GB 模型 + 上下文的机器）。仓库路径可以不同：脚本用自身位置推导仓库根目录；只有 `runtime/tui-venv` 缺失时，MCP 子进程才回退到当前 Python。

### 4.1 预检（1 分钟）

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/service | Select-Object -Expand Content
Invoke-WebRequest http://127.0.0.1:11434/api/tags   | Select-Object -Expand Content
nvidia-smi
```

要求：8765 返回 `gav-native-renderer`；11434 的 tags 里有 `qwen3.6:35b` 且 `vision`；显存/内存能放下模型。

### 4.2 单次 T4 烟雾（先验证不是 OOM）

```powershell
$py = "runtime\tui-venv\Scripts\python.exe"
& $py eval/scripts/run_pilot.py --task T4 --condition local35b_v0 --index 1 --timeout 1800
```

验收：`eval/runs/pilot/local35b_v0/T4/run-*/` 里有 `meta.json`、`claim.json`、`trace.jsonl`、`launcher.log`；`meta.json` 的 `inferenceTransportErrors=0`；如果模型完成，`final.png` 存在。

如果 `inferenceTransportErrors > 0`，先解决模型服务（OOM/崩溃），不要开始批量；如果 `harnessExitCode != 0` 且 `toolCalls = 0`，先看 `launcher.log`，这属于 infra，不是模型失败。墙钟切断（`exitReason=wall_clock_timeout`）且已经产生工具调用的，按模型失败记录，不重跑。

### 4.3 过夜批量（9 次）

```powershell
& $py eval/scripts/run_batch.py --condition local35b_v0 --rounds 3 --tasks T4 T1 T5 --timeout 1800
```

- 顺序：T4/T1/T5 × 3 轮；中途断了可以重跑同一条命令（`--skip-existing` 默认开）。
- 只有 infra 失败会重试一次；模型失败不重跑。
- 预计：按历史 12–22 min/次，9 次约 2–3.5 h；如果单次超过 30 min 被墙钟切断，`meta.exitReason=wall_clock_timeout`，仍算数据。

### 4.4 汇总

```powershell
& $py eval/scripts/collect_pilot.py
```

产出：`docs/improvement/12-pilot-v0.md` 表格、`eval/results/pilot_v0_runs.csv`、`eval/results/pilot_v0_montage.png`。评分/归因两列可以手填，重跑 collect 不会覆盖。

### 4.5 可选：T1 见证解

先看 `eval/tasks/T1/sweep_contact_sheet.png` 选阈值，再用 `--plan` 写一段固定调用序列（open → render（tfStart 选定）→ finish）。示例：

```json
{
  "volume": "volumes/foot.dat",
  "calls": [
    {"name": "inspect_scene", "arguments": {}},
    {"name": "render_scene", "arguments": {
      "expectedRevision": "current", "reason": "witness tf",
      "parameters": {"tfStart": 0.24, "tfWidth": 0.08}}},
    {"name": "finish_task", "arguments": {"frameRevision": 1, "summary": "T1 witness"}}
  ]
}
```

```powershell
& $py eval/scripts/run_witness.py --task T1 --plan eval/tasks/T1/witness_plan.json
```

注意：`expectedRevision: "current"` 由脚本替换为当前 revision；`frameRevision` 必须等于 render 之后的 revision（示例是 1）。默认视角的完整性问题见 §1 P4，先确认 C3 再定稿。

## 5 没做什么（如实）

- 没有跑 35B 的正式 pilot（本机 OOM；按你的要求 GPU 实验由你跑）。
- 没有跑 T2/T3/T6 的模型 run。
- 没有云端条件（launcher 不支持）。
- 没有评委脚本、κ、T-plus、`isolate_nodes`、任何工具改动。
- 没有改 `02-dataset-facts.md §6` 的真值 JSON，没有改渲染引擎/前端。
- 没有 push；本地 commit 见 git log。

## 6 需要你决定/确认的

1. 是否接受 `think=true`（与两次教堂 run 一致；模板是 false）。一旦正式开跑就固定。
2. T1 的 C3：默认视角脚踝/脚跟触到体数据边界，是否仍按“整只脚在画面内”验收；如果数据本身被裁，需要改任务卡而不是改 prompt。
3. T7 旧 run 的声明分类（DONE 按语义）是否同意；不同意就改 `12-pilot-v0.md §1`，不影响评分。
4. 批量在哪台机器跑；如果换机器，先跑 §4.2 的 T4 烟雾确认模型服务。
