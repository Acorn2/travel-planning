# Travel Planning Plugin

面向 Codex 的旅行研究与行程交付插件。它会先比较路线并让用户确认，再按需查询地图、交通、住宿、天气和社区体验，最后生成带来源的 `itinerary.json` 与可独立打开的响应式 `itinerary.html`。

## 在线 Demo

[查看宜昌峡江慢游｜武汉纯电自驾 5 日](https://merry-sunshine-eaa71d.netlify.app/trips/yichang-2026-10-15/)

这是本项目当前的宣传与演示入口，可体验水彩旅行手账、“玩什么·吃什么 / 全程概览 / 每日行程 / 出发准备”四个视图、外部地点导航、行程分享与两种 PDF 打印版本。页面中的 AI 插画已标注为旅行氛围示意，不是景点实景。

[查看全部已发布行程](https://merry-sunshine-eaa71d.netlify.app/history/)。每份行程都有独立分享地址；站点根网址保留第一份行程，宣传宜昌示例时使用上面的详情链接。

Demo 用于展示插件的页面交付形态，其中的开放时间、价格、天气和交通状态属于生成时的快照，不能作为当前出行确认依据。

插件不会代订、占座、付款、发送消息或修改订单。价格、余票、库存、开放时间和天气等动态信息只表示查询时点的快照，最终下单前仍需回到官方或供应商页面确认。

当前已验证的安装主路径是 Codex 本地 marketplace。仓库同时保留 Claude Code 与通用宿主所需的清单，方便后续适配，但不把它们表述成已经验收的一键安装流程。

## 能力概览

| 能力 | 数据源与实现 | 是否需要凭证 |
| --- | --- | --- |
| 公开网页研究 | 宿主网页搜索与原页面阅读、证据归档和冲突核验 | 取决于宿主，无插件专用 Key |
| 完整行程规划 | `travel-planning` Skill、结构化研究工作区、审查器和响应式页面渲染器 | 否 |
| 中国境内地点与路线 | 高德地图官方 MCP | `AMAP_API_KEY` |
| 航班、火车和旅行产品；具体酒店按需查询 | 飞猪 FlyAI 官方 CLI | `FLYAI_API_KEY` |
| 航班运行、价格、铁路和空铁联运 | 飞常准 Aviation 与 Tripmatch MCP | `VARIFLIGHT_API_KEY` |
| 近期玩法、美食和避坑 | 固定版本 `xpzouying/xiaohongshu-mcp`，本机独立浏览器 | 需要用户本人扫码登录 |
| 境外地点与近期天气 | OpenStreetMap、Open-Meteo | 否 |

插件统一安装、能力发现、权限边界和旅行编排；各来源使用对应的官方 CLI、MCP、公开 API 或明确标注的本地页面自动化。

调用按[执行效率规则](plugins/travel-planning/skills/travel-planning/references/execution-efficiency.md)选择范围：preflight 只检查 --source/--require 指定来源，不默认探测全部供应商。带 workspace 的包装查询在同一行程短时复用同条件有效结果，--refresh 强制重新请求，保留原查询时间与报价有效期。timing 记录阶段边界并汇总执行报告，区分已记录工具耗时、复用、失败和用户等待；直接宿主工具与 AI 研究只在已记录阶段内计时，不宣称覆盖未记录步骤。

旅行研究默认使用宿主网页搜索与原页面阅读，主动获取官方资料、公开攻略、商家信息和亲历体验；小红书、高德均可用时仍执行网页研究。小红书补充近期玩法和餐饮体验，高德补充身份、入口及路线，两者是按需增强，未配置不阻止规划。来源按问题交叉核验、去重与处理冲突，不要求每个地点查询三个渠道。无法精算的路线记录依据、耗时区间及缓冲；影响安排的关键缺口须补查或调整。

交通、餐饮和天气按[决策重要性分配研究深度](plugins/travel-planning/skills/travel-planning/references/research-depth.md)：普通移动保留来源支持的耗时区间与缓冲，交通比较只查相关维度；普通餐有时间、具体区域、正餐供给与备用，核心特色餐再研究主备门店和路线；天气复用同地点日期结果，远期季节条件和天气敏感活动分开处理。小吃街可独立承载逛吃。图片笔记按需看图，原图不进入页面；旧结果保持可读。宿主需提供真实搜索与页面读取，Skill 不增加搜索 API、付费凭证或全局配置。

住宿默认推荐[区域范围和大概位置](plugins/travel-planning/skills/travel-planning/references/lodging-planning.md)，说明入住夜数、选址理由和预算预留；交通按区域估算，具体酒店由用户选择。默认不查酒店报价、库存、房型、图片或评论，不因住宿需求启用酒店供应商预检。用户明确要求酒店比选时才开启；已选酒店复用地址调整行程。区域住宿可完成规划，无需酒店 Key 或酒店快照。

抖音和大众点评暂停接入，未注册对应 MCP；实验代码保留供维护，不要求安装或登录。

查询按[跨渠道搜索设计](plugins/travel-planning/skills/travel-planning/references/search-design.md)从目的地攻略、玩法和美食推荐发现选择，再核对具体对象与执行条件；网页和小红书使用自然的单一问题，高德附近餐饮使用坐标与半径。新工作区逐次保留平台、工具、非敏感输入和结果取舍，审查来源归属；旧行程兼容读取，查询语义与推荐价值仍需人工审查。

## 快速接入 Codex

以下命令均从仓库或解压包的根目录执行；可选步骤也不需要切换到插件子目录。

### 1. 准备运行环境

请先准备：

- 支持 `codex plugin` 命令的 Codex CLI
- Python 3.10 或更高版本
- Node.js 22.14.0 或更高版本，并确保 `npm`、`npx` 可用
- 一个不会被临时清理的仓库目录或解压目录

Node.js 22.14.0 覆盖了高德、飞猪和飞常准三个 Node Provider 的运行要求。小红书的预编译工具目前支持 macOS Apple Silicon 和 Linux x86_64；不使用小红书时不影响其他能力。

### 2. 注册 marketplace 并安装插件

拿到本仓库或发布包并进入它的根目录。该目录下应同时存在 `.agents/plugins/marketplace.json` 和 `plugins/travel-planning/`。

```bash
cd /absolute/path/to/travel-planning
codex plugin marketplace add "$PWD" --json
codex plugin add travel-planning@local --json
```

检查安装结果：

```bash
codex plugin list
```

列表中应出现 `travel-planning@local`，状态为 `installed, enabled`。安装或升级后请新建一个 Codex 会话，让新 Skill 与 MCP 配置从干净上下文加载。

如果 `local` marketplace 已经注册，无需重复执行 `marketplace add`。拉取新版仓库或替换发布包后，重新执行 `codex plugin add travel-planning@local --json`，然后新建会话即可。

### 3. 配置需要的数据源

数据源按需启用，不必一次配齐。OpenStreetMap 和 Open-Meteo 无需 Key；高德、飞猪和飞常准分别只接收自己的凭证。

推荐把配置放在用户目录，避免凭证进入仓库或分发包：

```bash
mkdir -p ~/.config/travel-planning
cp plugins/travel-planning/config/sources.example.env \
  ~/.config/travel-planning/sources.local.env
chmod 600 ~/.config/travel-planning/sources.local.env
```

编辑 `~/.config/travel-planning/sources.local.env`，只填写需要启用的项目：

```dotenv
AMAP_API_KEY=
FLYAI_API_KEY=
VARIFLIGHT_API_KEY=
```

凭证申请入口：

- 高德 Web 服务 Key：<https://console.amap.com/dev/flow/detail?type=0>
- 飞猪 FlyAI：<https://flyai.open.fliggy.com/console>
- 飞常准：<https://ai.variflight.com/keys>

如需使用其他文件，可在启动 Codex 前设置 `TRAVEL_SOURCES_CONFIG=/absolute/path/to/sources.local.env`。源码开发时也可使用 `plugins/travel-planning/config/sources.local.env`；该文件已被 Git 忽略，且不会进入安装包。

### 4. 可选启用小红书

小红书使用固定版本的本机 HTTP MCP 和独立浏览器，不依赖 Chrome 扩展。首次使用时运行：

```bash
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py install
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py login
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py start
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py status
```

`login` 会打开上游登录工具，扫码和账号确认必须由用户本人完成。二进制、Cookie、日志与进程状态保存在 `~/.local/share/travel-planning/xiaohongshu-mcp/`，不会写入插件源码。

后续常用命令：

```bash
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py start
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py stop
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py logs
```

### 5. 运行接入检查

先查看本机具备哪些来源，再明确选择需要检查的来源：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/research_sources.py capabilities
python3 plugins/travel-planning/skills/travel-planning/scripts/research_sources.py preflight \
  --city "上海" --source amap-maps --source xiaohongshu
```

只探测 --source 或 --require 选择的来源；未选择的不探测，也不判为不可用。选定的可选来源不可用或降级不阻止其他来源工作。若某次行程必须依赖特定来源，可重复添加 --require；任何必需来源失败时，命令都会以非零状态退出。无选择时返回 not_requested；接入全部诊断可显式传 --all-sources，不用于默认规划。

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/research_sources.py preflight \
  --city "上海" \
  --require amap-maps \
  --require xiaohongshu
```

capabilities 只用于了解配置，不能代替真实调用成功的证据。preflight 新检查按来源执行协议与只读上游探测；带 workspace 时也可复用近期成功业务调用，scope=observed_operation 不表示完成全部握手或所有产品核验。

### 6. 在新会话中使用

完整行程优先调用主 Skill：

```text
使用 $travel-planning 帮我规划 10 月 1 日到 10 月 6 日从北京出发的成都、重庆行程。
2 人，预算 12000 元，偏好美食和历史，节奏不要太赶。先给我 2～3 个路线方案，等我确认后再深度调研并生成页面。
```

也可以直接调用单一数据源：

```text
使用 $amap-maps 比较成都东站到宽窄巷子的地铁和打车路线。
使用 $flyai 查询指定日期北京到成都的航班候选，只做只读比较。
使用 $variflight 核验 3U8882 的运行与舒适度信息。
使用 $xiaohongshu 搜索近期成都早餐体验，不发布、不点赞、不评论。
```

主 Skill 默认先确认路线，再进行深度研究。路线确认后，对已配置来源的只读查询无需逐次授权；登录、验证码、付费凭证、预订、付款和任何外部写操作仍由用户掌控。

## 规划与交付流程

```text
旅行需求
  → 2～3 个路线方案
  → 用户确认路线
  → 选定来源，按需预检或直接业务查询
  → 网页研究 + 按需社区、地图补充与证据归档
  → 合并本次行程的共享实体
  → itinerary-plan.json
  → itinerary.json 审查
  → itinerary.html 渲染
```

每次行程的数据都保存在当前项目的 `.travel-research/<trip-id>/`，不会建立跨行程缓存。最终交付位于：

```text
.travel-research/<trip-id>/artifacts/itinerary.json
.travel-research/<trip-id>/artifacts/audit.json
.travel-research/<trip-id>/artifacts/itinerary.html
```

页面内联交互脚本和样式，可直接打开或作为单个 HTML 文件分享。JavaScript 被附件预览禁用时，页面仍可通过纯 CSS 完成主要视图切换。

新生成的行程默认使用用户确认的“水彩旅行手账”UI：暖米白纸底、水彩封面、日期路线叙事、体验明信片与少量美食贴纸拼贴，面向手机阅读和小红书攻略收藏。默认先看“玩什么·吃什么”，再进入全程概览、每日行程和出发准备；任意目的地与天数复用通用模板。标准 Skill 流程在事实审查后实际生成并查看水彩图片，将提示词、原图和视觉清单归档，通过 `--visuals` 内联到 HTML；同一位图仅内联一次。AI 图明确标注非景点实景/非实拍菜品，不能创建旅行事实。图片能力不可用时可交付可读草稿，但须明确缺少图片。每日执行信息、候选状态、完整表格、来源、无脚本阅读、分享和两种 PDF 沿用原有流程。设计内置于渲染器及编译资源，普通生成不依赖设计目录或现场构建；先交付本地 HTML 审核，未经当前版本发布确认不上线。详见 [视觉叙事与生图](plugins/travel-planning/skills/travel-planning/references/visual-storytelling.md)。

为方便找到并分享结果，将渲染后的 HTML 复制成项目内可见的静态目录：

正式页面统一存放在 `travel-plans/<trip-id>/index.html`，不散放到项目根目录。行程目录沿用研究工作区 ID，新建时采用“小写英文或拼音目的地 + 出发日期”，例如 `travel-plans/wudangshan-2026-10-06/index.html`；文件入口固定为 `index.html`，中文行程名称显示在页面标题中。同一行程更新时复用该路径。

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/prepare_share.py \
  .travel-research/<trip-id>/artifacts/itinerary.html \
  --output-dir travel-plans/<trip-id>
```

页面保留“保存 PDF”与本地 HTML 备份。推荐的随身简版包含每日安排、主推餐厅、地址、关键预约和地点二维码；完整资料版包含所有备选与核验依据。选择后通过系统打印窗口保存 PDF，地图和二维码需要联网。手机分享使用一个 Netlify 站点承载多份行程，每份行程的固定链接为 `/trips/<trip-id>/`。`/history/` 列出所有已发布行程，点击可进入详情；Skill 每次发布成功都会把历史列表和本次行程的完整网址一起告诉规划者。该列表也是公开页面，分享单份行程时使用对应详情链接，未发布草稿不会进入列表。具体操作见[手机分享与发布](plugins/travel-planning/skills/travel-planning/references/publishing.md)。

### 一次配置 Netlify，每版审核后由 Skill 发布

新增或修改行程时，Skill 默认先交付本地 `travel-plans/<trip-id>/index.html` 供审核，不自动上传。打开 HTML 检查内容，有调整就继续在本地修改；满意后明确回复“确认发布这一版”，Skill 才部署到已配置站点。只认可内容或曾经授权自动发布，不替代本次确认；确认后内容再修改须重新审核确认。

1. 登录 Netlify，打开 [Personal access tokens](https://app.netlify.com/user/applications#personal-access-tokens)，选择 **New access token**，填写名称和有效期，再生成 Token。生成后只在本机保存，不发到聊天中。申请流程见 [Netlify 官方认证说明](https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/#authentication)。
2. 在 Netlify 目标项目的 `Project configuration > General > Project details > Project information` 复制 **Project ID**。保留同一个项目，之后所有行程共用它。
3. 在旅行项目根目录将 [.env.example](.env.example) 复制为 `.env`。已有 `.env` 时只补充以下字段，不覆盖其他配置。把刚才的 Token 和 Project ID 分别填在等号后：

   ```dotenv
   NETLIFY_AUTH_TOKEN=
   NETLIFY_SITE_ID=
   ```

   本仓库的 `.env` 已被 Git 忽略，不随插件打包；在其他旅行项目使用时也需将 `.env` 加入该项目的 `.gitignore`。发布时只上传审阅包中的页面和版本文件。无需 `export`，也无需把 Token 放进 Python 命令。发布器只读取这两个字段，不执行 `.env` 内的 shell 语句；文件中的非空值优先，仍兼容已设置的进程环境变量。
4. 每次收到本地 HTML 后，先审核当前内容，满意后明确回复，例如：**“这版 HTML 已审核，确认发布到已配置的 Netlify 站点。”** 配置 Token 本身不等于公开授权。确认后由 Skill 完成打包、部署和匿名验证，最后给出可分享链接，不需要用户手动执行 Python。没有本次确认就保留本地版；没有配置或发布失败时会说明原因，不宣称已上线。

**在哪里查看自己的历史记录：**首次行程成功发布后，发布器会读取该用户 Netlify 项目实际返回的 HTTPS 域名，并在结果里同时输出“全部规划历史：`https://<该用户的站点域名>/history/`”和本次行程的独立链接；Skill 应将这两个实际网址直接发给用户，不能沿用本仓库示例站点的域名。只完成 Netlify 配置、还没有成功发布行程时，历史页尚不存在。以后忘记地址，可让 Skill 查询“我的历史记录链接”，或从项目根目录运行 `python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py status --state-file .travel-research/publication/site.json` 重新验证并打印当前地址；`status` 不会重新上传。

修改同一行程时先更新本地 HTML，用户审核并确认发布后才更新线上原路径的内容；链接和二维码地址保持不变，同行者刷新即可查看。其他行程保留；本地历史发布快照仍可审阅和用于回退。审核期间的本地修改不上传，每次实际部署仍计入托管用量。

从仓库根目录或插件目录运行均可：发布器根据 `.travel-research/` 状态文件找到旅行项目根目录 `.env`；自定义状态目录时可传 `--env-file /absolute/path/to/.env`。不要误把 Token 填进前端的 `VITE_*` 变量。

<details>
<summary>开发与排障：发布器命令</summary>

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py prepare \
  travel-plans/<trip-id>/index.html --trip-id <trip-id> \
  --output-dir travel-plans/<trip-id>/releases/<YYYYMMDD-HHMMSS>
```

用 `prepare-site <单行程快照> --output-dir travel-plans/_site/releases/<release-id>` 生成完整站点包。后续新增或更新时加 `--state-file .travel-research/publication/site.json`，自动合入最后验证的全部行程。首次迁移已有单行程站点时，用 `--base-release <当前线上单行程快照>`；旧根页面保留，新增独立行程路径，不改变域名。

首次 `publish <整站快照> --state-file .travel-research/publication/site.json --confirm-public` 从 `.env` 读取站点 ID 并绑定站点；后续继续复用相同状态。也可显式传入 `--site-id <Project-ID>`，与既有绑定不同会停止。只有用户另行授权创建全新站点才用 `--create-site`。同一包重复执行只验证。每次都须用户审核当前 HTML 并明确确认发布后，才由 Skill 执行 publish；`--confirm-public` 代表本次确认，不能根据历史授权自动添加。脚本核对所有公网页面后才报告正式 URL。

也可在原 Netlify 项目的 Deploys 页上传整站快照中的完整 `public/`；不能只上传新行程文件夹，否则会覆盖其他页面。只上传白名单页面和版本文件，不上传研究工作区、状态、整个项目或 release 根目录。本地或部署预览不提供正式分享链接；本地验证不能代替真实手机网络和微信内访问验收。
同一行程重新生成时，`prepare_share.py` 默认保护已有且内容不同的 `index.html`；确认要更新该行程的分享文件后，加 `--replace`。

</details>

## 仓库结构

```text
.
├── .agents/plugins/marketplace.json       # Codex marketplace 入口
├── .codex/config.toml                     # 当前仓库的开发启用配置
├── .env.example                          # Netlify 发布配置模板；真实 .env 不分发
├── plugins/travel-planning/
│   ├── .codex-plugin/plugin.json          # Codex 插件清单
│   ├── .claude-plugin/plugin.json         # Claude Code 兼容清单
│   ├── plugin.json                        # 可移植清单
│   ├── .mcp.json                          # MCP Server 声明
│   ├── config/sources.example.env         # 凭证模板
│   ├── scripts/providers/                 # Provider 启动器
│   ├── skills/                            # 主 Skill 与数据源 Skills
│   └── web/                               # Vue/Vite 页面前端
├── scripts/package_plugin.py
└── Makefile
```

插件包含六个 Skill：`travel-planning` 负责跨来源编排，`xiaohongshu`、`flyai`、`amap-maps`、`variflight` 负责对应数据源，`food-discovery` 保留已暂停的实验美食来源说明，不作为默认规划依赖。插件清单注册高德、飞常准 Aviation、飞常准 Tripmatch 和小红书四个 MCP Server；FlyAI 由主 Skill 通过固定版本 CLI 调用。

## 开发与验证

本节与下面的打包命令用于完整源码仓库；安装 ZIP 仅用于插件接入，不包含 Makefile 和仓库打包脚本。

修改 Vue 页面资源后先构建前端，再校验插件和测试：

```bash
make frontend
python3 /absolute/path/to/plugin-creator/scripts/validate_plugin.py \
  plugins/travel-planning
python3 -m unittest discover -s plugins/travel-planning/tests
node --test plugins/travel-planning/web/tests/share.test.js
```

`make frontend` 会把兼容转译后的脚本和样式写入 `skills/travel-planning/assets/frontend/`，Python 渲染器再将它们内联进最终页面。若本机没有 `plugin-creator`，至少运行测试，并确认三个插件清单、marketplace 路径和 Skill 名称保持一致。

## 打包与分发

```bash
make package
```

默认产物为 `output/travel-planning-marketplace.zip`：

```text
travel-planning-marketplace/
├── .agents/plugins/marketplace.json
├── plugins/travel-planning/
├── README.md
├── LICENSE
└── .env.example
```

接收方需要先把 ZIP 解压到持久目录，再按照“快速接入 Codex”中的命令注册该目录并安装 `travel-planning@local`。Codex CLI 接收的是 marketplace 目录，不是 ZIP 文件本身。

打包候选仅来自根目录的 README、许可证、发布配置模板、marketplace 清单和插件目录。已跟踪文件与未忽略的新文件都再次检查 Git ignore 规则，已跟踪的忽略文件也会排除。打包器独立过滤真实环境配置、Cookie、私钥、研究工作区、运行缓存、IDE 和系统文件，只保留 `.env.example`、`*.example.env` 配置模板。符号链接会使打包停止，资源需使用实际文件。凭证模板只能填写占位值，其他源码和文档仍须在提交前检查是否误写了敏感内容。

如需覆盖默认路径：

```bash
make package \
  PLUGIN_DIR=plugins/travel-planning \
  PACKAGE_OUTPUT=output/custom-name.zip \
  BUNDLE_NAME=custom-marketplace
```

## 常见问题

`codex plugin list` 中看不到插件

确认执行 `marketplace add` 时传入的是包含 `.agents/plugins/marketplace.json` 的根目录，而不是 `plugins/travel-planning/`。然后重新执行 `codex plugin add travel-planning@local --json`。

插件已安装，但当前会话找不到 Skill 或 MCP

安装或升级后新建 Codex 会话。旧会话不会可靠地重新加载插件能力。

Provider 提示缺少 Key

确认文件位于 `~/.config/travel-planning/sources.local.env`、变量名与模板一致，并且等号后没有多余引号或空格。也可以显式设置 `TRAVEL_SOURCES_CONFIG`。

小红书返回 `login_required`

先执行 `setup.py stop`，再执行 `setup.py login` 并由用户完成扫码，最后执行 `setup.py start`。不要在多个进程中同时操作同一个账号的登录态。

小红书 `stop` 提示进程身份不匹配

新启动会在本机状态目录同时记录 `service.pid` 与 `service.json`，停止前核对启动时间和固定版本服务命令。旧版仅有 PID 的运行记录、已复用的 PID 或无法核对的进程不会被自动停止。先由本人通过系统进程工具核对旧服务及路径，确认停止后再运行 `setup.py start`；不要直接按旧 PID 强制结束进程。

预检显示 `degraded`

先看具体来源的原因。未被 `--require` 声明的来源允许降级；行程仍可使用其他实时来源或生成带官方入口、查询条件和复核时间的手动检查项。

预检显示 not_requested

没有选择需要探测的来源，未发送上游请求。按本次研究需要传 --source 或 --require；不能把这个状态解释为所有工具已可用。

## 许可证

本项目原创内容采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，允许个人学习、研究、实验和其他非商业用途，禁止未经授权的商业使用。

项目引用或调用的第三方组件仍适用各自的许可证和版权声明，不因本项目许可证而改变。
