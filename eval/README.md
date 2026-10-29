# eval — pilot 基础设施

固定项见 `eval/CONFIG.md`；工具边界见 `docs/improvement/11-tool-audit.md`；pilot 记录见 `docs/improvement/12-pilot-v0.md`。

## 目录

```
eval/
  CONFIG.md                  # 模型、采样、上限、任务→数据集、prompt 来源
  HARNESS_INSTRUCTION.txt    # 固定的终局报告指令（所有任务相同）
  tasks/T1..T7/prompt.txt    # 逐字 prompt；T7 用旧 run 原文（含 "chaple"）
  scripts/pilot_common.py    # 常量 + claim 解析 + HTTP 小工具
  scripts/run_pilot.py       # 单次 run：调 launcher、限墙钟、落 meta/claim/trace
  scripts/run_batch.py       # 过夜批量：轮次顺序、infra 重试一次、续跑
  scripts/run_witness.py     # 闸门 1：固定调用序列生成见证解（不调模型）
  scripts/sweep_tf.py        # T1 阈值扫描 + 联系表（不调模型）
  scripts/collect_pilot.py   # 汇总表 + CSV + 拼图
  runs/pilot/<condition>/<task>/run-*/   # 原始证据（git 忽略 http/workspace/codex-home）
  results/                   # pilot_v0_runs.csv / pilot_v0_montage.png
```

## 命令

```powershell
# 0) 环境（必须两个都活着）
Invoke-WebRequest http://127.0.0.1:8765/api/service | Select-Object -Expand Content
Invoke-WebRequest http://127.0.0.1:11434/api/tags   | Select-Object -Expand Content

# 下面的 python 用仓库自带的 venv，保证有 PIL/numpy；脚本本身只依赖标准库
$py = "runtime\tui-venv\Scripts\python.exe"

# 0) 两个服务分别探活（不启动 run）
& $py eval/scripts/run_pilot.py --task T4 --check
# 若 renderer 不在：npm --prefix frontend run dev:backend

# 1) 只打印命令，不跑模型
& $py eval/scripts/run_pilot.py --task T4 --condition local35b_v0 --index 1 --dry-run

# 2) 烟雾 run（单次，30 min 上限）
& $py eval/scripts/run_pilot.py --task T4 --condition local35b_v0 --index 1 --timeout 1800

# 3) 过夜批量：3 轮 × (T4, T1, T5)，可中断后续跑
& $py eval/scripts/run_batch.py --condition local35b_v0 --rounds 3 --tasks T4 T1 T5 --timeout 1800

# 4) 汇总（可重复执行；保留已填的评分/归因两列）
& $py eval/scripts/collect_pilot.py

# 5) 见证解（渲染器 only；T4 已有默认计划）
& $py eval/scripts/run_witness.py --task T4 --dry-run
& $py eval/scripts/run_witness.py --task T4

# 6) T1 阈值扫描（渲染器 only；11–14 张）
& $py eval/scripts/sweep_tf.py --task T1 --start 0.08 --stop 0.60 --step 0.04 --dry-run
& $py eval/scripts/sweep_tf.py --task T1 --start 0.08 --stop 0.60 --step 0.04
```

## 约定

- `run_pilot.py` 退出码：`0` 已产生 run 目录（模型好坏都算 0）、`2` 预检失败、`3` infra 失败（没有 run 目录）。
- `meta.json` 的 `infraCandidate=true` 表示没有观察到任何工具调用；不要当模型失败。
- `claim.json` 的 `basis=missing` 表示终局没有 STATUS 格式，按 `status: NONE` 记录，不重跑。
- 评分的人看 `final.png` 与 `docs/improvement/12-pilot-v0.md`；不要看 `claim.json` 以免锚定（正式评测才要求盲评，pilot 不盲评）。
- `eval/runs/**/http/`、`workspace/`、`codex-home/` 是本地大文件，已 git 忽略；`trace.jsonl`/`meta.json`/`claim.json`/`final.png`/`final_message.txt` 会被提交（除非体积异常）。
