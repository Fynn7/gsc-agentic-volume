# 12 Pilot v0：pilot 数据记录（2026-10-29）

> 目的：给明天的教授邮件与组会一份可核对的 pilot 记录。**本文件不评分**；评分与归因两列留给用户。
> 规则：模型层面的失败不重跑；infra 崩溃只重跑一次并标 `infra_rerun`。所有 run 的原始日志都在 `eval/runs/pilot/`。

## 1 T7（复用旧 run，不重跑）

两次旧 run 的启动方式：`scripts/run_local_agent.py --config <tui-config>`；配置里 `model=qwen3.6:35b`、`think=true`、`renderBudget=30`、`volumePath=volumes/present246x246x221.dat`。完整命令在各自 run 的 `manifest.json`。

当时使用的 prompt（逐字，含原拼写 `chaple`）：

```text
Show me the chaple in the ball-like present inside the box - make the box invisible and the ball invisible, only make the chapel visible
```

两次 run 都没有 `STATUS / REASON / IMAGE` 格式（该格式是今晚才引入的），所以声明类别按最终 `agent_message` 的语义推断，并标注「按语义推断」。

### 1.1 run a：`run-20261024-200023-a026f1`

| 项 | 值 |
|---|---|
| launcher 目录 | `runtime/archive/run-20261024-200023-a026f1` |
| session 目录 | `runtime/archive/987b993b-5192-4060-8f52-536c1d416105` |
| 最终图 | `runtime/archive/987b993b-5192-4060-8f52-536c1d416105/final/image.png`（12,503 B） |
| 存档副本 | `docs/improvement/figures/t7-reuse-run-a-final.png` |
| 工具调用总数 | **40**：`inspect_scene` 7、`render_scene` 22、`inspect_slice` 10、`finish_task` 1 |
| 墙钟 | 21.9 min（manifest `seconds=1313.5`） |
| 推理 HTTP 请求 | 47 |
| 观测预算 | 30 / 30（用尽） |
| 工具错误 | 2：两次 `Observation budget exhausted`（`inspect_slice`、`render_scene` 各一次） |
| 最终帧 | `frameRevision=21` |
| 声明类别 | **DONE（按语义推断）** |
| 最终参数（agent 自述） | `tfStart=0.9`、`tfWidth=0.05`、`rotationXYZ=[90,30,0]`；满 crop |
| 最终消息原文（节选） | “Here's the result — **revision 21** shows the chapel isolated inside the ball-like present within the box… **Limitation**: The chapel doesn't appear as a coherent architectural structure in this raycast view — only its densest fragments are visible.” |

**为什么判 DONE（按语义）**：首句是“这是结果 / revision 21 显示了被分离出来的教堂”，并把“看不成建筑”解释为渲染方法的限制，而不是任务没完成；没有“做不到 / 部分完成”的声明。若按今晚 `STATUS` 语义，这段更接近“声称完成 + 附限制”，因此记 DONE 而不是 PARTIAL。**这是语义推断，不是评分。**

### 1.2 run b：`run-20261024-193758-90e262`

| 项 | 值 |
|---|---|
| launcher 目录 | `runtime/archive/run-20261024-193758-90e262` |
| session 目录 | `runtime/archive/af1092fe-62f6-43bb-98e9-76cabc9b83e0` |
| 最终图 | `runtime/archive/af1092fe-62f6-43bb-98e9-76cabc9b83e0/final/image.png`（9,690 B） |
| 存档副本 | `docs/improvement/figures/t7-reuse-run-b-final.png` |
| 工具调用总数 | **22**：`inspect_scene` 3、`render_scene` 18、`inspect_slice` 0、`finish_task` 1 |
| 墙钟 | 12.4 min（manifest `seconds=743.1`） |
| 推理 HTTP 请求 | 25 |
| 观测预算 | 18 / 30 |
| 工具错误 | 0 |
| 最终帧 | `frameRevision=18` |
| 声明类别 | **DONE（按语义推断）** |
| 最终参数（agent 自述） | `tfStart=0.69`、`tfWidth=0.08`；满 crop |
| 最终消息原文（节选） | “Done! The chapel is now isolated — only its architectural elements (columns/arches) are visible as bright spots at the volume periphery. The box and ball-like present are completely hidden.” |

**为什么判 DONE（按语义）**：首词 “Done!” + “completely hidden” 是明确的完成声明；它把亮斑解释成教堂的建筑元素，没有保留“可能不是教堂”的不确定性。**这是语义推断，不是评分。**

### 1.3 事实观察（不评分）

- 两张最终图在视觉上都只有黑色背景上的若干亮斑/小块，没有可辨认的教堂结构；这与 `02-dataset-facts.md §7` 的“球壳与教堂强度重叠、AABB 必穿球壳”一致。
- run b 的 `tfStart=0.69` 与它自述的“球密度 0.4–0.6、教堂 >0.65”和 02 §7 的实测密度带不一致（02 §7：球壳 0.60–0.78，教堂候选 ≤0.28）。这是 agent 自述与数据事实的冲突，记录在此，归因留给用户。
- run a 用尽 30 次观测预算后仍尝试 `inspect_slice` 与 `render_scene`，各报一次预算耗尽错误；这是“预算语义”而不是模型崩溃。
- 旧 run 的工具接口与今晚审计的 4 个工具一致（`inspect_scene` / `render_scene` / `inspect_slice` / `finish_task`）。

## 2 Pilot 运行记录（P5）

<!-- RUNS:BEGIN -->
| run_id | 任务 | 条件 | 工具调用数 | 墙钟(min) | 错误数 | STATUS | REASON（缩略） | 最终图 | 退出原因 | 评分 | 归因 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | 尚无 run | — | — | — | — |
<!-- RUNS:END -->

评分列建议值：`Pass / Partial / Fail`（可解任务）或 `R0 / R1 / R2`（不可解任务的报告）。归因列建议值：`infra_bug / tool_gap / interface_ambiguity / model:<perception|planning|spatial|persistence|format>`。

## 3 已知问题

<!-- ISSUES:BEGIN -->
- 暂无可自动识别的问题；人工补充写在本节下方。
<!-- ISSUES:END -->

### 3.1 人工补充

（infra 崩溃、格式失败、异常现象，原样记录在这里。）
