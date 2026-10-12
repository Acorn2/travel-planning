---
name: amap-maps
description: 使用插件已配置的高德地图 MCP 查询中国境内地址坐标、POI、周边地点、路线、距离和城市天气。适用于“高德查地点/导航/路线/经纬度/附近”等只读请求；不用于高德 JSAPI 前端开发或境外地图检索。
---

# 高德地图 MCP

通过插件清单中的 `amap-maps` MCP Server 调用高德 Web Service。直接使用宿主暴露的 MCP 工具，不安装 `mcporter`，也不重复调用社区 Skill 自带的 HTTP 脚本。

## 工具路由

一次关键词搜索对应一个地点或一种需求；不同门店分别查询，城市、半径等条件使用对应参数。旅行中的发现、补查与逐次记录遵循 [跨渠道搜索设计](../travel-planning/references/search-design.md)。

- 地址转坐标使用 `maps_geo`；坐标转地址使用 `maps_regeocode`。
- 关键词找地点使用 `maps_text_search`；以坐标为中心找附近地点使用 `maps_around_search`；需要核准某个候选的身份和详情时再用 `maps_search_detail`。
- 查附近餐厅时，先用餐饮锚点坐标调用 `maps_around_search`，再对保留候选逐个调用 `maps_search_detail`。周边返回顺序只是平台搜索结果，不等于高德官方推荐或本行程最终推荐。
- 路线使用 `maps_direction_walking`、`maps_direction_driving`、`maps_bicycling` 或 `maps_direction_transit_integrated`；只有距离比较而不需要路线步骤时使用 `maps_distance`。
- 城市天气使用 `maps_weather`。
- `maps_ip_location` 只有在用户明确要求 IP 定位并提供或授权使用 IP 时才调用。

调用前按需读取[工具参数与组合方式](references/tool-routing.md)，工具名以当前 MCP `tools/list` 返回为准。

## 约束

- 坐标统一为高德 GCJ-02，格式严格为 `经度,纬度`。不得与境外常用 WGS84 坐标直接混算。
- 用户只给地点名称时，先查 POI 或地理编码取得明确坐标，再做路线；同名地点必须结合城市、行政区或地址消歧。
- 公交工具始终传起点城市 `city` 和终点城市 `cityd`，同城时两者填写同一城市。
- 返回的是查询时的平台结果。营业、临时封路、景区入口开放等会变化的信息应标注查询时间，并在必要时回到运营方或景区官方来源复核。
- 餐厅详情可能返回评分但不返回评价量或评论正文；保留真实评分并明确缺失项，不把不完整平台信号写成“无评分”，也不据此声称口碑最佳。
- 已核验高德 POI 可使用官方 URI API 生成 `https://uri.amap.com/poidetail` 详情入口；有 GCJ-02 坐标时可生成 `https://uri.amap.com/navigation` 导航入口。链接只用于查看和导航，不代表预约或下单。
- 不输出、记录或传递 `AMAP_MAPS_API_KEY`/`AMAP_API_KEY`。不要批量扫点或高频调用。
- 工具均用于只读检索和规划，不代表已经导航、叫车、预约或下单。

完整旅行规划中由 `$travel-planning` 负责把地图结果合并到路线、餐厅与事件数据；本 Skill 不自行生成另一份行程。
