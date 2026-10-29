# 11 工具审计：本仓 4 个 MCP 工具 vs BorgVR 14 个工具

> 日期：2026-10-29。范围：只读代码，不改工具。对照对象：`borgvr-fusssohle-protokoll.txt` 里的 14 个工具与参数约定（见 `docs/improvement/10-solvable-demo-tasks.md` 第 19 节）。
> 证据等级：所有结论都标了代码位置；没有运行新的模型实验。

## 0 最重要的 5 个缺口或歧义（明天展示用）

1. **没有 `reset_state`，run 内也不能撤销单次改动。** 本仓唯一的“重置”是 launcher 为每个 run 新开一个 session（`src/external_tools.py:62-79`，初始 scene 在 `src/renderer.py:93`）。模型把 TF/裁剪改坏之后，只能自己重新提交一套完整参数；没有“回到上一步/回到初始状态”的低成本动作。归因风险：这种失败容易被误记成 `model`，其实是工具面缺原语。
2. **`wait_until_rendered` 没有独立工具，只有隐式同步阻塞；单帧 60 s 硬超时会伪装成工具错误。** `render_scene` 在原生进程退出并完成读回校验后才返回（`src/external_tools.py:137-140`、`src/renderer.py:199-232`、`scripts/renderer_mcp.py:47-55`），所以“截图不是渲染一半的帧”这一点是有保证的；但模型无法查询“还在渲染/已失败”，60 s 超时直接抛 `RuntimeError`（`src/renderer.py:188,208-209`）。
3. **关键参数语义没写全，是 `interface_ambiguity` 的高危区。** `translation` 没有单位/归一化说明（`src/renderer.py:116,157-158`）；`crop` 只写了“half-open storage voxel bounds”，没写 6 个数的顺序是 `[x0,x1,y0,y1,z0,z1]`（`src/external_tools.py:27` 描述 vs `src/renderer.py:117-121` 校验）；`tfStart/tfWidth` 没写默认值 0.1/0.12（`src/renderer.py:93`）与输入强度域（renderer-uint8/255，`src/volume.py:34-41`）。`expectedRevision` 只写在 launcher 前缀里，没写进 `/api/tools` 的描述。**实测旁证（非评测）**：2026-10-29 01:56 用 `qwen3.5:9b` 做 wrapper 烟雾时，模型在 `inspect_scene`（revision 0）之后传了 `expectedRevision: 1`，直接收到 `Stale scene revision`；这正是“描述缺失导致猜错”的样子，不是模型看不懂图。
4. **相对 BorgVR 的表达力缺口。** 无 `set_render_mode`（固定 method 0，`src/renderer.py:124-126`）、无 `set_iso_value`（只有 smoothstep TF，`src/renderer.py:109-112,161`）、无 `set_lighting`、无 `set_annotation_visibility`、无任意裁剪平面（只有轴对齐 AABB，`src/renderer.py:117-121`）、无相对 `rotate_view`（只有绝对欧拉角，`src/renderer.py:159-160`）。
5. **预算语义与“40 次工具调用”不一致。** `renderBudget` 上限 30（`src/external_tools.py:63`、`scripts/run_lifecycle.py:276-278`），且只计 `render_scene` + `inspect_slice`（`src/external_tools.py:116-126,136`）；`inspect_scene`/`finish_task` 不计数。失败的 render 也扣预算（`src/external_tools.py:136` 在 render 之前 `used += 1`）。launcher 没有任何 timeout 或调用次数上限（`scripts/run_local_agent.py:334-335`）。今晚只能限墙钟并在 `eval/CONFIG.md` 注明。

## 1 对照表（BorgVR 14 → 本仓）

