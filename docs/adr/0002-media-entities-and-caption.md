# ADR-0002：媒体实体登记与 caption 生成器

- 状态：已接受
- 日期：2026-11-06
- 相关票：#14（媒体实体登记）、#13（内嵌图抽取）、#15（WeKnora 索引）

## 背景

图片/视频是独立顶层实体：图片要能被文本检索命中，就得有 caption；视频第一阶段
不抽帧、不做自动概述，简介由人填。两件事都有「谁来生成」「什么时候生成」的选择。

图片有两条入口：文档转 md 时抽出的内嵌图（#13 已落），和微信直接收到的独立图
（本票）。两者共用同一套编号与去重规则，区别只在「有没有文档关联」。

spec §2.1 的 2026-11-06 拍板曾写「GLM-4V-Flash 作废，统一用 WeKnora 已配模型」，
票面则写 GLM-4V-Flash。核实 WeKnora：它的视觉模型只在**它自己的索引管线**里跑
（docreader 解析 → Go 服务做 OCR/caption → 入索引），没有「描述这一张图」的对外
接口。caption 是真相源字段，必须能脱开派生索引独立重建——把真相源写库依赖在
一个「可重建的派生索引」上，正是 spec 开头点名要避免的双源漂移。

**本 ADR 明确取代 spec §2.1 里「caption 用 WeKnora 模型」那半句**，并已于
2026-11-06 经用户确认（选项：保持独立视觉模型 GLM-4V-Flash、改 spec 口径）。
OCR 的口径不受影响，仍是本机 RapidOCR 一期。

## 决策

**1. caption 由独立的 OpenAI 兼容视觉模型生成，默认 GLM-4V-Flash。**
`caption.py` 按 `/chat/completions` 的通用形状实现（智谱、阿里云百炼 qwen-vl 同形），
换模型只改环境变量：`CAPTION_API_KEY` / `CAPTION_BASE_URL` / `CAPTION_MODEL`
（密钥缺省回落 `ZHIPU_API_KEY`）。一期选 `glm-4v-flash`：免费、key 已在 `.env`、
实测中文截图能照抄文字并说清用途。将来若想让 WeKnora 里的 qwen3.8-flash 接管，
那是换 backend 而不是改调用方——接口只有 `describe(bytes, ext)`。

**2. caption 生成失败不阻断登记。**
模型超时/鉴权失败只让 `images.caption` 留空，登记回执里带 `caption_error`。
图片编号、母本、项目关联照样成立——一张没描述的图仍然能被「按编号/按项目」找到。

**2b. caption 的网络调用放在 DB 事务外。**
先落库（编号、母本、项目、标签）并 commit，再逐张调模型补描述；每补完一张
立刻 commit，下一张的 HTTP 不落在写事务里。否则一次最长 60 秒的 HTTP 会把
SQLite 写锁占住，批量入库互相堵。视频时长的 ffprobe 同理，放在分配编号之前。

**3. 重复登记复用已有 caption；要重生成走 `--recaption`。**
同一张图（sha256 相同）第二次登记不重复调模型（省钱、省时）；已有 caption 的
不覆盖、没有的补生成。`register-image --recaption` / `convert --recaption`
显式重生成。生成失败的图会在回执里带 `caption_error`，CLI 逐条打印，不静默。

**4. 项目关联照文档的语义：单归属 + 关联项目标签。**
`images.project_id` / `videos.project_id` 是主项目，关联项目挂
`image_project_tags` / `video_project_tags`（与 `document_project_tags` 同构）。
主归属一旦定下，后来的登记不改写它（避免同一次导出顺序不同导致归属漂移）。

**5. 视频不抽帧、不自动概述。**
`register-video` 只登记编号、落母本、记项目和简介；简介用 `set-intro` 随时改。

**6. 顺手补齐媒体元数据：名称、图片宽高、视频时长。**
`images.name` / `videos.name` 取原文件名主干；宽高用 Pillow 读（读不到留空）；
时长先用 ffprobe（能处理所有容器和 moov 在尾部的文件），没有 ffprobe 就读文件头解析 MP4/MOV 的 `mvhd`；两样都不行就留空，不为一个展示字段引入 ffmpeg 依赖。
这些字段 spec §4.1 列了，之前一直是 NULL。

**7. 内嵌图的 caption 与主归属由转换流程一并补。**
`convert_version(..., captioner=...)` 在登记完内嵌图后补 caption，并让图继承
文档的主归属（已有归属的不改）。这样内嵌图和独立图在图片库里是同一种记录。

## 后果

- 图片编号与内容绑定：同一张图可能既被某文档"内嵌"引用、又被当独立图登记，
  但永远只有一个 `IMG-####`，只是 `document_images` 与项目归属各自记各自的。
- 没有 caption 的图（无 key 或生成失败）在检索里只能靠编号/项目命中，不会被
  文本语义搜到——这是可接受的降级，不是数据丢失。
- 视频简介是人的输入，质量取决于人；换来的是零推理成本、零幻觉。
- caption 生成是同步调用（一张图一次请求）。批量入库时这是明显的墙钟成本，
  如需批量再在 #15 之前加队列，不影响现在的调用契约。
- 没有 ffprobe 的环境里，非 MP4/MOV 视频的时长会是 NULL；这是可接受的降级，
  不为了一个展示字段引入 ffmpeg 依赖。
