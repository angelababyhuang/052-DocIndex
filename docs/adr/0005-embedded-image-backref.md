# ADR-0005：内嵌图 → 检索结果的图片回指

- 状态：已接受
- 日期：2026-11-06
- 相关票：#21（内嵌图 → WeKnora chunk 关联）、#15（WeKnora 派生索引）、#13（内嵌图抽取）、#14（媒体实体）

## 背景

#15 把文档现行版本的 md 整篇推成一条 WeKnora 知识条目，md 里图片的位置写的是
`{{IMG-0001}}` 这种引用（ADR-0001、spec §6.2）。#21 之前，这条引用进了 WeKnora
就只是一段文本：检索命中一段含图的正文，能看见 `{{IMG-0001}}` 字样，但没有任何
机器可读的路径把「这段正文」和真相源里那张图对应起来。图片母本只在真相源
（`archive/images/IMG-####.png`，`images.path` 记相对路径），并不在 WeKnora 里。

要做的是让「命中一段正文 → 知道它引用了哪几张图 → 找到母本打开」这条链闭合，
并且图不可用时不能把文本检索一起拖垮。

## 真机取证（2026-11-06，WeKnora v0.9.x，Lighthouse 实例）

先用临时知识库跑了一遍，结论是方案的直接依据（临时库用完即删）：

1. **标记原样保留**：手工 markdown 里写 `{{IMG-0001}}`，分块正文和
   `knowledge-search` 返回的 `content` 里都是原样的 `{{IMG-0001}}`，没有被
   markdown 解析器吃掉。
2. **检索结果带 chunk 身份**：`SearchResult` 含 `id`（分块 id）、`knowledge_id`、
   `start_at` / `end_at`、`chunk_type`，以及 `knowledge_custom_metadata`。
3. **`manual` 接口没有 metadata 参数**：`POST .../knowledge/manual` 只收
   `title` / `content` / `status` / `tag_ids` / `channel` / `process_config`。
4. **`PUT /api/v1/knowledge/:id` 能写 `custom_metadata`**：整体覆盖；校验是
   ≤20 个字段、键 1–64 字符、值 ≤1000 字符（超了返回 500 `invalid custom_metadata
   field`）。`GET /api/v1/knowledge/:id` 读回来是对象。
5. **检索结果里的 `knowledge_custom_metadata` 是拍平文本**：`键: 值`，多字段用
   换行分隔（WeKnora 的 `CustomMetadataText()`，键按字典序），不是 JSON 对象。
   空元数据返回 `null`。

## 决策

**1. 索引入口：把「图片清单」写进 knowledge 的 `custom_metadata`。**
`index.collect_units` 产出的文档条目带 `Unit.images`（具体在 `_document_units`
里从**实际要推送的 md** 抽 `{{IMG-####}}`，去重保序）；`index.index_units` 在创建
知识拿到 `knowledge_id` 后，用 `PUT /api/v1/knowledge/:id` 写
`custom_metadata.图片 = "IMG-0001,IMG-0002,…"`。没有内嵌图的文档不写（少一次
请求、也少一份噪声元数据）。编码/解析格式只定义一处（`manifest.py`），写
（index）和读（search）共用。

清单从 md 里取而不是从 `document_images` 里取：清单描述的是**这条知识条目正文
引用了哪些图**，md 的标记才是索引里真实存在的东西；`document_images` 仍是
「内嵌图」这个事实的真相源，两者各管一件事。

**2. 检索出口：分块标记优先，文档级清单兜底。**
`search.hit_image_codes(hit)` 返回两层：

- `chunk`：命中 `content` 里的 `{{IMG-####}}`。这是分块级的精确回指——正文写了
  哪张图，就是哪张图；裸写的编号（比如图片条目正文里的 `IMG-0001`）不算引用。
  WeKnora 的上下文补齐会把相邻块并进 `content`，其中的标记也算这条结果的图——
  它们仍是同一知识条目的上下文，打印出来不会误导；要精确到「原始检索块」得
  另存偏移映射，本票不做。
- `knowledge`：`knowledge_custom_metadata` 里的清单。它只到文档级，用来覆盖
  「分块边界刚好把标记切走」的情况；打印时对清单里多出来的图单独标「清单图片」。
