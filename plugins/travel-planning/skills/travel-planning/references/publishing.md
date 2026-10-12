# 手机分享：一个站点，多份行程

交付闭环：生成并验证本地 HTML → 准备本地审阅快照 → 用户审核本次 HTML 并明确确认发布 → 更新固定站点 → 匿名核对所有页面 → 交付该行程的稳定链接与二维码。默认停在本地审核，不自动部署；需要发布时复用一个 Netlify 项目，不为新行程反复创建项目。正常使用由 Agent 执行脚本，以下命令用于实现和排障，不把它们当成用户每次必做的步骤。

## 权限与范围

- 每次新增或修改行程，先交付本地 HTML 的绝对路径链接供用户审核，说明尚未发布。prepare 与 prepare-site 只写本地文件，可用于准备审阅包；发布前展示本次页面、保留的行程清单和目标站点，等待用户对当前 HTML 明确确认发布，例如“审核通过，发布这一版”。只认可内容、路线确认、提前提出分享需求或历史自动发布授权都不替代本次确认；不得为审核创建 Netlify 部署预览。
- 确认只适用于本次审核的行程版本。确认后内容再修改，先重新交付审核并取得发布确认；用户要求草稿、仅本地或暂不发布时不部署。当前版本已获确认后由 Agent 执行发布，不重复确认同一次操作。
- 持有链接的人都可查看；`noindex` 和未展示列表不能代替权限控制。检查姓名、家庭地址、证件、订单等不应公开的信息。`/history/` 与 `_site.json` 都是公开的行程清单，不能宣称各路径相互私密。
- 凭证读取行程项目根目录 `.env` 的 `NETLIFY_AUTH_TOKEN`，兼容已有进程环境；文件中非空值优先，只解析发布字段，不执行 shell、不展开变量，不进入页面、清单、状态或聊天。`.env` 不得跟踪或分发，仅分发 `.env.example`。Token/站点配置本身不代表本次发布确认。
- 不覆盖 README 的历史 Demo、个人网站或无关站点。旧单行程发布命令保留兼容；新发布默认使用完整站点包，不能把单行程 public/ 直接上传到多行程站点。
- `/history/` 从本次完整站点快照生成已发布行程列表，点击进入独立 `/trips/<trip-id>/`；详情页不提供列表入口，草稿不入列表。暂不增加账号、口令、多人编辑或离线缓存。根网址继续显示第一份行程；该行程更新时根页面同步。无需重定向，旧链接的视图 hash 继续有效。

## 1. 目录与命名

正式本地文件仍为 `travel-plans/<trip-id>/index.html`。行程 ID 使用小写英文或拼音目的地加出发日期，例如 `wudangshan-2026-10-06`。线上入口为 `/trips/<trip-id>/`，同一行程更新复用原路径。

```text
travel-plans/
├── <trip-id>/
│   ├── index.html
│   └── releases/<release-id>/     # 单行程快照，仍是三文件格式
└── _site/releases/<release-id>/   # 累积的整站快照
    ├── public/
    │   ├── index.html            # 保留第一份行程的根入口
    │   ├── _site.json            # 整站版本、行程路径与摘要
    │   ├── _headers
    │   ├── history/index.html    # 公开的已发布行程列表
    │   └── trips/<trip-id>/
    │       ├── index.html
    │       └── _trip.json
    ├── review.json               # 本地审阅清单和基线，不上传
    └── site.zip                  # 只包含 public/ 白名单文件
```

站点级状态统一放在 `.travel-research/publication/site.json`，保存项目 ID、最后验证的整站快照绝对路径、部署 ID 和行程 URL，不保存凭证。它独立于旧 `.travel-research/<trip-id>/artifacts/publication.json`；迁移不覆盖旧状态与快照。以上目录都不随插件打包。

以下示例从仓库根目录运行，已包含 `plugins/travel-planning/` 前缀；如果当前在插件目录内，改用脚本的实际绝对路径，避免重复拼接。`<release-id>` 使用 `YYYYMMDD-HHMMSS`，已存在目录拒绝覆盖。

## 2. 准备本地快照

先用 `prepare_share.py` 得到正式单文件 HTML，再冻结单行程：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py prepare \
  travel-plans/<trip-id>/index.html --trip-id <trip-id> \
  --output-dir travel-plans/<trip-id>/releases/<release-id>
```

首次创建全新站点，合入第一份行程时省略基线：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py prepare-site \
  travel-plans/<trip-id>/releases/<release-id> \
  --output-dir travel-plans/_site/releases/<release-id>
```

之后新增或更新行程，从已验证的站点状态自动取得完整基线：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py prepare-site \
  travel-plans/<trip-id>/releases/<release-id> \
  --state-file .travel-research/publication/site.json \
  --output-dir travel-plans/_site/releases/<release-id>
