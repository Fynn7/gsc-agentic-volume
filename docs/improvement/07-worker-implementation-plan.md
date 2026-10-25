# 07 Worker 实施计划（给 coding agent）

> **读者**：在 `gsc-agentic-volume` 仓库里执行本计划的 coding agent。
> **前置阅读**：`02-dataset-facts.md`（坐标约定与数据事实）、`06-evaluation-protocol.md`（评分口径）、`05-paths-and-roadmap.md` 第 3 节路径 A、B、C。
> **本计划的范围**：路径 A、B、C 加评测骨架，对应里程碑 M0 到 M3。路径 D 到 H 只在第 12 节列为待办，**不要在本轮实现**。
> 文中“【待核实】”表示接口或配置项名称我没有在你的环境里确认过，必须先在对应 Spike 里核实，再据实际情况修改。

## 0. 任务与完成定义

**一句话任务**：给渲染服务增加 `toolProfile: v2lite` 与 `v2` 两个工具集（高斯窗、光照、直方图、探针；以及结构树、候选联系表、体素手术、帧验证），保持旧 profile 行为不变，并建立可程序化评分的评测骨架，使 E0 到 E3 四档实验可以在同一套证据体系下运行。

**完成定义（DoD）**，全部满足才算完成：

1. `legacy` profile 的工具列表、参数架构、渲染脚本文本与改动前逐字节一致；改动前通过的测试仍然通过。
2. 两个新 profile 按第 6 节的契约实现并有单元测试：`v2lite`（5 个工具：扩展后的 `inspect_scene`、`render_scene`，沿用的 `inspect_slice`、`finish_task`，新增 `probe_voxel`）与 `v2`（9 个工具：`v2lite` 的全部加 `analyze_structure`、`render_candidates`、`isolate_nodes`、`verify_frame`，并扩展 `finish_task`）。
3. 在合成体上，`analyze_structure` 能恢复出预期节点（第 5 节阶段 1 的验收）。
4. 在真实体数据上（设置 `GAV_TEST_VOLUME` 后），节点表满足 `05` 路径 B 的成功标志。
5. `eval/score_run.py` 能对旧 run 的最终帧评分，且按 `06` 第 4 节的校准，正负对照全部符合预期。
6. 第 10 节的 5 个实验预设文件存在，且 `python scripts/run_local_agent.py --config <预设>` 能启动（真实模型运行由人来跑，不属于本计划的验收）。
7. `docs/improvement/progress.md` 记录了每个任务的状态、测试输出摘要、与计划的偏差。

## 1. 护栏（不可违反）

| 编号 | 规则 | 原因 |
|---|---|---|
| G1 | `toolProfile` 默认值是 `legacy`；旧 profile 的行为、旧测试的期望都不改。如果必须改旧测试，先停下来在 `blockers.md` 里说明 | 保证 A/B 对比的基线可信 |
| G2 | 真值文件只放 `eval/golden/`。不得放进 `runtime/`、`src/`、`scripts/`、`experiments/`，不得出现在任何发给模型的字符串里 | 防泄漏，见 `06` 第 7 节 |
| G3 | 发给模型的字符串（工具描述、工具返回、提示词、联系表标签）**只含数值**，不含“教堂”“球”“盒子”“chapel”“cathedral”“golden”等词 | 仓库原则：不给模型语义标签 |
| G4 | `taskSuccess` 字段保持 `unverified`。新的验证结果放在单独的 `structureCheck` 字段，并注明它只代表几何一致性 | 不把结构检查冒充任务成功 |
| G5 | 证据链不绕过：每个新工具写事件（`s.event(...)`）；图像带 sha256；派生体数据登记 `parentVolumeHash`；渲染一律走 `validate_scene` 与原生回读校验 | 现有的可复现性机制 |
| G6 | 不修改 `VirtualSampling/` 下的 C++ 与着色器 | 避免要求重新编译；可选补丁放在第 12 节 |
| G7 | 依赖只用 numpy 和 Pillow。**不要新增 scipy 或其他包作为硬依赖**。可以在代码里 `try: import scipy` 做可选加速，但每个加速路径必须有与纯 numpy 结果一致的测试 | `Start-VolumeTUI.ps1` 只校验 numpy 与 PIL，并声明不修改日常依赖 |
| G8 | 不引入任何网络访问；渲染服务只监听回环地址；不使用云模型 | 仓库的启动检查与配置校验强制如此 |
| G9 | 沿用现有代码风格：封闭式参数校验（`validate_arguments`），错误用 `ValueError`，新文件尽量不超过约 400 行 | 可维护性 |
| G10 | 同一任务连续两次验收失败就停止，把现象、已尝试的做法、日志路径写进 `docs/improvement/blockers.md`，等人来决定 | 避免在错误方向上空转 |

## 2. 仓库地图与改动落点

| 文件 | 现状（已核对） | 本计划的改动 |
|---|---|---|
| `src/renderer.py` | `initial_scene`、`validate_scene`、`render`、`_render_unlocked`；GSC 脚本写死 `setmethod 0`、`settfparams false`；`validate_scene` 要求场景键集合与 `initial_scene` 完全一致，`tf.type` 必须是 `smoothstep`，`method` 必须是 0 | 放宽 `tf.type` 与 `method` 的取值；抽出原生执行与回读检查的公共部分；新增 `render_views`（单次启动多张截图） |
| `src/volume.py` | `prepare()` 写 `render.raw`（uint8，形状 `(z,y,x)`）与 `render.dat`；`inspect_slice()` | 不改。新增读取渲染体数据的辅助函数放在 `structure.py` |
| `src/external_tools.py` | `TOOLS`（4 个工具）、`validate_arguments`、`ExternalTools.open/snapshot/call`；`open()` 的参数架构是封闭的；预算在 `call()` 里按次累加 | 引入 `tools_for(profile)`；新工具；`open()` 接受可选 `toolProfile`；预算与事件 |
| `src/app.py` | `GET /api/tools` 返回 `TOOLS`；`POST /api/tools/open`、`/api/tools/call`；POST 请求体上限 32768 字节 | `GET /api/tools` 解析 `?profile=` |
| `scripts/renderer_mcp.py` | `tools/list` 调 `GET /api/tools`；工具结果里若有 `imageUrl` 就附带 PNG | 增加 `--profile`；其余不变 |
| `scripts/run_local_agent.py` | `contextSize` 仅作为 Codex 的 `model_context_window`；代理只改写 `temperature` 与 `think`；`mcp_args` 里没有 profile；任务提示词是英文 | profile 透传、工具超时与压缩的覆盖项、v2 提示词、Ollama 上下文预检 |
| `scripts/run_lifecycle.py` | `TASK_FIELDS` 决定重复运行检测；配置校验不拒绝未知键，`renderBudget` 限 1 到 30 | 校验 `toolProfile`；把它加进 `TASK_FIELDS` |
| `scripts/volume_tui/config.py`、`event_text.py` | 默认设置、工具名到文案的映射 | 新键；新工具文案 |
| `tests/` | `unittest` 风格；`test_external_tools.py` 导入 `TOOLS` 与 `validate_arguments` | 新增测试文件，不改旧用例 |

