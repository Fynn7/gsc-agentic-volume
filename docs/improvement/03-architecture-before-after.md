# 03 改动前后流程对比

> 颗粒度是“agent 调用某个工具”。两张图的骨架相同，只有不同的地方才高亮。图中的“新工具”是**提案**，仓库里目前都不存在；详细接口见 `07-worker-implementation-plan.md`。

## 1. 怎么读这两张图

- 灰色：新旧流程里相同的步骤，例如用户 prompt。
- 红色（旧图）：新流程里被替换或删除的步骤。
- 绿色（新图）：新增的步骤。
- 黄色：步骤还在，但内容变了，例如 `inspect_scene` 多返回了直方图，`finish_task` 多了验证指标。
- 旧图按 run b 的轨迹画，只有 `inspect_slice` 那一步来自 run a，已在节点里标注。
- 如果你的环境不渲染 Mermaid，可以把代码块贴到 <https://mermaid.live>。

## 2. 改动前（当前）

```mermaid
flowchart TB
  classDef same fill:#f4f4f8,stroke:#777,color:#111
  classDef gone fill:#ffe3e3,stroke:#d63031,stroke-width:2px,color:#111
  classDef chg fill:#fff3bf,stroke:#f08c00,stroke-width:2px,color:#111

  P(["用户 prompt<br/>Show me the chaple in the ball-like present inside the box<br/>make the box invisible and the ball invisible, only make the chapel visible"]):::same

  subgraph S1["① 观察场景"]
    A1["agent 调用 inspect_scene<br/>返回 frame-0：tfStart 0.1 / tfWidth 0.12<br/>整只礼盒可见，只有数值参数，没有密度信息"]:::chg
  end

  subgraph S2["② 理解结构"]
    G1(["模型凭空推断：教堂密度比盒子和球更高<br/>没有直方图，没有探针，纯猜"]):::gone
  end

  subgraph S3["③ 搜索目标"]
    A2["agent 调用 render_scene<br/>tfStart 0.5 / tfWidth 0.15<br/>画面：同心环 + 5 个亮斑<br/>实际是玻璃球壳和金属件"]:::gone
    G2(["模型误认：同心环和亮斑就是教堂"]):::gone
    A3["agent 反复调用 render_scene<br/>tfStart 0.55 到 0.69，一次只改一个参数<br/>反馈只有画面亮不亮"]:::gone
    K["上下文自动压缩<br/>run a 压缩 6 次，run b 压缩 2 次<br/>图片丢失，摘要写成教堂密度大于 0.7"]:::gone
    A4["agent 调用 inspect_slice(axis=x, index=122)  仅 run a<br/>看到球内有建筑，但切片像素无法换算成 crop<br/>观察预算 30 次用尽"]:::gone
    A5["agent 调用 render_scene<br/>crop 收紧 + tfStart 0.6 同时改动<br/>画面近乎全黑，模型归咎于 crop"]:::gone
    A6["agent 调用 render_scene<br/>tfStart 0.69 / tfWidth 0.08 → frame 18<br/>只剩 5 个金属件，判为 柱子和拱门"]:::gone
  end

  subgraph S4["④ 验证"]
    V0["没有验证步骤<br/>唯一判据是亮点数和模型自评"]:::gone
  end

  subgraph S5["⑤ 收尾"]
    F1["agent 调用 finish_task(frameRevision=18)<br/>summary：教堂已隔离<br/>task check: unverified"]:::chg
    O(["最终 image.png<br/>5 个金属件，没有教堂"]):::chg
  end

  P --> A1 --> G1 --> A2 --> G2 --> A3
  A3 -->|"重复 10 次以上"| A3
  A3 --> K --> A4 --> A5 --> A6 --> V0 --> F1 --> O

  subgraph L["图例"]
    L1["灰：新旧流程相同"]:::same
    L2["红：新流程中被替换或删除"]:::gone
    L3["黄：步骤还在，但内容变了"]:::chg
  end
```

## 3. 改动后（提案）

