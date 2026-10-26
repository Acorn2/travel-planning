# 工具路由

服务地址由插件固定为 `http://127.0.0.1:18061/mcp`。旅行研究优先使用下列只读工具：

| 意图 | MCP 工具 | 关键参数 |
| --- | --- | --- |
| 检查登录 | `check_login_status` | 无 |
| 获取扫码二维码 | `get_login_qrcode` | 无；二维码只用于当次登录，不归档 |
| 首页候选 | `list_feeds` | 无 |
| 搜索笔记 | `search_feeds` | `keyword`；可选 `filters.sort_by/note_type/publish_time/search_scope/location` |
| 笔记详情与评论 | `get_feed_detail` | `feed_id`、`xsec_token`；MCP 原生默认前10条，旅行包装命令默认 `load_all_comments=true, limit=20, click_more_replies=true, reply_limit=50, scroll_speed=normal`；参数是加载目标，不保证覆盖全部讨论 |
| 用户主页 | `user_profile` | `user_id`、`xsec_token` |

包装命令 `xhs-detail` 返回标准化 comments、review_threads、thread_manifest 与 comment_analysis。v2 的 comment_insights/needs_recheck 为空，规则只提示阅读顺序，不自动生成共识或有价值数量；Agent 通过 xhs-review 阅读本地讨论串并提交语义洞察。

锁定版本的实际详情结构为 `data.comments.list[]`，其中一级评论通过 `subComments[]` 携带回复；不得将 `comments` 对象误读为空数组。`reply_limit` 是跳过过多回复讨论串的阈值，不是每串读取条数；超过50且影响判断的讨论可在预算内显式提高 `--reply-limit`（最高1000），无法读完须保留缺口。`comment_collection.unexpanded_reply_threads` 记录返回样本中回复未读全的串数；它不代表整个评论区的缺口总量。

图片笔记沿用详情中的 `imageList`。包装命令 `xhs-detail` 输出图片数量与序号，临时链接只在用户私有缓存保存；`xhs-images --note-id <id> --indices 1 2 3` 按需准备本地图片，再用 `view_image` 看图。没有正文也要判断是否需要图片内容；预算、复用和归档遵循 [图片笔记](image-notes.md)。

旅行发现必须先分别调用 `xhs-discover --destination "宜昌" --topic eat --workspace <工作区>`、`xhs-discover --destination "宜昌" --topic play --workspace <工作区>`；不要默认加旅行天数或先定菜品/景点。具体对象由用户指定或已读结果发现后才补查。直接 MCP 搜索仅用于底层诊断。包装命令默认不传 `filters`，以免触发不必要的筛选控件。结果列表中按主题相关性、作者独立性和内容角度选笔记，再用 `xhs-detail --note-id <搜索所得ID>` 核对正文、必要图片、评论及发布时间。需要筛选时，上游中文枚举为：排序 `综合/最新/最多点赞/最多评论/最多收藏`，类型 `不限/视频/图文`，时间 `不限/一天内/一周内/半年内`，范围 `不限/已看过/未看过/已关注`，位置 `不限/同城/附近`；默认的 `综合` 与 `不限` 字段必须省略。

包装代码自动恢复上游报告的超时与筛选控件失败：忽略缓存、实时复查登录，等待2秒后只重试一次；超时保留原参数，筛选控件失败则省略所有筛选。总计最多两次搜索，不在 Agent 层再次叠加重试。降级时 `query.requested_filters` 保留原请求，`query.filters` 表示实际采用条件，`warnings` 提示需要从详情核对；降级结果不缓存成原筛选查询。`search_attempts` 同时保留首次失败和最终结果，在命令输出及工作区搜索记录可查。登录失效、人工验证、风控、限流、连接失败、格式异常及真实空结果都不自动重复查询。

以下均是写操作，只有用户明确要求相应动作时才能调用：`publish_content`、`publish_with_video`、`post_comment_to_feed`、`reply_comment_in_feed`、`like_feed`、`favorite_feed`。`delete_cookies` 会清除本地登录态，也需要用户明确要求。
