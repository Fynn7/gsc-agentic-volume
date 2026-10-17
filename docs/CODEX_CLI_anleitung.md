# Codex CLI + Qwen 35B + MCP + Native Raycaster 操作手册

**版本：2026-10-18（运行目录生命周期 + 可重复启动修复）**

## 当前状态

| 项目 | 状态 |
|---|---|
| Codex CLI + Qwen 35B | 已有真实成功记录 |
| MCP + Native Raycaster | 已有真实成功记录 |
| 真实 PNG 反馈给模型 | 已有验证记录 |
| CLI 实验与日常 Codex 配置隔离 | 脚本已实现 |
| Codex CLI 实时控制台轨迹 | Worker 已实现；历史记录回放通过，待你新一轮现场验收 |
| CLI 运行目录生命周期 | 已修复：每轮自动独立目录，同一配置可安全重复执行 |
| Codex Desktop 实验 GUI | 暂时不用 |
| 学校 H200 连接 | 尚未验证 |

本手册使用现有非交互式 `codex exec`。每运行一次脚本，就自动完成一项自然语言任务并保存执行记录。

**注意：CLI 仍没有完整的图形化 Chat 界面，但现在可以实时查看文字执行轨迹。** 新增 `scripts/agent_console.py`：显示模型调用、MCP Tool、参数、Scene Revision、截图路径、错误和汇总；可选终端缩略图或打开真实 PNG。Worker 已用历史记录回放测试，新增实时功能仍需你亲自运行一次验收。

---

## 1. 检查本机准备情况

先结束当前 Codex Worker 的任务，打开一个独立的 Windows PowerShell。

运行：

```powershell
cd D:\fynns_local_ws\gsc-agentic-volume

Test-Path .\runtime\gate-volume\render.dat

Test-Path .\VirtualSampling\Raycaster\VS\x64\Release\Raycaster.exe

Get-Command codex
Get-Command ollama
```

检查结果：

- 两个 `Test-Path` 都应返回 `True`。
- `codex` 和 `ollama` 都应能找到安装位置。

首次使用默认测试 Volume，不必修改 Renderer。

**完成条件：** 文件和工具全部存在。

若缺少默认 Volume 或 Native EXE，停止并记录缺失文件，不要通过其他数据集或 Mock 绕过检查。

## 2. 创建第一次 CLI 实验

在同一个 PowerShell 中，整段复制执行：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmssfff"

$configFile = "runtime\cli-task-a-$stamp.json"

$config = Get-Content `
    .\experiments\local-vlm.example.json `
    -Raw | ConvertFrom-Json

$config.harness = "codex"
$config.model = "qwen3.6:35b"
$config.endpoint = "http://127.0.0.1:11434"
$config.rendererService = "http://127.0.0.1:8765"

$config.volumePath = (
    Resolve-Path .\runtime\gate-volume\render.dat
).Path

$config.resultDirectory = "runtime/local-vlm/cli-task-a"

$config.task = "Set the camera rotationXYZ to [25,-35,10] degrees. Preserve transfer function, translation, crop and all unrelated parameters. Inspect the real initial and resulting images. Save the final native PNG and report the actual settings."

$config | ConvertTo-Json -Depth 20 |
    Set-Content -Encoding UTF8 $configFile

Write-Host "Config: $configFile"
Write-Host "Results base: $($config.resultDirectory)"
```

`resultDirectory` 是**实验基准目录**，不是单次运行目录：启动器每次运行都会在其中自动新建一个唯一的 `run-<时间戳>-<随机>` 子目录。因此这份配置可以**原样反复使用**，不需要每次改路径，也不会覆盖旧结果。只生成配置文件，不启动模型。

**完成条件：** PowerShell 显示新的配置文件和结果基准目录路径。

## 3. 启动真实 CLI Agent 实验

继续在同一个 PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\scripts\start-local-vlm.ps1 `
    -Config $configFile `
    -StartOllama
```

脚本会执行：

1. 检查 Ollama；若未运行，尝试启动本地服务。
2. 检查指定模型和 Renderer 服务。
3. 为**本轮**新建唯一运行目录，并写入本轮生效配置与来源。
4. 建立独立的 Codex CLI 环境。
5. 通过 MCP 控制 Native Raycaster。
6. 将真实截图传给 Qwen 35B。
7. 保存模型请求、工具调用、截图、场景和运行日志。

