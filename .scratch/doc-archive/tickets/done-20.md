## 完成

微信另存的图片/视频现在有自动入口了：`scan` 按扩展名把它们直接送进媒体库，不再登记成出现、也不会再产出「转换时才发现不支持」的半成品文档。TDD 实现，本机 **169 passed / 1 skipped**，CI（Linux/Python 3.12）**168 passed / 2 skipped**。

**验收标准全部达成**：

1. ✅ `scan` 后截图直接进图片库并分配 `IMG-####`，不再需要人工 `register-image`（`test_scan_routes_image_straight_to_library`：IMG-0001、出现 0 条、母本落盘）
2. ✅ 视频同理得到 `VID-####`，简介仍人填（`test_scan_routes_video_straight_to_library`；`set-intro` 不变）
3. ✅ 同一张图出现在两个日期目录只分配一次编号（`test_scan_same_image_in_two_date_dirs_gets_one_img_code`：看到 2 次、新入库 1、只有 IMG-0001）
4. ✅ `new-doc` 对媒体文件不再产出半成品文档（`archive.new_doc` 在建任何实体前拒绝；CLI 转成可读提示，提示指向「重扫目录」或 `register-image` / `register-video`）
5. ✅ 端到端跑通（scan → media）：混放 docx + png + mp4（含 zip 内 png/docx/mp4）的目录，输出「文档 2 / 图片 2（新 1）/ 视频 2（新 2）」，zip 里那张图与文件系统同图去重成同一个 IMG-0001；spec §5.1/§6.2 与 ADR-0004 已补

**设计（`docs/adr/0004`）**：媒体是顶层实体，不产生「出现」——出现是**文档**的登记与 triage 单位；`scan` 就是媒体入口（`--project` / `--tag` 在扫描时定归属，`--no-caption` 控制是否调 VLM）；zip 包内媒体同样走这条路（条目落临时文件登记，来源记 `<zip>!<entry>`）。没有 `--no-media`：退回「媒体当普通文件」只会留下永远 triage 不掉的出现。

**两轴 code review 后修掉的问题**：

- 硬违规：`scan` 吞掉 caption 失败 → `ScanStats.caption_errors` + CLI 逐条打印（ADR-0002 要求）
- `scan_dir` 缺 `archive_dir` 时会扫到一半才炸、前面已登记的不回滚 → 入口 fail-fast
- zip 内媒体没有进库路径 → 一并路由（含去重与 caption）
- 删掉 `--no-media`（死路）
- `tags` 生成器会被第一张图吃光 → 入口 `tuple(tags)`
- `ScanStats.images/videos` 名不副实 → `images_seen/videos_seen`
- 文档一致性：GLOSSARY「出现是登记与去重的最小单位」改为「文档的 triage 单位」；ADR-0004「两层身份模型」改为三层；补两条真实后果（`images.source` 只存位置不存来源类型、旧库里历史媒体文档不自动迁移）

**已知限制**：`#20` 之前建的库里若已有媒体型 Document，不会自动迁移（`convert` 仍会抛 `UnsupportedFormat`），需人工 `ignore` 或重扫 + 手工登记；`images.source`/`videos.source` 只记位置字符串，不记 wechat/zip/manual 来源类型。

实现：`ingest.ScanStats` / `scan_dir` 的媒体分流与 zip 分支、`media.IMAGE_EXTS`/`VIDEO_EXTS`/`is_media`、`media.register_*` 新增 `source` 覆盖、`archive.new_doc` 拦截、CLI `scan --project/--tag/--no-caption` 与输出、`docs/adr/0004`。
