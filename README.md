# gsc-agentic-volume

本地体数据 agent：Codex CLI 做 harness，本地 Ollama 模型（如 `qwen3.6:35b`）做推理，经 `scripts/renderer_mcp.py` 暴露的 MCP 工具调用 `src/` 渲染服务，用 `VirtualSampling/Raycaster` 原生渲染体数据。可运行体包括 `foot`、`skull`、`aneurism`、`bonsai`、`c60`、`head512`、`xmas` 与 TU Wien Christmas Present（246×246×221）等；评测任务用 foot / skull / aneurism / Christmas Present。

## 快速开始

```powershell
.\Start-VolumeTUI.ps1
```

单屏 TUI：顶部是任务，其次模型思考、工具调用、渲染帧与结论。只经 Codex CLI 控制，不自带 agent 循环；详见 `scripts/volume_tui/README.md`。

GUI：在 `frontend/` 运行 `npm run dev`，Vite 默认监听 5177，并在同一终端启动 8765 渲染后端；详见 `frontend/README.md`。

## 结构

| 路径 | 说明 |
|---|---|
| `Start-VolumeTUI.ps1` | TUI 入口：校验文件、引导 `runtime/tui-venv`、探活后启动 TUI |
| `src/` | 渲染服务（`app.py`、`external_tools.py`、`renderer.py`、`volume.py`） |
| `scripts/renderer_mcp.py` | MCP 代理：把服务的 4 个工具暴露给 Codex |
| `scripts/run_local_agent.py` | 无 TUI 的批量运行（`experiments/local-vlm.example.json` 为配置模板） |
| `scripts/volume_tui/` | TUI 本体与 wording/转录/证据逻辑 |
| `VirtualSampling/` | 原生 Raycaster（C++，`Raycaster.exe` 需本地构建） |
| `scripts/materialize_dataset.py` | 把内置预览体（`Datasets/*.dat`+lz4）落成可运行体到 `volumes/`；Stage 的 Dataset 只有存在该文件时才能同时驱动 run |
| `frontend/` | React GUI 与 Wasm Live Preview；`npm --prefix frontend run dev` 同时启动 Vite（5177）与渲染后端（8765） |
| `eval/` | 评测任务、脚本与运行数据；用法见 `eval/README.md`，固定项见 `eval/CONFIG.md` |
| `tests/` | unittest；需要原生渲染器的用例在 `Raycaster.exe` 不存在时跳过 |
| `docs/` | 唯一权威文档 `docs/PROJECT.md`（状态、执行路径、评测、工具审计、历史决策）；索引见 `docs/README.md` |

Stage 的 **Dataset** 是预览与运行体的同一个选择：带 `— run + preview` 的条目会同时改下一次运行的 `volumePath`，`— preview only` 的条目只改画面、运行体不变，界面文案会写明当前运行体是哪个文件。

运行产物在 `runtime/`（run 目录、session、证据、`tui-settings.json`），只读复核即可。

## 测试

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B -m compileall -q scripts/volume_tui
```

CI（`.github/workflows/tests.yml`）在 Windows + Python 3.12 上跑同样两条命令。不启动模型、渲染器与网络服务。
