# 实时数据工具

除“多来源路线前轻量预研”外，只在路线已确认并进入深度规划时读取。这里说明跨来源预检、标准快照落盘和本 Skill 自带的适配命令；供应商工具参数、安装与登录遵循 `$flyai`、`$variflight`、`$amap-maps` 和 `$xiaohongshu`。

## 多来源路线前轻量预研

在给出 2～3 个路线方案前，先用宿主网页搜索阅读公开资料，再按需补充可用的小红书；两者围绕问题交叉验证，执行有上限的只读扫描：

1. 按[跨渠道搜索设计](search-design.md)先用“目的地旅游攻略”“目的地美食推荐”“目的地小吃推荐”等自然查询发现选择，再按实际天数、自驾或季节补查相关问题；不预先塞入多个景点、菜名和门店。
2. 每类查询只挑选少量高相关原页面或原帖读取详情，优先覆盖不同作者、不同发布时间和不同路线取舍，不按互动数机械取前几名。
3. 提取反复出现的景点组合、推荐时段、排队与绕行风险、地方菜品或美食类别；同时记录冲突、疑似推广和样本不足。
4. 输出 `route-context.json`，供路线方案和后续餐厅研究读取。这里的美食内容是“目的地美食主题”，不是具体门店推荐，也不能证明某家店有售、营业或值得专程绕行。

若服务未启动、未登录、触发验证或查询失败，记录状态和建议查询词后继续生成路线方案；路线确认阶段不得因此启动安装、扫码登录、反复重试或改用搜索摘要冒充原帖。

此处的失败只允许路线方案继续，**不能视为深度研究的小红书缺证结论，也不能阻止公开网页研究**。路线确认后重新检查运行态，并按实际游玩与餐饮主题补搜；若是筛选控件错误，按 `$xiaohongshu` 的规则对同一关键词去筛选重试一次。保留预研失败与后续补查的时间、查询词和状态，不把失败记录悄悄改成“没有社区内容”。

## 前置健康检查

先按[调用范围与执行效率](execution-efficiency.md)选择本次需要的来源；也可先执行真正需要的业务查询，复用其短时运行记录，不为每次查询额外做 smoke query。

```bash
python3 skills/travel-planning/scripts/research_sources.py preflight \
  --city "杭州" \
  --source amap-maps --source xiaohongshu --require open-meteo \
  --workspace ".travel-research/example-trip" --phase preflight
```

`preflight` 只对 --source 与 --require 选定的来源探测或复用有效运行记录，不检查未选择来源。新探测执行对应 MCP 协议、契约与上游检查；scope=observed_operation 的复用仅说明实际业务操作曾成功，不声称做过完整握手。状态使用 ready/degraded/unavailable；未选择来源返回 not_requested，不能据此填写任何来源 ready。只有 --require 来源未就绪时返回非零。--all-sources 只用于显式全量诊断；--skip-upstream 只用于离线诊断，不复用实时运行记录，不能作为开始深度研究的依据。

可要求的来源为 `amap-maps`、`variflight-aviation`、`variflight-tripmatch`、`flyai`、`open-meteo` 和 `xiaohongshu`。只有涉及航班时要求 Aviation，铁路或空铁联运要求 Tripmatch，中国境内有高德时优先采用；没有高德时仍可用公开地图或官方交通资料。仅当前任务不可替代的来源或用户明确指定来源使用 `--require`。`capabilities` 只表示适配器和配置存在，不表示实时健康。供应商预检不探测宿主网页搜索；由 Agent 实际调用搜索和页面读取并归档到研究契约。不因辅助来源未配置而宣称不能规划。

## 查询与落盘边界

