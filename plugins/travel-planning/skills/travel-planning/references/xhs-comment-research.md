# 小红书评论研究闭环

评论用于发现正文之外的纠错、反向体验、问答和替代选择。新工作区使用 `planning.xhs_comment_research_version=2`：规则只提示阅读顺序，Agent 阅读实际取得的有限样本并形成语义洞察，脚本检查覆盖、来源和产物版本。脚本不能证明语义判断正确，交付前仍须抽查对象、上下文和安排是否一致。

来源选择与已读审查分开：新行程默认 xhs_research_mode=optional，未在最终 sources、社区证据或餐厅口碑引用小红书时，无需完成15篇；正式引用或 required 模式才执行逐城配额。已经取得的样本仍全部审查，不能借可选模式漏审。服务不可用或搜索失败后未采用，记录 research_contract.xiaohongshu_fallback_reason，再由其他已读来源完成研究；不能保留小红书引用却按未使用跳过配额。

## 采集与逐帖审查

正式研究使用带 workspace 的包装命令：

```bash
python3 skills/travel-planning/scripts/research_sources.py xhs-detail \
  --note-id <搜索所得ID> --workspace ".travel-research/<trip-id>" --task-id main
```

默认 `load_all_comments=true, limit=20, click_more_replies=true, reply_limit=50`，是有限样本而非评论全集。一级评论来自 `data.comments.list[]`，回复来自 `subComments[]`。统计以实际返回为准，平台显示总数与加载目标不能冒充已读数量。格式变化报错；真实空列表才能记0。路线前轻量预研可用 `--sample-comments`，正式降级须说明原因。

已请求回复时，未知对象结构、字符串、数字、布尔值或含无效元素的回复数组都会报错，采样立即停止，不写入成功的0条评论证据。合法空数组与上游未展开时返回的 `null` 或缺省字段保持可读；若原帖报告仍有回复，则计入 `unexpanded_reply_threads`，不能解释为没有讨论。显式 `--no-replies` 或 `--sample-comments` 不解析未请求的回复内容，仍保留未覆盖范围。

需要配额或显式选择系统采样时，新工作区交替运行 `xhs-sample` 与语义审查：采样器按已确认路线 `cities[]` 逐城补足，每取得一篇后返回 `status=awaiting_review`、`next_action=xhs-review`，并在 `review_packet` 附上该篇的标题、正文首段、图片提示与第一批评论片段；已有待审查样本时也返回这份内容，不重新请求平台。Agent 读完该篇正文、必要图片和全部取得的评论后保存结论，再继续采样。路线名单中的每个城市都到达15篇、吃和玩各至少5篇且没有待审查笔记后，才返回 `complete`；`--destination` 指定的单城达标不代表整条路线达标。optional 未引用时可停止继续采样并据其他来源交付，但保留真实缺口，不能改写为采样 complete。旧工作区仍可批量采样，但不能省略已取得样本的后续逐帖审查。

`unavailable` 是尚未取得样本的失败记录，不进入待审查队列，也不计入15篇配额。`xhs-state`、`xhs-sample` 和 `xhs-review` 保留 `unavailable_note_count/unavailable_notes` 中的原时间、原帖和原因；采样先跳过这些失败笔记读取其他候选，不自动重试。只有失败记录而无待审查样本时，`xhs-review` 返回 `status=unavailable`，不会报成已完成。需要重试时显式调用 `xhs-detail --note-id <ID> --workspace <工作区> --refresh`（缺访问令牌先重新搜索），成功的新观察重新进入 pending；仍遵守登录、验证、风控与限流的停止规则。配额达标不代表失败记录已解决，交付须另行说明覆盖缺口。

`xhs-detail` 的 `note.description` 保留实际取得的完整正文，评论与回复的 `content` 保留完整文本及换行，不再按320/500字符截断。`description_excerpt`、父评论摘要和证据摘录只用于预览，不是完整阅读依据。`review_threads` 每组包含根评论及其回复、父评论上下文和阅读优先级。不因分数低、含问号、出现“广告”或缺少亲历关键词而排除。空内容、同一线程中的重复评论身份只作机械标记；不按同主题或共享“排队”等关键词合并主张，不自动生成共识、可信度或有价值评论数量。兼容字段 `comment_insights/needs_recheck` 在 v2 抓取结果中为空，语义洞察由 Agent 提供。

