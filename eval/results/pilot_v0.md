# Pilot v0：pilot 数据记录（2026-10-29）

> 目的：给明天的教授邮件与组会一份可核对的 pilot 记录。**本文件不评分**；评分与归因两列留给用户。
> 规则：模型层面的失败不重跑；infra 崩溃只重跑一次并标 `infra_rerun`。所有 run 的原始日志都在 `eval/runs/pilot/`。
> 本文件由 `eval/scripts/collect_pilot.py` 维护（`RUNS` / `ISSUES` 标记块之间是脚本生成的）；整理后的摘要与结论在 `docs/PROJECT.md` §6.3。

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
| 存档副本 | `docs/figures/t7-reuse-run-a-final.png` |
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
| 存档副本 | `docs/figures/t7-reuse-run-b-final.png` |
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

- 两张最终图在视觉上都只有黑色背景上的若干亮斑/小块，没有可辨认的教堂结构；这与 `docs/PROJECT.md` §6.2 的“球壳与教堂强度重叠、AABB 必穿球壳”一致。
- run b 的 `tfStart=0.69` 与它自述的“球密度 0.4–0.6、教堂 >0.65”和 PROJECT.md §6.2 的实测密度带不一致（PROJECT.md §6.2：球壳 0.60–0.78，教堂候选 ≤0.28）。这是 agent 自述与数据事实的冲突，记录在此，归因留给用户。
- run a 用尽 30 次观测预算后仍尝试 `inspect_slice` 与 `render_scene`，各报一次预算耗尽错误；这是“预算语义”而不是模型崩溃。
- 旧 run 的工具接口与今晚审计的 4 个工具一致（`inspect_scene` / `render_scene` / `inspect_slice` / `finish_task`）。

## 2 Pilot 运行记录（P5）

