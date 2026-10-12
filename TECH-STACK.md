# 调用范围与计时（2026-10-12）

复用 Python 标准库的 JSON、SHA-256、monotonic、UTC 时间和原子文件替换；每次调用单独保存计时文件，避免并行任务覆盖一份汇总。相同查询用本机 fcntl（POSIX）或 msvcrt（Windows）文件锁协调，报告再聚合；没有跨设备或分布式协调。短时缓存只存供应商既有脱敏结果，不新增依赖、凭证、数据库或后台调度。完整运行耗时需阶段标记，脚本无法自动测量宿主直接工具调用与模型思考。

# 多来源研究选型（2026-10-11）

公开研究复用宿主提供的网页搜索、页面读取与必要浏览器，不新增搜索服务、依赖、凭证或全局配置。Python 原生模块检查来源闭合、身份、查询覆盖、保守路线估算与推荐契约；Agent 负责原文阅读、适配判断和语义审查。辅助 MCP 按需增强，供应商 preflight 不等于宿主能力检查。

# 当前技术与第一阶段取舍

- 已暂停的实验美食来源（不注册 MCP）：标准库实现五个工具的 stdio MCP，Playwright Python 1.63.0 + 本机 Google Chrome 做专用浏览器只读页面适配。Playwright 只在用户 venv 按需安装，无全局依赖、数据库或新 HTTP 服务；不整体部署通用采集 Server。搜索字段、点评 DOM 与故障边界参考已核查的 cn-scraper-mcp、mcp-dianping、travel-agent；具体版本与真实账号验收缺口见 food-discovery 的 integrations.md。普通旅行规划仍可在该来源未安装时使用其他数据源。

- 规划、审查、渲染与发布：Python 3.10+。发布脚本使用标准库处理 ZIP、SHA-256、HTTPS 和状态文件。默认 CA 缺失时可读取本机已安装的 certifi 根证书包；不自动安装、不替换显式 CA 配置、不关闭 TLS 校验。
- 页面：沿用 Vue 3、Vite 8；发布为内联 JS/CSS 的 HTML，线上不需要应用服务器或数据库。
- 二维码：qrcode-generator 2.0.4，MIT；在构建时内联，不把行程 URL 发给第三方服务。
- 默认托管：Netlify 整站 ZIP API，一个固定项目承载 `/trips/<trip-id>/` 多份行程；每次上传包含所有已发布页面。保留旧三文件单行程格式供迁移与兼容，也支持手工上传完整 public/。用户审核本次 HTML 并明确确认发布前不调用发布 API，也不创建部署预览。Cloudflare 或自有静态托管可以复用 public/，目前没有发布脚本适配器。
- 持久化：JSON 与不可覆盖的单行程/整站快照；站点状态记录最后验证的基线与部署，单进程文件锁和线上摘要检查防止本地竞争与过期覆盖。没有跨设备原子锁。暂不引入账户、数据库、服务端认证或 Service Worker；本次复用标准库，无新增依赖。
- 发布配置：项目根目录 `.env`（权限 0600）保存 `NETLIFY_AUTH_TOKEN` 与 `NETLIFY_SITE_ID`，标准库解析字面值，不执行 shell 或引入 dotenv 依赖。分发只带 `.env.example`；既有环境变量仍可作为兼容入口。Skill 默认只交付本地 HTML，每版经用户审核并确认发布后才驱动发布脚本，不是文件监控或后台任务。

参考依据：Netlify 官方 API 文档与 qrcode-generator 上游源码、包信息。浏览器目标保持 es2015；发布后的大陆手机网络和微信 WebView 仍需要真实设备验收。

游客展示继续使用现有 Python 渲染器、Vue 与原生 `details/summary`，无新增依赖。展开状态仅用于当前页面阅读；PDF 继续调用浏览器打印：Python 派生随身手册，Vue 选择简版或完整版，复用现有二维码库生成地点二维码；打印后恢复阅读状态，无新增 PDF 服务或依赖。没有新增预订同步或待办持久化。

- 小红书图片准备：Python 标准库从上游 imageList 按需读取，限制来源、文件大小和重定向；视觉理解使用 Agent 现有看图能力，不增加 OCR 或多模态 API 依赖。

水彩手账沿用 Python 标准库、Vue/Vite 与原生 SVG，无新增依赖。Agent 使用环境现有的图片生成能力（原生 image_gen 可用时优先）产生位图，系统图片工具可压缩交付副本；不内置付费 API、密钥或生图后端。`web/src/notebook.css` 编译进随插件分发的 CSS，普通渲染只读编译资源；本地位图和图注通过 manifest 内联，重复 raster 使用 SVG symbol 复用，支持无脚本。