需要恢复或分批阅读时使用本地入口：

```bash
python3 skills/travel-planning/scripts/research_sources.py xhs-review \
  --workspace ".travel-research/<trip-id>" --note-id <笔记ID> --offset 0 --limit 5
```

每批同时返回 `note`、`image_reading`、`body_reading` 和 `review_threads`，序列化 UTF-8 JSON 不超过24KB；线程清单只包含本批评论引用，累计全部批次后才是完整清单。正文默认每页4000字符，沿 `body_reading.next_offset` 设置 `--body-offset` 继续，必要时用 `--body-limit 1..12000` 调整每页字符数；实际返回还受字节预算限制，按 returned_characters 和 next_offset 续读，把各页 `note.description` 顺序连接可还原完整正文。评论优先沿 `next_cursor` 设置 `--cursor` 继续，游标绑定 capture_id、线程、评论及字符位置；单条超长评论或回复也可分片。每个片段的 `content_reading` 给出原文 offset、total_characters 和 next_offset。`next_offset/--offset` 只兼容完整线程分页，不能替代片段游标。正文与评论分别续读，读完其中一种不代表另一种已完成。不把首段正文、前5串或20段摘录当成全样本。

`image_reading` 保留图片数量、原序号、正文是否缺失和 `not_read/no_images`，有图时提示 `next_action=xhs-images`；按[图片笔记](../../xiaohongshu/references/image-notes.md)准备相关图片并实际调用 `view_image`。返回图片提示或下载成功不代表已经看图，正文与评论也不能替代图片中的店名、地址和路线信息。

`text_content_status=complete` 只表示本地缓存保留本次取得的完整文本，不证明 Agent 已读完全部分页或平台全部评论。此命令不请求平台，不刷新 `checked_at`；过期缓存只能作为原观察阅读，不能证明当前事实。正文与图片提示的摘要哈希也绑定本次观察，不能混用其他观察的内容。`context_unavailable` 表示原正文或有限样本无法完整恢复；旧版截断缓存返回 `text_content_status=legacy_excerpt`，不能补造被截去的尾部。必要摘录仍可参考，但须定向重读、绑定新观察，不能伪称全部已审查。

存在 `has_more` 或 `unexpanded_reply_threads` 时说明未覆盖讨论。重要争议可在预算内提高 `--reply-limit`（最高1000）补读；遇到登录、验证、风控或限流立即停止。未加载的回复不代表没有异议。

## 正文、图片和评论的覆盖记录

新抓取带 `content_review_version=1`，归档时生成 `content_manifest`，包含正文长度/摘要、图片数量/清单摘要；线程引用保留每条评论的 `content_characters`。旧观察不补造这些字段。新观察缺少下列 `content_review` 时，即使 reviewed_thread_ids 已齐全，仍属于待审查，采样、final 审计、分享与快照准备均不能把它当成已完成。

从每次实际阅读的正文和评论片段累计半开区间 `[offset, offset + 返回字符数]`；范围可分段，但必须无遗漏覆盖原文，不能把首段扩写为全长。空正文/空评论对应空数组。`body_digest` 与 `image_manifest_digest` 原样取自 content_manifest。评论范围按匿名 comment_id 汇总；仅填线程 ID 不证明长评论片段已覆盖。

```json
{
  "content_review": {
    "body_digest": "原观察正文摘要",
    "body_ranges": [[0, 4000], [4000, 7000]],
    "comment_ranges": {"根评论ID1": [[0, 600]], "回复ID1": [[0, 900]]},
    "image_manifest_digest": "原观察图片清单摘要",
    "images": [
      {"image_index": 1, "status": "reviewed", "summary": "图中入口位于北侧，与正文旧入口有区别", "reason": "用于核对进出路线"},
      {"image_index": 2, "status": "not_needed", "reason": "纯风景照片，无补充文字或决策信息"}
    ],
    "reason": "已读完整正文和评论，北侧入口的变化需要官方复核"
  }
}
```