- 路线确认后的已配置只读查询可直接执行，不逐次请求同意；不得扩展到预订、占座、付款、发布、互动或订单修改。
- 正式研究命令同时传入 `--workspace` 和 `--task-id`。航班、铁路和空铁联运属于 `route-data`，酒店属于 `stay-food`。
- 包装查询带 --workspace 与 --phase 时自动记录调用与短时复用；原 checked_at 和供应商有效期不变。明确刷新使用 --refresh，失败和空结果不复用；直接宿主工具先查看已读来源，具体计时与匹配规则见调用范围与执行效率。
- 飞猪和飞常准响应由 `scripts/source_adapters.py` 转换为 `travel-source-snapshot/v1`，遵守 `schemas/travel-source-snapshot.schema.json`。错误遵守 `schemas/travel-source-error.schema.json`。
- STDIO MCP 按 UTF-8 读取，每个进程共用一个读取器，保留连续到达的响应。每次响应等待使用固定超时，通知与未完成的行不延长等待；超时后调用方清理子进程和读取器，并沿用已有失败分类与降级流程。
- 标准快照写入 `snapshots/<task_id>/`；研究结果只提交 `source_snapshot_ids[]`。采用的交通或住宿候选通过 `inventory_refs[]` 绑定具体 `snapshot_id + offer_id`。
- 原始供应商私有字段、凭证、Cookie、授权头和临时令牌不得进入 workspace 或行程。

## 航班、铁路与按需酒店

交通比较按[研究深度](research-depth.md)选择真正需要的时间、价格或推荐维度，指定/已选班次不重新做全天全排序。flyai-flight-coverage/flyai-train-coverage 可重复传 --dimension time/price/recommendation；省略参数保持旧全排序，不作为新规划默认选择。

住宿默认按[住宿区域规划](lodging-planning.md)提供区域范围与大概位置，不因需要住宿就检查酒店供应商或查询报价。下方酒店命令仅在用户明确要求具体酒店比较时使用；已选酒店复用地址，相关服务按行程需要补核。

通用发现先使用 flyai，运行状态、指定航班、铁路和空铁联运按实际缺口补 variflight。开放交通按所需比较维度补查；时间问题核对最早/最晚/时长，预算问题查价格，综合选择查推荐，不默认两个供应商都跑全部排序。不能用单次低价榜推断全天没有合适班次。

需要写入标准快照时使用包装命令：

```bash
python3 skills/travel-planning/scripts/research_sources.py flyai-flight-coverage \
  --origin "北京" --destination "上海" --date 2026-10-03 \
  --dimension time \
  --workspace ".travel-research/example-trip" --task-id route-data

python3 skills/travel-planning/scripts/research_sources.py flyai-train-coverage \
  --origin "北京南" --destination "上海虹桥" --date 2026-10-03 \
  --dimension time \
  --workspace ".travel-research/example-trip" --task-id route-data

python3 skills/travel-planning/scripts/research_sources.py flyai-hotel \
  --destination "杭州" --poi "西湖" \
  --check-in 2026-10-03 --check-out 2026-10-05 \
  --adults 4 --rooms 2 --bed-type "双床房" \
  --workspace ".travel-research/example-trip" --task-id stay-food
```

商圈酒店查询只用于发现。采用前以酒店全名再次查询，并用地图核对城市、行政区、地址、坐标和目标锚点。供应商没有按多间同房型校验库存时，只能标为报价候选。

飞常准包装命令包括 `variflight-flight`、`variflight-flight-number`、`variflight-flight-price`、`variflight-flight-comfort`、`variflight-train`、`variflight-train-stations` 和 `variflight-air-rail`。IATA 参数使用三位城市或机场代码；价格和空铁联运只接受城市代码。缺凭证、授权失败、额度不足、空结果和供应商故障必须分别保留，不能统一改写成“没有班次”。

## 天气、地图与境外地点

天气先按研究深度检查旅行日期是否在预报范围及活动敏感度；同地点一次请求复用其日期预报，不逐景点查天气。范围外只做有来源的季节风险与临近复核，室内日常活动不默认逐小时查询。

近期天气使用 Open-Meteo；中国天气预警另保留官方预警入口：

```bash
python3 skills/travel-planning/scripts/research_sources.py weather \
  --location "杭州" --days 7
```

中国境内 POI 与路线遵循 `$amap-maps`。需要包装输出时使用：

```bash
python3 skills/travel-planning/scripts/research_sources.py amap-place \
  --city "杭州" --keywords "西湖风景区 曲院风荷入口"

python3 skills/travel-planning/scripts/research_sources.py amap-route \
  --origin "120.130210,30.259002" \
  --destination "120.144590,30.243710" \
  --mode walking
```

高德返回的路线耗时不等于门到门时间；还要加入出入口步行、等待、换乘、安检和缓冲。入口开放状态回到景区官方确认。

