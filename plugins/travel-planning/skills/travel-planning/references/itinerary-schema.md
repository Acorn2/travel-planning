# 行程数据结构

`itinerary.json` 使用 UTF-8。主 Agent 先按 `schemas/itinerary-plan.schema.json` 编写 `state/itinerary-plan.json`，再由通用 `assemble_itinerary.py` 生成最终结构，不直接手写完整成品，也不为单次行程创建 Python。本文给出生成行程所需的字段规则；动态交通、住宿和餐厅对象还要遵守 `schemas/` 中对应的机器契约。只有需要排查渲染器或查看完整成品形态时，才读取[完整示例](../assets/example-itinerary.json)。最终以 `render_itinerary.py` 和 `audit_itinerary.py` 的校验结果为准。

## 装配计划

事件的 `details` 与 `tips` 可省略或使用空数组；提供内容时必须是非空文字组成的数组。不能放入供应商原始对象、嵌套数组、数字或布尔值，也不能用 `str()` 绕过类型检查。装配（含事件 overrides）、审查、网页渲染与随身打印共用此校验；结构化地点与应对安排应写入对应模型字段，再由渲染器生成文字。

小吃街与夜市使用 `days[].events[].type=food_walk`，事件内的 `food_walk` 对象遵循 `itinerary-plan.schema.json` 的 `foodWalk` 与[餐饮研究](restaurant-research.md)的逛吃规则。它保存具体入口与出口、实际有来源的食物、人均预算估算和备选；`meal_type=午餐/晚餐` 可满足相应餐窗。食物来源引用最终 `sources[].id`，地点仍需地图核对；事件费用只通过 `cost_items[]` 纳入总预算。渲染器与审查器拒绝缺少来源、地点或预算的逛吃安排，随身打印保留执行要点。

新规划的用餐备选统一写为 `{"name":"可定位的备用地点","physical_address":"具体地址","plan":"在什么条件下，改为执行什么安排"}`，三个字段均为非空文字，不混入供应商原始字段。`food_walk.fallback` 为兼容旧行程仍接受非空文字；区域用餐 `area_dining.fallback` 只接受上述对象。装配入口、审查和渲染共用字段类型校验；缺字段、空白、数组、数字或嵌套对象不能靠 `str()` 转换通过。网页与随身打印将合法对象格式化为地点、地址和应对安排，并转义 HTML。

商场内坐下吃简餐属于 `type=meal` 并引用 `mode=area` 的 `meal_options[]`，不为了填满逛吃字段而编造商圈入口、出口或摊位。主方案由街区逛吃改为室内用餐时，重新检查事件类型、用餐模型、时间、费用与备选，不只修改标题；同步核对日摘要、路线叙事、全程概览和两种打印内容，避免旧方案残留。备选须说明触发条件与不同的应对动作；允许同一建筑内换餐厅，但不能重复主方案。

餐窗审查兼容旧门店用餐的中文餐别与当前英文餐别：`午餐/lunch`、`晚餐/dinner` 分别满足对应餐窗，不改写原始数据。区域用餐仍按 schema 使用 `breakfast/lunch/dinner`；逛吃仍使用 `小吃/午餐/晚餐`，仅明确声明午餐或晚餐时可替代该餐，茶歇、小吃或另一餐别不能抵消缺餐。

- `research_state_sha256` 必须等于当前 `state/research.json` 的 SHA-256；重新 merge 后必须重新审视计划并更新摘要。
- `collections` 声明目标集合来自哪个任务结果或 workspace 文件、对象路径和选用 ID；通用装配器负责来源、快照、字段归一化和引用绑定。
- `attraction_events`、`meal_configs`、`days[]`、`booking_tasks[]` 和 `planning` 保存本次旅行的语义决策。时间、采用候选和降级策略属于 JSON 数据，不属于 Python 代码。
- 餐厅 `time_window` 默认继承研究阶段的候选集合，逐日展示时间写在 `days[].events[]`；只有研究餐窗本身改变时才设置 `contract_time_window` 并重新核验动态快照。

