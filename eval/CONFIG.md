# eval/CONFIG — 固定项（2026-10-29）

> 任何一项变化都要在论文里写明，并整体重跑受影响的 run。
> 本文件是今晚 pilot 的唯一配置来源；`eval/scripts/*.py` 只读取这里的约定，不另设默认值之外的开关。

## 1. 模型与端点

| 项 | 值 | 说明 |
|---|---|---|
| 被测模型 | `qwen3.6:35b`（本地 Ollama，Q4_K_M，36.0B） | `GET /api/show` 必须报告 `vision`；`:cloud` 变体被 launcher 拒绝（`scripts/run_lifecycle.py:250-251`） |
| 模型端点 | `http://127.0.0.1:11434` | launcher 还会把推理请求经本地 recorder 代理一圈，原始请求/响应落在 run 目录 `http/` |
| 渲染服务 | `http://127.0.0.1:8765` | `/api/service` 必须返回 `gav-native-renderer` |
| 采样 | `temperature=0.0`，`think=true`（reasoning effort `medium`），`contextSize=16384` | 与 `run-20261024-193758-90e262` / `run-20261024-200023-a026f1` 两个教堂 run 的 `config.json` 一致；`experiments/local-vlm.example.json` 里 `think=false`，此处不采用，理由见 §7 |
| 最大输出 token | 不设上限（launcher 不强制 `maxOutputTokens`，`scripts/run_lifecycle.py:270-275`） | 记录为 `null` |
| 模型版本漂移 | 评测期间不更新 Ollama 模型 tag | 若更新，所有 run 作废 |

## 2. harness 与固定指令

- harness：Codex CLI `exec`，由 `scripts/run_local_agent.py` 启动（唯一已验证路线，`scripts/run_local_agent.py:292-311`）。
- 任务输入 = launcher 固定前缀（`scripts/run_local_agent.py:288-291`）+ `eval/HARNESS_INSTRUCTION.txt` + 空行 + `eval/tasks/<task>/prompt.txt`。
  当前 launcher 没有单独的系统指令通道，因此 `HARNESS_INSTRUCTION.txt` 作为任务正文的前缀传入；这一点在论文里必须写明。
- 任务 prompt 逐字固定，所有模型、所有 run、所有次数完全相同。任何一次为让模型通过而改 prompt 的 run 作废。
- 终局声明格式为 `STATUS / REASON / IMAGE` 三行。解析失败记 `status: NONE`，本身是一个数据点，不重跑。
- 数据集在 `/api/tools/open` 时由 harness 固定；agent 不能切换数据集（本仓也没有 `open_dataset` 工具）。T5/T6 不会退化成“换个数据集”。

## 3. 步数与墙钟上限

| 限制 | 实际值 | 能否由 harness 强制 | 备注 |
|---|---|---|---|
| 观测预算 `renderBudget` | **30** | 是 | 只计 `render_scene` 与 `inspect_slice`（`src/external_tools.py:116-126,136`）；`inspect_scene`/`finish_task` 不计数。schema 与 launcher 都把上限锁在 30（`src/external_tools.py:63`、`scripts/run_lifecycle.py:276-278`） |
| 工具调用总数 40 | **无法强制** | 否 | 本仓没有任何按调用次数中止的机制；`run_pilot.py` 只在事后统计。与 impl-plan §3.3 的“40 次”不一致，今晚按“只限墙钟并注明”执行 |
| 单次墙钟 | **1800 s（30 min）** | 由 `eval/scripts/run_pilot.py` 外部强制 | launcher 明确没有 task timeout（`scripts/run_local_agent.py:334-335`；`scripts/run_lifecycle.py:270-275` 只校验不执行）。到时由 run_pilot 写 stop 文件，再不行 kill 进程树 |
| 单帧渲染 | 60 s 硬超时 | 渲染器内部 | `src/renderer.py:188`；超时表现为工具错误，不是“继续等待” |

失败处理：模型层面的失败（超步数、无效调用、循环、无 STATUS、超墙钟）一律不重跑；只有 infra 崩溃（launcher 退出且未产生任何一次工具调用、渲染器崩溃、run 目录未建立）允许重跑一次并标 `infra_rerun: true`。

## 4. 干净起点与状态

- 每个 run 由 launcher 新开一个 session（`scripts/run_local_agent.py:263`），初始 scene 固定为 `src/renderer.py:93`：`tfStart=0.1`、`tfWidth=0.12`、`rotationXYZ=[0,0,0]`、`translation=[0,0,0]`、crop=全体积、正交投影、512×512。
- 本仓**没有** `reset_state` 工具；run 内无法重置或撤销单次改动（见 `docs/improvement/11-tool-audit.md`）。因此“干净起点”只能靠每个 run 新 session 实现，run 之间不复用 session。
- 截图分辨率固定 512×512（`src/renderer.py:122-123`）。

