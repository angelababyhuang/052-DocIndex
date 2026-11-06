## 完成

媒体实体登记（独立图 + 视频）已实现（TDD），本机 86 passed / 1 skipped（跳过的是默认关的真模型冒烟），真机带 caption 跑通：`RUN_LIVE_CAPTION=1` 下冒烟用例通过，docx 内嵌图实测拿到了可读中文描述。

**验收标准全部达成**：

1. ✅ 一张独立图入库分配 IMG-0001（sha256 去重，重复登记复用同一编号、不新建行、不改来源）
2. ✅ caption 由视觉模型生成且可读：真机实测中文微信截图，模型照抄了「连杆间隙已复测 0.02mm」并说清这是群聊截图；重复登记复用已有描述，不再调模型
3. ✅ 视频登记 VID-0001，简介可填可查（`--intro` 登记时填，`set-intro` 随时改）
4. ✅ 图片/视频与项目关联：`images.project_id` / `videos.project_id` 单归属 + `image_project_tags` / `video_project_tags` 关联标签；主归属定下后不被后来的登记改写

**caption 后端**：独立视觉模型（OpenAI 兼容 `/chat/completions`），默认智谱 `glm-4v-flash`（免费，key 已在 `.env`）；`CAPTION_API_KEY` / `CAPTION_BASE_URL` / `CAPTION_MODEL` 可换任意同形模型。**没有用 WeKnora 里的 qwen3.8-flash**：那个模型只在 WeKnora 自己的索引管线里跑，没有「描述这张图」的对外接口，而 caption 是真相源字段、必须能脱开派生索引重建。**这条偏离原拍板，已当面确认**（2026-11-06 用户选「保持现状：默认 GLM，spec 口径改过来」），spec §2.1/§7/§11 与 GLOSSARY 已同步，`docs/adr/0002` 明确记录了取代关系。caption 生成失败只让字段留空并回执错误，不阻断入库；网络调用在 DB 事务之外（先落库释放写锁）。

**内嵌图也补 caption**：`convert_version(captioner=...)` 抽完图后补描述，并让内嵌图继承文档的主归属；pandoc 的媒体名（rId20）不适合当显示名，显示名用「文档标题 图N」。

**元数据补齐**：图片宽高（Pillow）、显示名；视频时长（先 ffprobe，没有就读文件头解析 MP4/MOV 的 mvhd）、显示名。视频按流算哈希 + 流式复制，不把大文件读进内存。

**schema 补齐**：`images.project_id`（幂等 ALTER）+ `image_project_tags` / `video_project_tags`（与 `document_project_tags` 同构）；`get_project` 从 archive 挪到 `db.get_or_create_project` 给两处共用。

**CLI**：`register-image <文件> [--project] [--tag]... [--no-caption] [--recaption]`、`register-video <文件> [--project] [--intro] [--tag]...`、`set-intro <VID-0001> --intro`、`media [--project] [--tag]`（列图片/视频与描述/简介/标签）、`convert <版本ID>... [--no-caption]`。

实现：`src/docarchive/media.py`（登记原语）、`src/docarchive/caption.py`（VLM 客户端 + .env 读取）；内嵌图仍走 `convert.py`，共用 `imagelib` 的去重与编号。
