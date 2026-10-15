# 多来源研究契约

## 能力与研究方法

旅行规划的基础是用户需求、研究问题与证据。模型知识只提出候选和假设。网上搜索与原页面阅读是默认路径；**小红书、高德都可用时也主动执行网页搜索**，不能等辅助工具失败才查网页。已配置来源只读可用，不自动安装、扫码或修改凭证。

获取能力包括宿主搜索、原页面读取、可用浏览器、已配置 MCP/API 和用户资料；来源类型包括官方、运营方、商家、媒体、公开攻略、社区与地图。同一页面从两个工具读取仍是一份证据，转载不构成独立共识。搜索摘要只能发现候选；采用结论必须阅读原页面及相关上下文。发布时间未知就留空，查询日期不能充当原文发布日期；动态事实必须适用本次日期，未来不能核验时保留条件与复核入口。

查询设计遵循[跨渠道搜索设计](search-design.md)：先用游客自然表达的目的地攻略、玩法和美食推荐发现选择，再按用户需求缩小范围，最后核查具体对象与执行条件。每次解决一个主要问题，网页、小红书与高德使用各自适合的查询形式；不把多个预设菜名、门店或多个渠道调用合成一串关键词。新工作区另按小红书采样版本完成每目的地15篇、吃/玩各5篇的配额；配额外的研究仍按来源充分和决策缺口控制预算，不机械增加平台调用。

来源不足、工具不可用、网页无法读取、成功搜索无结果分别记录。小红书不可用时可继续读取公开亲历资料，没有高德 MCP 时可读其他地图页面、官方交通说明；无精算时采用有依据的区间估算。小红书采样配额未达标的新工作区仍可继续研究并交付待核草稿，但 final 审计会报告缺口并阻断；不虚构店名、坐标、POI、精确时间或已核验状态。

## 来源归档

沿用 `sources/<task-id>.jsonl` 和顶层 `sources[]`。新来源记录：

```json
{
  "id": "main-merchant-menu",
  "title": "具体分店菜单与门店说明",
  "url": "https://example.invalid/branch/menu",
  "kind": "merchant",
  "provider": "商家官网",
  "publisher": "该门店经营方",
  "acquisition": "web_search",
  "accessed": "full",
  "checked_at": "2026-10-11T10:00:00+08:00",
  "published_at": null,
  "origin_url": "https://example.invalid/branch/menu",
  "independence_key": "merchant-branch-menu",
  "freshness": "dynamic"
}
```

示例域名均为契约示意，不是可采用旅行来源。`acquisition` 是找到并取得原页面的渠道：`web_search/browser/mcp/api/user`。`kind` 是原始信息类型，不能写成 web_search。`publisher` 记录原始发布者，`origin_url` 指向原文，`independence_key` 将同文、同作者同一次经历和转载归为同一组；共识默认按原始发布者分组，同一作者多篇内容不算多个独立作者。independence_key 标识原始作者或机构，不以获取工具或文章编号分组。不要通过另设 key 将同 URL 拆成不同证据。未读的摘要、不可达页面不提交成已采用来源。

## 新行程契约

在 `state/itinerary-plan.json.planning` 中写入并随装配保留：

```json
{
  "research_contract": {
    "version": 1,
    "query_design_version": 1,
    "capabilities": {"web_search": "ready", "xiaohongshu": "unavailable", "amap": "unavailable"},
    "queries": [{
      "id": "web-eat-discovery-01", "stage": "discovery", "purpose": "发现符合本次美食偏好的地方吃法和候选",
      "channel": "web_search", "provider": "web", "tool": "web.search", "scope": "eat",
      "query": "目的地美食推荐", "parameters": {"q": "目的地美食推荐"},
      "checked_at": "2026-10-11T10:00:00+08:00", "status": "success",
      "outcome": "原页面提供一个门店线索；继续核对菜单、营业和用餐路线，不据此形成口碑共识",
      "source_ids": ["main-merchant-menu"]
    }],
    "decisions": [{
      "target_id": "restaurant-stable-id",
      "user_value": "具体菜品与用户偏好、同行限制的适配",
      "tradeoff": "采用理由与相比其他候选付出的时间、费用及体力",
      "consensus": false,
      "conflicts": [],
      "evidence": [{
        "aspect": "experience", "summary": "资料实际支持的具体体验；单一资料不得写成口碑共识",
        "status": "partial", "reason": "只有一份公开资料，尚无独立亲历评价",
        "source_ids": ["main-merchant-menu"], "checked_at": "2026-10-11"
      }]
    }]
  }
}
```

新工作区的 `query_design_version=1` 要求每次调用保留 id、stage、purpose、provider、tool、parameters、outcome；query 必须与实际关键词参数一致。获取方式 channel 与具体平台 provider 分开：小红书 MCP 是 channel=mcp、provider=xiaohongshu，高德 MCP 是 channel=mcp、provider=amap；网页搜索是 channel=web_search、provider=web。浏览器取得原网页可绑定网页搜索记录；MCP 取得的原文不能挂到网页搜索记录下，已采用的小红书或高德 MCP 来源须有对应平台查询。parameters 只保存非敏感实际输入。