这里不需要另外运行 `codex` 命令，也不需要打开 Codex Desktop。

启动时会先打印一行 `run_started`，其中的 `runId` 和 `directory` 就是**本轮真实运行目录**；结尾的 `SUMMARY` 会再次显示对应的 `manifest` 路径。终端显示的 Run ID、PNG 路径与最终结果都只属于本轮运行。

### 3.0 运行目录生命周期（可重复启动）

- 每轮运行自动获得独立目录，命名形如 `run-<日期>-<时间>-<随机>`，位于配置的 `resultDirectory`（实验基准目录）之下。
- 目录通过原子操作预留；即使两轮在同一秒启动、或目录名撞名，也会自动改用新目录。
- **不会**删除、清空、覆盖或重命名已有实验目录；旧证据原样保留。
- 如果某份旧配置的 `resultDirectory` 仍指向一个已存在的运行目录，新一轮会放在它的**同级**目录，绝不会写入旧运行目录。
- 同一份配置可以**原样连续运行多次**，每次都得到新目录，无需手动删除或改名。
- 若同一逻辑任务已经有一个启动器在运行，新的启动会被**明确拒绝**（不会重复发起模型请求，也不会结束任何 Codex/Ollama/Renderer/未知进程）。
- 重复执行永远表示**开始新一轮新实验**，不会“恢复”旧实验。当前没有安全恢复功能，因此不会假装恢复。

### 3.1 运行中应该看到什么？

同一个终端应持续出现按时间排列的事件（下例只是格式示意，**不是本次实验的实际输出**）：

```text
[+00:03.2] MODEL   turn 1 started
[+00:03.2] INFER   call #1 request sent, waiting for the model
[+00:34.5] INFER   call #1 POST /v1/responses -> 200 in 31.35s
[+00:34.6] TOOL    -> render_scene {"expectedRevision":0,"parameters":{"rotationXYZ":[25,-35,10]}}
[+00:37.1] IMAGE   native frame rev=1 nonblack=69670
[+00:37.1]         D:\...\runtime\<session-id>\frame-1\image.png
[+00:37.1] TOOL    <- render_scene 2.50s
[+00:37.5] FINISH  selected revision 1 verification=unverified
[+00:37.5] FINAL   D:\...\runtime\<session-id>\final\image.png
```

事件速读：`MODEL` 是 Agent 回合，`INFER` 是推理请求及状态，`TOOL ->` / `<-` 是调用及返回，`TOOL !!` 是工具失败，`IMAGE` 是真实渲染帧，`FINISH` / `FINAL` 是选择及保存结果。结束时的 `SUMMARY` 展示图片、工具、推理次数与耗时，不再把整个 Manifest 打到终端；完整 Manifest 仍保存在文件里。

### 3.2 可选：在运行时查看图片

如需在兼容的交互式终端显示缩略图，并用 Windows 图片查看器打开真实图片，可在同一命令末尾加入这两个开关：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\scripts\start-local-vlm.ps1 `
    -Config $configFile `
    -StartOllama `
    -ShowImages -OpenImages
```

这里的两个命令只是同一次运行的两种写法（带不带开关），不必为同一轮运行执行两次。现在同一份配置**可以安全地反复执行**：每次都会新建独立目录，`-ShowImages` / `-OpenImages` 只影响终端显示，不影响运行生命周期。

`-ShowImages` / `-OpenImages` 仅在检测到真实交互式终端且 PNG 存在时才起作用；某些 PowerShell 终端不支持内嵌图像，届时仍会显示 `IMAGE` 路径。大量图片窗口可能打断观察，首次测试建议先只用默认实时文字模式。

**完成条件：** 运行中可看到新事件持续出现；运行结束，本轮运行目录下出现 `run.json`、`manifest.json`、`session.json` 和工具执行证据。

若失败，保留日志，不要删除本轮运行目录。启动失败会以简洁的一行诊断结束（不是一大段 Python Traceback），退出码见第 9 节。

## 4. 验证执行结果

