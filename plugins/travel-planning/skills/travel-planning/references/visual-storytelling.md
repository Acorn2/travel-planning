# 水彩旅行手账：视觉叙事与生图

默认风格来自用户确认的宜昌手账：浅暖纸纹、水彩山水/街巷、深青绿和鼠尾草绿、少量赭黄与锈红、细铅笔线。拼贴用 HTML/CSS 的白边明信片、胶带与日期票实现，不把整张网页生成成不可读图片。

## 选图与生成

在最终行程事实审查通过后，从事件中选出最有差异的场景，先写一份简短 shot list：用途、日期/事件 ID、画面主题、对应事实和提示词文件。正常多日行程建议 4–6 张：一张封面、2–3 个主要体验场景、一张食物插画或贴纸画。短行程至少封面与一个体验场景；没有食物证据时不生成地方菜品。一次生成的同一图片可用于路线缩略图、体验卡与每日明信片，不重复花费生成。

使用环境中现有的图片生成能力，原生 image_gen 可用时优先使用，并遵循其工具要求。此 Skill 不要求专用 API key、安装生图服务或指定付费供应商；用户指定工具时以用户要求为准。必须实际调用生图并查看输出，不能只写提示词。工具不可用、调用失败或图不合格时，记录缺口并提供可读草稿；不能改称代码图标或网络占位图就是已生成插画。

提示词可用以下骨架，再按真实行程改写场景：

> Editorial watercolor travel journal illustration, warm ivory textured paper, sage green and deep teal, restrained ochre and rust accents, delicate graphite outlines, soft natural pigment edges, generous breathing room, subtle handmade collage mood. [scene based on the itinerary]. Atmospheric imagined illustration, not a documentary image or exact architectural reconstruction. No text, numbers, labels, logos, watermarks or UI.

封面适合 3:2 横图；体验适合 4:3；食物贴纸留纸色空白、少量阴影且菜品相互分离。不要虚构具体建筑外观、道路地图、景区安全设施或店铺招牌；若根据可靠实景参考生成仍需标注为 AI 插画。参考图只取配色和排版灵感，不复制图中的文字、标识和完整作品。

查看每张原图，检查主题是否对应本次行程、是否误画未安排景点/食物、有无乱码文字、人物畸变、危险场景或大量空白。保留原图和完整提示词。过大的图片可用系统图片工具压缩交付副本，原图保留；不要求新增依赖。单张交付图片不超过 2 MB，建议长边约 1200px，同一行程控制总体体积，避免手机长时间下载。

## 清单与绑定

目录示例：`.travel-research/<trip-id>/visuals/{manifest.json,prompts/,assets/}`。图片路径相对清单目录，只接受其内部的 JPEG、PNG、WebP；绝对路径、跨目录或逃逸的符号链接会拒绝。`alt` 描述画面，`credit` 是页面可见说明，AI 场景使用“AI 生成水彩插画 · 非景点实景”，食物使用“AI 生成水彩插画 · 非实拍菜品”。

```json
{
  "cover": {
    "path": "assets/cover.jpg",
    "alt": "本次旅行的山水与街巷氛围插画",
    "credit": "AI 生成水彩插画 · 非景点实景",
    "aspect_ratio": 1.5
  },
  "experiences": {
    "event-stable-id": {
      "path": "assets/river.jpg",
      "alt": "乘船看山水的水彩示意",
      "credit": "AI 生成水彩插画 · 非景点实景",
      "aspect_ratio": 1.333333
    }
  },
  "days": {
    "2026-10-12": {
      "path": "assets/cover.jpg",
      "alt": "抵达日的旅行氛围插画",
      "credit": "AI 生成水彩插画 · 非景点实景",
      "aspect_ratio": 1.5
    }
  },
  "food_items": {
    "研究中的完整食物名称": {
      "path": "assets/food.jpg",
      "alt": "对应食物的水彩插画",
      "credit": "AI 生成水彩插画 · 非实拍菜品",
      "aspect_ratio": 1.5,
      "crop": {"columns": 2, "rows": 2, "index": 0}
    }
  }
}
```

- `experiences` 优先以事件稳定 ID 绑定，兼容旧清单的完整事件标题。标题改变不应造成 ID 绑定的图片丢失。`days` 以实际 ISO 日期绑定，未绑定日期优先采用当天事件图片；不要把不存在的日期作为已完成素材。
- `food_items` 使用原始食物名称，包含括号中的候选和待核说明；名称取自 `food_walk.food_items`、`planning.food_highlights` 或已选正餐。清单仅提供图片，不能增加食物、门店或旅行事实。可选旧字段 `food` 作为一张整体风味插画，缺少餐饮数据时不会展示。
- `aspect_ratio` 是原始整张图片的宽/高，默认 1.5。可选 `crop` 仅用于等分贴纸网格，`columns/rows` 为 1–8 的整数，`index` 从零开始按从左到右、从上到下计数。每个绑定独立写清对应菜品，查看裁切后画面；不能拿同一菜品配所有食物。
- 原图路径和提示词记录留在私有研究目录，HTML 只包含图片、替代文本与来源说明。不给未来行程默认绑定当前宜昌图片。合法实景资料沿用事件 `images[]` 的作者、许可和原始页规范，不能热链小红书临时封面。

## 渲染与验收

使用通用渲染器并显式传入 `--visuals <manifest.json>`。路线上标注“行程顺序示意，非导航地图”；水彩图片保持氛围用途，文字仍由行程 JSON 生成，不把图中想象的内容变成景点事实。

检查封面、体验卡、食物裁切和每日图片是否实际显示、替代文本与来源是否可读；查看手机及桌面，没有整页横向溢出。点击路线票和体验入口应打开正确日期；无脚本保留完整行程和图片，图片失败不影响文字和导航入口。简版与完整版打印隐藏装饰图并保留原有执行信息，分享沿用正式 HTTPS 与本地草稿区分。生图完成不等于已公开发布，仍先交付本地审核页。