<!-- RUNS:BEGIN -->
| run_id | 任务 | 条件 | 工具调用数 | 墙钟(min) | 错误数 | STATUS | REASON（缩略） | 最终图 | 退出原因 | 评分 | 归因 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `run-20261029-030550-5e035b` | T1 | local35b_v0 | 5 | 1.6 | 0 | DONE | Clear foot skeleton rendered showing all major bone group... | `eval/runs/pilot/local35b_v0/T1/run-20261029-030550-5e035b/final.png` | completed |  |  |
| `run-20261029-032431-ac288b` | T1 | local35b_v0 | 6 | 2.0 | 0 | DONE | The lateral view clearly shows the foot skeleton includin... | `eval/runs/pilot/local35b_v0/T1/run-20261029-032431-ac288b/final.png` | completed |  |  |
| `run-20261029-035014-611271` | T1 | local35b_v0 | 42 | 23.1 | 3 | PARTIAL | The foot skeleton (metatarsals and tarsals) is visible bu... | `eval/runs/pilot/local35b_v0/T1/run-20261029-035014-611271/final.png` | completed |  |  |
| `run-20261029-030355-3dde78` | T4 | local35b_v0 | 4 | 1.8 | 0 | DONE | The blood vessels are clearly visible with enhanced contr... | `eval/runs/pilot/local35b_v0/T4/run-20261029-030355-3dde78/final.png` | completed |  |  |
| `run-20261029-032335-ed2082` | T4 | local35b_v0 | 3 | 0.9 | 0 | DONE | Widened the transfer function width to 0.25, revealing bo... | `eval/runs/pilot/local35b_v0/T4/run-20261029-032335-ed2082/final.png` | completed |  |  |
| `run-20261029-034906-73c584` | T4 | local35b_v0 | 4 | 1.1 | 0 | DONE | The oblique view at rotation [0, 45, -20] provides a clea... | `eval/runs/pilot/local35b_v0/T4/run-20261029-034906-73c584/final.png` | completed |  |  |
| `run-20261029-030726-c046cc` | T5 | local35b_v0 | 32 | 16.1 | 0 | CANNOT | The volume contains skeletal structures of a distal extre... | `eval/runs/pilot/local35b_v0/T5/run-20261029-030726-c046cc/final.png` | completed |  |  |
| `run-20261029-034420-f40452` | T5 | local35b_v0 | 11 | 4.7 | 0 | CANNOT | The volume contains foot anatomy (metatarsals and phalang... | `eval/runs/pilot/local35b_v0/T5/run-20261029-034420-f40452/final.png` | completed |  |  |
| `run-20261029-041324-f8ffcf` | T5 | local35b_v0 | 16 | 6.8 | 0 | CANNOT | The 256³ volume contains hand/wrist skeletal anatomy (fin... | `eval/runs/pilot/local35b_v0/T5/run-20261029-041324-f8ffcf/final.png` | completed |  |  |
<!-- RUNS:END -->

评分列建议值：`Pass / Partial / Fail`（可解任务）或 `R0 / R1 / R2`（不可解任务的报告）。归因列建议值：`infra_bug / tool_gap / interface_ambiguity / model:<perception|planning|spatial|persistence|format>`。

## 3 已知问题

<!-- ISSUES:BEGIN -->
- `run-20261029-035014-611271`：工具错误 3 次；明细见 `trace.jsonl` 的 error 字段。
- `run-20261029-032635-aa445a`：已排除 — Renderer backend 8765 was killed at 03:29:09 local by the agent background shell's 30-minute deadline, 2.5 min into this run. The last served tool call was 03:28:20; the model then received connection refused and claimed CANNOT. Infra contamination, not model behavior. Rerun T5 index 2.
<!-- ISSUES:END -->

### 3.1 人工补充

**2026-10-29 03:29 后端中断（已定性为 infra，不是模型失败）**

- 03:03–03:30 的批量跑到第 2 轮结束，共完成 6 个 run（T4×2、T1×2、T5×2）。
- 03:29:09，渲染后端 8765 被 agent 的后台 shell 30 分钟 deadline 杀掉；当时 T5 index 2（`run-20261029-032635-aa445a`）已服务 5 次 render（最后成功工具调用 03:28:20），之后模型收到 connection refused，最终声明 `CANNOT`，原因写的是 MCP server unreachable。
- 该 run 的 `meta.json` 已标 `excluded: true`，原因写在 `exclusionReason`；它不计入 T5 的模型数据。**恢复后重跑 T5 index 2**。
- 03:26 之后的 T4/T1/T5 index 3 没有开始（预检在 8765 上失败），因此没有产生 run 目录。
- 结论：以后后端必须由独立终端常驻，不能挂在 agent 的后台 shell 上；`run_batch.py` 的连续 3 次 infra 中止行为是正确的。

**2026-10-29 03:44–04:20 续跑完成（9 个有效 run + 1 个 excluded）**

| run | 调用数 | 墙钟 | 声明 | 备注 |
|---|---:|---:|---|---|
| T5-2 `run-20261029-034420-f40452` | 11 | 4.7 min | CANNOT | 替换 03:26 的污染 run；理由正确（foot anatomy，无心脏）；交出脚趾特写尽力图 |
| T4-3 `run-20261029-034906-73c584` | 4 | 1.1 min | DONE | 血管树清晰 |
| T1-3 `run-20261029-035014-611271` | 42 | 23.1 min | PARTIAL | 3 次工具错误；超出 40 次软上限；裁剪切掉了部分骨，图不完整 |
| T5-3 `run-20261029-041324-f8ffcf` | 16 | 6.8 min | CANNOT | 结论“没有心脏”正确，但把数据说成 hand/wrist（实际是 foot）；R2/R1 由你定 |

**T1-3 的 3 次错误是 `interface_ambiguity`，不是模型乱来**：模型三次给 `translation` 传了 `[0,0,-30]`、`[0,0,-10]`、`[0,0,-5]`，服务端按 `-2..2` 的范围拒绝，返回 `Invalid bounded numeric vector`。工具描述里没有写 translation 的范围/单位/归一化（`docs/PROJECT.md` §5.3），这是审计里已经标出的高危项；这 3 次失败应归 `interface_ambiguity`，修法是把范围写进工具描述。

**T1-3 的 42 次调用**：超过 brief 的“40 次工具调用”软上限，但本仓 harness 没有调用次数上限（`CONFIG.md §3`），所以它跑完了，墙钟 23.1 min 仍在 30 min 内。这属于 `model:persistence`/探索策略，不是 infra；保留为数据点。

**T5-3 的肢体误判**：`REASON` 说 “hand/wrist skeletal anatomy (finger bones, carpals)”，而数据是脚。它抓住了“没有心脏”这个正确结论，但身体部位识别错误。按 `docs/PROJECT.md` §4.4 的 R 级定义，R2 要求原因与“报告证据要点”一致（数据是脚、不含胸部结构）；这条至少要在评分时标注为“结论对、证据部分错”。