## 顶层结构

```json
{
  "trip": {},
  "workflow": {},
  "route_proposals": [],
  "planning": {
    "source_snapshots": [],
    "readiness": [],
    "attractions": [],
    "transport_edges": [],
    "daily_routes": [],
    "intercity_options": [],
    "lodging_options": [],
    "restaurants": [],
    "restaurant_snapshots": [],
    "meal_baseline_routes": [],
    "meal_route_evaluations": [],
    "meal_options": [],
    "weather": [],
    "community_evidence": [],
    "community_evidence_conflicts": [],
    "booking_tasks": []
  },
  "days": [],
  "sources": [],
  "claims": []
}
```

`planning` 保存规范化研究和可追溯关系，`days[].events[]` 是旅行者实际执行的逐日事件流。最终页面不能要求用户回到独立研究区拼接信息。

新行程由装配器设置 `planning.xhs_comment_research_version=1`，`xhs_comment_reviews[]` 保存真实评论统计、范围、查询时间和逐洞察决策；原文摘录只留私有证据目录。采用或待核评论通过 `comment_note_id/comment_insight_ids[]` 绑定 `community_evidence`。新契约的最终行程在审查和渲染阶段都须通过[评论研究闭环](xhs-comment-research.md)，旧行程保留兼容并提示未知覆盖。

`planning.community_evidence[]` 保存小红书评论等社区来源提取出的主题洞察，`community_evidence_conflicts[]` 保存同一实体/主题下的不同说法及选择依据。社区证据只表示近期体验，不替代官方营业、开放、价格、预约、交通和安全核验；其置信度最高为 `medium`。

## 基础与工作流

- 必填字段为 `trip.title`、`days[]`、`day.date`、`day.events[]`、`event.id`、`event.time`、`event.type` 和 `event.title`。`confirmed_planning` 与 `final` 阶段的事件 ID 必须唯一。
- `workflow.phase` 使用 `proposal`、`awaiting_confirmation`、`confirmed_planning` 或 `final`。路线未确认时不得生成标记为 `final` 的完整日程。
- `route_proposals[]` 保存路线候选与状态；深度调研只能使用 `selected_route_id` 指向的已确认方案。
- 详细规划提供 `planning.attractions[]`、`planning.transport_edges[]` 和 `planning.readiness[]`；跨城旅行还要提供 `planning.intercity_options[]`。行前检查不适用项使用 `not_applicable`，不得留空。
- 日期尽量使用 ISO 格式；事件时间使用目的地当地时间。跨时区航班同时保存当地出发、到达日期和时区。
- 事件类型只允许 `attraction`、`transport`、`meal`、`lodging`、`rest` 或 `note`。
- `details`、`tips`、`images`、`sources` 及其他展示元数据为可选字段，不得代替结构化执行字段。
- `planning.daily_routes[]` 每个日期最多一条，包含 `id`、`date`、`title` 和按执行顺序排列的 `stops[]`。每站至少有 `name`，应通过 `event_id` 绑定当天事件；已核验坐标与高德 POI ID 可分别保留为 GCJ-02 `coordinates` 和 `poi_id`。从第二站起可用 `arrival_mode` 标注实际到达方式，未知时省略；旧版整日 `mode` 字段可保留为 `mixed`，页面不据此生成统一导航路线。完整旅行日应覆盖住宿出发点、所有实际景点、主选正餐及当晚住宿或结束点，不能只复制 `transport` 事件。

## 来源、快照与动态状态

新行程使用 `planning.research_contract.version=1`；餐厅使用 `restaurant_research_version=4` 和 `restaurant-source-snapshot/v2`。新来源区分获取渠道与原始信息类型，记录已读原页面和发布者；v2 餐厅快照通过 evidence[] 绑定身份、菜单、营业与体验证据，社区与评分可选。没有 POI 时以 location.verification 核对具体经营地址；路线允许有依据的区间估算。完整字段和示例见[多来源研究契约](multi-source-research.md)。v3 与 v1 快照保留旧验证，不能将其视为完成新标准。

