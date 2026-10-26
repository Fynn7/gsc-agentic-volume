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

## 后续：脚本执行与回读（2026-10-26 追加）

> 用户手写脚本 `settfparams true 0.28 0.04` + `setclipbounds 0.25 …` 后按 Run，
> 画布全蓝、面板维持旧值、诊断显示 Standby——三处各自独立的原因：

1. **黑屏主因是脚本语义不是引擎 bug**：`setclipbounds` 取**体素坐标**
   （`resetclipbounds` 传的就是 `0,voxelCount…`），`0.25 0.75 …` 是合法但只有
   0.5 体素宽的切盒，渲染出来就是一个看不见的点。同样体数据改用
   `61.5 184.5 61.5 184.5 44.2 176.8` 后教堂结构正常显示。【实测】
   教训：凡是“看起来引擎坏了先查单位约定”，GSC 参考里已补体素单位说明。
2. **面板不同步是父子乒乓**：脚本批量逐帧执行，C++ 回声到达时父级已推送了
   更新的快照，旧回声把新值洗回去来回打架，UI 表现为抖动/面板不跟。
   修法：回声取消器——回声键若匹配最近 8 次推送中的任一快照就地丢弃
   （`wasRecentlySent`，UI 输入永远走另一条路不受影响）。【实测：交替推送消失】
3. **诊断被 settle 回写踩回 Standby**：Run 完成后 500ms 内的回声触发 settle
   重写编辑器 → 校验通过 → 逻辑回落到启动文案。修法：`completedRunRef`
   守住已完成的 Run 结果只进不出。【实测：诊断稳定在 Ran】
4. **生成区不再写 transform**：位姿没有 GUI 旋钮，面板值只是只读回读；
   把它写进执行区会用过期位姿覆盖手写 `resetrotation/addrotation*`
   （之前时好时坏取决于启动回声竞态）。位姿归脚本所有。【实测：旋转脚本稳定生效】

## 后续2：setLevel 旁路上传（2026-10-26 追加）

> setVolume 加守卫后重测：Christmas 512³ + 2880px 风暴依然卡（13.9s/61 长任务）。
> 原因是 `Raycaster::setLevel()` 无条件调 `updateVolume()` 做全量纹理重传，
> 而 shell 每个 `applyControls` 都带 level（恒为 0）——守卫被旁路了，
> 每 tick 照样重传 13MB。修法同 setMethod/setVolume：在 C++ 侧 level 相等直接返回。

- 验证（headed 真机 + 外部体 + 用户参数，45 秒人味拖拽）：
  主线程从 ~1fps 恢复到锁定 60fps（2701 帧/45s，max 17ms，0 长任务），
  104 采样 0 回跳，松手后 15s 静默；风暴测试 13.9s→4.1s，61 长任务→0。
- 教训：**守卫必须盖住所有触发重传的入口**，只修 setVolume 不够；
  凡是“每 tick 都带下来的字段”（dataset/method/level/rate…），
  对应 C++ setter 必须全部是等值 no-op。以后加新字段先查这一条。

## 决策记录：停手 + 双案例结构（2026-10-26）

- **停手手动调参**：六档阈值扫描证明不存在能干净分离教堂的单带宽；继续试是零期望收益。
- **教堂转入“边界/局限性”案例**：已有证据链完整（直方图 + 连通域扫描 + AABB 几何 + 最好结果渲染图 `figures/chapel-best-effort.png`），适合做论文的局限性章节，不适合做成功演示。
- **新增旗舰演示任务（可解）**：判据=单连通主目标 + 未被致密壳包裹 + 密度可分 + AABB 可裁。候选见 `10-solvable-demo-tasks.md`。
- **refuse 策略**：不做硬编码拒绝；做证据驱动的拒绝——先跑直方图+连通域统计等轻量检查，测到重叠/包容超过阈值才输出带数字的局限说明；同时永远先给最好结果（best-effort + 残留量化）。
- **与教授方向对齐**：BorgVR 范式（找数据集→裁剪隔离→迭代优化视角→存图→报参）照搬到本仓库 agent 循环，无需换栈（同为 Jens Der Krüger 引擎家族）。