## 3. 环境与基线（阶段 0 的任务）

在仓库根目录（下文记作 `<ROOT>`）执行。Windows 下用 PowerShell；Python 用仓库启动脚本选定的那个解释器（`$env:GAV_PYTHON` 或 `Start-VolumeTUI.ps1` 打印出来的路径），**不要**为本计划创建新的虚拟环境或安装包。

```powershell
# T0.1 基线测试：记录通过与失败的数量，原样保存输出
& $env:GAV_PYTHON -B -m unittest discover -s tests -v 2>&1 | Tee-Object runtime\baseline-tests.txt
Push-Location scripts
& $env:GAV_PYTHON -B -m unittest discover -s volume_context/tests -t . -v 2>&1 | Tee-Object ..\runtime\baseline-volume-context.txt
Pop-Location
```

如果上面的发现命令因为导入路径失败，换成按文件运行（例如 `python -B tests/test_external_tools.py`），并把实际可用的命令写进 `docs/improvement/baseline.md`，后续所有验收都用那条命令。基线里已经失败的测试，记录下来，**不要顺手修**。

还要记录：`git rev-parse HEAD`（如果不是 git 仓库，就写“非 git”，后续用变更日志代替提交）、Python 版本、numpy 与 Pillow 版本、`Raycaster.exe` 是否存在（`VirtualSampling/Raycaster/VS/x64/Release/Raycaster.exe`，不存在则按 `scripts/build-raycaster.ps1` 构建）、体数据路径、Ollama 里是否有 `qwen3.6:35b`。同时从现有运行目录的 `run.json` 抄下 `temperature`、`think`、`contextSize` 的实际值，作为 E0 预设的依据（已核对：`temperature=0.0`、`think=true`、`contextSize=16384`）。

## 4. Spike：先验证假设，再写代码

每个 Spike 都是一个小实验，产出写进 `docs/improvement/spike-results.md`（表格：编号、结论、证据文件、对计划的影响），原始产物放 `runtime/spikes/`。**S2、S3、S5、S6 不通过时，按“失败后备”改计划，不要硬做。**

| 编号 | 要验证的假设 | 做法 | 通过判据 | 失败后备 |
|---|---|---|---|---|
| S1 | 一个 GSC 脚本可以做多次相机设置并多次截图，也可以 `resize` 到别的分辨率 | 手写脚本：`loadvolume`，然后两次 `resetrotation` 加 `addrotation*` 加 `screenshot view-k.png`；再试 `resize 768 768` | 一次启动生成两张不同的 PNG，进程退出码 0，标记 `COMPLETE` 出现，`native-state.json` 里的维度、crop 回读一致 | 每个视角单独启动一次（成本约 3 秒乘视角数），`render_views` 内部循环调用现有的渲染路径 |
| S2 | `setmethod 7`（线性采样加光照）能在这台机器的构建上运行并产生有明暗的画面 | 复制一个场景，手写脚本把 `setmethod 0` 换成 `setmethod 7`（`validate_scene` 目前不允许，所以手写脚本绕过它） | 无 `scripting error` 等关键字；与 `setmethod 0` 的图像平均绝对差明显大于噪声；人眼确认有明暗 | 试 `setmethod 79`；仍不行则不提供光照，联系表改用无光照渲染，并在文档里注明 |
| S3 | `settfparams true <center> <sigma>` 能运行，且 `dumpstate` 的 `tfStart`、`tfWidth` 回读等于输入 | 手写脚本，`settfparams true 0.28 0.02`；有体数据时人眼确认画面里出现中等密度结构 | 回读一致；画面与 smoothstep 明显不同 | 高斯窗改用“派生体数据加 smoothstep”实现，即第 5 节阶段 2 的 `isolate_nodes` 路径，此时 v2-lite 里不暴露 `tfType` |
| S4 | 纯 numpy 的分析流程在 246×246×221 上能在约 30 秒内完成，内存低于约 2 GB | 原型：三点二项式平滑、中心差分梯度、梯度门控直方图、对一个掩膜做纯 numpy 的三维连通域标记；用 `time` 与内存监控 | 总耗时不超过约 30 秒；连通域结果与朴素洪水填充一致（小样本） | 允许可选的 scipy 加速（G7 的条件）；或把分析放到 `open()` 阶段预计算 |
| S5 | Codex 能为 MCP 工具调用设置更长的超时，使 30 到 60 秒级的工具不被中止 | 查 Codex 的配置文档或 `codex --help`，核实 `mcp_servers.<id>.tool_timeout_sec`【待核实】；写一个会睡 90 秒的测试工具验证 | 设置后 90 秒的调用成功返回 | 把 `render_candidates` 拆成“预计算并缓存、按节点分批返回”，每次调用不超过默认超时 |
| S6 | 能控制 Codex 的自动压缩；能确认 Ollama 实际上下文长度 | 核实 `model_auto_compact_token_limit`【待核实】的语义；运行中用 `ollama ps` 看实际上下文长度，并与 `contextSize` 比较；确认 `/api/ps` 是否返回上下文长度字段 | 能把压缩次数降到 0 或明显减少；能读到实际上下文长度 | 若实际上下文小于 `contextSize`：通过服务端环境变量或派生 Modelfile 提高；仍压缩则缩小每轮输入（联系表只含 4 个节点、缩略图 160 像素） |
| S7 | 记录 scipy 在服务所用的 Python 里是否可导入 | `python -c "import scipy"` | 仅记录，不影响计划 | 不适用 |
| S8 | 一个换了目录与哈希的派生体数据能被 `loadvolume` 加载，且原生回读的维度等于派生体数据的维度 | 把 `render.raw` 的一个子立方体写成新的 `render.raw` 与 `render.dat`（改 `Resolution`），用手写脚本渲染 | 回读维度等于子立方体维度；画面里的物体铺满画面（等价于原生放大） | 若原生渲染对非立方体或小体数据有问题，改为补零到立方体 |

## 5. 阶段计划

估计工时是**人工参考**，仅用于排序。每个任务都给出：改动文件、规格、测试、验收命令。验收命令里的 `<PY>` 指基线里记录的那个 Python 解释器。

