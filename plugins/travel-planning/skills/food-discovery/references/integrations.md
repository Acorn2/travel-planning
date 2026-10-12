# 接入依据与验收范围

2026-10-09 核查：

| 项目与锁定审阅版本 | 本项目采用的经验 | 保留的限制 |
| --- | --- | --- |
| [cn-scraper-mcp](https://github.com/goesByhc/cn-scraper-mcp/tree/f878f78f844ea55b48bdc39166ce96a07c7b8939)，MIT | 抖音搜索页面字段、点评商户列表与公开链接解析 | 上游抖音等待验证码可达120秒；这里遇验证立即停止，不接入完整多平台 Server |
| [mcp-dianping](https://github.com/shawnq-msft/mcp-dianping)，MIT | 专用浏览器会话、门店页面中的 shopName / star-score / price / addressText 等字段 | 页面结构改变会失效，缺字段不可补造 |
| [travel-agent 已知问题](https://github.com/huahuanao/travel-agent/blob/3ed18b4c57444df843580ae94ad9297e1ebf4959/docs/known-issues.md)，MIT | 搜索强制登录、城市处理、JSON-LD 移除和评价迁往 App 的故障记录 | 本适配器从平台城市列表选择城市，解析实际 DOM；不依赖默认北京、登录 Cookie 字段或 JSON-LD；不开放完整评价工具 |

实现位于 `scripts/providers/food_browser.py` 与 `food_sources.py`。独立实现只读浏览器适配器，未复制上游完整代码、捆绑其服务或使用逆向签名请求。依赖 [Playwright Python 1.63.0](https://pypi.org/project/playwright/1.63.0/)（Apache-2.0），由可选安装命令装到用户 venv；Google Chrome 由用户已有安装提供。

美团外卖自动加购项目与到店旅行研究需求不同，本版未接入美团。大众点评是本版的门店平台来源，不能表述成“美团和点评都已接入”。

验证分三层：标准库测试覆盖工具白名单、链接与参数边界、查询上限、失败停用和隐私；专用 Chrome 加本地夹具验证搜索与门店字段提取；真实账号验证需要本人登录后少量执行“城市＋商圈／菜名／小吃街”查询，核对门店城市、分店地址、原帖链接与查询时间。前两层不证明第三层成功。

2026-10-09 已执行前两层：15项新增测试通过，包括 Chrome 下的城市选择、点评详情、抖音文字搜索与验证阻断；逛吃页面在375/1440px的三视图、无脚本降级和打印通过。真实账号搜索尚未验收。浏览器夹具全部拦截平台请求，不把本地模拟数据当作实时旅行事实。
