# 小红书评论研究闭环

评论是独立的体验证据。正文空、夸张或疑似推广时，仍读取评论中的纠错、反向体验和问答；不以帖子点赞或评论总数代替阅读。小红书不可用时继续其他来源，不为评论研究自动安装或登录；新工作区若采样配额未达标，最终行程保留缺口并由硬门槛阻断。

## 读取与数量

正式研究统一用包装命令，带本次 workspace 和 task-id：

```bash
python3 skills/travel-planning/scripts/research_sources.py xhs-detail \
  --note-id <搜索所得ID> --workspace ".travel-research/<trip-id>" --task-id main
```

默认 `load_all_comments=true, limit=20, click_more_replies=true, reply_limit=50`，只读取有上限的样本；不是抓全集。上游详情是 `data.comments.list[]`，回复是 `subComments[]`。真实返回可能少于或超过加载目标，统计以返回内容为准。

- `comment_analysis.top_level_count/reply_count/input_count`：实际处理的一级评论、二级回复与合计；有效、过滤、重复、主题数量分别记录，不混用。
- `comment_collection.reported_total`：笔记显示的总数，不能当成已抓数量；`has_more` 和 `coverage` 保留覆盖局限。
- `reply_limit` 是展开阈值，超过它的串可能被跳过。查看 `unexpanded_reply_threads`；重要问题或高回复争议须预算内定向提高阈值（最高1000）并补查，或说明无法展开的具体缺口，不宣称讨论一致。
- 路线前预研可显式 `--sample-comments`；正式研究仅明确降级时用此模式或 `--no-replies`，记录原因。新工作区的正式小红书研究每目的地至少15篇独立详情，吃和玩各不少于5篇；复用同帖，不为每餐重复读取。缺口或冲突追加需说明原因。
- 评论字段缺失或格式变化报错；真实空列表才能记0。工具失败不能改写成“没有评论”。

## 阅读与存储

`comment_insights` 是规则候选，不是最终判断。Agent 实际读 `comments` 中的问题和回答、亲历补充、反驳正文、替代路线与争议，并检查 `discussion_samples` 中可能被规则漏掉的信号。问题本身不证明事实；反问式纠错不能因问号丢弃。二级回复的父评论 ID 和必要上下文保留，孤立的“可以/不用”不能脱离提问下结论。

带 workspace 的包装调用自动归档到 `evidence/<task-id>/xhs-comments/<摘要>.json`，包括实际统计、读取范围、规则候选及最多20个必要讨论摘录（每段最多180字符）；只保留必要证据，不保存完整原始响应、评论全集、用户 ID、临时令牌或媒体地址。复用不会重复计数或刷新查询时间，后续读取保留独立观察。缓存过期不删除证据。

## 进入决策与验收

新工作区由装配器启用 `planning.xhs_comment_research_version=1`。主 Agent 在 `state/itinerary-plan.json` 的 `planning.xhs_comment_reviews[]` 为每篇原帖写下：

```json
{
  "note_id": "搜索所得ID",
  "checked_at": "与归档观察完全相同的时间",
  "review_status": "reviewed",
  "reason": "评论如何补充或纠正正文、影响哪些安排；有未读串时说明局限",
  "insight_decisions": [
    {"insight_id": "comment-group-1", "decision": "needs_recheck", "reason": "入口施工可能改变路线，需核对当日公告"},
    {"insight_id": "comment-group-2", "decision": "rejected", "reason": "讨论的是另一分店，不适用本次安排"}
  ]
}
```

每个规则候选明确 `adopted/rejected/needs_recheck` 与理由，不强制采纳低质量评论。没有候选仍实际阅读讨论样本并说明无有效信号的原因。规则漏掉的有用讨论由 Agent 补入 `community_evidence`，用 `comment_note_id` 和必要 `evidence` 中的 `comment_id/parent_comment_id` 追溯；不能伪造规则分组 ID。

采用或待复核的规则候选必须写入 `community_evidence[]`，除既有目标实体、主题、洞察、支持/冲突数量、置信度及来源字段外，加 `comment_note_id` 和 `comment_insight_ids[]` 绑定原帖及规则候选。结合本次用户需求与事件安排解释如何使用；涉及官方规则的评论保留复核，存在冲突降低置信度。不把单次体验外推为未来开放或客流保证。

装配器从实际抓取证据导入统计和候选 ID，不允许手填数量；语义审查绑定相同 `checked_at`，刷新后须重新审查。最终 JSON 只保留统计和决策，不带评论原文或摘录。`audit_itinerary.py` 和渲染器对新契约的最终行程阻断：原帖缺评论审查、未审查候选、数量不闭合，以及采纳候选未进入社区证据。旧行程只警告，不能宣称已按新标准研究。

确实无法取得评论时可写 `review_status=unavailable`、原帖 `source_url`、真实 `checked_at` 和具体失败 `reason`；不填写虚假的0条，不声称已完成评论研究。交付说明失败和覆盖缺口，并汇报采用原帖范围内实际一级评论、回复、规则有效样本、洞察以及采纳/排除/待核数量，不能把各阶段重复观察相加。

格式依据：[锁定版本 types.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/6583124dfda92312b6bc19a042a6acfae63fe498/xiaohongshu/types.go) 与 [MCP 参数定义](https://github.com/xpzouying/xiaohongshu-mcp/blob/6583124dfda92312b6bc19a042a6acfae63fe498/mcp_server.go)。