```

也可显式指定 `--base-release <上次完整站点快照>`。仅合入本次明确选中的单行程，不扫描项目中其他草稿；其余行程逐字保留。找不到最后验证的基线、状态仍待验证、线上已改变时先核对，不从零打包覆盖。更新根行程时根页面与其子路径同步。

审阅新包内本次 `public/trips/<trip-id>/index.html`、`public/history/index.html`、根页面和 review.json 的完整行程清单。历史列表只读取已纳入发布包的页面标题与行程 ID，按最近生成时间排列；新增、更新行程时自动重建，旧发布包仍可作为基线。单行程快照的“本版生成”时间保留；聚合时间只代表站点打包，不冒充行程价格、天气等事实的核验时间。发布前比对 ZIP、白名单、清单和预览字节。

### 从旧单行程站点迁移

把线上当前版本对应的旧单行程快照作为基线，目标站点 ID 保持原值：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py prepare-site \
  travel-plans/<trip-id>/releases/<当前线上单行程版本> \
  --base-release travel-plans/<trip-id>/releases/<当前线上单行程版本> \
  --output-dir travel-plans/_site/releases/<新release-id>
```

这会把同一份 HTML 同时放在根路径与 `/trips/<trip-id>/`，保留已有链接。首次发布用原项目的 `--site-id` 和新的站点状态文件；脚本先读取线上文件清单，确认没有基线之外的内容，再匿名核对旧根页面及 `_trip.json` 摘要。原站点包含无关内容、版本不同或基线不完整即停止；不修改名称、域名或团队配置。迁移后旧单行程发布命令不能覆盖此站点。

## 3. 一次配置，每版审核确认后发布

