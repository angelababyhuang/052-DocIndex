## 完成

检索命中一段含 `{{IMG-####}}` 的正文时，现在能拿到 IMG 编号、并从真相源解析到图片母本路径了；图缺失/未登记/真相源不可用时只标注原因，文本命中照常返回。TDD 实现，本机 **209 passed / 1 skipped**，CI（Linux/Python 3.12）**208 passed / 2 skipped**。

**验收标准全部达成**：

1. ✅ 检索命中含 `{{IMG-####}}` 的正文 → 拿到 IMG 编号（`search.hit_image_codes` 从命中 `content` 抽标记；`test_hit_image_codes_extracts_chunk_markers`；真机 `search` 输出 `图片 IMG-0001`）
2. ✅ 编号 → `images.path` 母本并打开（`media.resolve_image` 解析成绝对路径，CLI 打印 `图片 IMG-0001 /…/archive/images/IMG-0001.png`；`test_cli_search_prints_image_master_path`；真机 CLI 跑通）
3. ✅ 图片不可用时文本检索照常返回：母本文件丢失 → `母本缺失`、编号未登记 → `未登记`、真相源不存在/旧 schema → `真相源不可用：…`，命中正文照常打印（三条 CLI 用例 + `test_resolve_image_*`）；索引侧清单 PUT 失败只记 `metadata_warnings`，知识条目照常入索引（`test_index_units_degrades_when_manifest_write_fails`）
4. ✅ 方案进 ADR：新增 `docs/adr/0005-embedded-image-backref.md`，ADR-0003 增加指针；`GLOSSARY.md` 补「图片引用标记 / 知识条目图片清单 / 图片回指」；spec §7.1 与 `docs/weknora-api.md` 同步

**设计（ADR-0005）**：

- **索引**：`index._document_units` 从实际推送的 md 里抽 `{{IMG-####}}`（去重保序）→ `Unit.images`；`index.index_units` 建完知识后 `PUT /api/v1/knowledge/:id` 写 `custom_metadata.图片 = "IMG-0001,IMG-0002,…"`。没图的文档不写。编码/解析集中在 `manifest.py`（值 ≤1000 字符，超了截断并带 `图片数`）。
- **检索**：`search.hit_image_codes` 三层回指——正文标记（分块级）、`knowledge_custom_metadata` 清单（文档级兜底）、图片条目自身标题（caption 命中时标「本图」）。清单被截断时 CLI 提示「只列了前 N 张，共 M 张」。
- **母本解析**：不把图片二进制传 WeKnora，也不依赖它的 `resource://` 直链；`media.resolve_image` 查真相源 `images.path`（相对受管目录 → 绝对路径，COS URL 原样返回）。搜索用只读连接打开真相源，不凭空建库。

**真机验证（2026-11-06，WeKnora v0.9.x，Lighthouse；临时库用完即删）**：

- `{{IMG-####}}` 在分块正文和 `knowledge-search` 返回的 `content` 里原样保留；
- `manual` 接口没有 metadata 字段，只能事后 `PUT`；`custom_metadata` 限 ≤20 字段、值 ≤1000 字符（超了返回 500）；
- 检索结果里 `knowledge_custom_metadata` 是拍平文本（`键: 值`、多行），解析按行取 `图片:`；
- 端到端：临时真相源 + 真 WeKnora 跑 `index --wait` → `search`，文档命中打印 `图片 IMG-0001 /…/IMG-0001.png`，图片条目命中打印 `本图 IMG-0001 …`。

**两轴 code review 后修掉的问题**：

- 硬违规：清单追加图原标「文档图片」，与 GLOSSARY 的 `_Avoid_` 冲突 → 改「清单图片」
- 判断项：图片清单编解码收进 `manifest.py`（index/search 共用，search 不再 import index）；`imagelib.codes_in` → `all_codes` 与 `ref_codes` 对齐；测试共用 `support.register_fake_image`
- 截断不再静默：`图片数` 也随检索结果返回，CLI 明确提示「只列了前 N 张，共 M 张」
- 命中图片条目（caption）补上母本回指（标「本图」）
- 去掉真相源缺失时的重复提示；旧 schema/损坏库的失败原因带具体异常文本
- ADR/接口文档与实现对齐（`GET /api/v1/chunks/:knowledge_id` 命名、`--dry-run` 显示张数、`图片数` 可见性）

**已知限制（接受的）**：

- 分块级回指依赖「标记原样保留在 chunk 正文里」（已实测，非 WeKnora 书面契约）。将来若变化，退路是用命中 `id` + `GET /api/v1/chunks/by-id/:id` 的 `start_at`/`end_at` 做偏移映射，本票不做。
- 我们拿到的是命中结果返回的 `content`（含 WeKnora 的上下文补齐块），不是「原始检索块」本身；同一知识条目的相邻标记会一并列出。
- 「并打开」按 spec §7.1 落实为打印母本绝对路径，不在工具里拉起图片查看器。
- 图片二进制不进 WeKnora，飞书渠道/Web 前端看不到图；要在 WeKnora 界面渲染得等图片进 COS（第二阶段）。
- 清单超过约 110 个编号会截断（值 ≤1000 字符），只损失文档级兜底；分块正文标记不受影响。

实现：`imagelib.ref_codes/all_codes/unique_codes`、`manifest.encode/decode`、`index.Unit.images` + 清单写入、`weknora.set_knowledge_metadata`、`media.resolve_image`、`search.hit_image_codes`、`db.connect_readonly`、CLI `search --db` 与图片行输出、`docs/adr/0005` 及配套文档。
