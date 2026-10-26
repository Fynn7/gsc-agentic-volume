# 10 可解旗舰演示任务菜单（教授范式：找数据集→裁剪隔离→迭代优化→存图→报参）

> 日期：2026-10-26。选择标准（判据）：**单连通主目标 + 未被致密壳包裹 + 密度可分 + AABB 可裁**。
> 教堂反例见 `02-dataset-facts.md §7` 与 `09-live-preview-loop-discipline.md`（嵌套拓扑 + 全带宽连通）。

| # | 任务（教授式 prompt 改写） | 数据集 | 为何可解 | 验证状态 |
|---|---|---|---|---|
| 1 | 足底视角：“找到脚体数据，用裁剪去掉小腿，只留脚，旋转到垂直俯视足底，优化 TF/视角后存 PNG 并报告参数” | `volumes/foot*.dat/.raw`（256³ 真实 CT，已入库可导入） | 单连通骨骼+软组织、无包裹壳、密度分层清晰 | ✅ 已验证导入+默认渲染出脚形（`figures/candidate-foot.png`） |
| 2 | 颅骨侧面像：“找到颅骨数据，裁掉下颌平面以下，转到侧面，优化后存图报参” | `volumes/skull*.dat/.raw`（256³ 已入库可导入） | 单个颅腔壳体、背景干净、AABB 可切下颌 | ✅ 已验证导入+默认渲染出颅骨（`figures/candidate-skull.png`） |
| 3 | 动脉瘤血管段：“加载 Aneurism 内置体，裁掉周围分支聚焦瘤体，转到最佳视角存图报参” | 内置 `Aneurism (256³)`（免导入） | 血管树是唯一致密结构、无包裹 | ✅ 已验证默认渲染出血管树（`figures/candidate-aneurism.png`） |

推荐顺序：1 > 3 > 2。1 与教授演示同构（连“垂直看足底”都一样）答辩时最容易被认可；
3 无需导入链路最稳；2 作为备选。

下一步（待与教授面谈确认后执行）：选定一个任务后，用现有 agent 循环完整跑一遍
（render_scene 迭代 + 截图判定 + 最终 PNG + 参数报告），把轨迹记为 eval 用例；
教堂保持为边界案例，不再投入调参。

## 附：BorgVR 足底协议对照（教授 2026-10-26 会话原文见 `borgvr-fusssohle-protokoll.txt`）

对方 agent 的真实循环（约 10 次截图迭代含一次失败的平移与一次换数据集）：
list_datasets → open_dataset → wait_until_rendered → set_render_mode(isovalue) →
set_iso_value（0.13→0.2→0.23→0.21→0.23）→ set_transform（四元数旋转+缩放+平移）→
set_clipping（归一化 min/max）→ set_lighting → capture_screenshot → close_dataset，
最后存 PNG + 完整设置 JSON。

映射到本仓的等价物：list_datasets→导入/数据集下拉；iso_value→TF start/width；
set_clipping（归一化）→setclipbounds（体素先乘维度）；wait_until_rendered→Run+读回；
capture_screenshot→导出 PNG；settings JSON→GSC 文本本身即设置记录。

三点可直接借鉴：(1) 失败尝试如实记录进日志（对方 7.2 平移出界被保留）；
(2) 数据集不行就换（对方 WholeBody→Melanix），本仓等价动作是换数据集/重导入；
(3) 交付物固定为 PNG+参数（对方存 einstellungen.json；本仓存 .gsc+PNG）。
