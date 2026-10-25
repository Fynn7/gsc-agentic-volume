# 08 本地仓库复核记录

> 日期：2026-10-25　　依据：`conversations.json` 中唯一有内容的对话（2026-10-24 18:59 → 2026-10-25 11:31，附件含日志、截图 ZIP 与 8 张切片）加上本仓库的两次真实运行证据。语言中文，代码标识符英文；证据标签沿用 `00-README.md`。

## 1. 已复核的本地事实【实测】

### 1.1 两次运行

- run a：`runtime/local-vlm/cli-custom/run-20261024-200023-a026f1`，会话 `runtime/987b993b-5192-4060-8f52-536c1d416105`。`used=30/30`：`inspect_scene` 7 次、`render_scene` 22 次调用（成功 21 次）、`inspect_slice` 10 次调用（成功 9 次）、`finish_task` 1 次。
- run b：`runtime/local-vlm/cli-custom/run-20261024-193758-90e262`，会话 `runtime/af1092fe-62f6-43bb-98f6-336c9abc9b83e0`。`used=18/30`：`inspect_scene` 3 次、`render_scene` 18 次（全部成功）、`finish_task` 1 次。
- 两次配置均为 `temperature=0.0`、`think=true`（Codex 侧 `reasoning_effort=medium`）、`contextSize=16384`，来自运行目录 `run.json`。run a 上下文压缩 6 次，run b 2 次；`harness.jsonl` 有真实 reasoning 文本。
- 最终帧：run a `frame-21`（`nonblackPixels=5085`，`tfStart=0.9`、`tfWidth=0.05`，12 个纺锤形金属件侧视）；run b `frame-18`（`nonblackPixels=2365`，`tfStart=0.69`、`tfWidth=0.08`，5 个金属件）。两张最终图都没有教堂。
- 全部 40 次 `render_scene` 调用的 `tfStart` 均 ≥0.5，一次都没有探测过 0.5 以下。
- run b 的 crop `[80,166,80,166,80,141]` 在第 5–11 帧生效，第 10/11 帧全黑，第 12 帧恢复全体积；模型曾误判为 crop 问题。

### 1.2 体数据与切片

- 体数据 `dimensions=[246,246,221]`，`volumeHash=32db26626c7c57d29cfed2a56edfbad26951c0e2c029e4b5e4f8a91b5a2`。
- 中心邻域 renderer-uint8 约 73（≈0.286），与“块状物约 0.28”的推断相容。
- `runtime/987.../slice-28/slice.png` 可见球壳、带垛口块状物、下方亮色造型，与 `02` 第 2.2 节的描述一致。

### 1.3 代码事实

- `src/renderer.py` 写死 `setmethod 0` 和 `settfparams false`；`validate_scene` 只允许 `tf.type='smoothstep'`、`method=0`。
- `ExternalTools.snapshot()` 不返回 `volume.statistics`（`volume.json` 里虽有直方图，模型拿不到）。
- `scripts/run_local_agent.py` 只把 `contextSize` 传给 Codex（`model_context_window`），代理转发给 Ollama 的请求体只改写 `temperature` 和 `think`，不设置 `num_ctx`。

## 2. 对原稿的更正

| 位置 | 原稿 | 更正后 |
|---|---|---|
| `00` 一句话结论、`01` 摘要/§3.2 | “39 次 `render_scene`（run a 21 次、run b 18 次）” | 40 次调用（run a 22 次、run b 18 次），其中 39 次成功执行 |
| `01` §2 工具调用行 | run a “21 次 + 9 次” | run a 22 次调用（成功 21 次）+ 10 次调用（成功 9 次） |
| `01` R6、`05` E0/E1、`07` 预设 | “think 关闭”、E0 `think=false` | 两次运行均为 `think=true`；E0 保持 `think=true`，E1 只改 temperature/上下文/压缩 |
| `01` R6 | “TUI Thought 0s = 没有推理” | `harness.jsonl` 有 reasoning 文本；TUI 显示 0s 是显示/时间戳统计问题 |
| `01` R4 | “示例配置 contextSize 16384” | 两次 `run.json` 实测均为 16384 |
| `03` 新流程图/对照表、`diagrams/agent-flow-after.mermaid` | `finish_task` 写 “task check: structure-verified”；`verify_frame` 指标含“连通块数量”；候选渲染写“高斯窗” | `taskSuccess` 恒为 `unverified`，只新增 `structureCheck`；指标为 `coverage`/`leak`/`framePixels`；候选渲染为“派生体数据 + 光照 + 低阈值 smoothstep + 包围盒” |
| `05` 路径 A 第 5 条、`07` §3/T3.2 | “打开 think” | 改为“保持 `think=true`”；`07` 另补“从 `run.json` 抄实际值，已核对” |

## 3. 仍待验证项

1. 完整体数据上块状物的平均值是否确实落在 0.25–0.31，以及它的 x 方向范围（现有切片只覆盖 x=120/122/123）。
2. “块状物 = 教堂、亮色小造型 = 别物”是否成立，需人工在候选联系表上确认（见 `02` §6）。
3. 高斯窗与光照模式 7 在本机 Raycaster 构建上是否可用（Spike S2/S3，先做这两个）。
4. Ollama 实际生效的上下文长度（`ollama ps`），以及 Codex 自动压缩阈值的配置项名称（Spike S6）。

## 4. 建议的下一步

1. 先跑 Spike S2（光照模式 7）、S3（高斯窗回读）——若失败，路径 A 对应部分改用“派生体数据 + smoothstep”实现，不阻塞后续。
2. 做真值标注（`structure_cli.py` 输出 `montage.png`，人工确认后写入 `eval/golden/christmas-present.json`），再用 run a/run b 最终帧校准评分器（预期都判失败）。
3. 按 E0 → E1 → E2（v2lite）→ E3（v2）顺序做 A/B；E2 已稳才上结构工具，避免在评分器就绪前做自动搜索或训练。
