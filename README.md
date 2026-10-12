<div align="center">

# Travel Planning

**把旅行需求，变成一本有来源、能执行、可分享的旅行手账。**

A Codex plugin for travel research, itinerary planning, and watercolor travel journals.

[![Codex Plugin](https://img.shields.io/badge/Codex-Plugin-10a37f)](plugins/travel-planning/skills/travel-planning/SKILL.md) [![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab)](#运行要求) [![MCP](https://img.shields.io/badge/MCP-Integrated-236c48)](#数据源配置) [![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue)](LICENSE)

[**查看在线 Demo →**](https://merry-sunshine-eaa71d.netlify.app/trips/yichang-2026-10-15/) · [快速安装](#快速安装) · [开始使用](#开始使用)

<img src="assets/readme/demo-desktop.png" width="100%" alt="宜昌 Demo 桌面首页：水彩封面、出行信息和四个行程视图">

</div>

## 它能做什么

告诉 Codex 你从哪里出发、什么时候旅行、和谁同行，以及预算和偏好。插件先比较路线，等你确认后再调研，最后交付可以在手机上阅读、分享给同行者或打印随身携带的行程页面。

- **先选路线，再查细节。** 比较 2～3 个方案，结合时间、交通和旅行节奏做取舍。
- **研究有来源，安排能执行。** 阅读公开原页面，按需补充地图、交通和社区体验，记录地址、费用、预约、复核时间与备选。
- **把结果做成旅行手账。** 水彩封面、路线叙事、体验明信片和地方风味，配合全程概览、每日时间轴与出发准备。
- **交付由你掌控。** 默认给本地 HTML；审核后明确确认才发布。保留结构化 JSON、分享二维码与两种打印版本。

```mermaid
flowchart LR
  A[旅行需求] --> B[路线方案]
  B --> C[你确认路线]
  C --> D[检索与证据归档]
  D --> E[编排行程与审查]
  E --> F[本地旅行手账]
  F --> G[审核后按需分享]
```

## Demo 效果

[宜昌峡江慢游｜武汉纯电自驾 5 日](https://merry-sunshine-eaa71d.netlify.app/trips/yichang-2026-10-15/) 展示同一份行程在桌面和手机上的阅读方式。

<img src="assets/readme/demo-overview.png" width="100%" alt="宜昌 Demo 全程概览：按日期展示路线摘要和当天安排入口">

<table>
<tr>
<td align="center" width="50%"><strong>每日行程 · 手机</strong><br><br><img src="assets/readme/demo-mobile-day.png" width="320" alt="手机每日行程：按日期阅读游玩主题与时间安排"><br><sub>选中某一天，查看执行时间、地点与当天安排。</sub></td>
<td align="center" width="50%"><strong>出发准备 · 手机</strong><br><br><img src="assets/readme/demo-mobile-prepare.png" width="320" alt="手机出发准备：预约状态、办理期限与官方入口"><br><sub>把预约、购票和需要复核的事项集中查看。</sub></td>
</tr>
</table>

> 截图来自实际 Demo 页面。水彩插画为 AI 生成的旅行氛围示意，非景点实景或实拍菜品；Demo 中的价格、营业、天气和交通信息是生成时的快照。

## 快速安装

### 运行要求

| 环境 | 要求 |
| --- | --- |
| Codex CLI | 支持 `codex plugin` 命令 |
| Python | 3.10 或更高版本 |
| Node.js | 22.14.0 或更高版本，`npm`、`npx` 可用 |
| 宿主能力 | 网页搜索与原页面阅读；水彩图片需要可用的生图与看图能力 |
| 小红书（可选） | 固定版本预编译工具支持 macOS Apple Silicon、Linux x86_64 |

当前已验证的安装路径是 **Codex 本地 marketplace**。仓库保留 Claude Code 和通用宿主清单，尚未验收它们的一键安装流程。

### 安装到 Codex

克隆到持久目录，以下命令均从仓库根目录执行：

```bash
git clone https://github.com/Acorn2/travel-planning.git
cd travel-planning

codex plugin marketplace add "$PWD" --json
codex plugin add travel-planning@local --json
codex plugin list
```

列表中应出现 `travel-planning@local`，状态为 `installed, enabled`。**安装或升级后新建一个 Codex 会话**，让 Skill 与 MCP 配置重新加载。

<details>
<summary><strong>已经注册 marketplace，或使用安装 ZIP？</strong></summary>

如果 `local` marketplace 已注册，无需重复执行 `marketplace add`。拉取新版源码后重新执行 `codex plugin add travel-planning@local --json`，然后新建会话。

安装 ZIP 时，先解压到持久目录。该目录下应同时存在 `.agents/plugins/marketplace.json` 和 `plugins/travel-planning/`，再进入该目录执行安装命令；CLI 接收的是目录，不是 ZIP 文件。

</details>

## 开始使用

### 规划一趟旅行

在新会话里，把需求直接交给主 Skill：

```text
使用 $travel-planning 帮我规划 2026 年 11 月 1 日到 11 月 3 日的杭州旅行。
从上海出发，2 人，预算 5000 元，喜欢历史街区和当地美食，节奏不要太赶。

先给我 2～3 个路线方案，等我确认后再深度调研。
最后生成可以在手机阅读、分享和打印的旅行手账，先给本地 HTML 审核。
```

尽量说明出发地、日期、人数、预算、交通方式、偏好和硬性限制。路线确认后，插件才进入深度规划。

### 单独查询一种信息

| 你可以这样说 | 对应能力 |
| --- | --- |
| 使用 `$amap-maps` 比较成都东站到宽窄巷子的地铁和打车路线。 | 地点定位与路线比较 |
| 使用 `$flyai` 查询指定日期北京到成都的航班候选，只做只读比较。 | 航班、铁路和旅行产品搜索 |
| 使用 `$variflight` 核验指定航班的运行与舒适度信息。 | 航班运行、价格及空铁联运 |
| 使用 `$xiaohongshu` 搜索近期成都早餐体验，不发布、不互动。 | 社区体验与避坑线索 |

### 你会得到什么

| 交付物 | 内容 |
| --- | --- |
| 本地旅行手账 | `travel-plans/<trip-id>/index.html`，内联样式和交互，可独立打开 |
| 结构化行程 | `.travel-research/<trip-id>/artifacts/itinerary.json`，保留研究结果与执行安排 |
| 审查结果 | 同一 `artifacts/` 目录下的 `audit.json` |
| PDF | 通过页面“保存 PDF”选择随身简版或完整资料版，由浏览器打印生成 |
| 在线分享（可选） | 你审核并确认发布后，生成固定 HTTPS 链接与二维码 |

页面包含“玩什么·吃什么 / 全程概览 / 每日行程 / 出发准备”四个视图。禁用 JavaScript 时仍可阅读与切换主要视图；图片能力不可用时可交付明确标注的可读草稿。

## 数据源配置

**按需配置，不必一次配齐。** 公开网页研究是默认流程，高德和小红书用于补充，未配置它们也可以使用其他可用来源。

| 来源 | 用途 | 配置 |
| --- | --- | --- |
| 宿主网页搜索与阅读 | 官方资料、公开攻略、商家信息和原文核验 | 无插件专用 Key |
| 高德地图 | 中国境内地址、POI、路线、距离与天气 | `AMAP_API_KEY` |
| 飞猪 FlyAI | 航班、铁路、旅行产品；具体酒店按需比较 | `FLYAI_API_KEY` |
| 飞常准 | 航班运行与价格、铁路、空铁联运 | `VARIFLIGHT_API_KEY` |
| 小红书 | 近期玩法、美食和避坑体验 | 用户本人扫码登录 |
| OpenStreetMap / Open-Meteo | 境外地点与近期天气 | 无需 Key |

住宿默认提供区域范围、选址理由、入住夜数与预算预留；具体酒店由你选择，明确需要酒店比选时才查询报价和库存。抖音、大众点评实验接入当前暂停。

<details>
<summary><strong>配置高德、飞猪或飞常准凭证</strong></summary>

推荐放在用户目录，避免进入仓库或安装包：

```bash
mkdir -p ~/.config/travel-planning
cp plugins/travel-planning/config/sources.example.env \
  ~/.config/travel-planning/sources.local.env
chmod 600 ~/.config/travel-planning/sources.local.env
```

编辑该文件，仅填写需要使用的字段：

```dotenv
AMAP_API_KEY=
FLYAI_API_KEY=
VARIFLIGHT_API_KEY=
```

申请入口：[高德](https://console.amap.com/dev/flow/detail?type=0) · [飞猪 FlyAI](https://flyai.open.fliggy.com/console) · [飞常准](https://ai.variflight.com/keys)。

自定义配置文件时，在启动 Codex 前设置 `TRAVEL_SOURCES_CONFIG=/absolute/path/to/sources.local.env`。源码开发也可使用已被忽略的 `plugins/travel-planning/config/sources.local.env`。

</details>

<details>
<summary><strong>可选启用小红书</strong></summary>

它使用固定版本的本机 HTTP MCP 与独立浏览器，不依赖 Chrome 扩展。首次使用：

```bash
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py install
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py login
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py start
python3 plugins/travel-planning/skills/xiaohongshu/scripts/setup.py status
```

扫码与账号确认由你本人完成。二进制、Cookie 和运行状态保存在 `~/.local/share/travel-planning/xiaohongshu-mcp/`。

后续用 `setup.py start / stop / logs` 管理服务。若 `stop` 提示旧 PID 或进程身份不匹配，先通过系统进程工具核对旧服务，确认停止后再启动，不直接按旧 PID 强制结束进程。

</details>

<details>
<summary><strong>检查接入状态与排查降级</strong></summary>

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/research_sources.py capabilities
python3 plugins/travel-planning/skills/travel-planning/scripts/research_sources.py preflight \
  --city "上海" --source amap-maps --source xiaohongshu
```

`capabilities` 只说明配置与能力存在；`preflight` 只检查显式选择的来源。必须可用的来源使用 `--require`，失败时返回非零；可选来源允许降级。未选择来源返回 `not_requested`，不表示全部工具已可用。

插件已安装但会话中找不到 Skill：新建会话。提示缺少 Key：核对配置路径和变量名。小红书返回 `login_required`：先停止服务，再由本人登录，最后重新启动。

</details>

## 分享与使用边界

本地 HTML 无需发布即可使用。需要发给同行者时：

1. 在**自己的旅行项目**根目录将 [.env.example](.env.example) 复制为 `.env`，在本机填写 `NETLIFY_AUTH_TOKEN` 与 `NETLIFY_SITE_ID`。
2. 审核当前 HTML，明确回复“这版已审核，确认发布到我配置的 Netlify 站点”。
3. Skill 完成打包、部署与匿名验证，再交付可分享链接；修改内容后需要重新审核和确认。

你的站点由自己的配置决定，不使用本 README 的 Demo 站点。真实 `.env` 不提交、不分发，也不进入前端。完整设置和排障见 [发布指南](plugins/travel-planning/skills/travel-planning/references/publishing.md)。

插件不代订、占座、付款或修改订单。开放时间、价格、余票与天气仍需临行确认；来源缺口会保留状态、备选与复核动作，不把缺证信息当成已确认事实。

## 开发与贡献

安装插件不需要构建前端。维护源码时，使用仓库根目录的命令：

```bash
# 修改 web/src/ 后同步编译资源
make frontend

# Python 与分享逻辑回归
python3 -m unittest discover -s plugins/travel-planning/tests
node --test plugins/travel-planning/web/tests/share.test.js

# 生成本地 marketplace ZIP
make package
```

构建资源位于 `plugins/travel-planning/skills/travel-planning/assets/frontend/`。ZIP 用于安装，不包含仓库的 Makefile 和打包工具；README 配图会随包提供。打包会过滤真实配置、私有运行数据和忽略文件，符号链接会使打包停止。

<details>
<summary><strong>仓库结构与进一步阅读</strong></summary>

```text
.
├── .agents/plugins/marketplace.json   # Codex marketplace 入口
├── assets/readme/                    # 对外演示截图
├── plugins/travel-planning/
│   ├── .codex-plugin/plugin.json     # Codex 插件清单
│   ├── .mcp.json                     # MCP Server 声明
│   ├── config/sources.example.env    # 数据源凭证模板
│   ├── scripts/providers/           # Provider 启动器与实验适配器
│   ├── skills/                      # 主 Skill 与数据源 Skills
│   ├── tests/                       # Python 回归
│   └── web/                         # Vue/Vite 源码
├── .env.example                     # 仅含发布配置占位值
├── scripts/package_plugin.py        # 安装包生成器
└── Makefile
```

六个 Skill 中，`travel-planning` 负责跨来源编排；`xiaohongshu`、`flyai`、`amap-maps`、`variflight` 负责专用数据源；`food-discovery` 保留已暂停的实验来源说明。

- [主 Skill](plugins/travel-planning/skills/travel-planning/SKILL.md)：完整执行流程。
- [研究工作流](plugins/travel-planning/skills/travel-planning/references/research-workflow.md)：工作区与证据归档。
- [研究深度](plugins/travel-planning/skills/travel-planning/references/research-depth.md)：按决策重要性安排研究。
- [视觉叙事](plugins/travel-planning/skills/travel-planning/references/visual-storytelling.md)：生图、看图与素材归档。
- [交互页面](plugins/travel-planning/skills/travel-planning/references/interactive-page.md)：静态阅读、渐进增强和打印。
- [行程结构](plugins/travel-planning/skills/travel-planning/references/itinerary-schema.md)：机器契约与结果规范。

`plugin-creator` 可用时，也可运行它的 `validate_plugin.py plugins/travel-planning`。提交前检查源码与文档中的凭证、个人数据和截图；保留相关回归证据。欢迎提交能复现的问题、安装反馈或附有验证结果的改进。

</details>

## 许可证与致谢

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，允许个人学习、研究、实验等非商业用途；商业用途需另行获得授权。第三方前端资源说明见 [THIRD-PARTY-NOTICES](plugins/travel-planning/skills/travel-planning/assets/THIRD-PARTY-NOTICES.txt)。

基于 [mater1996/travel-planning](https://github.com/mater1996/travel-planning) 持续开发。感谢地图、交通、天气与社区数据源，以及 Vue、Vite 和 qrcode-generator；各平台内容及商标归原权利人所有。

README 的展示顺序参考 [video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft)，使用本项目自己的功能说明与 Demo 截图。

---

由 [Hresh赫什](https://hreshhao.com/) 持续构建。