先读取刚才那轮**实际运行目录**（启动器会在基准目录写入 `LATEST.txt` 指向最近一轮）：

```powershell
$run = (Get-Content (Join-Path $config.resultDirectory "LATEST.txt")).Trim()
Write-Host "Run directory: $run"
```

使用仓库已提供的验证脚本：

```powershell
$python = Join-Path $env:USERPROFILE `
    ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

& $python -B .\scripts\verify_local_agent.py $run
```

这个验证器会核对真实 PNG、SceneSpec、Native Readback，以及实际送入模型的图片内容。

查看核心结果：

```powershell
$report = Get-Content `
    (Join-Path $run "verification.json") `
    -Raw | ConvertFrom-Json

$report | Select-Object `
    state, actualFrames, agentRenders, visuallyDeliveredRevisions
```

**验收重点：**

- `actualFrames`：真实生成了多少帧。
- `agentRenders`：Agent 产生了多少次新 Native Render。
- `visuallyDeliveredRevisions`：哪些真实帧出现在模型请求中。
- `state`：Session 最终状态。

Task A 应当保存指定相机旋转后的实际结果。还要确认原本不相关的参数没有改变。

这些技术检查不能证明语义目标或可视化质量一定正确。

## 5. 查看 PNG、工具轨迹及运行数据

### 5.1 回放已经完成的实验（不再调用模型）

沿用第 4 步的 `$python` 和 `$run`。查看之前的 Task A/Task B 步骤：

```powershell
& $python -B .\scripts\agent_console.py $run
```

如终端支持图片显示，可尝试：

```powershell
& $python -B .\scripts\agent_console.py $run --show-images
```

这只是**只读回放**，不会重新运行 Codex、MCP 或 Renderer，也不会改变原始 JSONL、HTTP 和 PNG。要跟随**仍在进行**的实验，可在另一个终端对该运行目录加 `--follow`；已结束的运行不必使用它，若保持等待可按 `Ctrl+C` 停止显示。

```powershell
& $python -B .\scripts\agent_console.py $run --follow
```

### 5.2 打开最终 PNG 和原始证据

查看最终结果：

```powershell
if ($report.final.image) {
    Start-Process -FilePath $report.final.image
}
```

打开完整实验目录：

```powershell
Start-Process (Resolve-Path $run).Path
```

| 文件 | 内容 |
|---|---|
| `run.json` | 本轮 Run ID、模式（new-experiment）、状态、原始配置来源与哈希、运行目录 |
| `config.json` | 本轮**实际生效**的配置（`resultDirectory` 已指向本轮运行目录） |
| `config.source.json` | 配置文件的**原始字节副本**（启动器从不修改原配置） |
| `manifest.json` | Harness、调用命令、版本、耗时、Run ID |
| `harness.jsonl` | Codex 模型回合、回复与 Harness 事件 |
| `runtime/<session-id>/events.jsonl` | Renderer/MCP 工具事件、图像、状态变化与错误 |
| `scripts/agent_console.py` | 只读实时/历史轨迹展示器，不是另一套 Agent |
| `session.json` | 完整 Session 状态与事件 |
| `http/` | 模型真实请求、响应及耗时 |
| `telemetry.jsonl` | CPU、RAM、GPU 采样 |
| `verification.json` | 图像交付与场景验证结果 |
| `failure.json` | 启动或执行失败时的错误（若存在） |

以上文件都位于**本轮运行目录**（即上面 `$run`）内；不同轮次互不覆盖。基准目录 `$config.resultDirectory` 下还会有一个 `LATEST.txt`，仅指向最近一轮运行目录。

实际 Native Renderer 的每帧截图、GSC 和 Scene 文件保存在：

```text
runtime\<session-id>\frame-*/
```

最终结果保存在：

```text
runtime\<session-id>\final\
```

可以通过 `verification.json` 中的 `sessionId` 和 `final.image` 找到相应目录。

**完成条件：** 控制台可回放 `MODEL / INFER / TOOL / IMAGE / FINAL` 等实际发生的事件，能打开真实最终 PNG，并找到对应的工具和场景证据。`nonblack` 只表示像素存在，不证明目标显示成功。

## 6. Task B：进行真正的自主视觉反馈探索

