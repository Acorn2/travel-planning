# Travel Planning 开发约定

此项目交付可安装的旅行规划插件及单文件行程页面；源码入口 `plugins/travel-planning/`，主 Skill 位于 `skills/travel-planning/SKILL.md`。

本仓库的旅行规划执行以 `plugins/travel-planning/skills/travel-planning/SKILL.md` 源码为准。本项目新增或修改旅行行程，通过事实/语义审查和页面验证后，先交付本地 `travel-plans/<trip-id>/index.html` 供用户审核，默认不发布。此前“后续完成版自动发布”的长期授权已撤销；每次必须等用户审核当前 HTML 并明确确认发布，才由 Skill 发布到固定 Netlify 项目 `dd93dae3-2d4e-4a15-8df6-3ddb8744e95b`（`https://merry-sunshine-eaa71d.netlify.app/`）。确认只适用于本次审核的行程版本；确认后内容再修改须重新审核确认。发布操作由 Skill 执行，无需用户手动运行命令；授权不包括改站点、创建项目、改可见性/域名/团队设置、删除行程或发布非旅行内容。

- 后续行程默认复用用户已确认的“水彩旅行手账”：暖纸底、水彩封面、日期路线叙事、体验明信片和少量美食贴纸。标准 Skill 流程必须实际生成并查看本次行程图片，按 `plugins/travel-planning/skills/travel-planning/references/visual-storytelling.md` 归档素材与清单；不把宜昌素材或 `designs/` 变成通用依赖。维护视觉优先改 `web/src/notebook.css`，保留完整执行信息、无脚本降级和双版本打印。
- `web/src/` 是交互和样式源，运行 `npm run build --prefix plugins/travel-planning/web` 同步 `skills/travel-planning/assets/frontend/`；普通渲染只用内联编译资源，不依赖设计目录或现场构建。
- 正式结果是 `travel-plans/<trip-id>/index.html`，研究数据在 `.travel-research/<trip-id>/`，设计预览在 `designs/`，临时验证在 `.tmp/`。这些行程目录不随插件分发。
- 项目宣传与 README 只展示指定的宜昌 Demo `https://merry-sunshine-eaa71d.netlify.app/trips/yichang-2026-10-15/`，不链接或罗列作者的调试行程历史。对外截图保存在 `assets/readme/`，随 README 进入安装包；个人原型仍留在忽略的 `designs/`。宣传链接使用详情路径，不改站点根入口；其他用户安装插件时仍绑定自己的站点，不沿用本项目示例站点。
- 发布流程以 `skills/travel-planning/references/publishing.md` 为准；默认一个站点承载多份行程，线上路径为 `/trips/<trip-id>/`，公开的 `/history/` 列出已发布行程，详情页不提供历史列表入口，根网址保留第一份行程。`prepare-site` 从最后验证的快照累积所有已发布页面，整站包在 `travel-plans/_site/releases/`，状态在 `.travel-research/publication/site.json`；不可用单行程包覆盖整站。旧单行程发布状态与快照保留供迁移和回退，不得把 README 历史 Demo 当成默认发布目标。
- 每次发布成功的用户回复必须同时给出历史列表 `/history/` 的完整 HTTPS 地址和本次行程 `/trips/<trip-id>/` 的完整 HTTPS 分享地址；只给其中一个不足以让用户找到全部规划。
- 发布凭证使用本项目根目录 `.env`，只填 `NETLIFY_AUTH_TOKEN` 与 `NETLIFY_SITE_ID`；真实文件忽略且不得进入插件包或公网页面，分发仅带 `.env.example`。发布器根据 `.travel-research/` 状态路径定位项目，避免误读插件目录配置。Token 缺失时只提示在本机填写，不索取或打印凭证；不能把未完成的发布说成成功。
- 测试：`python3 -m unittest discover -s plugins/travel-planning/tests`；分享辅助函数：`node --test plugins/travel-planning/web/tests/share.test.js`。浏览器验证需覆盖窄屏、三视图、无脚本降级、分享与打印；公网可访问性需实际匿名访问。
- `scripts/package_plugin.py` 依据 Git 文件清单打包。新增 Skill 引用或资源须确认进入发布包；凭证、本地发布状态和研究数据不得进入包。