### 阶段 0：基线与 Spike（约 0.5 到 1 天）

- **T0.1** 基线测试：第 3 节的命令；产出 `docs/improvement/baseline.md`。
- **T0.2** 执行 S1 到 S8；产出 `docs/improvement/spike-results.md` 与 `runtime/spikes/`。
- **T0.3** 建分支（如果是 git），建 `docs/improvement/progress.md`，把本计划的任务清单复制进去当检查表。
- **验收**：三个文件存在；S2、S3、S5、S6 有明确结论，并在 `spike-results.md` 里写出“对计划的影响”。

### 阶段 1：离线分析库（约 2 到 3 天，不改渲染器、不改 agent）

**T1.1　`src/phantom.py`：合成嵌套体生成器**

```python
def make_phantom(seed: int, size=(96, 96, 96), noise=0.02) -> tuple[np.ndarray, dict]:
    """返回 (vol_u8 形状 (z,y,x), truth)。
    truth = {'labels': uint16 数组, 'objects': [{'label': int, 'role': 'box'|'globe'|'bulk'|'family',
             'mean': float, 'bbox': [x0,x1,y0,y1,z0,z1]}]}"""

def write_tuwien_dat(path, vol_u8) -> None:
    """TU Wien 二进制 DAT：3 个小端 uint16 维度 (x,y,z) 加 uint16 体素（取 v*257）。"""
```

内容规格：薄壁空心长方体（"box"，1 到 2 体素厚，值约 0.20）；薄球壳（"globe"，1 到 2 体素厚，值约 0.70，一端留开口）；球内 2 个实心物体（"bulk"，值取自 0.15 到 0.50，两者间距至少 0.12，且与 box、globe 的值至少相差 0.12，默认种子得到约 0.28 与 0.50）；体外 6 到 12 个小椭球（"family"，半径 2 到 3，值约 0.92）；高斯噪声；空气区域里稀疏的亮点；一次 `[1,2,1]/4` 的部分体积模糊。真值标签在加噪声和模糊**之前**生成。相同种子必须得到逐字节相同的结果。

- 测试 `tests/test_phantom.py`：确定性；形状与 dtype；各角色的对象数量；`write_tuwien_dat` 写出的文件能被 `volume.prepare` 读入，且 `render.raw` 与原数组的差不超过 1。
- 验收：`<PY> -B -m unittest tests.test_phantom -v` 通过。

**T1.2　`src/structure.py`：确定性结构分析（纯 numpy）**

公共接口：

```python
@dataclass(frozen=True)
class AnalysisParams:   # 全部进入 analysis_hash
    bg_margin: float = 4/255          # 背景峰之上的余量
    grad_gate: float = 0.5            # 低梯度阈值 = max(1.5/255, grad_gate * 前景梯度中位数)
    peak_min_share: float = 0.003     # 峰所含的体素占门控体素的最小比例
    peak_prominence: float = 0.25     # 峰相对邻近谷的最小突出度
    band_halfwidth: tuple = (2, 12)   # 峰带半宽的取值范围（灰度级）
    open_iters: int = 1               # 掩膜的开运算次数
    min_voxels_bulk: int = 60
    min_voxels_tail: int = 30
    shell_max_thickness: int = 2
    shell_max_fill: float = 0.05      # 体素数 / 包围盒体积
    shell_min_side: int = 20          # 包围盒最长边
    family_min_members: int = 3
    family_mean_tol: float = 0.05
    family_size_ratio: float = 3.0
    contain_margin: int = 1
    max_nodes: int = 16

def read_render_volume(volume: dict) -> np.ndarray      # (z,y,x) uint8；校验 rendererPayloadHash
def histogram_facts(vol_u8, bins: int = 64) -> dict      # 给 inspect_scene 用，见第 6 节
def analyze(vol_u8, volume_hash: str, params=AnalysisParams()) -> "Analysis"
def save_analysis(a, directory) -> None                  # analysis.json 加 labels.npy
def load_analysis(directory) -> "Analysis"
def node_mask(a, node_ids, window=None, dilate=0) -> np.ndarray  # bool，(z,y,x)，window 为原体数据坐标 [x0,x1,y0,y1,z0,z1]
def probe(vol_u8, x, y, z) -> dict
```

算法（建议实现顺序；每一步都先在合成体上调通）：

1. `v = vol/255`，一次三点二项式平滑（每个轴 `[1,2,1]/4`，边缘复制）。
2. 对平滑后的体数据做中心差分，得到梯度幅值 `g`。
3. 背景阈值：直方图低端的众数加 `bg_margin`。前景为 `v` 大于它。
4. **梯度门控直方图**：只统计前景里 `g` 小于 `grad_gate` 阈值的体素；在 256 档上做 5 点箱式平滑，取局部极大值，保留占比不低于 `peak_min_share` 且突出度不低于 `peak_prominence` 的峰；峰带 = 中心加减半宽（半宽取半高宽，夹在 `band_halfwidth` 之内）。
5. **体材料带**：对每个峰带，`inband` 为 `v` 落在带内；保留 `inband` 且（`inband` 与低梯度体素的 1 次膨胀相交）的体素；做 `open_iters` 次六邻域开运算，去掉紧贴薄壳的薄层（部分体积的过渡层）。
6. **高值尾部**：`v` 大于最高体材料带上界再加 1 个灰度级的前景体素（薄壳与小金属件通常没有自己的峰，所以单独取尾部）。
7. 对每个掩膜做 26 邻域连通域标记（纯 numpy，见下），体材料带用 `min_voxels_bulk`，尾部用 `min_voxels_tail` 过滤。
8. 每个连通域计算：体素数、包围盒（半开区间，原体数据坐标）、重心、`v` 的平均值与 p05、p95、`thickness`（在包围盒内做六邻域腐蚀直到为空的次数，封顶 12）、`fill`（体素数除以包围盒体积）。
9. 种类：`thickness ≤ shell_max_thickness` 且 `fill < shell_max_fill` 且包围盒最长边 `≥ shell_min_side` 记为 `shell`；其余记为 `bulk`。
10. **家族**：同一带内、非 `shell`、平均值差不超过 `family_mean_tol`、体素数比不超过 `family_size_ratio` 的连通域，成员数不少于 `family_min_members` 时合并为一个 `family` 节点（`members` 记成员数，包围盒取并集）。
11. **包含关系**：节点 B 的父节点是包围盒包含 B 的包围盒（四周余量至少 `contain_margin`）的、体积最小的 `shell` 节点；没有则为根。
12. 编号：按体素数从大到小命名 `N1`、`N2`……（最多 `max_nodes` 个）。`analysis_hash` 是对 `volume_hash`、全部参数、排序后的（包围盒、体素数、平均值取 3 位小数）的 sha256 前 16 个十六进制字符。
13. `labels` 是 `uint16`，值为节点编号的整数部分（`N3` 对应 3），家族的所有成员共用同一个值；0 表示不属于任何节点。

