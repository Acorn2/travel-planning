# 子 Agent 编排

## 触发条件

默认单 Agent，天数、城市数或工具数量不自动触发。用户明确要求派发，或存在两个可独立交付且减少等待收益足以覆盖重复上下文的研究域时，可并行；默认最多两名子 Agent，不为每个供应商或餐饮阶段新建 Agent。先说明任务、来源与查询预算；每个任务只加载相关 reference。路线尚未确认时不派发深度研究。

## 任务域与字段所有权

| 任务 | 独占产出 | 可引用但不可改写 |
| --- | --- | --- |
| `attractions` | 景点实体、官方入口、适用日期运营、入口/出口、checkpoints、景点费用和预约草案 | 路线日期、人群约束 |
| `route-data` | 城际方案、交通边、快照引用、包车方案、餐饮基准路线和门到门时间 | 景点入口出口、住宿锚点 |
| `stay-food` | 住宿区域、入住夜与预算预留、住宿补丁和餐窗初稿；按需酒店候选 | 每日片区、交通窗口、checkpoint ID |
| `restaurant-research` | discovery 的餐厅、动态快照和候选集；ranking 的最终 `meal_options[]` | 餐窗、锚点和路线评估 |
| `weather-risk` | 天气对象、固定图标、事件与路线影响、天气备选 | 景点、交通和 checkpoint ID |
| `readiness` | 节假日、公告、行李、入境、通信、保险和分阶段复核 | 已选交通与住宿夜 |
| `audit` | 阻断项、警告和已检查事件 ID | 合并后的 `itinerary.json` |

子 Agent 不决定城市顺序，不生成完整 `days[]`，不修改最终 `itinerary.json`。

## 统一结果契约

每个任务从 assignment 指定的领域模板复制结果，保留 `schema_version`、`template_version`、`template_digest`、`input_revision` 和 `task_id`。结果只使用：

- `summary`：不超过 200 字，只写结论和阻塞项，不进入页面。
- `source_snapshot_ids[]`：引用本任务落盘的标准快照。
- `entities`：本次旅行的规范化实体，字段受任务域所有权约束。
- `shared_entities[]`：本次旅行中需要被其他 Agent、排程、审查或页面读取的规范化实体；合并时按 `entity_id` 进入 canonical state，互补字段自动合并，冲突字段保留记录并采用较新的已提交值。
- `event_bindings[]`：带稳定 ID、目标、操作、快照和来源的事件补丁。
- `constraints[]`：目标、规则与严重度明确的硬约束。
- `unresolved[]`：字段、原因、下一动作、复核时间、入口与严重度。
- `source_ids[]`：本任务已归档来源。
- `research_queries[]`：发生检索的任务按[跨渠道搜索设计](search-design.md)交回逐次真实调用，查询 ID 使用 task_id 前缀；无查询时为空。主 Agent 从结果读取并追加到最终 research_contract.queries，与路线前记录一起保留。不能从 summary 或 source_ids 反推查询；当前 merge 不自动合并此字段。

`event_bindings[].operation` 只允许 `merge`、`append_reference` 或 `invalidate`。交通和住宿候选通过 `inventory_refs[]` 绑定 `snapshot_id`、`offer_id` 和 `role`。存在 blocking unresolved 时不能提交 `complete`；有 quote 快照却没有投影候选时提交会被拒绝。

## 分波次调度

1. 主 Agent 创建唯一 workspace，并通过脚本创建带模板和字段所有权的 assignment。
2. 第一波同时启动 `attractions`、`route-data` 和 `stay-food`。`route-data` 先完成城际与已有明确 POI 的路线；`stay-food` 只产住宿锚点和餐窗，不提前选择餐厅。
3. 景点出入口、住宿锚点和餐窗稳定后创建 `restaurant-discovery`。
4. 槽位释放后启动 `weather-risk`；城际方案稳定后启动 `readiness`。
5. discovery 完成后依次创建 `route-data-meals` 和 `restaurant-ranking`。三阶段结果不可覆盖，候选集合必须一致。
6. 第一阶段提交后，主 Agent 生成硬时间候选事件流并预审；冲突立即重排或创建 `gap-<domain>-vN`。
7. 所有任务提交后合并实体与事件补丁，再单独启动 `audit`。审查失败时修补并重新运行。

