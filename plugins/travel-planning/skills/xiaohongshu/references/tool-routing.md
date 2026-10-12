# 工具路由

服务地址由插件固定为 `http://127.0.0.1:18060/mcp`。旅行研究优先使用下列只读工具：

| 意图 | MCP 工具 | 关键参数 |
| --- | --- | --- |
| 检查登录 | `check_login_status` | 无 |
| 获取扫码二维码 | `get_login_qrcode` | 无；二维码只用于当次登录，不归档 |
| 首页候选 | `list_feeds` | 无 |
| 搜索笔记 | `search_feeds` | `keyword`；可选 `filters.sort_by/note_type/publish_time/search_scope/location` |
| 笔记详情与评论 | `get_feed_detail` | `feed_id`、`xsec_token`；默认前10条一级评论；需要深入研究时使用 `load_all_comments=true`，并限制 `limit`、`click_more_replies`、`reply_limit` 和 `scroll_speed` |
| 用户主页 | `user_profile` | `user_id`、`xsec_token` |

包装命令 `xhs-detail` 除了返回标准化 `comments`，还返回 `comment_insights`、`needs_recheck` 和 `comment_analysis`。洞察只保留高价值评论形成的主题结论；冲突主题会降低置信度并进入复核列表。

图片笔记沿用详情中的 `imageList`。包装命令 `xhs-detail` 输出图片数量与序号，临时链接只在用户私有缓存保存；`xhs-images --note-id <id> --indices 1 2 3` 按需准备本地图片，再用 `view_image` 看图。没有正文也要判断是否需要图片内容；预算、复用和归档遵循 [图片笔记](image-notes.md)。

旅行检索默认调用 `xhs-search --keyword "宜昌 清江画廊 10月 游玩 避坑"`，包装命令默认不传 `filters`，以免触发不必要的筛选控件。结果列表中按主题相关性、作者独立性和内容角度选笔记，再用 `xhs-detail --note-id <搜索所得ID>` 核对正文、必要图片、评论及发布时间。需要筛选时，上游中文枚举为：排序 `综合/最新/最多点赞/最多评论/最多收藏`，类型 `不限/视频/图文`，时间 `不限/一天内/一周内/半年内`，范围 `不限/已看过/未看过/已关注`，位置 `不限/同城/附近`；默认的 `综合` 与 `不限` 字段必须省略。筛选失败后只对相同关键词做一次无筛选查询，不通过频繁换词或重复点击绕过平台限制。

以下均是写操作，只有用户明确要求相应动作时才能调用：`publish_content`、`publish_with_video`、`post_comment_to_feed`、`reply_comment_in_feed`、`like_feed`、`favorite_feed`。`delete_cookies` 会清除本地登录态，也需要用户明确要求。