**纯 numpy 连通域标记**（`label3d`）：取掩膜中为真的体素的扁平下标，建立扁平下标到紧凑序号的查找表；对 13 个“半邻域”偏移，找出两端都为真的体素对（注意边界）；用向量化的最小标签传播加指针跳跃（`np.minimum.at` 加 `lab = lab[lab]`）迭代到收敛。与朴素洪水填充在小随机掩膜上逐一比较（测试）。

- 测试 `tests/test_structure.py`（全部在合成体上）：
  1. `label3d` 与洪水填充一致（多个随机掩膜与两种邻域）。
  2. 对默认种子的合成体，每个真值对象都能匹配到恰好一个节点（匹配标准：与真值掩膜的交并比不低于 0.6，平均值误差不超过 0.04）；family 被合并成一个节点；包含关系树正确（bulk 的父节点是 globe，globe 与 family 的父节点是 box）。
  3. 噪声水平 0.0、0.02、0.04 下仍然成立（最高噪声下允许少数小件丢失，但 bulk 与壳不得丢）。
  4. 相同输入两次得到相同的 `analysis_hash`；改任何参数得到不同的 `analysis_hash`。
  5. `histogram_facts` 的计数总和等于体素总数；`probe` 的邻域在边界处被正确裁剪。
- 验收：`<PY> -B -m unittest tests.test_structure -v` 通过。

**T1.3　`scripts/structure_cli.py`：人工检查与真值标注的辅助工具**

```
<PY> scripts/structure_cli.py --volume <runtime/<session>/volume/volume.json> --out <目录>
```

输出 `analysis.json`、`nodes.csv`，以及一张 `montage.png`：每个节点一行，三张正交方向的最大强度投影（只含该节点的体素，裁到包围盒，放大到统一尺寸），旁边是数值标签（编号、平均值、体素数、包围盒）。不依赖原生渲染器，因此可以在 Spike 之前就给人工标注真值用。

**T1.4　真实体数据验收测试 `tests/test_structure_real.py`**

只有设置了环境变量 `GAV_TEST_VOLUME`（指向 TU Wien 的 `.dat`）才运行，否则 `skipTest`。流程：`prepare` 到临时目录，`analyze`，断言：

1. 存在一个平均值在 0.25 到 0.31 之间的节点，其 y 范围与 [108, 145]、z 范围与 [119, 149] 的重叠都至少占较小者的一半。
2. 存在一个 `family` 节点，`members ≥ 8` 且平均值不低于 0.85。
3. 存在一个 `shell` 节点，平均值在 0.55 到 0.80 之间，且其包围盒包含第 1 条的节点。
4. 分析总耗时记录下来，硬上限 60 秒。

这些期望来自我对 8 张切片的测量（见 `02`）。**如果真实数据与它们不符，不要放宽断言来让测试通过**：把实测的节点表写进 `blockers.md`，等人判断是算法问题还是我的测量有误。

### 阶段 2：服务端工具（约 3 天）

**T2.1　`src/renderer.py`：放宽场景取值，新增 `render_views`**

1. 常量 `TF_TYPES = ('smoothstep', 'gaussian')`，`ALLOWED_METHODS = (0, 7)`（7 仅在 S2 通过时保留）。
2. `validate_scene`：把“`tf.type` 必须是 smoothstep”改成“在 `TF_TYPES` 里”；从逐项比较的键列表里去掉 `method`，另加 `scene['method'] in ALLOWED_METHODS`。**场景的键集合保持不变**，这样旧的重放脚本（`scripts/verify_local_agent.py`）仍然能用。
3. 抽出 `build_script(scene, volume, directory, token)`，返回脚本行列表：`setmethod {scene['method']}`；`settfparams {true 或 false} {start} {width}`。**旧场景生成的脚本必须与改动前逐字节相同**，用一个固定期望字符串的测试锁定。
4. 抽出 `_run_native(script_lines, directory, stop)`（进程启动、60 秒超时、停止信号、`process.json`、失败关键字检查、标记检查）与 `_check_state(state, scene, volume)`（回读校验）。`render()` 与 `_render_unlocked()` 的签名不变。
5. 新增：

```python
def render_views(volume, directory, views, size=512, tf=('smoothstep', 0.02, 0.04), method=0, stop=None) -> dict:
    """一次原生启动，按 views（每项是 [rx,ry,rz] 度）依次 resetrotation、addrotation*、screenshot view-<k>.png。
    返回 {'images': [路径], 'imageSha256': [..], 'nativeState': 最后一次 dumpstate, 'views': views, 'scriptPath': ..}
    持有 NATIVE_LOCK；做与 render() 相同的失败关键字与回读校验（旋转只校验最后一个视角）。"""
```

若 S1 不通过，`render_views` 内部改为对每个视角各启动一次，对外接口不变。

- 测试：`validate_scene` 接受与拒绝的用例；旧场景的脚本逐字节回归；高斯加方法 7 场景的脚本文本快照（不运行原生程序）。
- 验收：`tests/test_renderer_v2.py` 通过，且基线里原本通过的测试仍通过。

**T2.2　`src/derived.py`：派生体数据（体素手术）**

```python
def build_derived(volume0, analysis, keep_node_ids, directory, dilate=1, fit_margin=None) -> dict:
    """volume0 是原体数据记录。
    mask = 保留节点的并集，做 dilate 次六邻域膨胀（保留部分体积边缘）。
    fit_margin 为 None 时窗口是全体积；否则窗口取保留节点并集包围盒的外接立方体加 fit_margin，并裁到体数据边界内。
    把窗口内的体素写成新的 render.raw（掩膜外置 0）和 render.dat（Resolution 改成窗口尺寸，SliceThickness 与 Format 沿用）。
    返回新的体数据记录（见第 7 节）。"""
```

- `volumeHash = sha256(parentVolumeHash + '|' + 派生参数的规范 JSON + '|' + 渲染载荷哈希)`。
- 派生记录不复制 `statistics`；`canonicalPath` 置 `None`。
- 窗口裁剪等价于原生放大：渲染器会把整个体数据缩放到画面里，所以小窗口里的物体铺满画面（S8 验证）。
- 测试 `tests/test_derived.py`：在合成体上，窗口外与掩膜外为 0；窗口内保留节点的体素与原值一致；`Resolution` 与窗口尺寸一致；哈希确定；记录可 `json.dumps`；同一参数两次调用结果相同。