Task A 验收后，再进行 Task B。

在同一个 PowerShell 中运行：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmssfff"

$configFile = "runtime\cli-task-b-$stamp.json"

$config.resultDirectory = "runtime/local-vlm/cli-task-b"

$config.task = "Find a more informative view of this volume. Inspect the initial real PNG. Choose a camera, transfer-function or crop adjustment based on its visible content. After receiving the new native PNG, examine it before deciding a second adjustment. Produce at least two new native renders. Preserve unrelated parameters. Avoid aggressive clipping without evidence. Save the most informative actual frame and state limitations honestly."

$config | ConvertTo-Json -Depth 20 |
    Set-Content -Encoding UTF8 $configFile

powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\scripts\start-local-vlm.ps1 `
    -Config $configFile `
    -StartOllama
```

运行时观察 `TOOL ->`、`IMAGE` 和下一次 `MODEL/INFER` 是否形成真实反馈顺序。结束后重复第 4、5 步核验真实图像交付及最终效果。

**本次重点不是 JSON 或截图是否成功生成，而是：**

- 模型是否看到初始图？
- 第一次操作是否根据初始图选择？
- 第二次操作是否参考了第一张新图？
- 最终截图是否真的比先前更有观察价值？

不要因为模型说成功就认为 Task B 通过。之前 Task B 曾出现 Crop 过度的问题。

## 7. 结束 CLI 实验

CLI 是一次性任务。运行结束后，Codex 子进程、临时推理记录代理会退出并保留实验文件。

**无需执行 `-Restore`。**

注意：启动脚本可能留下它启动的 Ollama 和 Renderer 服务，以便下次复用。它们不是仍在运行的 Agent 任务。

不要使用 Desktop 的 `Start-IsolatedVolumeDesktop.ps1 -Stop` 来清理 CLI 实验，也不要盲目结束所有 `codex.exe`、`python.exe` 或 `ollama.exe` 进程。

日常 Codex Desktop 可以打开，但为了准确测量 35B 的运行性能，应避免同时执行其他高内存模型任务。当前电脑的 8GB 显存和约 32GB 系统内存余量有限。

## 8. 学校 H200：迁移同一 CLI 实验

学校端尚未实测。以下是需要现场完成的流程。

### 8.1 确认学校推理服务

先取得服务器登录权限，检查 H200 实际显存配额、驱动、Ollama 是否可用、是否允许部署模型以及 SSH 转发。

优先保留本机 Windows 上的 Codex CLI、MCP 和 Native Raycaster；只让 H200 负责推理。

### 8.2 建立 SSH 隧道

在本机另开 PowerShell：

```powershell
ssh -o ExitOnForwardFailure=yes `
    -o ServerAliveInterval=30 `
    -N `
    -L 127.0.0.1:11435:127.0.0.1:11434 `
    USER@HOST
```

将 `USER@HOST` 替换为学校实际分配的账户与服务器。

前提是管理员允许 SSH 转发，并且学校推理服务监听服务器本机 `11434` 端口。

保持这个 PowerShell 窗口运行。

另开一个 PowerShell 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:11435/api/version
```

**完成条件：** 能收到远程 Ollama 的版本信息。

### 8.3 创建 H200 实验配置

在仓库目录中执行：

```powershell
cd D:\fynns_local_ws\gsc-agentic-volume

$stamp = Get-Date -Format "yyyyMMdd-HHmmssfff"

$configFile = "runtime\cli-h200-$stamp.json"

$config = Get-Content `
    .\experiments\local-vlm.example.json `
    -Raw | ConvertFrom-Json

$config.endpoint = "http://127.0.0.1:11435"
$config.rendererService = "http://127.0.0.1:8765"

$config.volumePath = (
    Resolve-Path .\runtime\gate-volume\render.dat
).Path

$config.resultDirectory = "runtime/local-vlm/h200"

$config | ConvertTo-Json -Depth 20 |
    Set-Content -Encoding UTF8 $configFile
```

首次联调建议保持 `qwen3.6:35b`、相同任务、相同 Context 和相同数据集。确认服务器实际已经提供这个模型，并且运行 Context 与配置一致。