- 可能变化的事实通过一个或多个 `source_ids` 引用来源。估算值明确包含“估算”“预计”或“约”。
- `sources[].kind` 建议使用 `official`、`transport_official`、`merchant`、`editorial`、`travel_guide`、`booking_platform`、`community` 或 `map`。社区内容只支持体验判断，不能单独证明营业、价格或交通规则。
- 重要动态字段通过 `claims[]` 保存字段级证据；`status` 使用 `verified`、`platform_reported`、`community_consensus`、`estimated` 或 `to_recheck`。
- `to_recheck` 对象至少提供一个可执行的 HTTPS `action_links[]`。链接标签包含地点、日期、车站或其他查询条件，`disclaimer` 说明待核字段和复核时间；不得只给无关首页。
- 飞猪、飞常准等动态交通与住宿查询写入 `planning.source_snapshots[]`，遵守 `schemas/travel-source-snapshot.schema.json`，并设置 `planning.inventory_contract_version=1`。候选通过 `inventory_refs[]` 的 `snapshot_id + offer_id + role` 关联，不得透传供应商私有响应。
- `inventory_refs[].role` 使用 `candidate_quote`、`operational_check` 或 `station_lookup`。中国铁路候选还要提供 `rail_verification`；最终阶段要求 `channel=12306` 且 `status=verified`，境外铁路使用相应运营方官方渠道。
- 快照的 `snapshot_kind` 使用 `quote`、`operational` 或 `lookup`；状态 `platform_reported` 表示本次返回记录，`no_results` 表示成功响应但结果为空，不能改写成供应商故障。
- 查询失败使用 `travel-source-error/v1`，遵守 `schemas/travel-source-error.schema.json`，不得伪装成空的成功快照。凭证、Cookie、授权头和临时令牌不得进入快照；原始响应只保留 SHA-256 哈希。
- `items[]` 内的 `offer_id` 非空且唯一。价格同时保留 `amount`、`currency`、`basis` 和原始 `display`；只有供应商实际返回的 HTTPS 地址才能进入 `action_link`。
- quote 默认 30 分钟、运行状态默认 15 分钟、lookup 默认 24 小时失效。已选候选过期时审查警告，`workflow.phase=final` 时作为阻断项。
- 酒店快照用 `query.requested_occupancy` 保存成人数与房间数；`supplier_capacity_filter_supported=false` 时，即使返回价格也只能称为报价候选。
- `readiness[].status` 使用 `verified`、`platform_reported`、`estimated`、`to_recheck` 或 `not_applicable`。

## 景点、预约与天气

- 进入日程的景点事件通过 `attraction_id` 引用景点记录。`best_time` 给出明确时段，`best_time_reason` 写出季节、光线、客流、演出、开放时间或返程交通等实际依据。
- 景点事件提供结构化 `admission`，分别记录 `opening_hours`、`last_entry`、`reservation_method`、`entry_requirement` 和 `notice`。景点实体分别保存 `official.physical_address`、`official.homepage_url`、`official.notice_url` 与 `official.checked_at`；需要预约时增加 `official.booking_url`，无需预约且无购票页时写 `official.booking_status=not_applicable`。
- 官方预约主要在微信公众号完成时，可增加 `official.wechat`，其中 `account_name`、`menu_path`、`checked_at` 必填，已核验到官方预约说明文章时再提供 `guide_url`。`guide_url` 只接受 `https://mp.weixin.qq.com`；未取得文章入口时页面提供复制公众号名称的降级动作，不猜测公众号主页、不生成二维码。
- 景点事件提供 `execution.entry`、`execution.exit` 和 `execution.checkpoints[]`。入口包含名称、导航定位词与选择理由；出口包含名称和定位词。二者作为路线计算的独立锚点保留，页面分别把它们投影到首、末 checkpoint，不另起展示区。
- 每个 checkpoint 至少包含 `id`、`order`、`time`、`end_time`、`kind`、`name`、`required` 和 `instruction`；按需要增加讲解、图片、上一节点移动、`meal_id`、内部交通、链接和失败备选。`kind` 使用 `entry`、`ticket_check`、`visit`、`experience`、`internal_transport`、`meal`、`rest`、`photo` 或 `exit`。
- `visit_order` 只用于旧数据摘要，不能代替 checkpoints。现场动作、避坑和天气备选写入对应 checkpoint；`narration` 只在该节点确有研究到的看点或讲解时填写，不得用全局默认句补齐。出发前必须完成的装备或证件要求写入 `preparation[]`。
- 景点事件通过 `weather_id` 引用 `planning.weather[]`。天气对象包含固定 `icon_code`、摘要、状态、查询时间和 `type=weather` 的入口；会改变动作的影响写入 checkpoint 备选。
- `icon_code` 使用 `clear_day`、`clear_night`、`partly_cloudy`、`cloudy`、`fog`、`drizzle`、`rain`、`heavy_rain`、`snow`、`thunderstorm`、`wind`、`dust`、`warning` 或 `unknown`。
- `reservation_required=true` 时提供 `booking_task_ids[]`。对应 booking task 用 `event_id` 和 `attraction_id` 双重绑定，并包含目标日期或场次、人数、产品、状态、放票时间或规则、下一动作、截止时间和官方入口。
- `booking_tasks[].priority` 使用 `book_now`、`book_when_open`、`recheck_later` 或 `optional`。前两类必须提供 HTTPS 操作入口；相对期限换算为实际日期或明确触发条件。

