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

两层身份模型本来就分得很清：**出现是文档 triage 的单位**，媒体是**顶层实体**
（§4.1 的 images / videos，与 documents 平级，没有「版本」「归档」这类流程）。
把媒体塞进出现，等于给它套了一套用不上的文档语义。

## 决策

**1. 媒体永不产生出现。**
`scan` 在日期筛选之后按扩展名分流：命中 `media.IMAGE_EXTS` / `media.VIDEO_EXTS`
的文件直接调 `media.register_standalone_image` / `register_video`，不写
`occurrences`。去重口径与文档一致（sha256）：重复扫描幂等，同一张图出现在两个
日期目录也只占一个 `IMG-####`（第二次回执 `created=False`）。扩展名集合只定义在
`media.py`，`ingest`（分流）与 `archive`（拦截）都 import 它，不另开模块。

**2. scan 是媒体入口，媒体归属在扫描时定。**
`scan --project` / `--tag` 作用于本次登记的媒体（主归属 + 关联项目标签，语义
同文档，见 ADR-0002 第 4 条）。图片 caption 沿用现有开关：配置了视觉模型密钥
才生成，`--no-caption` 可关；生成失败只回执、不挡入库（ADR-0002 第 2 条）。
代价是 caption 的墙钟成本落在扫描上：一张图一次 HTTP，`--no-caption` 是省钱
省时的逃生口。

**3. `--no-media` 保留旧路径，做逃生舱。**
开了分流就不能再「媒体也登记成出现」（否则同一文件两处记，语义打架），所以
显式开关 `--no-media` 才退回旧行为。它服务于排查与历史目录，不是常规用法。

**4. `new-doc` 对媒体不再产出半成品。**
`archive.new_doc` 在建任何实体之前按同一套扩展名判断，命中就抛 `ValueError`，
消息写明改用 `register-image` / `register-video`；CLI 捕获后 `SystemExit`，
不留 traceback、也不留半截文档。压缩包内 entry 的扩展名同样适用这条拦截。

**5. scan 的返回值是结构化回执，不是计数。**
`ingest.scan_dir` 返回 `ScanStats(occurrences, images, images_new, videos,
videos_new)`，CLI 打印「文档 N / 图片 X（新 Y）/ 视频 Z（新 W）」。出现数与媒体
数混成一个整数的话，操作者看不出这次扫进来的是文档还是截图。

## 后果

- triage 队列只剩文档：`scan` 之后每条 pending 出现都值得人工判断，不会再出现
  「打开一看是张截图」的噪声。
- 微信截图/录屏零人工入库；`scan` 可以反复跑，编号不会漂（sha256 去重）。
- `scan` 变慢：媒体要算 sha256、落母本、跑图片尺寸/视频时长，配了密钥还要逐张
  调 VLM。`--no-caption` 去掉最贵的一步；不配密钥时默认不花这笔钱。
- 媒体不再有「来源出现」这层记录，溯源只靠 `images.source` / `videos.source`
  （登记时的文件路径）。同一份视频换路径重扫不会新增记录，只多一次读文件。
- `--no-media` 扫进来的媒体仍可能在 triage 时被 `new-doc` 拒绝；这是刻意的：
  拒绝信息直接告诉操作者该用哪个命令，或重扫时不加 `--no-media`。
- 已知限制（本票范围外）：**zip 包内的媒体仍走出现登记**——zip 展开在
  `_register_zip_entries` 里一次性登记，媒体分流只覆盖文件系统上的文件。要补
  的话是另一个票，且得先定「要不要把压缩包里的截图也抽出来入库」。
- 视频简介仍由人填（`set-intro`）；scan 只登记编号、母本、项目/标签和时长。
