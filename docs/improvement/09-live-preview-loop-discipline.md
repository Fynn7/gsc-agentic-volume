# 09 Live Preview 渲染循环纪律：四轮 freeze/抖动/黑屏的根因与铁律

> 日期：2026-10-26　　范围：`frontend/src/preview/LiveStage.tsx`、`wasmHost.ts`、`public/wasm/preview-shell.html`、`VirtualSampling/Raycaster/Raycaster.cpp`
> 语言：中文，代码标识符保持英文。结论标签沿用 00-README：【实测】【推断】。

## 背景（一句话）

GUI 拖滑块 → Wasm 预览、`.gsc` 文本、localStorage 三条链路互相拖拽，
先后出现 UI 卡顿 → preview 冻结 → 调整一阵后黑屏 → 拖条无限抖动。
四轮排查证明：**没有一处是“调参问题”，全是循环与幂等性问题**——
渲染循环里任何一个无守卫的重复工作，在 60Hz 下都会被放大成灾难。

## 故障链（按发生顺序，都是【实测】）

1. **逐 tick 重写 `.gsc` + localStorage**：`syncParamsToScript` + `storeParams`
   写在 `useEffect([params])` 里裸奔，每 tick 正则重写大文本 + 同步落盘。
   修法：`.gsc`/落盘走 500ms trailing debounce（手势结束后写一次）。
2. **Wedged 调度器**：节流器的 unmount 清理只 `clearTimeout` 不把 ref 归零，
   StrictMode 挂载即触发一次清理，之后所有预览推送永久短路——画布停在启动帧，
   无报错、无崩溃。修法：清理必须 `clear + null`。
3. **黑屏**：外部大体数据（present 246×246×221）下持续调整 → GPU TDR 杀 context，
   canvas 永久黑（无恢复路径）。修法：shell 监听 `webglcontextlost`
  （`preventDefault` 允许恢复）+ `restored` 后**整机 reboot**
  （重建 shell + 重载外部体 + 重推快照）。已实测：单纯重推控制量救不回来，
   因为 GL 资源（纹理/FBO）已死，C++ 再渲染直接 abort。
4. **reboot 洗参数**：shell 启动回声（dataset 0 / TF 0.1/0.12）先于首推到达，
   把已存储/已恢复的用户参数覆盖回默认值。修法两件套：
   - boot gate：首推发出前只放行外部槽位发现，其余回声一律丢弃；
   - 先加载外部体再首推（空 MEMFS 上 `setDataset(8)` 会触发 C++ 回退默认）。
5. **无限抖动（本轮主角）**：`Raycaster::setVolume()` 无条件 `updateVolume()`——
   **每次 slider tick 全量重传体积纹理到 GPU**（13MB×60/s≈800MB/s）外加 clip 重置。
   同文件 `setMethod` 早就有变更守卫，同样的 bug 在 `setVolume` 里藏了两年。
   修法：C++ 侧同 index 直接返回（与 `setMethod` 同款写法），前端保持逐 tick 推送。

## 铁律（以后写这类代码先对着检查）

1. **跨语言 setter 必须幂等**：凡是 `setX` 进入 C++/Wasm，先写变更守卫再干活。
   新增 setter 时把 `setMethod` 的 `if (get...() != ...)` 当模板抄。
   反例：`setVolume` 无守卫 `updateVolume()`（全量纹理重传）。
2. **React updater 里零副作用**：`setState(updater)` 里调 `applyControls` 会被
   StrictMode double-fire，生产环境也纯属错误。副作用只准出现在 effect 里。
3. **定时器清理 = clear + ref 归零**，缺一不可；否则调度器静默永久短路，
   且无任何报错（本轮第 2 条就是这么丢了一整天）。
4. **回声通道必须收敛证明**：C++ 回声是文本协议，字符串字段精确比对
   （`transform`/`customTf`）在格式化不一致时会 ping-pong；
   float 一律 epsilon（本仓 1e-6），字符串先确认 C++ 侧格式化定点性
   （本仓 `max_digits10` + classic locale，是定点，可用精确比对）。
5. **启动/重建期的回声一律视为脏数据**：gate + 先加载后首推，缺一不可。
6. **同源 iframe 与父页共享主线程**：连续手势只认最新快照——本仓用 rAF 邮箱
   （一帧一提交，提交值永远不比当前帧旧，thumb 不可能回跳），
   离散点击立即推送；文本与落盘走 settle。禁止 per-tick 重文本/regex/同步 IO。
7. **先证服务的是当前代码再排查**：多 vite 并存 + HMR 断链的 tab 会跑旧 bundle，
   动手前先 `GET /src/...` 对字节/标识符（本轮为此空转一整轮）。

## 验证纪律（本轮沉淀的固定动作）

- 真浏览器 + 真链路（外部体走 stub 文件对话框的真实导入）。
- 计数器：`postMessage` 出/入数；单调性度量：拖拽中 thumb 值出现反向即判抖动；
  松手后静默观察 15s，计数器动一下即判循环。
- 风暴对比必须留数字：本轮 Christmas 512³+2880px 风暴，
  修前 13.9s/61 长任务/248 帧 → 修后 9.7s/1 长任务/535 帧；
  headed 外部体 45 秒人味拖拽：104 采样 0 回跳。
- headless 通过 ≠ headed 通过（GPU/负载差异）；context kill/restore 全链路
  用 `WEBGL_lose_context` 强制走一遍，看画布是真回来还是假恢复。
