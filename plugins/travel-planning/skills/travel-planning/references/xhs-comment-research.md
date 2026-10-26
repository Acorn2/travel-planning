# 小红书评论研究闭环

评论用于发现正文之外的纠错、反向体验、问答和替代选择。新工作区使用 `planning.xhs_comment_research_version=2`：规则只提示阅读顺序，Agent 阅读实际取得的有限样本并形成语义洞察，脚本检查覆盖、来源和产物版本。脚本不能证明语义判断正确，交付前仍须抽查对象、上下文和安排是否一致。

## 采集与逐帖审查

正式研究使用带 workspace 的包装命令：

```bash
python3 skills/travel-planning/scripts/research_sources.py xhs-detail \
  --note-id <搜索所得ID> --workspace ".travel-research/<trip-id>" --task-id main
```

默认 `load_all_comments=true, limit=20, click_more_replies=true, reply_limit=50`，是有限样本而非评论全集。一级评论来自 `data.comments.list[]`，回复来自 `subComments[]`。统计以实际返回为准，平台显示总数与加载目标不能冒充已读数量。格式变化报错；真实空列表才能记0。路线前轻量预研可用 `--sample-comments`，正式降级须说明原因。

新工作区交替运行 `xhs-sample` 与语义审查：采样器每取得一篇后返回 `status=awaiting_review`、`next_action=xhs-review`，让 Agent 审查并保存该篇结论，再继续采样。到达每目的地15篇、吃和玩各至少5篇且没有待审查笔记后，才返回 `complete`。旧工作区仍可批量采样，但不能省略后续逐帖审查。

`unavailable` 是尚未取得样本的失败记录，不进入待审查队列，也不计入15篇配额。`xhs-state`、`xhs-sample` 和 `xhs-review` 保留 `unavailable_note_count/unavailable_notes` 中的原时间、原帖和原因；采样先跳过这些失败笔记读取其他候选，不自动重试。只有失败记录而无待审查样本时，`xhs-review` 返回 `status=unavailable`，不会报成已完成。需要重试时显式调用 `xhs-detail --note-id <ID> --workspace <工作区> --refresh`（缺访问令牌先重新搜索），成功的新观察重新进入 pending；仍遵守登录、验证、风控与限流的停止规则。配额达标不代表失败记录已解决，交付须另行说明覆盖缺口。

`xhs-detail` 返回 `review_threads`：每组包含根评论及其回复、父评论上下文和阅读优先级。不因分数低、含问号、出现“广告”或缺少亲历关键词而排除。空内容、同一线程中的重复评论身份只作机械标记；不按同主题或共享“排队”等关键词合并主张，不自动生成共识、可信度或有价值评论数量。兼容字段 `comment_insights/needs_recheck` 在 v2 抓取结果中为空，语义洞察由 Agent 提供。

需要恢复或分批阅读时使用本地入口：

```bash
python3 skills/travel-planning/scripts/research_sources.py xhs-review \
  --workspace ".travel-research/<trip-id>" --note-id <笔记ID> --offset 0 --limit 5
```

按 `next_offset` 继续，保留同一讨论串的完整返回上下文，不把前5串或20段摘录当成全样本。此命令不请求平台，不刷新 `checked_at`；过期缓存只能作为原观察阅读，不能证明当前事实。`context_unavailable` 表示原有限样本无法完整恢复，必要摘录仍可参考，但须定向重读、绑定新观察，不能伪称全部已审查。

存在 `has_more` 或 `unexpanded_reply_threads` 时说明未覆盖讨论。重要争议可在预算内提高 `--reply-limit`（最高1000）补读；遇到登录、验证、风控或限流立即停止。未加载的回复不代表没有异议。

## 语义判断与计划记录

每篇取得的正式研究笔记都必须有结论，允许零采纳。Agent 区分问题与答案、亲历与转述、反讽与推荐、平台摘要与独立体验，并检查低分评论。先确认具体门店、分店、入口、时段及主张，再决定是否跨评论合并。相同关键词不说明是同一对象；同一串中的作者回复、附和或复制摘要不能累加为独立共识。

在 `state/itinerary-plan.json` 的 `planning.xhs_comment_reviews[]` 写入下列字段；`capture_id`、时间和线程 ID 从 `xhs-review` 原样取得：

```json
{
  "note_id": "搜索所得ID",
  "checked_at": "与原观察完全相同的时间",
  "capture_id": "原观察摘要",
  "review_status": "reviewed",
  "reviewed_thread_ids": ["根评论ID1", "根评论ID2"],
  "reason": "已审查全部取得的讨论串，说明如何补充正文及未加载回复的局限",
  "insight_decisions": [
    {
      "insight_id": "entry-construction",
      "subject": "具体景区东门",
      "claim": "一条现场回复称东门施工",
      "comment_ids": ["实际回复ID"],
      "decision": "needs_recheck",
      "reason": "可能改变入场路线，单条评论不能证明旅行当日规则",
      "recheck_action": "核对运营方当日施工公告与可用入口"
    }
  ]
}
```