以上为确需委派时的依赖顺序，不要求每波次创建新 Agent；主 Agent 可执行所有阶段并使用相同 workspace 契约。依赖未完成时不提前创建 assignment；依赖内容进入 `input_revision`，变化后旧结果失效。

## Assignment 必填内容

初始消息和 assignment 包含 workspace 绝对路径、`task_id`、领域与 stage、输入路径、只读依赖路径、允许和禁止写入路径、字段所有权、完成门槛、来源 ID 前缀和 submit 命令。具体创建与提交命令见[行程研究工作区](research-workspace.md)。

## 各任务完成门槛

### `attractions`

- 每个拟采用景点有坐标、入口、出口、实体地址以及分开的官网、预约和公告入口；使用微信公众号预约时另有已核验的公众号名称、菜单路径和可用的官方说明文章入口。
- 有适用日期的开放、停票、停止入场、闭园、临时公告、费用和查询时间。
- 有入口到出口的结构化 checkpoints；预约项目生成可绑定事件的 booking task。
- 社区内容只支持体验、拥挤和避坑，不覆盖官方事实。

### `route-data`

- 按[研究深度](research-depth.md)为开放式航班与铁路选择 time/price/recommendation 比较维度，只补相应排序；指定或用户已选班次保留标识、依据与关键衔接，不重新查全天所有排序。标准快照写入本任务目录。
- 采用候选绑定具体 snapshot 和 offer；每条边有精确起终点、门到门时间、费用、备选和查询时间。
- 覆盖出发地、站点、住宿、相邻景点、返程及每餐统一基准与候选双腿路线。
- 地面交通有明确地址与可执行入口；坐标取得时声明坐标系，未取得时保留有依据的区间估算与缓冲，不强制高德；火车和航班不虚构余票，包车写清资质、合同、行李和全部费用边界。

### `stay-food`

- 默认按[住宿区域规划](lodging-planning.md)输出 kind=area 的区域范围、代表地标、入住日期和夜数、理由与预算预留，复用已读区域资料，不逐家搜酒店。
- 每个住宿夜有区域锚点，交通标明区域估算，涉及退房到入住的大件行李有可执行安排或明确的用户待确认条件。
- 用户明确要求酒店比选时才按日期、人数、房间、床型查询报价，核验酒店身份并绑定 offer；未验证库存只称报价候选。默认区域方案不要求酒店快照或库存。
- 每个完整旅行日有带前后锚点、预算、绕行上限和饮食约束的午餐、晚餐时间窗。

### `restaurant-research`

本任务三阶段只处理 detailed 门店餐窗；普通 mode=area 餐窗由主 Agent 或 stay-food 直接提交，并核查供给与备用依据，不为区域餐窗创建门店路线和排名任务。

- 正常正餐有 2～3 个可定位实体候选；受限场景提供有来源的例外和应急补给。
- 每个候选有地图 POI、动态快照、平台信号、近期社区证据和合法可视证据。
- 每个候选引用同口径的双腿路线评估；距离未知时不能提交最终排序。
- 主选、至少一个备选和切换条件明确。

### `weather-risk`

按地域与实际日期复用查询，按活动敏感度选择深度；远期用季节依据与复核计划，天气敏感事件提供 detailed 调整方案，不逐景点重复查或默认扩展为逐小时研究。

- 每个日期有天气状态或未来复核时间，并提供固定图标、实际天气网站和查询时间。
- 天气影响绑定到景点、交通或 checkpoint，并转化为缩短、取消、改时、换交通或切换备选等动作。

## 停止条件

- 登录、验证码或平台访问限制阻止查询时停止该来源，记录缺口并使用合法替代入口。
- 动态价格或库存无法核实时标记复核，不持续高频重试。
- 用户改变日期、人数、出发地或路线时，使受影响任务失效并重新派发。