以上长度仅为示例，必须使用实际返回值。图片逐个原始序号记录 `reviewed/not_needed/unavailable`：reviewed 须实际 view_image 并写摘要，not_needed 须有不相关理由，unavailable 保留真实失败与信息缺口。正文缺失而有图片时，全部跳过或全部失败不能算完成。准备图片时传 `xhs-images --note-id <ID> --indices 1 --expected-digest <image_manifest_digest>`；图片缓存已被另一观察覆盖会明确拒绝，不能混用图片序号。下载成功仍不等于看图完成。脚本验证覆盖声明、摘要与数量，不能证明 Agent 真正阅读或判断正确，交付前仍须语义抽查。

未知正文/评论文本类型或非数组图片结构会失败，不会变成空正文、0张图或空评论；合法空字符串和 null 保持可读。旧数据的兼容不豁免已有逐城配额：即使没有显式模式字段，HTML 交付仍执行原 required 契约。

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

包装器在 `evidence/<task-id>/xhs-comments/` 保留真实统计、范围、匿名评论/线程引用及内容摘要哈希、正文与图片提示的摘要哈希、最多20段必要摘录（每段最多180字符）。不保存完整原始响应、平台评论全集、用户 ID、凭证或媒体地址。完整正文和本次实际取得的有限评论仅存于私有查询缓存供本地分页阅读，不进入长期证据或公开产物；缓存丢失时不能用摘录补造全样本。最终 JSON 只带统计、匿名身份、覆盖和语义决策，不带正文、评论原文或摘录。

装配器导入每帖最新正式观察，按 `checked_at + capture_id` 匹配审查；刷新后旧结论恢复为 pending。审计遍历全部装配审查记录与原帖来源的并集，不允许通过不列入 `sources` 跳过未读笔记。final 在未审查线程、候选无决策、统计不闭合、引用虚构或采纳/待核无社区证据时阻断。

审计和渲染的 CLI 自动定位工作区，比较实际最新证据，拦截新增抓取后未重新装配的旧 JSON。v2 HTML 内含审查摘要；分享复制与发布快照准备时核对工作区及当前 JSON，重新装配后也必须重新渲染。独立复制到其他电脑的文件无法自动感知原工作区后续变化。以上校验不替代 HTML 审核与本次明确发布确认。

声明来源模式的 HTML 另带 xhs-policy-digest，绑定模式、是否正式引用及降级理由。模式变更后，旧 JSON 与工作区不一致会被拦截；重新装配后沿用旧 HTML 也会被分享和发布快照准备拒绝。不会通过改为 optional、移除来源后复用旧页面来跳过当前版本审核。

装配器成功写入 JSON 后，在私有 `state/xhs-comment-input.json` 中记录实际输入计划、输出 JSON 路径及本次评论审查摘要，不把本机路径放入公开 JSON 或 HTML。显式 `assemble_itinerary.py --plan <文件> --output <文件>` 支持工作区外的输入和输出：审计、队列与采样跟随实际计划，分享和快照准备读取实际输出；再次成功装配才切换绑定，输出写入失败保留上一次绑定。输入或显式输出不可读取、记录无效时明确失败，不回退到无关默认文件；没有输入记录的旧工作区继续使用 `state/itinerary-plan.json`，旧记录缺输出路径时沿用 `artifacts/itinerary.json`。过期比较覆盖当前计划与装配结果的完整审查记录集合，包含没有 capture 的 unavailable 记录；其新增、修改或删除均须重新装配与渲染。

底层诊断显式使用 `xhs-detail --capture-purpose diagnostic --diagnostic-reason "具体原因"`；诊断不计入正式采样、待审查队列或研究数量，也不能作为正式来源。后来的诊断不能覆盖已有正式观察。默认抓取均为 research，不根据任务名猜测用途，不事后把漏审查记录改成诊断。

旧 v1 观察和决策保持可读，旧规则候选仍按原 ID 处理，不补造 v2 审查历史。确实未取得评论时记录 `unavailable`、原帖链接、真实时间及失败原因，不填写虚假0条、不宣称研究完成。

交付从 `community_audit.comment_research` 分别汇报实际抓取的一级评论/回复、已审查篇数与评论量、待审查篇数、语义洞察及采纳/排除/待核数量。v2 的 `eligible_count` 仅为未被机械标记的待语义样本，不是有价值评论数量；旧字段保留用于数量兼容，不能当作已读或可信证据。
