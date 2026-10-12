# 按决策重要性分配研究深度

先判断本次信息会不会改变选择、费用、时间或可行性，再分配研究投入。深度用 basic/detailed 表示；这不是证据可信度，basic 仍须有真实来源和能执行的安排。记录 research_depth、depth_reason、decision_factors，按实际用户兴趣与硬条件选择，不按旅行天数或工具数量机械扩展。

## 交通

普通同片区移动、用户已选择的交通或指定班次：先提供方式、具名起终点、来源支持的门到门耗时区间与缓冲、费用范围和操作入口，复用区域与路线资料。只有方式、出入口、换乘或耗时会改变安排时才追加精查，不为每条边查询实时拥堵、完整公交步骤和多个报价平台。

开放的跨城选择、复杂换乘、末班交通、硬预约或特殊同行条件：深入核对相关时间边界、行李、费用和失败备选。供应商比较按待解决问题查询：时间用最早/最晚/时长，预算用价格，综合选择用推荐；不默认每种排序都查。比较停止于能做决定，不用单次低价榜断言全天没有方案。

新交通候选记录 selection_context=local_transfer/specific_service/user_selected/compare_options 及 selection_basis。compare_options 使用 comparison_dimensions 中的 time/price/recommendation；确定性审查只要求对应排序覆盖，仍核验所选快照与有效期。指定班次保留 service_or_train 或 service_id，user_selected 的依据须引用用户已选安排。旧记录缺 selection_context 时保持原排序门槛，不自动降级。

## 餐饮

所有旅行仍有明确午晚餐时间、吃法、位置、预算及失败安排。默认先研究目的地吃法与实际用餐片区，复用同区域信息；不为普通每餐搜两家店、读评论和计算候选双腿绕行。

- 日常用餐 basic：可用 mode=area 的区域餐窗，具名街段/入口及地址、在该处有来源的正餐吃法、人均预算预留、适配理由、营业适用范围和定位入口。明确是区域建议，不称已选餐厅或已确认每家营业；失败时给可定位的备用区域或具名补给。保留用餐时间与前后衔接的估算说明，不做门店排名与逐候选路线。
- 美食核心体验、用户指定门店、饮食限制、供给受限或用餐与硬时间冲突：detailed，沿用餐厅 v4 身份/菜单/运营证据、主备门店及同口径路线。用户偏好美食时投入核心特色餐和地方吃法多样性，不能把全部餐降为区域自由选择。
- 街区逛吃沿用 food_walk；区域餐窗与逛吃不同，前者主要解决普通正餐，不能借区域模式宣称已研究逛吃体验或具体门店口碑。

区域餐窗放入 meal_options，通过 meal_id 绑定实际事件，使用 [area-meal.schema.json](../schemas/area-meal.schema.json)。decision_factors 默认包含 routine；核心美食、饮食限制、供给受限、硬时间等因素不能使用区域模式。示例结构：

```json
{
  "id": "meal-d1-lunch", "mode": "area", "research_depth": "basic",
  "depth_reason": "日常午餐，供给充足且不影响核心体验", "decision_factors": ["routine"],
  "name": "街区午餐", "meal_type": "lunch", "applicable_date": "2026-10-15",
  "time_window": "12:00–13:00", "checked_at": "2026-10-12T10:00:00+08:00",
  "area_dining": {
    "name": "已研究的餐饮街段", "physical_address": "已研究的街段或公开入口地址",
    "food_options": ["原文支持的正餐吃法"], "budget_per_person": 50,
    "why_here": "承接当天片区，保留一小时午餐", "availability_basis": "原文支持该区域有正餐供给；具体商户营业到店确认",
    "route_note": "前后交通按街区入口估算并留缓冲",
    "fallback": {"name": "已研究的备用补给区域", "physical_address": "备用区域入口地址", "plan": "未营业或拥挤时到备用区域吃正餐"},
    "source_ids": ["main-area-food"], "action_links": [{"type": "map", "label": "查看用餐区域", "url": "https://example.com/map"}]
  }
}
```

示例内容须由本次已读原文替换；预算是预留，不能据此声称商户菜单价格。区域供给和备用安排须有来源支持，菜名不能仅从城市印象推断。未能证明普通餐供给就升级具体补给研究，不以“附近自选”交差。

区域餐窗由主 Agent 或 stay-food 任务直接提交；不进入门店 discovery→候选双腿路线→ranking 流程。该三阶段只处理 detailed 餐窗，普通区域餐窗仍参与时间、来源与用户价值审查。

## 天气

按地点与适用日期共享天气对象，不为每个景点单独请求。basic 给季节条件/临近预报、穿着要点、会改变活动的风险和复核入口；季节对象可用 dates 覆盖实际适用的多个日期。一次地点查询得到的多日预报拆成逐日对象复用，同一天多个事件共用该对象，不能将某一天的预报扩写到其他日期。

旅行日期在可用预报范围之外，优先读取季节资料并设定临近出发复核日期，不查询当前天气来填未来日程。天气对象保存 basis_type=seasonal/forecast、risk_summary、recheck_at 以及 source_ids；远期季节对象用 icon_code=unknown、status=estimated/to_recheck，不冒充逐日预报。常规室内或容易调整的活动不反复查逐小时天气。

徒步、高海拔、涉水、船班、日出日落、长时间户外等实际天气敏感安排使用 detailed；查会影响动作的降雨、风、温度、日照等字段，必要时核官方公告。检查对象保存相关日期、风险和具体改时/缩短/换活动规则，不能只重复“注意天气”。weather_sensitive 标记不能采用 basic；安全关键条件不因节约时间而弱化。

## 记录与审查

新计划 planning.research_depth_version=1，餐窗、交通及天气对象记录深度和依据。decision_factors 采用简洁 ID：routine、user_priority、fixed_time、limited_supply、dietary_constraints、complex_transfer、weather_sensitive、cost_tradeoff；按实际问题填写，不以漏标关键因素绕过门槛。

自动审查检查结构、显式因素与深度是否一致、区域餐窗和来源、交通比较范围、天气适用日期；主 Agent 另检查因素是否遗漏、核心偏好是否兑现、轻量信息是否足够执行。核心信息不足就补查或调整安排，不能以 basic 包装未完成研究。用户修改日期、兴趣或硬条件时重新评估受影响对象，沿用未受影响资料。

结束时用执行报告观察投入是否集中在影响决策的项目。少查几次不是完成标准，来源仍须支持结论；历史计划不自动获得新深度标准通过声明。