境外地点可用 Nominatim/OpenStreetMap 低频发现坐标候选：

```bash
python3 skills/travel-planning/scripts/research_sources.py osm-place \
  --query "Kiyomizu-dera Niomon, Kyoto, Japan" \
  --countrycodes jp --limit 3
```

一次只发一个请求，不并发扫点或批量抓取。返回坐标只是候选，入口名称与开放状态仍需官方和实际地图平台确认；同一组路线不得混用 GCJ-02 与 WGS84。

## 小红书体验研究

搜索、详情、登录和工具参数遵循 `$xiaohongshu`。旅行研究默认只读；`xhs-detail` 默认保留有限的一级评论样本，只有评论区确实影响路线判断时才请求更多评论或有限回复。包装输出会生成 `comment_insights` 和 `needs_recheck`：路线逻辑优先消费主题洞察，不直接消费全部评论原文。使用本 Skill 的 `xhs-search` 或 `xhs-detail` 包装输出时，临时令牌只保存在权限受限的用户缓存，workspace 只保存公开原帖链接、必要摘要、必要评论主题和查询时间。研究方法与证据边界见[信息获取与核验策略](source-strategy.md)。

路线确认后的具体景点和餐厅研究可以复用 `route-context.json` 的查询词与地方美食主题，但必须重新核验具体实体、适用日期、地图位置和经营信息；不得把路线前样本数算作某家餐厅的门店口碑样本。

先对每个会改变选点、时段或游览顺序的玩法主题，以及各片区的主要餐饮主题列出待证问题。一次搜索返回的候选列表可覆盖多个问题，但需逐篇读取高相关原帖；用不同作者和正反体验检查取舍。记录“主题 → 关键词 → 已读原帖 → 采用或舍弃的判断”，并明确仍缺的主题。未读详情的搜索结果不能进入最终来源列表；最终来源应链接到已核验原帖，无法生成可用原帖链接时保留标题、作者和笔记 ID 并明确标为站内检索入口，不能把平台首页伪装成原帖。

## 图片笔记

正文为空、只是标签或关键信息写在图片中时，不能只看标题和评论。`xhs-detail` 返回 `image_reading.image_count/image_indices`，并把临时图片链接留在权限受限的用户缓存；需要看图时运行 `xhs-images --note-id <id> --indices 1 2 3`，再用 `view_image` 读取返回的本地图片。图片缓存20分钟内有效，此命令不重复读取笔记详情，也不自动识别文字。具体预算与证据归档遵循 `$xiaohongshu`；原图、临时链接和本地路径不进入 workspace 或行程页面。

餐饮研究主动搜索公开网页，并按需补充小红书和高德。

## 手动查询降级

自动获取失败或不允许自动化时，使用 `fallback` 生成带查询条件的 `action_links[]`：

```bash
python3 skills/travel-planning/scripts/research_sources.py fallback --kind train \
  --origin "北京南" --destination "上海虹桥" --date "2026-10-03" \
  --fields "车次、时刻、二等座票价和余票"

python3 skills/travel-planning/scripts/research_sources.py fallback --kind ctrip \
  --product "酒店" --city "杭州" --date "2026-10-03 至 2026-10-05" \
  --travelers "2 位成人·1 间房"

python3 skills/travel-planning/scripts/research_sources.py fallback --kind map \
  --city "杭州" --keywords "西湖风景区 曲院风荷入口"

python3 skills/travel-planning/scripts/research_sources.py fallback --kind xiaohongshu \
  --keywords "西湖 10月 日落 入口 避坑"
```

景区 fallback 必须传入已经核对的官方 URL。手动入口只表示待复核，不能把平台首页或查询页写成已核验详情。

## 合并规则

- 主 Agent 将标准快照合并到 `planning.source_snapshots[]`；页面展示 Provider、查询时间、有效期和供应商实际返回的 HTTPS 入口。
- 动态报价、库存、时刻、运行状态和地图耗时只属于本次 workspace。机场、车站、酒店身份等跨 Agent 需要的字段写入本次 `shared_entities[]`，不建立跨行程缓存。
- 高德和天气结果使用 `platform_reported`，人工查询入口使用 `to_recheck`。