- `unit_image`：命中本身就是图片条目（标题 `图片 IMG-0001`）时，条目就是那张图，
  标「本图」走同一条母本解析——caption 命中也能回到母本。

**3. 编号 → 母本走真相源，不把图片二进制上传 WeKnora。**
`media.resolve_image` 查 `images.code → path`，相对路径按受管目录解析成绝对路径
（COS 等远程地址原样返回）。两边的取舍：

- 不把图片当 knowledge/附件传 WeKnora：真相源纪律要求图片只有一份母本，上传
  会在云端再存一份派生二进制，还要处理「真相源删了、云端还在」的反向漂移；
  WeKnora 的 `image_vector_enabled` 默认关（ADR-0003），传上去也不产生图片向量。
- 不用 `resource://` / `?resource_urls=public` 的直链：那只对 WeKnora 自己收的
  文件有效；我们的图不在它那儿。将来如果接入 COS，`images.path` 换成 URL 后
  `resolve_image` 原样返回即可，不用改调用点。

**4. 每一层失败都降级，不挡文本。**
- 索引：清单 PUT 失败只记 `IndexReport.metadata_warnings`，CLI 打
  `⚠ 图片清单写入失败：…`，知识条目照常入索引（图回指降级，文本检索不受影响）。
- 检索：真相源没打开（文件不存在、只读打开失败）、编号未登记、母本文件丢了，
  都只打印编号 + 原因（「真相源不可用」「未登记」「母本缺失：<路径>」）；
  文本命中照常返回。搜索用只读连接打开真相源，**不会凭空建库**。

**5. 清单超长就截断并记总数。**
`custom_metadata` 值上限 1000 字符，一个文档引用的图可能超过 100 个编号。超长时
按顺序截断到装得下为止，并加 `图片数 = 原始总数`；分块正文里的标记不受截断影响，
所以截断只损失「文档级兜底」，不影响分块级回指。`图片数` 同样会出现在检索结果的
`knowledge_custom_metadata` 里，CLI 在清单条数少于总数时打印
`（图片清单只列了前 N 张，共 M 张）`，不把截断藏起来。

## 后果

- 检索命中含图正文时，`search` 输出会多一行
  `图片 IMG-0001  /…/archive/images/IMG-0001.png`（清单里多出来的标
  `清单图片`，图片条目自己标 `本图`）；`python -m docarchive search` 因此需要读
  真相源，新增 `--db` 选项。`index --dry-run` 也会在文档条目后显示 `图片 N`，
  方便先确认清单会带哪些图。
- 「并打开」这一步交给调用方：CLI 打印母本绝对路径（spec §7.1 的承诺），不在
  工具里拉起图片查看器；飞书/Web 入口要看图得等图片进 COS（见下一条）。
- 索引每条**有内嵌图的文档**多一次 PUT；没有图就零额外请求。重建索引（`--rebuild`）
  会连同清单一起重建，不需要单独的台账。
- WeKnora 的飞书渠道 / Web 前端**看不到图**：图片二进制不在 WeKnora 里。第一阶段
  的「看到图」是通过命令行打印本地母本路径实现的；要在 WeKnora 界面里渲染，
  得等图片进 COS 并用公开直链（第二阶段）。
- 清单会参与 WeKnora 的摘要生成与文档级模型上下文（`CustomMetadataText()` 的
  设计用途）；`图片: IMG-…` 对摘要来说是弱信息，但可接受。元数据变化会触发一次
  文档摘要刷新（WeKnora 行为），不阻塞入索引。
- 截断的清单只提示总数，不补全：`search` 会说「共 M 张、只列了前 N 张」，剩下列表
  去真相源查 `document_images` / md 标记——派生索引本来就不承诺完整。
- 分块级回指依赖「标记原样保留在 chunk 正文里」。这条已实测（第 1 点），但不是
  WeKnora 的书面契约；它一旦变化，退路是 `knowledge_id` + `GET /api/v1/chunks/:knowledge_id`
  的 `start_at` / `end_at` 做偏移映射（本票不做，先在 `docs/weknora-api.md` 记下）。
