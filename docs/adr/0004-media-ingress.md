# ADR-0004：媒体从 scan 入口直接进媒体库

- 状态：已接受
- 日期：2026-11-06
- 相关票：#20（scan 区分媒体）、#12（摄入与去重）、#14（媒体实体登记）、#13（内嵌图抽取）

## 背景

#14 落地了 `register-image` / `register-video`，但独立图（微信另存的截图、录屏）
没有自动入口：`scan` 把所有文件一视同仁登记成出现，triage 时 `new-doc` 把一张
PNG 收编成 Document，之后 `convert` 抛 `UnsupportedFormat`、`index` 静默跳过。
结果是 spec §6.2 的「独立图直接进图片库」只能人工逐张补登，且库里留着一条
转不出 md 的文档。微信目录里截图占比不低，这条路必须先断掉。

三层身份模型（出现 → 文档 → 版本）本来就分得很清：**出现是文档 triage 的单位**，
媒体是**顶层实体**（§4.1 的 images / videos，与 documents 平级，没有「版本」「归档」
这类流程）。把媒体塞进出现，等于给它套了一套用不上的文档语义。

## 决策

**1. 媒体永不产生出现——文件系统和 zip 包内都一样。**
`scan` 在日期筛选之后按扩展名分流：命中 `media.IMAGE_EXTS` / `media.VIDEO_EXTS`
的文件直接调 `media.register_standalone_image` / `register_video`，不写
`occurrences`。zip 展开时逐个 entry 同样判断：媒体 entry 先落到临时文件，走同一套
登记接口（属于哪个包记进 source，见「后果」），登记完临时文件即删，仍然不写
`occurrences`；非媒体 entry 照旧登记出现。去重口径与文档一致（sha256）：重复扫描
幂等，同一张图出现在两个日期目录、或 zip 与目录各有一份，也只占一个 `IMG-####`
（第二次回执 `created=False`）。扩展名集合只定义在 `media.py`，`ingest`（分流）
与 `archive`（拦截）都 import 它，不另开模块。

**2. scan 是媒体入口，媒体归属在扫描时定。**
`scan --project` / `--tag` 作用于本次登记的媒体（主归属 + 关联项目标签，语义
同文档，见 ADR-0002 第 4 条），zip 内的媒体同样带上这两个参数。图片 caption
沿用现有开关：配置了视觉模型密钥才生成，`--no-caption` 可关；生成失败不挡入库
（ADR-0002 第 2 条），但**不再被吞掉**：`ScanStats.caption_errors` 带回每条错误，
CLI 逐条打印 `⚠ 描述生成失败：…`，与 `convert` / `register-image` 用同一句话。
代价是 caption 的墙钟成本落在扫描上：一张图一次 HTTP，`--no-caption` 是省钱
省时的逃生口。

**3. `new-doc` 对媒体不再产出半成品。**
`archive.new_doc` 在建任何实体之前按同一套扩展名判断，命中就抛 `ValueError`，
消息给操作者两条真能用的路：重扫该目录（媒体已不再走出现，会直接进媒体库），
或手工 `register-image` / `register-video`；CLI 捕获后 `SystemExit`，不留
traceback、也不留半截文档。压缩包内 entry 的扩展名同样适用这条拦截。

**4. scan 的返回值是结构化回执，不是计数。**
`ingest.scan_dir` 返回 `ScanStats(occurrences, images_seen, images_new,
videos_seen, videos_new, caption_errors)`，CLI 打印「文档 N / 图片 X（新 Y）/
视频 Z（新 W）」，随后逐条打印 caption 失败。`*_seen` 是「这次看到几个文件」
（含重复），`*_new` 是「真正新入库几个」；出现数与媒体数混成一个整数，操作者
就看不出这次扫进来的是文档还是截图。字段没有默认值：漏传是 bug，不是「这次
扫到 0 个」。

## 后果

- triage 队列只剩文档：媒体（文件系统上的和 zip 包内的）永不产生出现，所以 `scan`
  之后每条 pending 出现都值得人工判断，不会再出现「打开一看是张截图」的噪声。
- 微信截图/录屏零人工入库，文件系统和 zip 包内都是；`scan` 可以反复跑，编号不会
  漂（sha256 去重）。
- `scan` 变慢：媒体要算 sha256、落母本、跑图片尺寸/视频时长，配了密钥还要逐张
  调 VLM。`--no-caption` 去掉最贵的一步；不配密钥时默认不花这笔钱。
- 媒体没有「来源出现」这层记录，溯源只靠 `images.source` / `videos.source`。
  **接受的限制**：这两列只存位置字符串，不存来源类型（wechat / zip / manual），
  所以自动登记的媒体丢了「这张图是微信另存的还是从压缩包里抽的」这个维度——
  zip 来源记成 `<zip 路径>!<包内路径>`，只能从路径形状反推。
- **老库不自动清理**：#20 之前建的库里可能还留着媒体 Document（当初被 `new-doc`
  收编的那批）。本票不做数据迁移，它们照旧会让 `convert` 抛
  `UnsupportedFormat`；清理是手动的——`ignore` 掉对应出现，或把源文件
  `register-image` / `register-video` 登记进媒体库后再处理掉那条出现。
- 视频简介仍由人填（`set-intro`）；scan 只登记编号、母本、项目/标签和时长。
