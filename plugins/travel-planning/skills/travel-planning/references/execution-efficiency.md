# 调用范围、复用与耗时

先从本次已确认需求确定研究问题和来源，再调用工具。航班、铁路、住宿库存等无关能力不探测；工具已接入不代表本次必须用。默认住宿区域不触发酒店查询。健康检查只证明某次调用路径可用，不替代地点、报价、经营或规则的事实核验。

## 按需预检

`preflight --source` 选择需要探测的可选来源；`--require` 选择必须可用的来源并自动纳入探测。可重复传入。未选择来源时返回 not_requested，checks 为空，skipped_sources 列出未检查来源；不能把它们记为 ready 或 unavailable。`--all-sources` 只用于用户要求的全部诊断，不用于默认规划。

```bash
python3 skills/travel-planning/scripts/research_sources.py preflight \
  --source amap-maps --source xiaohongshu --require open-meteo \
  --city "本次城市" --workspace ".travel-research/<trip-id>" --phase preflight
```

可以直接执行需要的业务查询，不必先额外做同一来源的 smoke query。带 workspace 的成功供应商、天气或小红书业务查询会留下短时运行记录，后续同来源预检可复用。记录 scope=observed_operation 时只代表该实际操作成功，不代表做过完整 tools/list 或所有产品都可用。高德 REST 查询不证明高德 MCP 连接可用，两个路径不混用。

`--skip-upstream` 的离线诊断不使用或写入实时运行复用记录；只能报告 degraded。明确登录诊断仍检查实际会话。失败不自动连环重试，按错误类型选择修正参数、换来源或停止。

## 同一行程内复用

恢复旧工作区先执行 `research_sources.py xhs-state --workspace <工作区>` 查看各主题最新观察与实际详情/评论记录。它不联网，`live_service_checked=false`；不能把状态汇总当作新的搜索。历史失败、旧 route-context 和旧回复都不能证明当前不可用，也不能替代本轮调用。修复后或明确复核时用包装查询 `--refresh`；当次失败与验证/限流仍按供应商停止规则，不叠加重试。搜索、正文和评论分别记录真实结果，没有详情调用就说明未尝试读取评论。

包装命令加 `--workspace` 后，按工具、供应商、完整业务参数与当前脚本版本匹配本行程结果。日期、方向、坐标、城市、人数、房间、房型、筛选、排序、评论范围或结果上限改变时重新查询；任务 ID 与阶段名称不改变业务结果。成功结果最多复用10分钟，且不得超过供应商 freshness.expires_at；失败和空结果不复用。`--refresh` 用于明确重新核验或连接身份/账号配置变化后的查询。

报价复用仍把原快照绑定到当前有效 assignment，不制造新 offer、snapshot_id、独立证据或查询时间；排序覆盖按每个实际查询条件分别复用，不能用一种排序代替其他排序。相同查询的并发进程通过锁等待同一结果，不同查询不互相锁定；等待超时不发送重复请求。

执行输出的 execution.mode=fresh/reused、original_checked_at 和 elapsed_ms 说明本次是否请求上游。复用结果保留原 checked_at 和有效期，不声称重新读取了平台。失败刷新会使旧复用入口失效，原研究来源与报价快照继续保留。包装计时从 handler 开始，不包含此前的解释器启动、模块导入和结果输出；阶段计时可覆盖这些额外等待。

直接使用宿主网页或 MCP 时，优先读查询记录与已归档来源。同一已回答问题不重复查询；主题不同、证据不足、日期不适用或资料过期时再查。需要机械匹配时使用：

```bash
python3 skills/travel-planning/scripts/research_workspace.py query-reuse \
  --workspace ".travel-research/<trip-id>" --provider xiaohongshu \
  --tool "本次实际工具名" --parameters-file ".travel-research/<trip-id>/public-query-parameters.json" \
  --phase research
```

参数文件只保存公开、非敏感输入，查找与 research_contract.queries 或任务 research_queries 中的原参数精确匹配。reusable=true 只返回近期已读来源的引用，不代表获取了新证据；读取相应来源摘要后判断能否回答当前问题。没有匹配记录就正常研究，不能借此宣称平台无结果。新来源与查询及时归档，checked_at 保留含时区的实际查询时刻，不能用复用时刻或当天日期重写。

Python 无法自动拦截宿主直接工具调用，直接调用的逐次耗时和请求数不自动进入包装日志；记录阶段时间，查询记录按实际调用保留。阶段汇总只统计已记录的调用，不把包装日志当作全部平台请求。

## 阶段耗时

建档后立即开始预研计时。研究、路线方案、编排、视觉制作和交付前后记录阶段；进入用户确认等待时结束当前阶段，单独记录 waiting_for_user，收到回复再结束等待并开始下一阶段。建议阶段为 discovery、route_proposal、preflight、research、planning、visuals、delivery；需要区分景点、餐饮等瓶颈时使用 attractions、food、lodging、transport、weather、readiness。只按真正的阶段边界记录，不为每个小步骤额外调用计时命令。

```bash
python3 skills/travel-planning/scripts/research_workspace.py timing \
  --workspace ".travel-research/<trip-id>" --action start --phase research
# 使用返回的 id，阶段结束时执行：
python3 skills/travel-planning/scripts/research_workspace.py timing \
  --workspace ".travel-research/<trip-id>" --action finish --span-id "返回的id"
python3 skills/travel-planning/scripts/research_workspace.py timing \
  --workspace ".travel-research/<trip-id>" --action report
```

查询与工作区命令使用 --phase 标记当前阶段，使工具计时与阶段对应。带 workspace 的包装查询、归档、提交与合并自动计时；装配自动记录 assembly，审查和渲染从输入路径寻找 workspace，或显式传 --workspace，分别记录 audit/rendering。离开 workspace 的独立渲染不伪造行程计时。模型阅读、思考、宿主工具调用和生图只有对应阶段标记后才有阶段耗时，不能从命令耗时推测出来。

报告写入 state/execution-report.json，含阶段 elapsed、命令与查询耗时、已记录的新查询/复用/失败次数、用户等待时间及未结束记录。区间并集合计避免并行与嵌套重复计时；work_ms 是各调用工作量之和，不能当总等待时间。汇总 measured_phase_elapsed_ms 仅覆盖已标记阶段并扣除显式用户等待，不声称是未记录部分在内的完整运行时间。未结束记录仍列为 running，失败保留，不为好看删除慢调用或补造历史计时。

阶段较慢时先用报告区分上游等待、重复查询、模型研究和视觉制作，再调整调用范围；没有真实计时，不承诺节省百分比。

## 存储边界

state/execution/spans 保存独立计时记录，cache 保存脱敏成功结果与短时运行记录，locks 只协调同查询进程。工具脚本维护这些文件，Agent 不手工覆盖它们；并行任务仍只提交自己拥有的结果与来源。日志不保存提示词、关键词、URL、Cookie、Key、Token 或上游错误正文。复用载荷发现敏感字段或签名 URL 时不留存，临时媒体仍使用现有用户私有缓存。

执行记录不参与 canonical research 状态摘要、用户行程、插件分发或公开分享；不建立跨行程缓存，不新增网络服务、凭证或自动调度。历史工作区可以开始记录后续执行，不为之前的步骤补造耗时。