| BorgVR 工具 | 本仓对应 | 状态 | 参数语义写进描述 | 证据 / 备注 |
|---|---|---|---|---|
| `list_datasets` | 无 | 无 | — | 数据集由 harness 在 `/api/tools/open` 固定（`src/external_tools.py:63-79`）；agent 看不到列表，也不能换。评测需要如此，但和 BorgVR 的“先找数据集”流程不同 |
| `open_dataset` | 无（harness 侧打开） | 语义不同 | — | 打开动作发生在 agent 第一次工具调用之前（`scripts/run_local_agent.py:263`），且会先渲染 `frame-0`（`src/external_tools.py:75-76`） |
| `close_dataset` | 无 | 无 | — | session 随 launcher 退出而闲置；没有 agent 可调的关闭动作 |
| `get_application_state` | `inspect_scene` | 部分 | 部分 | 返回 scene、`nativeState`、volume 事实、剩余观测数、最后一张 PNG（`src/external_tools.py:23,81-91,105`）。`nativeState` 是**上一次 render 的读回**（`src/renderer.py:213-232`），不是对渲染器的实时查询 |
| `set_render_mode` | 无 | 无 | — | 固定 raycast `method=0`，其它值被 `validate_scene` 拒绝（`src/renderer.py:124-126`） |
| `set_transform` | `render_scene.parameters.rotationXYZ/translation` | 语义不同 | 部分 | 不是四元数，是欧拉角 + 平移（`src/external_tools.py:27`、`src/renderer.py:159-160`）。旋转顺序 X→Y→Z 已写进描述；平移的坐标系/单位/归一化**没写**（范围 -2..2，`src/renderer.py:116`） |
| `rotate_view` | 无独立工具（并入 `render_scene`） | 语义不同 | 部分 | 只能设绝对 `rotationXYZ`，没有“相对当前视角再转”的动作；每次都要带 `expectedRevision`（`src/external_tools.py:127`） |
| `set_iso_value` | `render_scene.parameters.tfStart/tfWidth` | 语义不同 | 部分 | 单调 smoothstep，不是 isovalue 模式（`src/renderer.py:109-112,161`）。描述写了“低于 start 透明、高于 start+width 亮/不透明”，没写默认值与强度域 |
| `set_clipping` | `render_scene.parameters.crop` | 语义不同 | 部分 | 轴对齐 AABB、存储体素、半开区间、**非归一化**（`src/renderer.py:117-121`）。描述没写 6 个数的排列顺序；BorgVR 的归一化 min/max 与任意平面都不支持 |
| `set_lighting` | 无 | 无 | — | 场景里没有光照字段（`src/renderer.py:93`）；渲染器用固定光照 |
| `set_annotation_visibility` | 无 | 无 | — | 没有标注对象 |
| `reset_state` | 无（仅新 session） | 无 | — | 见 §0.1；`src/external_tools.py:62-79` 只在 open 时建初始 scene |
| `wait_until_rendered` | 隐式（同步返回） | 部分 | 部分 | `render_scene` 阻塞到原生进程结束并校验 PNG/读回（`src/external_tools.py:137-140`、`src/renderer.py:199-232`）。没有独立工具、没有进度、没有“等多久”的语义 |
| `capture_screenshot` | 隐式（每次 render 返回 PNG）+ `finish_task` 保存既有帧 | 部分 | 部分 | `render_scene` 返回带 sha256 的 PNG（`src/external_tools.py:88-90`）；`finish_task` 复制既有帧（`src/external_tools.py:107-115`）。没有“不重新渲染、只截图”的动作 |

## 2 反向表（本仓有、BorgVR 没有）

| 本仓工具 | 存在的理由 | 备注 |
|---|---|---|
| `inspect_scene` | 免费读回当前 scene + 最后一张 PNG + 剩余预算；对应 BorgVR 的 `get_application_state` + 一次截图 | 不消耗预算（`src/external_tools.py:105` 在预算检查之前返回）。这是可靠性的关键：模型可以反复确认状态而不用付观测代价 |
| `render_scene` | 把 `set_transform` + `rotate_view` + `set_iso_value` + `set_clipping` + 渲染 + 截图合并成一次原子操作 | 少一个“改状态但没渲染”的中间态；代价是每次调整都要付一次渲染 + 一次观测（`src/external_tools.py:127-140`） |
| `inspect_slice` | 返回真实体素切片，不是 raycast；帮助模型判断内部结构 | **BorgVR 没有**。它属于“信息工具”，当前在 T-base 里（`src/external_tools.py:29`），T-plus 计划再加直方图/连通域。论文 RQ3 要把这一点写清楚：基线和加强版都含切片，差异只在分析工具 |
| `finish_task` | 显式选择“最终帧 + 诚实声明”，把 claim 与证据绑定 | 对应 BorgVR 的“存 PNG + 设置 JSON”。要求 `frameRevision` 必须匹配已存在的帧（`src/external_tools.py:108-111`）；描述没写“它就是该帧的 scene revision”，是一个小的 interface 风险 |

## 3 四个可靠性原语