## 费用与预算

- `activities[].fee_type` 使用 `free`、`included`、`paid_required` 或 `paid_optional`，不得把基础门票和景区二次消费混为一个价格。
- 景点事件提供 `cost_items[]`，至少包含 `kind=base_ticket`。`kind` 使用 `base_ticket`、`required_transport`、`optional_experience`、`package`、`transport`、`lodging`、`meal` 或 `other`。
- 每项费用包含 `unit_price`、`quantity`、`subtotal`、`pricing_role`、`required`、`status` 和 `source_ids`。`pricing_role` 使用 `baseline`、`optional` 或 `alternative`；`status` 使用 `official_confirmed`、`platform_reported`、`estimated`、`to_recheck` 或 `free`。
- 套票使用 `kind=package` 和 `pricing_role=alternative`，不能与同组单票同时计入基线。`cost_summary` 说明当前按人或全体基线以及未计入项。
- 页面预算从事件 `cost_items[]` 派生，不把 `planning.budget.items[]` 维护为另一套真值。保留聚合预算时注明生成时间和派生范围。
- `budget.items[].status` 使用 `confirmed`、`estimated` 或 `optional`；总价说明按人还是按全体及未计入项。

## 交通与住宿

- 交通事件通过 `route_id` 引用 `transport_edges[]`，跨城交通可引用 `intercity_options[].id`。每条交通边记录出口到入口的门到门路线。
- `ride_duration` 只表示乘车时间，`door_to_door_duration` 包含等候、换乘和步行，两者不可混用。
- 包车＋司机或包车＋司导方案增加车型、座位与行李容量、计价口径、报价、包含与排除费用、服务时长、超时费、上下车点、供应商、资质、保险、退改和紧急联系状态。
- 包车报价说明按车或按人，并区分油费、路桥、停车、司机食宿、异地返程和超时费；未取得书面报价或资质、保险确认时使用 `to_recheck`。
- 火车和高铁记录出发站、到达站、车次、时间、票价、余票状态、购票渠道和进站缓冲；无法实时取得的字段标记待复核，不得猜测。
- 长途大巴记录客运站全名、承运方、班次、上下车点、行李规则和查询平台，并尽量补充官方复核来源。
- 航班记录当地出发与到达日期和时区、机场与航站楼、含税口径、按人或全体价格、手提与托运行李、分开出票、自行提取和重新托运、退改规则。分开出票时同时核对过境与入境要求。
- 住宿事件通过 `lodging_id` 引用 `lodging_options[]`。默认 kind=area，按[住宿区域规划](lodging-planning.md)与 [lodging-area.schema.json](../schemas/lodging-area.schema.json)保存范围、大概位置、入住日期和夜数、选址理由、预算预留及路线说明；location.precision=area，不含酒店报价、房型或库存。按需酒店使用 kind=hotel，保存相关房型与核验；缺 kind 的旧酒店记录保持可读。

