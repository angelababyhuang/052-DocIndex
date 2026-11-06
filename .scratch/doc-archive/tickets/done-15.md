## 完成

WeKnora 派生索引已接通（TDD，本机 132 passed / 1 skipped），真机全链路验证通过两轮：建库 → 建标签 → 推 3 条知识 → 等解析 → 检索命中 → 删库收尾。

**验收标准全部达成**：

1. ✅ SQLite 正文 / caption / 简介导出为**知识条目**：文档现行版本 md、图片（caption，无描述时退化成编号文本）、视频（简介，无简介时退化成编号文本）各一条（`index.collect_units`）。票面写的「分块」按 ADR-0003 交给 WeKnora 做（父子块/上下文补齐），我们只切知识条目
2. ✅ 向量化写入云端 WeKnora：用空间里已配的 embedding（`builtin-embedding-bge-m3`，名 `text-embedding-v4`），分块与向量化都在 WeKnora 内完成
3. ✅ 标签建齐四维，且四个维度**总是**建齐：`类别/研发|测试|管理|商务|资料`（五个，即使这次没有该类别文档）、`图片`、`视频`、`项目/<项目名>`（主归属 + 关联项目标签）
4. ✅ WeKnora 里能搜到正文片段：`search 连杆间隙` 命中文档正文（0.678）、图片 caption（0.700）、视频简介（0.488）；`--tag 项目/浮标项目` 过滤可用

**实现**：

- `index.py`：`collect_units`（真相源 → 知识条目）+ `index_units`（建 KB / 按需建标签 / 逐条推）；同名 KB 默认报错，`--rebuild` 显式删库重建
- `weknora.py`：stdlib HTTP 客户端（登录换 JWT、找/建/删 KB、挑 embedding 模型、按名取建标签、推 manual markdown、查/等解析状态、`knowledge-search`）
- `env.py`：`.env` 读取从 caption 里抽出来共用
- CLI：`index [--kb-name] [--embedding-model] [--rebuild] [--wait] [--dry-run]`、`search <关键词> [--tag] [--limit]`

**真机踩到的两个契约坑（已按实测修）**：

- `GET /knowledge-bases/:id/tags` 返回的是**分页信封**（`data.data`），不是文档写的 `data:[…]`
- 解析状态要查 `GET /api/v1/knowledge/:id`，`/knowledge-bases/:kb/knowledge/:id` 是 404

**设计取舍**（`docs/adr/0003`）：推知识条目而不是外部分块（分块交给 WeKnora）；标签扁平、四维编码在名字里；用 `knowledge/manual` 推 md 而不是上传原文件（避免二次解析、口径不一致）；不保存增量台账（台账会成为第二真相源）；认证走账号密码换 JWT（一期单人命令行，够用）。

**评审后又补的几处**：

- embedding 模型按 spec §7.2 钉死：默认按名字找 `text-embedding-v4`，找不到直接报错，不静默用「空间里第一个」模型
- 图片/视频维度标签总是建齐（与类别一致），不再依赖「这次有没有带 caption 的图」
- 没有 caption 的图 / 没有简介的视频也进索引，正文退化成「名称（编号，无描述）」，兑现 spec §7.1「仍按编号/项目可查」
- 客户端：`repr` 不再输出密码/token；统一兼容分页信封（`data` 与 `data.data` 两种形状，KB/模型/标签/检索都走同一个解包）；非 JSON 响应包成 `WeKnoraError`
- CLI：`find_kb` 进 try、默认库名/默认 embedding 名抽成常量、dry-run 的标签清单与真实运行一致（`plan_units` 纯函数，不再给 `index_units` 塞 `client=None`）
- 协议拆成 `IndexClient`（推）与 `SearchClient`（查）；图片/视频条目构造合并成一个 `_media_units`

真机验证用的临时知识库已删除，WeKnora 里只剩原有的「项目资料库」。