### 8.4 运行 H200 实验

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\scripts\start-local-vlm.ps1 `
    -Config $configFile
```

**注意：这里不加 `-StartOllama`。** Ollama 已在学校服务器运行，本机只通过 SSH 隧道访问。

先执行简单 Task A 和图像交付检查，再开展复杂任务。

以后如果要换成更强 VLM，修改 `model`、Context、Timeout 等配置，并确认远端模型的 Vision、Tool Calling 和 Responses 接口兼容性。

不同模型的实验必须保留不同记录，不能把更换模型后的结果当作相同条件复验。

## 9. 常见错误及处理

| 错误 | 下一步 |
|---|---|
| Ollama unreachable | 检查本机服务或 SSH 隧道 |
| Python missing NumPy/Pillow | 使用已安装依赖的 Python，通过脚本的 `-Python` 指定 |
| Renderer service identity mismatch | 检查端口 `8765`，不要覆盖其他应用服务 |
| Context size mismatch | 核对实际 Ollama `context_length`，必要时调整推理服务 |
| `resultDirectory` 已存在 | 正常情况。它是实验基准目录，启动器会在其中新建唯一子目录，无需手动处理 |
| 无真实模型截图反馈 | 检查 `verification.json` 的 `visuallyDeliveredRevisions` 和 HTTP 记录；单有 `IMAGE` 行不足以证明模型收到图像 |
| 控制台没有缩略图 | 先确认是否真实交互式终端；直接打开 `IMAGE` 提供的 PNG 路径，不影响 Agent 实验 |
| `--follow` 一直等待 | 确认正在跟随活动中的运行目录；若运行已结束，用不加 `--follow` 的回放命令 |
| Codex 请求云端失败或 401 | 检查是否使用了现有启动器和隔离的本地 Provider |
| QVis LZ4 不支持 | 暂停此数据集测试；这属于导入问题，不能算 VLM 失败 |
| 同一逻辑任务已在运行被拒绝 | 正常防重复保护。等待那一轮结束，或查看报错中给出的 lock 文件路径；不要手动结束未知进程 |
| `STARTUP FAILED (configuration)` | 配置在启动前被拦截，未创建任何目录。按提示修正配置字段后重试 |
| `STARTUP FAILED (reservation)` | 基准目录不可用（例如被同名文件占用）；换一个可写的基准目录 |

### 9.1 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 本轮执行完成（任务是否语义成功仍需按第 4/5 节核验） |
| 1 | 运行期失败（网络/预检/Harness 非零退出）；本轮证据保存在对应运行目录的 `failure.json` / `manifest.json` |
| 2 | 配置无效或目录预留失败；未创建新实验目录 |
| 3 | 检测到同一逻辑任务已在运行，拒绝重复启动；未创建新实验目录 |
| 124 | Harness 超时（该轮运行目录仍保留证据） |

默认只打印简短诊断（不输出 Traceback）。需要完整 Python Traceback 时，可直接运行 `scripts/run_local_agent.py --config <配置文件> --debug`；运行期失败的完整 Traceback 也保存在该轮 `failure.json`。

## 10. 当前实验的完成标准

- [ ] Task A：实时终端能看到 `MODEL / INFER / TOOL / IMAGE / FINISH / SUMMARY` 中实际出现的事件。
- [ ] Task A：真实 Native Render，指定相机参数正确，其他参数保持。
- [ ] 模型实际接收真实 PNG。
- [ ] Task B：两次新的图像驱动操作被记录。
- [ ] 完整 `harness.jsonl`、Renderer `events.jsonl`、HTTP、Scene、PNG 及验证报告存在，控制台展示不修改原始证据。
- [ ] 能独立查看最终图片，诚实记录可视化质量。
- [ ] 日常 Codex 配置未被 CLI 实验覆盖。
- [ ] H200 首次连接后重复 Task A 和图片反馈验收。

**当前下一步：** 已完成过 Task A 的话，直接从第 5.1 节回放原有实验；要验证新的实时显示，直接用**同一份配置**再运行第 3 节即可——启动器会自动创建新的独立运行目录，无需手动新建、删除或改名任何目录。CLI 核心链路已有历史证据；新控制台实时模式及 H200 仍待你现场验收。