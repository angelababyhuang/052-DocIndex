## 完成

自然语言检索继续走 WeKnora RAG；**同义词/别名/权重不做**（用户拍板，向量空间自然覆盖近义表达）。本票收窄为两件小事：检索命中文档时打印受管文件路径；修线上 WeKnora v0.8.0 下失效的 `--limit`。TDD 实现，本机 **228 passed / 1 skipped**，CI（Linux/Python 3.12）**227 passed / 2 skipped**。

**验收标准全部达成**：

1. ✅ 自然语言查询命中文档时打印受管文件绝对路径（`archive_path` 优先，原件缺失退 `markdown_path`）：真机 `search "浮标怎么上报"` → `文档 doc-2 /…/doc-2/v2-a.docx`；`test_cli_search_prints_document_path`、`test_resolve_document_*`
2. ✅ 旧索引只提示、不报错；真相源不可用/文件缺失同样降级，文本命中照常：`test_cli_search_hints_stale_index_without_document_ref`、`test_cli_search_reports_missing_document_file`、`test_resolve_document_reports_missing_file`
3. ✅ `--limit` 在 v0.8.0 生效：客户端截断（`hits[:limit]`）；`test_cli_search_truncates_hits_to_limit`，真机 `--limit 2` 只回 2 条
4. ✅ 不引入同义词/别名/权重；默认 `knowledge-search` 路径不变；`grep` 无相关代码
5. ✅ spec §7.1/§7.3/§7.4、`docs/weknora-api.md`、GLOSSARY（文档位置）、ADR-0005 已同步

**实现**：

- `index` 给文档 knowledge 的 `custom_metadata` 写 `文档=doc-<id>`（和 #21 的 `图片` 清单共用一份元数据）；`archive.document_ref/parse_document_ref` 收敛编码。
- `search` 解析 `文档`，`archive.resolve_document` 回查 `documents.current_version_id → versions.archive_path/markdown_path`，打印绝对路径。
- 旧索引：没有 `文档` 字段时输出一条 `index --rebuild` 提示；图片/视频条目用严格标题正则识别，普通文档标题不会被误判。

**真机发现与修复**：

1. **线上是 WeKnora v0.8.0**（`/api/v1/system/info`，commit `1edcd54`），`knowledge-search` **忽略** `match_count` / 阈值 / `disable_*` / `rerank`；v0.8.3 才支持。本票不升级，`--limit` 改客户端截断。
2. **元数据写入竞态**：解析完成会回写 knowledge 行，解析中写的 `custom_metadata` 会被覆盖（真机 3 条丢 1 条）。`index` 现在**先等解析完成（`wait_knowledge`）再 PUT**；等解析失败/超时/单条未完成时**不写元数据**、记 `parse_warnings`，避免静默丢失。
3. 解析失败（`failed`）可以安全写元数据，但会单独警告。

**两轴 code review 后修掉的问题**：

- 硬伤：`wait_knowledge` 在 try 外，失败会中断 index 且已建 KB 逼着重来 → 捕获、跳过元数据、记 `parse_warnings`
- 超时/未完成仍 PUT 会静默重现覆盖 → 只对 `completed/failed` 写
- 旧索引提示用标题前缀 `图片 ` 判断，文档标题以「图片」开头会漏提示 → 改用严格的条目标题正则
- `doc-<id>` 编解码分散在 index/archive → 收进 `archive.document_ref/parse_document_ref`
- ADR-0005 决策 1「没有内嵌图的文档不写」与实现漂移、weknora-api 把 `图片` 字段说成图片条目的 → 已改
- 补 COS 路径、缺文件、wait 失败、解析未完成、图片条目、标题前缀等用例

**已知限制**：

- 已有索引必须 `index --rebuild` 一次才有 `文档` 字段；旧索引只提示、不自动补。
- `wait_knowledge` 默认 300 秒；超大索引超时会跳过未完成条目的元数据并警告，需要重跑（`--rebuild`）。
- `archive_path`/`markdown_path` 都缺失时只打印原因，不猜路径；COS URL 原样返回。
- 同义词/别名/权重明确不做；阈值/开关的逐请求控制要等 WeKnora 升到 v0.8.3+，不在本票。

实现：`archive.resolve_document/document_ref/parse_document_ref`、`index.knowledge_metadata` + 解析后写元数据、`manifest.parse_fields/document_ref`、`search.document_ref/is_unit_hit`、CLI 文档路径/`--limit`/旧索引提示、spec/ADR/GLOSSARY/weknora-api 同步。