```mermaid
flowchart TB
  classDef same fill:#f4f4f8,stroke:#777,color:#111
  classDef new fill:#e3fbe3,stroke:#2d9a3a,stroke-width:2px,color:#111
  classDef chg fill:#fff3bf,stroke:#f08c00,stroke-width:2px,color:#111

  P(["用户 prompt<br/>Show me the chaple in the ball-like present inside the box<br/>make the box invisible and the ball invisible, only make the chapel visible"]):::same

  subgraph S1["① 观察场景"]
    A1["agent 调用 inspect_scene<br/>除 frame-0 外，还返回直方图峰值和材料带<br/>0.16–0.24 / 0.28 / 0.5 / 0.6–0.78 / 大于 0.9"]:::chg
  end

  subgraph S2["② 理解结构"]
    B1["agent 调用 analyze_structure  程序执行，不经模型<br/>峰值 → 材料带 → 3D 连通域 → 包含关系树"]:::new
    B2["返回节点表<br/>N1 礼盒壁 0.16–0.24    N2 玻璃球壳 0.6–0.78<br/>N3 块状物 约 0.28    N4 小造型 约 0.5<br/>N5 金属件 x12 大于 0.9<br/>树：N1 包含 N2 包含 N3 和 N4"]:::new
    NOTE["结构化笔记：节点表 + 已选 / 已拒节点<br/>关闭自动压缩，只保留最近 2 张图"]:::new
  end

  subgraph S3["③ 选择目标"]
    B3["agent 调用 render_candidates<br/>每个节点单独渲染：派生体数据 + 光照 + 低阈值 smoothstep + 裁到包围盒<br/>4 个视角拼成一张联系表，只占 1 次观察"]:::new
    B4(["模型做选择题：哪个节点像小教堂模型<br/>选 N3，N4 备选"]):::chg
  end

  subgraph S4["④ 渲染"]
    B5["agent 调用 isolate_nodes(keep=N3)<br/>其余节点体素置零，生成 derived volume<br/>盒子和球随之消失"]:::new
    B6["agent 调用 render_scene<br/>传输函数固定为低阈值 smoothstep，只动相机，3 到 4 个视角"]:::chg
  end

  subgraph S5["⑤ 验证"]
    B7["agent 调用 verify_frame<br/>程序比较候选帧与“只含 N3”的参考渲染<br/>输出 coverage、leak、framePixels<br/>阈值待标定"]:::new
    D1{"通过？"}:::new
  end

  subgraph S6["⑥ 收尾"]
    F1["agent 调用 finish_task(frameRevision, summary + targetNodeIds)<br/>taskSuccess: unverified（不变）<br/>structureCheck: coverage / leak / passed<br/>教堂这一语义仍由模型选择"]:::chg
    O(["最终 image.png<br/>只有教堂模型"]):::chg
  end

  P --> A1 --> B1 --> B2 --> B3 --> B4 --> B5 --> B6 --> B7 --> D1
  B2 -.-> NOTE -.-> B4
  D1 -->|"是"| F1 --> O
  D1 -->|"否：换备选节点 N4"| B4

  subgraph L["图例"]
    L1["灰：新旧流程相同"]:::same
    L2["绿：新增步骤"]:::new
    L3["黄：步骤还在，但内容变了"]:::chg
  end
```

## 4. 差异对照表

| 阶段 | 旧流程 | 新流程 | 解决的根因 |
|---|---|---|---|
| ① 观察场景 | `inspect_scene` 只返回 frame-0 和相机、传输函数、crop 数值 | `inspect_scene` 额外返回直方图峰值和材料带 | R2 信息不对称 |
| ② 理解结构 | 模型凭空推断“教堂比盒子和球密” | `analyze_structure` 由程序建出节点表和包含关系树，不经模型 | R2、R6 |
| ③ 搜索目标 | 反复 `render_scene` 改 `tfStart`，每次只看亮不亮；上下文压缩丢图；切片看到了建筑却无法换算坐标 | `render_candidates` 一次出一张联系表，模型做选择题；节点表写进结构化笔记，不依赖摘要 | R1、R4、R5、R6、R7 |
| ④ 渲染 | 用单调高通传输函数加 crop 在原体数据上硬凑 | `isolate_nodes` 把其他节点体素置零生成派生体数据，`render_scene` 只动相机 | R1、R5 |
| ⑤ 验证 | 没有，只有亮点数和模型自评 | `verify_frame` 用程序比较“只含目标节点”的参考渲染，给出覆盖率、泄漏率和帧像素数；不通过就换备选节点 | R3 |
| ⑥ 收尾 | `finish_task`，`taskSuccess` 为 `unverified` | `finish_task` 增加 `structureCheck` 指标；`taskSuccess` 仍为 `unverified`，语义仍标注为由模型选择 | R3 |

## 5. 不变的部分

这些保持原样，是为了让新旧流程可以在同一套证据体系下做 A/B 对比：

1. Codex CLI 作为 harness，经 MCP 调用渲染服务；所有图像带 sha256，原生状态回读校验照常进行。
2. 观察预算的概念不变，但只有“返回图像”的调用计入预算：`render_scene`、`inspect_slice`、`render_candidates`、`isolate_nodes`。`analyze_structure`、`probe_voxel`、`verify_frame` 不计入预算。
3. `inspect_slice`、`finish_task` 的旧参数保持兼容；新工具只出现在 `toolProfile: v2` 里，旧 profile 的行为与现在逐字节一致。
4. “不给模型语义标签”的原则：节点只带数值（编号、平均密度、体素数、包围盒），不带“教堂”“球”之类的名字。

## 6. 设计取舍说明

- 为什么不继续改传输函数：传输函数是一维的，而“选哪个物体”是空间问题。教堂在密度轴上夹在礼盒壁和球壳之间，球壳又在空间上包着它；用一维窗口加 crop 去逼近，要么泄漏要么截断。体素手术直接在空间上表达“只保留这个物体”。
- 为什么 `analyze_structure` 不经模型：这一步是确定性的几何计算，模型参与只会引入幻觉和延迟；同时它的输出可以被缓存、复现、做单元测试。
- 为什么验证只做到“结构层”：`verify_frame` 能证明“画面里只剩被选中的节点”，证明不了“那个节点就是教堂”。后一半仍由模型的选择和人工真值承担，所以 `finish_task` 的语义字段继续标 `unverified`。