查询记录 `scope=play/eat/operations/route/general`，包含游玩和餐饮的行程须分别执行 play 与 eat 网页研究；不是只搜一次无关网页就算参与。查询 `status=success/no_results/failed`，失败或无结果须有 `reason`。新记录中 discovery 搜索成功但尚未读候选时可暂缺 source_ids，outcome 说明候选与下一步，不能冒充已核验证据；详情和核验成功必须引用已读来源，读取后回填发现记录。旧记录仍要求成功查询引用已读来源。网页搜索 ready 时必须存在实际网页查询；不可用时写 web_unavailable_reason。查询词质量、日期是否正确、是否补齐用户需要和未采用内容的理由由语义审查确认，不能用自动通过证明关键词合适。

非景点的实际体验事件标记 `experience=true`。每个采用景点、实际体验（非日常休息）、主推餐厅和逛吃事件记录决策；备选的适配与切换保存在餐窗。`conflicts` 非空时须 `resolution`，不能静默选择。`consensus=true` 至少两份独立原始体验证据；两个工具取同一原文或多个转载不足以证明共识。

`evidence[]` 的主题可用 `identity/menu/operations/admission/experience/route`，状态为 `verified/platform_reported/community_reported/partial/estimated/to_recheck`。每项保存具体 summary、source_ids、checked_at，按需记录 applicable_date。partial/estimated/to_recheck 写明 reason；to_recheck 还须 recheck_at 与 HTTPS action_links。采用景点的 admission 与餐厅的 operations 不接受未解决状态。影响当前采用的其他硬条件标记 `critical=true`，未解决则不能通过，须补研究或改安排。verified 开放/营业必须有适用的官方、运营方或商家依据，地图返回信息是 platform_reported，社区不能替代经营承诺。

## 餐厅 v4 与路线

新餐厅配置 `restaurant_research_version=4`，使用 `restaurant-source-snapshot/v2`。保留原有实体、餐窗、候选、主备、快照、基准与双腿路线的分层，不复制候选摘要。

- 餐厅实体必须有真实名称、具体经营地址、特色菜、合法图片或原页面链接预览、操作入口与来源。location.verification 包含 checked_at、source_ids；没有 POI 仍须商家、官方或地点平台的地址证据。取得 POI 时原有 provider/id/核验与原始来源绑定仍必填；取得坐标时声明 GCJ-02 或 WGS84，不猜坐标。
- v2 快照保留 applicable_date、time_window、operations、checked_at、expires_at、action_links、source_ids，并提供 evidence[]，至少覆盖 identity、menu、operations、experience。评分与 community_consensus 是可选补充，无小红书不填虚假笔记对象；无评分不生成综合分。体验证据少可标 partial，推荐文字相应克制。
- 正常餐窗有主推与备选、连续 rank、选择理由、切换规则、前后锚点和唯一 baseline。锚点有名称与具体地址，取得坐标时核对坐标系。
- 所有路线明确 status。精算提供地图或运营方证据。estimated 提供 basis、duration_range_minutes=[下界,上界]、buffer_minutes 与来源，door_to_door_minutes 至少为上界加缓冲。候选和基准使用相同方式、策略与出发时间，双腿端点核对地址，绕行从保守排程值计算并满足最大约束；估算不是实际测得距离。没有距离时省略，不从直线距离伪造行车数据。
- 吃法线索必须绑定实际餐窗/逛吃事件；未采用线索与正式推荐分开标注。页面使用现有详情和来源区，链接显示实际来源平台，不将所有依据命名为小红书或高德。

## 缺餐例外与审查

普通餐按[研究深度](research-depth.md)使用有来源的区域餐窗，是实际用餐安排而非 meal_exemption；重要餐与供给受限餐仍深入研究具体补给。不能把核心美食、饮食限制或硬时间冲突降为普通区域建议。门店主备与绕行门槛只针对具体候选，区域餐窗单独检查位置、供给、时间、预算和失败补给。

新契约不接受自由文本 meal_exemptions。每项结构化记录 kind（onboard/service_area/restricted_supply/user_arranged）、reason、plan、source_ids、recheck_at。user_arranged 使用 user_confirmation={archive_ref, quoted_instruction, confirmed_at} 引用当前工作区 evidence/main/*.json 的用户明确安排档案，可省略外部 source_ids；其他类型必须有完整已读来源。user_arranged 必须有已归档的用户明确安排，不代表 Agent 可以让用户临时自选。服务区与景区受限补给说明能吃什么、在哪个阶段、失败补给；“酒店附近自选”“营业待核”不是有效豁免。

自动脚本检查查询参与、原页面读取声明、来源引用、去重、身份、路线数学、估算缓冲、冲突记录与字段覆盖；不能证明 Agent 实际读过正文或推荐一定有价值。主 Agent 另执行语义审查：按[跨渠道搜索设计](search-design.md)核对首轮开放发现、关键词是否无理由带入天数、具体对象的发现来源、原文候选和结果取舍；实际天数用于排程；同时检查证据是否真正支持结论、时效与主体是否一致、推荐是否适合用户、体验是否重复、核心安排是否值得成本、硬条件是否有具体失败方案。

验收同时覆盖工具齐全仍搜网页、仅网页、单辅助能力、所有外部来源失效、同文转载、分店冲突、关键缺口、实际餐窗与区间路线。协议夹具验证不等于真实旅行资料验收。旧版契约仍可读但不会自动获得新标准的通过声明；重新研究时显式写入新契约并通过审查。