`schemas/xhs-comment-review.schema.json` 的根 Schema 校验上述输入计划；`thread_manifest` 是只读观察元数据，由装配器导入，不要求在输入中手填。装配后的完整记录使用同文件的 `#/$defs/assembled` 契约，额外要求观察范围、统计、洞察 ID 和线程清单；运行时审计继续校验这些字段的真实性与闭合。

`reviewed_thread_ids` 必须覆盖原观察的全部线程。每条语义洞察包含明确对象 `subject`、具体主张 `claim`、实际 `comment_ids` 及 `adopted/rejected/needs_recheck` 和理由；不要复制原文长段。`adopted` 另写 `planning_effect`，说明对具体安排、备选或排除门店的影响；`needs_recheck` 写待核字段及后续动作。没有决策信息时，`insight_decisions=[]` 并说明理由即可，不强制凑采纳数。

队列与审计共用基础审查校验，只有线程覆盖、数量、洞察决策、实际评论引用和必要动作说明都完整，才计为 reviewed；缺少 decision 或理由的洞察仍留在 pending。基础审查完成后才进入社区证据装配阶段；缺少行程证据绑定会阻断 final，并计入 `evidence_issue_note_count`，不会把已完成的语义阅读重新算成未读。

采纳及待核洞察通过 `community_evidence[]` 的 `comment_note_id/comment_insight_ids[]` 绑定正式原帖来源与目标实体。每条社区证据的 `support_count` 上限，按其绑定的全部采纳及待核洞察所引评论汇总、去重独立讨论串；不同洞察引用同一根评论及其回复仍只算一串，未绑定、未知或已排除洞察不能增加上限。这只是上限，仍需 Agent 排除转述、同一作者和机器摘要。涉及开放、预约、价格、交通和安全须回官方核验，待核内容不写成确定规则。仅绑定证据不等于已经改变行程，必须核对安排与 `planning_effect` 一致。

## 归档与验收

包装器在 `evidence/<task-id>/xhs-comments/` 保留真实统计、范围、匿名评论/线程引用及内容摘要哈希、最多20段必要摘录（每段最多180字符）。不保存完整原始响应、平台评论全集、用户 ID、凭证或媒体地址。工作区短时查询缓存只用于重放本次有限样本；丢失时不能用摘录补造全样本。最终 JSON 只带统计、匿名身份、覆盖和语义决策，不带评论原文或摘录。

装配器导入每帖最新正式观察，按 `checked_at + capture_id` 匹配审查；刷新后旧结论恢复为 pending。审计遍历全部装配审查记录与原帖来源的并集，不允许通过不列入 `sources` 跳过未读笔记。final 在未审查线程、候选无决策、统计不闭合、引用虚构或采纳/待核无社区证据时阻断。

审计和渲染的 CLI 自动定位工作区，比较实际最新证据，拦截新增抓取后未重新装配的旧 JSON。v2 HTML 内含审查摘要；分享复制与发布快照准备时核对工作区及当前 JSON，重新装配后也必须重新渲染。独立复制到其他电脑的文件无法自动感知原工作区后续变化。以上校验不替代 HTML 审核与本次明确发布确认。

装配器成功写入 JSON 后，在私有 `state/xhs-comment-input.json` 中记录实际输入计划、输出 JSON 路径及本次评论审查摘要，不把本机路径放入公开 JSON 或 HTML。显式 `assemble_itinerary.py --plan <文件> --output <文件>` 支持工作区外的输入和输出：审计、队列与采样跟随实际计划，分享和快照准备读取实际输出；再次成功装配才切换绑定，输出写入失败保留上一次绑定。输入或显式输出不可读取、记录无效时明确失败，不回退到无关默认文件；没有输入记录的旧工作区继续使用 `state/itinerary-plan.json`，旧记录缺输出路径时沿用 `artifacts/itinerary.json`。过期比较覆盖当前计划与装配结果的完整审查记录集合，包含没有 capture 的 unavailable 记录；其新增、修改或删除均须重新装配与渲染。

底层诊断显式使用 `xhs-detail --capture-purpose diagnostic --diagnostic-reason "具体原因"`；诊断不计入正式采样、待审查队列或研究数量，也不能作为正式来源。后来的诊断不能覆盖已有正式观察。默认抓取均为 research，不根据任务名猜测用途，不事后把漏审查记录改成诊断。

旧 v1 观察和决策保持可读，旧规则候选仍按原 ID 处理，不补造 v2 审查历史。确实未取得评论时记录 `unavailable`、原帖链接、真实时间及失败原因，不填写虚假0条、不宣称研究完成。

交付从 `community_audit.comment_research` 分别汇报实际抓取的一级评论/回复、已审查篇数与评论量、待审查篇数、语义洞察及采纳/排除/待核数量。v2 的 `eligible_count` 仅为未被机械标记的待语义样本，不是有价值评论数量；旧字段保留用于数量兼容，不能当作已读或可信证据。
