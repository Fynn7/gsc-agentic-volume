# gsc-agentic-volume

本地体数据 agent：Codex CLI 做 harness，本地 Ollama 模型（如 `qwen3.6:35b`）做推理，经 `scripts/renderer_mcp.py` 暴露的 MCP 工具调用 `src/` 渲染服务，用 `VirtualSampling/Raycaster` 原生渲染体数据。当前数据为 TU Wien Christmas Present（246×246×221 一档）。

## 快速开始

```powershell
.\Start-VolumeTUI.ps1
```

单屏 TUI：顶部是任务，其次模型思考、工具调用、渲染帧与结论。只经 Codex CLI 控制，不自带 agent 循环；详见 `scripts/volume_tui/README.md`。

## 结构

| 路径 | 说明 |
|---|---|
| `Start-VolumeTUI.ps1` | 唯一入口：校验文件、引导 `runtime/tui-venv`、探活后启动 TUI |
| `src/` | 渲染服务（`app.py`、`external_tools.py`、`renderer.py`、`volume.py`） |
| `scripts/renderer_mcp.py` | MCP 代理：把服务的 4 个工具暴露给 Codex |
| `scripts/run_local_agent.py` | 无 TUI 的批量运行（`experiments/local-vlm.example.json` 为配置模板） |
| `scripts/volume_tui/` | TUI 本体与 wording/转录/证据逻辑 |
| `VirtualSampling/` | 原生 Raycaster（C++，`Raycaster.exe` 需本地构建） |
| `docs/` | 唯一权威文档 `docs/PROJECT.md`（状态、执行路径、评测、工具审计、历史决策）；索引见 `docs/README.md` |

运行产物在 `runtime/`（run 目录、session、证据、`tui-settings.json`），只读复核即可。

## 测试

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B -m compileall -q scripts/volume_tui
```

CI（`.github/workflows/tests.yml`）在 Windows + Python 3.12 上跑同样两条命令。不启动模型、渲染器与网络服务。