| 原语 | 是否有 | 实现方式 | 缺口 |
|---|---|---|---|
| `wait_until_rendered` | 隐式有 | `render_scene` 同步阻塞，原生进程退出后校验 `native-state.json`、PNG 尺寸、TF/裁剪/平移/旋转读回（`src/renderer.py:199-232`） | 无独立工具、无进度、60 s 硬超时（`src/renderer.py:188`）；超时与真实错误都表现为 `RuntimeError`（`src/renderer.py:208-209`） |
| `get_application_state` | 部分有 | `inspect_scene` 返回 scene + 上次读回 + 预算（`src/external_tools.py:81-91,105`） | 不是实时查询渲染器；如果上一次 render 失败，返回的还是旧 `result`，需要靠 `scene.revision` 判断 |
| `reset_state` | 无 | 只有“新 session = 初始 scene”（`src/external_tools.py:62-79`、`src/renderer.py:93`） | run 内无法重置/撤销；这是最值得在 v1 补的原语 |
| `capture_screenshot` | 隐式有 | 每次 render 返回 PNG（sha256 校验，`src/external_tools.py:88-90`）；`finish_task` 保存（`src/external_tools.py:107-115`） | 没有“只截图不渲染”；没有独立导出路径给 agent |

## 4 参数语义逐项核对

| 参数 | 声明范围（代码） | 实际语义 | 描述里写了吗 | 缺口 |
|---|---|---|---|---|
| `rotationXYZ` | 每轴 -360..360，单位度（`src/renderer.py:115`） | `addrotationx/y/z`，按 X→Y→Z 预乘；默认视线沿 z（`src/renderer.py:157-160,223-232`；`docs/improvement/02-dataset-facts.md §3`） | 写了“Euler degrees apply X then Y then Z”“Anatomical orientation is unknown”（`src/external_tools.py:24`） | 没写旋转中心、默认视线、正方向；数据轴与解剖方向的对应仍是 unknown |
| `translation` | 每轴 -2..2（`src/renderer.py:116`） | `settranslation`（`src/renderer.py:157-158`） | **没写** | 单位（归一化？体素？）、坐标系、正方向都没写；这是最可能被猜错的参数 |
| `tfStart` | 0..1，默认 0.1（`src/renderer.py:93,111`） | smoothstep 起点；低于 start 透明 | 写了单调性与透明/亮的关系（`src/external_tools.py:24`） | 没写默认值；没写横轴是 `renderer-uint8/255`（`src/volume.py:34-41`） |
| `tfWidth` | (0,1]，默认 0.12（`src/renderer.py:93,112`） | smoothstep 宽度；`start+width` 以上不透明 | 同上 | 同上 |
| `crop` | 6 个数，0..max(dim)，半开（`src/renderer.py:117-121`） | `[x0,x1,y0,y1,z0,z1]` 存储体素边界 | 写了“half-open storage voxel bounds, NOT normalized”（`src/external_tools.py:24`） | 没写排列顺序；没写必须 `lo < hi`；没写超出范围会被拒绝 |
| `expectedRevision` | 整数 ≥0，必须等于 `scene.revision`（`src/external_tools.py:127`） | 乐观并发控制；不匹配直接报错 | 工具描述没写；launcher 前缀写了“Use expectedRevision from the latest result”（`scripts/run_local_agent.py:289-290`） | 两处说明不一致；只看 `/api/tools` 的模型会缺这条。9B 烟雾里实际发生了一次 `Stale scene revision`（revision 0 时传了 1） |
| `frameRevision` | 整数 ≥0，必须匹配已存在的 `frame-<revision>`（`src/external_tools.py:108-111`） | 选择最终帧 | 没写它等于该帧的 scene revision | 模型可能传“当前 revision”而不是“想保存的那一帧的 revision” |
| `inspect_slice.axis/index` | `x/y/z`，index 0..dim-1（`src/external_tools.py:29,120`） | 存储轴切片；返回像素轴与 `verticalFlip`（`src/external_tools.py:90`） | 基本写了（storage axes、预算） | 没写切片方向/翻转；但返回值里带 `pixelAxes`/`verticalFlip`，可补救 |

## 5 结论与今晚的行动

- 本仓工具面**不是** BorgVR 的等价复刻：4 个粗粒度工具 vs 14 个细粒度工具；可靠性原语里 `reset_state` 完全缺失，`wait_until_rendered`/`capture_screenshot` 只是隐式保证。
- 这些缺口足以解释一部分失败，但**今晚不改**（tonight-brief 铁律 3、8）。审计的用途是：
  1. 归因时区分 `tool_gap` / `interface_ambiguity` 与 `model`；
  2. 论文第 3 章写清系统边界；
  3. v1 的候选修复清单（不在今晚实施）：`reset_state`、`wait/poll` 或进度、补全 `translation`/`crop` 顺序/`tf` 默认值、`frameRevision` 语义、可选的 `set_lighting`。
- 冻结判据仍按 impl-plan §3.6：连续两轮开发评测没有新的 `infra_bug`/`tool_gap`/`interface_ambiguity` 失败模式，才打 `eval-v1`。