**T2.3　`src/candidates.py`：候选联系表**

```python
def compose_contact_sheet(rows, thumb=192, path=None) -> dict:
    """rows: [{'nodeId':..., 'label': 数值文字, 'images': [PIL.Image,...]}]
    把每行的视角缩略图横排，行首画标签（只含数值），返回 {'image': 路径, 'imageSha256':..., 'layout': {...}}"""
```

默认视角 `[[0,0,0],[90,0,0],[0,90,0],[30,-35,0]]`。每个节点：`build_derived(keep=[该节点], fit_margin=4)` 加 `render_views`（方法 7 若可用，否则 0；smoothstep 起点 0.02、宽度 0.04）。测试只覆盖拼图（用假图像，不运行原生程序）。

**T2.4　`src/verify.py`：帧验证**

```python
COVERAGE_MIN = 0.8; LEAK_MAX = 0.05; MIN_FRAME_PIXELS = 500; PIXEL_THRESHOLD = 8; DILATE = 2   # 评测与服务共用这一份常量

def compare_frames(frame_rgb, ref_rgb, pixel_threshold=PIXEL_THRESHOLD, dilate=DILATE) -> dict
    # 返回 {'coverage', 'leak', 'framePixels', 'refPixels', 'passed'}，纯 numpy
def verify_frame(session, frame_revision, target_node_ids) -> dict
    # 找到该帧的场景与它所用的体数据（按 volumeHash 在 session.volumes 里查），
    # 用 build_derived 在同一窗口里生成“只含目标节点”的体数据（不膨胀、不加 fit），
    # 用同一个 scene、方法 0、smoothstep(0.02, 0.04) 渲染参考图，再 compare_frames
```

- 测试：`compare_frames` 在构造的图像上覆盖满足与不满足两种情况；空帧；参考为空时报错而不是通过。
- 集成测试（原生程序存在时运行，否则跳过）：合成体，用目标节点渲染的帧通过；只渲染 globe 节点的帧失败。

**T2.5　`src/external_tools.py`、`src/app.py`、`scripts/renderer_mcp.py`：v2 工具集**

1. `TOOLS` 保持原样；新增 `TOOLS_V2LITE`、`TOOLS_V2` 与 `tools_for(profile)`（三个取值 `legacy`、`v2lite`、`v2`，未知值抛 `ValueError`）。`v2lite` 有 5 个工具，`v2` 有 9 个工具，清单见 DoD 第 2 条，契约见第 6 节。
2. `open()` 的参数架构增加可选 `toolProfile`（枚举 `legacy`、`v2lite`、`v2`，默认 `legacy`），存为 `s.profile`。`v2lite` 与 `v2` 都记录 `s.volume0`；只有 `v2` 才做结构分析。`v2` 时在初始渲染之后：设 `s.volume0 = s.volume`、`s.volumes = {hash: 记录}`，调用 `structure.analyze` 并保存到 `s.directory/'analysis'`，写事件 `analysis`（耗时、节点数、`analysisHash`）。分析失败不终止会话：记录错误，`analyze_structure` 返回该错误。
3. `call()` 按 `s.profile` 选取 `tools_for(...)` 的架构做校验；不在该 profile 工具清单里的工具名一律拒绝。
4. 预算：`IMAGE_TOOLS = {'render_scene', 'inspect_slice', 'render_candidates', 'isolate_nodes'}` 计入预算；`analyze_structure`、`probe_voxel`、`verify_frame` 不计入。沿用现有“校验通过后、执行之前累加”的顺序。`probe_voxel` 每个会话最多 300 次。
5. `render_scene`（v2）：`parameters` 增加 `tfType`（枚举）与 `lighting`（布尔，映射到 `method` 为 7 或 0）。返回里增加 `changedFields`（本次相对上一场景改了哪些字段）与 `warnings`（例如 `crop` 不是全体积时给出 “crop restricts the volume to ...”）。现有返回已经包含完整的 `scene`，问题在于模型没读到，所以增加这两个醒目字段，而不是重复整个场景。
6. `inspect_slice`、`probe_voxel` 永远读**原体数据**（`s.volume0`），坐标是原体数据的存储坐标；节点包围盒也是原体数据坐标。派生体数据里的 `crop` 是相对该派生体数据的坐标，工具描述里要写明。
7. 事件：新增 `analysis`、`probe`、`candidates`、`derive`、`verify`，格式与现有 `render`、`slice` 事件对齐（包含结果与图像哈希）。
8. `app.py`：`GET /api/tools` 解析查询串里的 `profile`（缺省 `legacy`）。`scripts/renderer_mcp.py`：增加 `--profile`，`tools/list` 请求带 `?profile=`；`tools/call` 不变。
9. 帧目录：派生体数据上渲染的帧，另写 `frame-<rev>/volume-binding.json`（`volumeHash`、`parentVolumeHash`、`window`、`keptNodeIds`、`analysisHash`）；`finish_task` 把它一并复制到 `final/`。离线评分要靠它还原参考渲染。
10. 测试 `tests/test_tools_v2.py`：`tools_for('legacy')` 与现有 `TOOLS` 完全相同；v2 的 9 个工具名；封闭架构拒绝未知字段；预算计入与不计入；未知节点编号、过期的 `expectedRevision`、未先分析就 `isolate_nodes`、`probe_voxel` 越界、`verify_frame` 找不到帧，这些错误都不改变场景也不消耗预算；`legacy` 会话调用 v2 工具名被拒绝，`v2lite` 会话调用结构类工具被拒绝。

**T2.6　`finish_task`（v2）**

增加可选参数 `targetNodeIds`（仅 `v2`）。提供时自动运行 `verify_frame`，把结果放进返回值与事件的 `structureCheck`；不提供则 `structureCheck` 为 `null`。**验证不通过时不阻止结束**（实验要测量这种情况）。`taskSuccess` 仍为 `unverified`。

**T2.7　端到端集成测试（原生程序存在时运行）**

用合成体写成 TU Wien DAT，启动服务，按 v2 流程依次调用全部工具，断言：`render_candidates` 返回的联系表哈希可复核；`isolate_nodes` 后的帧只含目标；`verify_frame` 通过；`finish_task` 返回 `structureCheck.passed == true`。

### 阶段 3：agent 侧接线（约 1 天）

**T3.1　配置键与预设（第 10 节）**：`toolProfile`（`legacy`、`v2lite`、`v2`）、`mcpToolTimeoutSec`、`autoCompactTokenLimit`（后两个按 S5、S6 的结论决定是否保留）。`scripts/run_lifecycle.py` 校验 `toolProfile` 取值并把它加进 `TASK_FIELDS`；`scripts/volume_tui/config.py` 的 `DEFAULTS` 与 `_coerce_settings` 增加 `toolProfile`（TUI 里不加控件，在 `runtime/tui-settings.json` 里手改即可）。