## 餐饮与路线适配

新计划 planning.research_depth_version=1，按[研究深度](research-depth.md)为交通、餐窗、天气记录 research_depth、depth_reason、decision_factors。普通餐窗使用 mode=area 与 [area-meal.schema.json](../schemas/area-meal.schema.json)，仍绑定 meal_id 和日期/时间，不引用门店候选、排名或 baseline；下面的候选与双腿路线规则只用于门店餐窗。交通另记录 selection_context、selection_basis，开放比较按 comparison_dimensions 要求排序覆盖。天气记录 basis_type、risk_summary、recheck_at，detailed 有 impact_plan；dates 或 date 限定适用日期，季节对象不能标成日期预报。

- 餐饮事件通过 `meal_id` 引用 `meal_options[]`。餐厅研究遵守[餐厅候选研究与路线适配](restaurant-research.md)：`restaurants[]` 保存稳定身份，`restaurant_snapshots[]` 保存本次动态覆盖，`meal_baseline_routes[]` 保存每餐唯一基准，`meal_route_evaluations[]` 保存逐候选双腿路线和额外绕行。
- 正常正餐的 `meal_options[]` 包含 2～3 个真实去重的 `candidate_ids[]`、`selected_candidate_id`、至少一个 `fallback_candidate_ids[]`、逐候选 `snapshot_id` 和 `route_evaluation_id`、`baseline_route_id`、前后锚点、数值型最大绕行、选择理由与切换规则。前后锚点及双腿路线的每个端点都必须提供明确名称、具体地址和坐标；页面据此展示真实“地点 A → 地点 B”，不得退化成“上一站/下一站”。
- `meal_options[]` 只保存餐窗和跨候选决策，不产出 `location`、`signature_dishes`、`per_person`、`opening_hours`、`queue_note`、`why_here` 或自由文本 `fallback`。地址与特色菜读取 `restaurants[]`，人均与营业读取 `restaurant_snapshots[]`，顺路依据读取 `meal_route_evaluations[]`，备选读取 `fallback_candidate_ids[]` 与 `fallback_rule`，避免主选切换后出现两套冲突数据。
- 受限场景少于两个候选时使用 `candidate_policy.status=constrained`，提供有来源的限制原因与应急补给。
- 餐厅评分按平台分开，带量表、查询时间和来源；平台返回评价量时一并保存，未返回时使用 `review_count=null` 并在页面明确“评价量未取得”，不把评分展示值整体丢弃。不得把缺少评价量的展示值称为“口碑最佳”，也不得把平台评分与社区互动数合成一个分数。动态快照遵守 `schemas/restaurant-source-snapshot.schema.json`。
- 高德餐厅实体使用 `location.poi_provider=amap` 和已核验 `poi_id`。渲染器据此生成高德门店详情与到店导航 URI；`selected_candidate_id` 仍表示系统推荐，页面内用户选择不回写此字段。
- 每个候选至少提供一项合法可视证据：可展示图片包含作者或机构、许可和原始页面；否则使用 `kind=link_preview` 的官方相册、地图详情或社区原帖，不复制或热链临时社区图片。

## 链接与图片

- `action_links[].type` 使用 `map`、`official`、`official_homepage`、`official_notice`、`official_booking`、`official_wechat`、`weather`、`weather_warning`、`ticket`、`train`、`bus`、`hotel`、`restaurant`、`guide`、`image_source` 或 `source`。
- 操作链接使用 HTTPS，包含平台名和适用提示；不得自行拼接购买详情链接。
- 图片替代文本准确描述内容，不直接使用文件名。checkpoint 图片还要包含作者或机构、许可、`source_url` 和查询时间，且主题对应当前节点。
- 大尺寸照片不得使用 Data URL，以免页面无法分享。