## 5. 条件与目录

| 条件名 | 模型 | 工具层 | 今晚 |
|---|---|---|---|
| `local35b_v0` | qwen3.6:35b | 当前 T-base（4 个工具） | 跑 |
| `cloud_v0` | 强云端模型 | 当前 T-base | **未接通，跳过**；见 §6 |

目录：

```
eval/runs/pilot/<condition>/<task>/run-YYYYMMDD-HHMMSS-xxxxxx/
    meta.json  claim.json  final.png  trace.jsonl  final_message.txt
    harness.jsonl  manifest.json  run.json  session.json  launcher.log  http/ ...
eval/runs/pilot/<condition>/<task>/_launch-configs/*.json   # run_pilot 生成的 launcher 配置
```

`run_id` 由 launcher 生成（`run-<时间戳>-<随机 token>`），`meta.json` 里的 `index` 记录第几次重复（01/02/03…）。不重命名 run 目录，避免 `run.json`/`manifest.json` 里的路径失真。

## 6. 云端条件为什么没接

当前 launcher 只实现本地 Ollama 路线：`validate_config` 要求 `harness=codex` 且模型名不得含 `:cloud`（`scripts/run_lifecycle.py:246-251`），`execute()` 用 `OllamaDriver.inventory()` 校验本地已安装且带 vision 的模型（`scripts/run_local_agent.py:258-259`），并主动清掉 `OPENAI_API_KEY` 等环境变量（`scripts/run_local_agent.py:280-282`）。接云端需要改 harness/provider 配置，属于“改工具/脚手架”，今晚不做。tonight-brief P5 的云端 4 次 run 因此不执行，状态报告里如实记录。

## 7. 已知环境事实（2026-10-29 00:xx 本地）

- 渲染服务 8765、Ollama 11434 均在监听；5174 由 `fynns_ui_design_core` 的 Vite dev server 占用，5173 空闲。
- 当前机器：i5-12400F / 32 GB RAM / RTX 4060 8 GB（`nvidia-smi` 560.94）。`qwen3.6:35b` 权重约 23.9 GB，放不进 8 GB 显存，Ollama 会部分 offload 到 CPU。
- 历史参照：两次教堂 run（2026-10-24）分别 21.9 min / 40 次工具调用、12.4 min / 22 次工具调用。按此外推，T1/T4/T5 单次 5–25 min；9 次批量约 1–4 h，过夜可行。**注意**：本机 8 GB 显存 + 32 GB 内存装不下 35B（见下），批量应在实验室 2×RTX PRO 5000 或同等机器上跑。
- `think=true` 的选择理由：两个已归档教堂 run 都用 `think=true`；`experiments/local-vlm.example.json` 只是模板，未产生过评测数据。为与已有轨迹保持同一系统，pilot 采用 `think=true`。这一项在正式评测前可以再确认一次，但一旦开跑就固定。

## 8. 任务与数据集映射

| ID | 数据集（`volumePath`） | 类别 | dev/held-out | prompt 文件 |
|---|---|---|---|---|
| T1 | `volumes/foot.dat` | 可解（基线） | dev | `eval/tasks/T1/prompt.txt` |
| T2 | `volumes/foot.dat` | 可解（朝向） | held-out | `eval/tasks/T2/prompt.txt` |
| T3 | `volumes/skull.dat` | 可解 | held-out | `eval/tasks/T3/prompt.txt` |
| T4 | `volumes/aneurism_256x256x256_uint8.dat` | 可解（锚点） | dev | `eval/tasks/T4/prompt.txt` |
| T5 | `volumes/foot.dat` | 明显不可解 | held-out | `eval/tasks/T5/prompt.txt` |
| T6 | `volumes/skull.dat` | 明显不可解（需核实） | held-out | `eval/tasks/T6/prompt.txt` |
| T7 | `volumes/present246x246x221.dat` | 隐蔽不可解 | held-out | `eval/tasks/T7/prompt.txt` |

T7 prompt 采用旧 run 的原文（含原拼写 `chaple`），**不是** tonight-brief 里改写后的版本：

```text
Show me the chaple in the ball-like present inside the box - make the box invisible and the ball invisible, only make the chapel visible
```

理由：tonight-brief P3.1 明确“T7 若与之前教堂测试 run 用的措辞不同，以之前的为准”，且复用旧 run 时 prompt 必须一致。

## 9. 今晚不做什么

- 不改 `src/` 渲染引擎与服务行为；不改 `scripts/renderer_mcp.py`、`scripts/run_local_agent.py` 的工具面。
- 不新增工具（`set_lighting`、`reset_state`、分析工具、`isolate_nodes` 均不做）。
- 不写评委脚本、κ 计算；不做 T2/T3/T6 的模型 run。
- 不改 `docs/improvement/02-dataset-facts.md §6` 的真值 JSON。