**T3.2　`scripts/run_local_agent.py`**：`mcp_args` 追加 `--profile`；按 S5、S6 的结论追加 Codex 覆盖项；v2 时使用第 9 节的任务提示词（legacy 的提示词原样保留）；**上下文预检**：运行前调用 Ollama 的 `/api/ps`（若返回上下文长度字段），实际值小于 `contextSize` 时打印醒目警告并写进 `run.json`，不终止运行。

**T3.3　`scripts/volume_tui/event_text.py`**：为 5 个新工具补全各映射表里的条目（参考现有 `inspect_slice` 的写法）；更新 `tests/test_event_text.py` 与相关断言。

**验收**：`<PY> scripts/run_local_agent.py --config experiments/e3-v2full.json` 能走到“服务连通、工具列表含 9 个工具”这一步（`e2-v2lite.json` 应是 5 个）（可以在模型调用前手动中止）；全部测试通过。

### 阶段 4：评测骨架（约 1 到 2 天）

**T4.1　`eval/score_run.py`、`eval/summarize.py`、`eval/calibrate.py`**：按 `06` 实现。评分器从 `src/verify.py` 导入阈值常量，**不得**另存一份。`score_run.py` 的输入是运行目录，输出 `score.json`。`summarize.py` 汇总多个 `score.json`，输出 Markdown 表和 CSV，包含 Wilson 区间与 Fisher 精确检验（纯 Python 实现）。
- 测试 `tests/test_eval.py`：Wilson 区间与 Fisher 检验对照已知值；评分器在合成体上，对目标帧判成功、对 globe 帧判失败。

**T4.2　真值标注（人工步骤，agent 不得代做）**：用 `scripts/structure_cli.py` 的输出让人在 `montage.png` 上标出目标节点，agent 只负责把人给出的签名写成 `eval/golden/christmas-present.json`（模板见 `02` 第 6.2 节）。真值未就绪时，agent 在 `progress.md` 里标“等待人工标注”，并继续其他任务。

**T4.3　校准与基线评分**：真值就绪后运行 `eval/calibrate.py`（`06` 第 4 节的全部对照），产出 `eval/results/calibration.md`；对 run a、run b 的最终帧评分，预期两者都判失败。

### 阶段 5：实验（人来跑，agent 准备材料）

agent 的工作：确认 5 个预设可启动；写 `docs/improvement/run-experiments.md`，列出具体命令、交错运行顺序（E0、E1、E2、E3 重复 10 轮）、每次运行后调用 `score_run.py` 的命令、汇总命令。真实的模型运行与结果解读由人完成。

## 6. 工具契约

约定：输入架构沿用 `external_tools.py` 里的 `obj(properties, required)` 封闭式写法（未知字段一律拒绝）；所有数值必须是有限数（MCP 代理用 `allow_nan=False` 序列化，出现 NaN 或无穷会让整次调用失败）；错误用 `ValueError`，经 HTTP 400 的 `{"error": ...}` 返回，MCP 侧表现为 `isError: true`。**所有面向模型的文本只含数值（G3）。** 下面的 JSON 示例中的数值仅为说明字段形状，不是实测输出。

| 工具 | `legacy` | `v2lite` | `v2` | 计入预算 | 返回图像 |
|---|---|---|---|---|---|
| `inspect_scene` | 原样 | 加 `volumeFacts` | 加 `volumeFacts` | 否 | 是（当前帧） |
| `render_scene` | 原样 | 扩展 | 扩展 | 是 | 是 |
| `inspect_slice` | 原样 | 原样 | 原样 | 是 | 是 |
| `probe_voxel` | 无 | 有 | 有 | 否 | 否 |
| `analyze_structure` | 无 | 无 | 有 | 否 | 否 |
| `render_candidates` | 无 | 无 | 有 | 是 | 是（联系表） |
| `isolate_nodes` | 无 | 无 | 有 | 是 | 是（新帧） |
| `verify_frame` | 无 | 无 | 有 | 否 | 否 |
| `finish_task` | 原样 | 原样 | 加 `targetNodeIds` | 否 | 否 |

### 6.1 `inspect_scene`（v2lite、v2 的新增返回字段）

输入仍是空对象。在现有返回之外增加：

```json
"volumeFacts": {
  "toolProfile": "v2",
  "histogram": {"domain": "renderer-uint8/255", "bins": 64, "counts": [ ...64 个整数... ], "backgroundShare": 0.93},
  "bands": [{"id": "B1", "lo": 0.24, "hi": 0.32, "center": 0.28, "share": 0.31}],
  "window": [0, 246, 0, 246, 0, 221],
  "derived": false,
  "analysisAvailable": true
}
```

`bands` 是梯度门控直方图的峰带（`structure.histogram_facts`），按占比从大到小最多 8 个。`window` 是当前渲染所用体数据在原体数据里的窗口；`derived` 表示当前是不是派生体数据。

### 6.2 `render_scene`（v2lite、v2）

在现有 `parameters` 之外增加：`tfType`（`smoothstep` 或 `gaussian`）、`lighting`（布尔）。语义：`gaussian` 时 `tfStart` 是中心、`tfWidth` 是标准差（均在 [0,1] 的 renderer-uint8 域）；`lighting` 为真时用渲染模式 7，否则 0。`crop` 是**当前渲染体数据**的半开体素坐标（派生体数据时是相对其窗口的坐标）。返回在现有字段之外增加：

```json
"changedFields": ["tf.start", "camera.rotationXYZ"],
"warnings": ["crop restricts the volume to x[80,166) y[80,166) z[80,141)"]
```

### 6.3 `probe_voxel`（v2lite、v2）

输入：`{x, y, z}`（整数，原体数据存储坐标，必须在范围内）。输出：

```json
{"x": 124, "y": 126, "z": 133, "value": 0.282, "valueDomain": "renderer-uint8/255",
 "neighborhood": {"min": 0.27, "max": 0.29, "mean": 0.281}, "volumeHash": "..."}
```

邻域是 3×3×3，在边界处裁剪。每个会话最多 300 次。

### 6.4 `analyze_structure`（v2）

输入：`{maxNodes?: 整数 1 到 16，默认 8}`。输出：

