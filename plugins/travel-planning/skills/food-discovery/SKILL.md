---
name: food-discovery
description: 已停用的抖音与大众点评实验适配器维护说明。只用于用户明确要求维护或恢复实验代码；旅行研究不使用。
---

# 美食发现

当前不注册 `food-discovery` MCP，也不参与旅行研究；代码仅保留供维护，以下命令是实验实现说明。页面自动化参考已核查的开源实现；没有使用平台官方数据 API，尚未完成真实账号搜索验收。安装与登录状态不等于平台实时可用。

## 本机准备

从插件根目录运行：

```bash
python3 scripts/providers/food_sources.py install
python3 scripts/providers/food_sources.py login douyin
python3 scripts/providers/food_sources.py login dianping
```

安装仅在用户数据目录创建 venv，锁定 Playwright 1.63.0；需要已安装 Google Chrome，不修改全局 Python 或日常浏览器。两次登录分别打开专用窗口，由本人完成扫码和平台验证；登录后按终端回车关闭。资料存于 `~/.local/share/travel-planning/food-discovery/`，不进入插件、行程或公开页面。旅行研究不自动安装或触发登录。

## 读取工具

| 工具 | 参数 | 返回边界 |
| --- | --- | --- |
| `food_status` | `platform=douyin/dianping` | 本地运行时与 profile 是否存在；不探测登录，不宣称 ready |
| `douyin_search` | `keyword`, `limit=1..5`（默认3） | 页面文字标题、作者、发布时间文本与实际视频链接 |
| `douyin_detail` | `url` | 本会话搜索所得视频的页面文字，不含口播转录 |
| `dianping_search` | `city`, `keyword`, `limit=1..5`（默认3） | 从平台城市列表选择城市，再用该城市页面搜索；不默认北京 |
| `dianping_detail` | `url` | 本会话搜索所得门店的评分、人均、地址、营业及推荐菜页面字段 |

详情只能引用同一 MCP 会话内搜索返回的链接；不接受任意 URL。搜索最多5条，详情文字截断，服务总读取上限20次。缺字段保留 `null` 和 `missing_fields`；评分展示值与评价量文本分开，不能擅自补量表或数字。完整点评评价不可用，不能写成已读评论。

一次真实搜索成功只证明该次查询可用。`auth_required`、`risk_control`、`layout_changed`、`query_failed` 等失败会停止本会话的该平台，改用已有线索或高德兜底；`no_results` 只在页面明确报告无结果时返回，不把空白页面当作没有店。原始浏览器异常不传给 Agent，防止泄露会话信息。人工完成登录或验证后新建 MCP 会话。

归档只保存公开链接、必要摘要、查询条件、查询时间和适用日期。返回的发布时间若只是“几天前”或缺失，不能凭空生成精确日期或计入“近12个月原帖”证据。未看视频口播时，不声称提取了视频中的口味、菜单或宠物政策。

来源选择和餐窗使用见主 Skill 的[餐饮研究](../travel-planning/references/restaurant-research.md)；实现依据、版本与验证范围见[接入说明](references/integrations.md)。