用户在 Netlify 的 [Personal access tokens](https://app.netlify.com/user/applications#personal-access-tokens) 页面创建 Token，设置名称与有效期，生成后只在本机保存。在行程项目根目录从 `.env.example` 创建 `.env`，将 Token 填入 `NETLIFY_AUTH_TOKEN`，目标 Project ID 填入 `NETLIFY_SITE_ID`：

```dotenv
NETLIFY_AUTH_TOKEN=
NETLIFY_SITE_ID=
```

不再要求 export 或把 Token 放进命令行。根据 `--state-file` 所在 `.travel-research/` 定位行程项目根目录 `.env`，从插件目录运行仍有效；自定义布局用 `--env-file <绝对路径>`。命令行 `--site-id` 优先，否则读取 `.env` 的站点 ID；与已有绑定不一致会停止，不自动切换站点。Token 缺失只提示本机填写，不索取值。

Agent 的审核与发布步骤：

1. 完成行程审查和手机/桌面页面检查，更新同一 `trip-id` 的本地 HTML，给出绝对路径链接，说明“本地待审核，尚未发布”。
2. 有发布需求时，读取站点绑定，在本地冻结单行程并准备完整站点快照。已有整站状态时，以最后验证的完整快照合入本次行程；首次迁移旧站时，从旧单行程状态找到摘要匹配的快照作为基线，不能把已有站点当空站点。
3. 展示待发布页面、完整行程清单与目标站点，等待用户审核当前 HTML 并明确确认发布。用户提出修改时，更新本地文件、验证并重新准备审阅包，再等待确认；未确认不调用 publish。
4. 确认待发布快照与用户审核版本一致后，调用 publish 并传入 `--confirm-public`；该参数仅代表已获得本次确认，不能由历史授权或脚本检查代替。站点 ID 从 `.env` 读取，不新建项目。待验证的旧部署先运行 status；结果未知的写请求暂停核对。
5. 匿名校验根页、历史列表和所有行程，只在成功后交付本次固定 URL、二维码入口与本地备份。凭证缺失时留下本地包，说明一次性配置缺口，不退回让用户每次手动部署。

首次绑定空站点或迁移原站点：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py publish \
  travel-plans/_site/releases/<release-id> \
  --state-file .travel-research/publication/site.json \
  --site-id <Project-ID> --confirm-public
```

只有用户同意创建全新站点，且包没有已有发布基线时，才用 `--create-site` 替代 `--site-id`。绑定后所有行程复用同一状态文件，省略这两个参数：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py publish \
  travel-plans/_site/releases/<release-id> \
  --state-file .travel-research/publication/site.json --confirm-public
```

同一包重复执行只验证，不再上传。新增或修改行程在用户审核并确认发布后才部署；审核期间的本地修改不上传。每次实际部署计入套餐用量，一个站点只创建一次不代表以后无需部署。

Netlify 新项目可能继承 Private 默认值。已获公开授权后，在该项目选择 Make public，或在 `Project configuration > General > Visitor access > Project visibility` 将生产版本设为 Public 并保存；只改当前项目，保留名称，不改团队全局默认值。旧套餐看到 Password Protection 时核对生产版本是否受 Team login protection。[Project visibility](https://docs.netlify.com/manage/security/secure-access-to-sites/project-visibility/)

无脚本凭证时仍交付整站本地包；可在原 Netlify 项目的 Deploys 页手动上传整站 `public/`。不要再用 Drop 创建项目，也不要上传单行程目录、研究数据、release 根目录或状态。手动部署后脚本状态不会自动同步，应核对当前部署和对应完整快照再恢复脚本流程，不能沿用过期基线。

## 4. 验证与异常恢复

脚本等待部署 ready 后，检查它是站点当前正式版本，再匿名读取根页面、历史列表、整站清单和每份行程的 HTML/标识，逐一比对审阅摘要；只有通过才报告已发布和正式 URL。Netlify 偶尔在 HTML 响应中插入带当前站点 ID 的固定说明注释和辅助脚本，校验器只剔除这两段后比较审阅摘要，其他字节差异仍阻断发布。网页不会依赖第三方二维码服务，二维码编码固定页面地址，去掉视图 hash 和追踪 query。

上传成功但等待超时、验证失败时：

```bash
python3 plugins/travel-planning/skills/travel-planning/scripts/publish_itinerary.py status \
  --state-file .travel-research/publication/site.json
```

status 只继续等待和验证，不重复上传。创建站点或上传写请求若丢失响应，状态记为 `creation_unknown` / `upload_unknown`，先在控制台确认实际结果；不得盲目重试。人工核对得到站点/部署 ID 后再修正本地状态，不推测 ID。同一状态文件有本地互斥锁；进程异常退出遗留 `.lock` 时，先确认没有运行中的进程且远端结果已核对，再移除该锁。不能同时从多台电脑或控制台更新同一站点；版本检查不能提供跨设备的原子锁。回退使用控制台的历史部署，并先让用户确认目标版本；本地旧 release 快照和 `previous_deploy_id` 可供核对。

首次成功发布后，发布器从该用户 Netlify 项目实际返回的 `ssl_url` 生成并打印 `history_url`，格式为 `https://<该用户的站点域名>/history/`；同时打印本次行程的独立链接。`NETLIFY_SITE_ID` 是项目 ID，不是可直接推断的域名。用户忘记历史入口时，运行 `status` 重新匿名校验并再次打印实际地址；只配置凭证而未发布时不要提供猜测的历史 URL。

部署为 `ready_unverified` 但公网读取失败时，使用 status 获取具体 HTTP 状态或 DNS/TLS/超时错误，不再次运行带 `--create-site` 的 publish。状态文件中的 `candidate_url` 是待验证地址；只有匿名读取并比对通过后的 `url` 才是已验证的交付链接。排查只需要公开网址和脱敏错误，不要求用户传递令牌。

公网 401 且响应为 Netlify `edge-access` 登录跳转页，说明访客登录保护仍在生效；发布令牌不是旅行者访问凭证。按上文公开此项目生产版本后重新 status，无需重建站点、重新上传或将 Token 带到分享链接。其他 401 不自动归因于 Netlify 项目可见性。

TLS 证书验证在 HTTP 请求发送前完成；此类失败会明确提示并将创建状态记录为 `creation_not_sent`，修复 CA 后可重试原命令。若上传前证书校验失败，保留绑定站点为 `bound`，重试时省略 `--create-site`。不要将响应丢失、超时或其他未知网络错误自行改为“未发送”。

Python 的默认 CA 优先；显式 `SSL_CERT_FILE` / `SSL_CERT_DIR` 不被替换。默认 CA 文件、目录和已加载根证书都缺失时，脚本会使用本机已安装的 `certifi`（若有），继续校验证书与主机名，不自动安装依赖或修改系统。没有现成 CA 时，可由用户在同一终端设置 `SSL_CERT_FILE` 指向可信 PEM 证书包；不要关闭 HTTPS 校验。macOS python.org 安装包的证书配置见 [Python 官方说明](https://docs.python.org/3.14/using/mac.html)。

发布后还要检查手机上打开三个视图、复制链接、二维码扫码和外部导航入口。大陆移动网络与微信内访问必须用真实设备确认，桌面模拟不能声称完成这一项。

## 5. 交付

最终在用户回复中分别列出“查看全部规划” `/history/` 和“分享本次行程” `/trips/<trip-id>/` 的完整 HTTPS 链接、该版本时间与行程 ID；不能只给其中一个，也不能只说“在历史页查看”却不写具体 URL。详情页“更多”中的“复制行程链接”复制独立链接，“查看分享二维码”可保存二维码发给同行人；详情页不展示历史入口。更新后刷新同一地址即可。没有真实发布或匿名验证未通过时，只交付本地审阅版和缺失条件，不提供猜测的正式链接。

依据：[Netlify API](https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/)、[Netlify Drop](https://docs.netlify.com/start/quickstarts/netlify-drop-quickstart/)。