```json
{"analysisHash": "9f2c...", "toolProfile": "v2",
 "nodes": [
  {"id": "N1", "kind": "shell", "meanIntensity": 0.21, "voxels": 41210, "bbox": [0,246,0,246,10,200], "centroid": [123,123,105],
   "thickness": 2, "fill": 0.002, "members": 1, "parent": null, "children": ["N2", "N5"]},
  {"id": "N3", "kind": "bulk",  "meanIntensity": 0.28, "voxels": 18432, "bbox": ["x0","x1",108,145,119,149], "parent": "N2", "children": []}
 ],
 "note": "node ids are numeric handles; kind is geometric (shell, bulk, family); no semantic labels"}
```

示例里的节点编号与数值只说明形状。节点字段定义见第 7 节。

### 6.5 `render_candidates`（v2，计入预算 1 次）

输入：`{nodeIds?: 字符串数组（默认按编号取前 maxNodes 个）, views?: 整数 1 到 4（默认 4）, thumb?: 整数 128 到 256（默认 192）}`。输出是 `snapshot` 风格的字典，另含：

```json
{"imageKind": "contact-sheet", "imageUrl": "/artifact/...", "imageSha256": "...",
 "layout": {"rows": [{"nodeId": "N3", "meanIntensity": 0.28, "voxels": 18432}],
            "cols": ["rot [0,0,0]", "rot [90,0,0]", "rot [0,90,0]", "rot [30,-35,0]"]},
 "remainingObservations": 24}
```

联系表里每行行首只画数值文字（编号、平均密度、体素数）。图像必须带 sha256，MCP 代理会校验。该调用耗时较长（每个节点一次原生启动加一次派生体数据写盘），见 S5。

### 6.6 `isolate_nodes`（v2，计入预算 1 次）

输入：`{keepNodeIds: 字符串数组（1 到 8 个）, dilate?: 整数 0 到 3（默认 1）, fit?: 布尔（默认 true）}`。做的事：`build_derived(..., fit_margin=4 若 fit 否则 None)` → 登记到 `s.volumes` → `s.volume` 换成派生体数据 → 新场景（沿用上一场景的相机与传输函数的类型，`crop` 重置为派生体数据的全体积，`tf` 设为 smoothstep(0.02, 0.04)，`method` 用 7 若可用，`revision` 加 1）→ 渲染一帧 → 返回 `snapshot`，另含 `derivedVolumeHash`、`window`、`keptNodeIds`、`voxelsKept`。节点编号不存在、`dilate` 越界、未分析就调用，都抛 `ValueError`。

### 6.7 `verify_frame`（v2，不计入预算）

输入：`{frameRevision: 整数, targetNodeIds: 字符串数组（1 到 8 个）}`。输出：

```json
{"frameRevision": 7, "targetNodeIds": ["N3"],
 "structureCheck": {"coverage": 0.97, "leak": 0.01, "framePixels": 4210, "refPixels": 4100, "passed": true,
                    "thresholds": {"coverageMin": 0.8, "leakMax": 0.05, "minFramePixels": 500, "pixelThreshold": 8, "dilate": 2},
                    "scope": "geometric consistency only; not task success"}}
```

### 6.8 `finish_task`（v2 的新增）

可选 `targetNodeIds`（字符串数组，最多 8 个）。提供时自动运行 `verify_frame`，把 `structureCheck` 放进返回值与 `finish` 事件的 `structureCheck` 字段；`taskSuccess` 仍为 `unverified`。工具描述里写明：该字段只代表几何一致性。

## 7. 数据结构

**节点（`analysis.json` 的 `nodes[]`）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | 字符串 | `N1`、`N2`……按体素数降序 |
| `kind` | 字符串 | `shell`、`bulk`、`family`，纯几何分类 |
| `meanIntensity`、`intensityP05`、`intensityP95` | 数值 | renderer-uint8/255 域，保留 3 位小数 |
| `voxels` | 整数 | 体素数（家族是成员总和） |
| `bbox` | 6 个整数 | `[x0,x1,y0,y1,z0,z1]`，原体数据存储坐标，半开区间 |
| `centroid` | 3 个数值 | 原体数据坐标 |
| `thickness`、`fill`、`members` | 整数、数值、整数 | 见阶段 1 的算法 |
| `parent`、`children` | 字符串或空、字符串数组 | 包含关系 |

**体数据记录的扩展字段**（原记录是 `prepare()` 的返回值；派生记录在其上修改）：

| 字段 | 原体数据 | 派生体数据 |
|---|---|---|
| `dimensions` | 原维度 | 窗口尺寸 `[wx,wy,wz]` |
| `window` | `[0,X,0,Y,0,Z]`（建议补上，便于统一处理） | 窗口在**原体数据**坐标里的 `[x0,x1,y0,y1,z0,z1]` |
| `parentVolumeHash` | 缺省 | 原体数据的 `volumeHash`（多层派生时始终指向根） |
| `derivation` | 缺省 | `{"op":"isolate_nodes","keptNodeIds":[..],"dilate":1,"fitMargin":4,"analysisHash":"..."}` |
| `canonicalPath`、`statistics` | 原值 | `null`（不复制） |
| `rendererPath`、`rendererPayloadHash`、`volumeHash` | 原值 | 新值，`volumeHash` 的算法见 T2.2 |
| `geometry`、`orientation`、`axisOrder`、`rendererMapping`、`spacingDefaulted` | 原值 | 原样复制（`snapshot()` 要读它们） |

**帧绑定文件 `frame-<rev>/volume-binding.json`**（仅派生体数据上的帧）：`{"volumeHash","parentVolumeHash","window","keptNodeIds","analysisHash"}`。

**会话目录新增内容**

```
runtime/<session>/
  analysis/            analysis.json  labels.npy
  derived-<n>/         render.raw  render.dat  volume.json
  candidates-<n>/      node-<id>/(派生体数据与 view-<k>.png)  contact-sheet.png  layout.json
  frame-<rev>/         ...现有文件  volume-binding.json（派生帧才有）
  verify-<n>/          reference.png  result.json
  final/               ...现有文件  volume-binding.json（若有）
```

大数组（直方图计数、标签体）只放文件，**不要**塞进体数据记录或事件里：`Session.save()` 每次事件都会把体数据记录序列化进 `session.json`。

## 8. 测试矩阵

| 测试文件 | 覆盖 | 需要原生程序 | 需要环境变量 |
|---|---|---|---|
| `tests/test_phantom.py` | 合成体确定性与读入 | 否 | 否 |
| `tests/test_structure.py` | 连通域、节点恢复、包含关系、哈希、直方图、探针 | 否 | 否 |
| `tests/test_structure_real.py` | 真实体数据上的节点表 | 否 | `GAV_TEST_VOLUME` |
| `tests/test_renderer_v2.py` | 场景校验、脚本逐字节回归、脚本快照 | 否 | 否 |
| `tests/test_derived.py` | 派生体数据 | 否 | 否 |
| `tests/test_candidates.py` | 联系表拼图 | 否 | 否 |
| `tests/test_verify.py` | 帧比较；集成部分 | 集成部分需要 | 否 |
| `tests/test_tools_v2.py` | 工具清单、参数校验、预算、错误不改状态 | 否 | 否 |
| `tests/test_e2e_v2.py` | 端到端 | 是 | 否 |
| `tests/test_eval.py` | Wilson、Fisher、评分器 | 否（集成部分需要） | 否 |
| 现有测试 | 基线原本通过的必须仍然通过 | 视情况 | 否 |

需要原生程序的测试在 `Raycaster.exe` 不存在时 `skipTest`，并在 `progress.md` 里注明“被跳过”，**不要把跳过说成通过**。

## 9. v2 任务提示词（草稿）

仅在 `toolProfile` 为 `v2` 时使用；`v2lite` 用 legacy 提示词加一句“`render_scene` 现在支持 `tfType` 与 `lighting`，`probe_voxel` 可读单个体素值”；`legacy` 保持原文不变。提示词保持英文，与现有提示词一致。

```
You operate the real Native Raycaster using only the renderer MCP tools.
Workflow:
1. inspect_scene: read the actual PNG, the numeric facts and volumeFacts (histogram and density bands).
2. analyze_structure: read the numeric node table (ids, mean intensity, voxel counts, bounding boxes, containment).
   Node ids are numeric handles; they carry no meaning.
3. render_candidates: inspect the contact sheet and decide which node or nodes best match the user task.
   Judge by the shapes in the images, not by density alone.
4. isolate_nodes with your chosen ids and inspect the returned frame. Then use render_scene to change only the
   camera and lighting for one or two better views.
5. verify_frame with your chosen ids. If it fails, or the image does not match the task, choose another candidate
   and repeat from step 4.
6. finish_task with frameRevision, targetNodeIds and an honest summary.
Rules: never guess densities; use probe_voxel or the node table. Omitted render_scene parameters stay unchanged;
read changedFields and warnings in every result. Respect the observation budget; analyze_structure, probe_voxel
and verify_frame are free. structureCheck reports geometric consistency only, not task success; state your
uncertainty honestly. Do not use shell or filesystem tools.
User task: <task>
```

## 10. 实验预设

放在 `experiments/`，格式沿用 `local-vlm.example.json`。五个预设共用的字段：`harness` 为 `codex`，`endpoint` 与 `rendererService` 沿用示例，`volumePath` 由人填，`renderBudget` 为 30，`task` 为下面这句原文，`resultDirectory` 各用各的子目录。

```
Show me the chaple in the ball-like present inside the box - make the box invisible and the ball invisible, only make the chapel visible
```

| 预设文件 | `toolProfile` | `contextSize` | `think` | `temperature` | 其他 |
|---|---|---|---|---|---|
| `e0-legacy.json` | `legacy` | 16384（与现有运行一致） | true | 0 | 基线，不改任何东西；已核对当前运行就是 `think=true` |
| `e1-memory-sampling.json` | `legacy` | 按 S6 的结论提高（建议 32768 起，视显存） | true | 0.6 | 按 S6 放宽或关闭自动压缩；确认 Ollama 实际上下文 |
| `e2-v2lite.json` | `v2lite` | 同 e1 | true | 0.6 | 同 e1 |
| `e3-v2full.json` | `v2` | 同 e1 | true | 0.6 | 同 e1；`mcpToolTimeoutSec` 按 S5 |
| `e4-qwen38-27b.json` | `v2` | 同 e3 | true | 0.6 | `model` 换成 Qwen3.8-27B 在 Ollama 里的实际标签（先 `ollama list` 确认，不要猜） |

## 11. 汇报与阻塞

- **每个任务完成后**更新 `docs/improvement/progress.md`：任务编号、状态（完成、被跳过、被阻塞）、改了哪些文件、跑了哪条测试命令及结果摘要、与本计划的偏差及理由。
- **每个阶段完成后**跑一遍全部测试，把通过、失败、跳过的数量贴进 `progress.md`。
- 提交信息格式：`feat(v2): T2.2 derived volumes`。不是 git 仓库时，在 `progress.md` 里维护变更日志。
- **阻塞**：按 G10 写 `docs/improvement/blockers.md`，每条包含任务编号、现象、复现命令、已尝试的做法、日志路径、你认为可能的原因（标明是推测）。
- **不得**：声称完成了未经验证的东西；放宽测试断言来让测试通过；把被跳过的测试说成通过；替人做真值标注。

## 12. 待办（本轮不要实现）

1. **可选的 C++ 补丁**：让 `dumpstate` 额外输出传输函数类型（`tfType`），这样高斯窗的类型也能被回读校验。需要用 `scripts/build-raycaster.ps1` 重新构建，所以放在本轮之外。
2. **路径 D**：先在已有的 8 张切片上离线试 SAM 2 的点提示与传播，再决定是否集成。
3. **路径 E**：批量联系表加进化搜索，前提是评测协议已冻结。
4. **路径 G**：把 `phantom.py` 加 `verify.compare_frames` 包成可批量跑的环境。
5. **路径 H、F**：见 `05`。
6. **泛化**：328 与 492 档分辨率、别的数据集；TUI 里加 `toolProfile` 的控件。

## 13. 附录：常见坑

1. 原生脚本里的路径不能含空白或引号（`renderer.py` 的 `token()` 会抛错）；派生体数据目录名只用 ASCII。
2. 原生渲染器同一时间只允许一个渲染（`NATIVE_LOCK`），第二个会报 “Native renderer is busy”；`render_candidates` 和 `verify_frame` 内部串行调用。
3. `render()` 创建目录时 `exist_ok=False`；每次都要用新目录。
4. POST 请求体上限 32768 字节；工具参数很小，不会触及，但测试里别塞大数组。
5. 切片图像的行列、`crop` 的半开区间、节点包围盒都是**存储坐标**，行是 z 还是 y 要看轴（`02` 第 3 节）。
6. 传输函数的横轴是 renderer-uint8 除以 255，不是物理密度；阈值一律在这个域里。
7. 测试里 `app.RUNTIME` 会被替换成临时目录；新代码必须用 `app.RUNTIME` 或 `session.directory`，不要写死路径。
8. MCP 代理的 stdio 是 UTF-8，但面向模型的文本一律用 ASCII 英文，避免 Windows 代码页问题。
9. 事件和返回值里不得出现 NaN 或无穷。
10. 不要在 `Session` 类里加新字段到 `save()` 的清单里（会改变 `session.json` 的结构，影响 TUI 与旧工具）；新增的会话状态用动态属性，像现有的 `s.external` 那样。
