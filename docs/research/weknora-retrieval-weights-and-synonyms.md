# WeKnora 混合检索参数与同义词能力调研（#22）

调研日期：2026-11-06
调研问题：052 的 `search` 要支持「混合检索权重/阈值 + 同义词兜底」（spec §7.3，票 #22），动手前必须钉死：
WeKnora 到底把哪些旋钮暴露给逐请求调用、RRF 权重能不能按请求改、有没有同义词/查询扩展能力、同义词表应该放在 052 真相源的哪里。
固定版本：WeKnora **v0.9.0**（tag `f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7`；调研时 main 为 `e3fb2335`）。本机部署为 v0.9.x，线上只读实测见 §4.3。
取证方式：仓库 `website-docs/` 与 Go 源码（固定 commit 链接）+ 本机 `GET /api/v1/tenants/kv/retrieval-config` 只读探测 + 052 现有代码。

---

## 0. 结论速览

| # | 结论 | 置信度 | 一手出处 |
|---|---|---|---|
| 1 | `knowledge-search` 逐请求可覆盖的召回参数只有 `vector_threshold`、`keyword_threshold`、`match_count`、`disable_keywords_match`、`disable_vector_match`、`rerank`；**没有 RRF 权重字段** | 已验证（源码） | [`SearchKnowledgeRequest`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/session/types.go#L108-L123)、[`SearchParams`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/search.go#L259-L291) |
| 2 | 混合检索确实是**加权 RRF**，默认 `k=60`、向量权重 `0.7`、关键词权重 `0.3`；spec §7.3 的 0.7/0.3 有出处，**但这是租户级配置，不是逐请求参数** | 已验证（源码） | [`fuseWithRRF`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search_fusion.go#L170-L205)、[`RetrievalConfig.GetEffectiveRRFWeights`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L100-L115) |
| 3 | 权重/`k` 只能改**空间级** `GET/PUT /api/v1/tenants/kv/retrieval-config`（GET Viewer、PUT Admin），PUT 是**整体替换**，作用域是整个租户（聊天、Agent、检索 API 共用） | 已验证（源码） | [路由](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/router/routes_auth_tenant.go#L87-L88)、[handler](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/tenant.go#L1719-L1794) |
| 4 | 本机实例的 retrieval config 当前全为 0/空（没有显式配置），因此实际生效的就是全部默认值（0.15 / 0.3 / 50 / 10 / 0.2 / 0.7 / 0.3 / k=60） | 已验证（真机只读） | 本文 §4.3 |
| 5 | WeKnora **没有同义词/别名词典**，也没有「查询扩展」类 API；`tag_ids` 是过滤（`tag_id IN`），不是同义词扩展 | 已验证（源码 + 文档检索） | [`postgres/repository.go`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/repository/retriever/postgres/repository.go#L253-L265)；`website-docs` 全库无同义词章节 |
| 6 | WeKnora 有一个**本地**查询扩展（去停用词、词序、短语抽取），但它只在聊天管线、召回不足时触发，且只走关键词；`knowledge-search` 没有设置该开关，不用它 | 已验证（源码，未做运行时验证） | [`query_expansion.go`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/chat_pipeline/query_expansion.go#L22-L50)、[`search.go`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/chat_pipeline/search.go#L140-L146) |
| 7 | 关键词侧是 ParadeDB `content ||| ?`（**匹配任一词**），所以把同义词直接拼进一个查询串确实能扩大关键词召回；风险在向量侧查询被稀释 | 已验证（源码） | [`postgres/repository.go`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/repository/retriever/postgres/repository.go#L262-L265) |
| 8 | 052 真相源现在只有 `projects(name)`，没有别名列/表；同义词表要新加，且因为扩展在查询侧完成，**不需要重建索引** | 已验证（本仓库） | `src/docarchive/db.py#L11-L14`、`src/docarchive/index.py#L160-L182` |

---

## 1. 版本与取证边界

- 代码链接全部固定在 **v0.9.0** tag：`f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7`。
- v0.9.0 与调研时 main（`e3fb2335`）的检索相关文件抽查一致（`RetrievalConfig`、`SearchParams`、`fuseWithRRF` 均存在且字段相同）；本文以 v0.9.0 为准。
- 线上实例版本为 v0.9.x；只做了 `GET /tenants/kv/retrieval-config`（只读、不打印凭据），没有写配置、没有建/删知识库。
- 「已验证（源码）」= 直接读到实现；「已验证（真机）」= 对线上只读接口的实测；「推断」= 由代码行为推出但未跑运行时实验。

---

## 2. 逐请求检索参数

### 2.1 `POST /api/v1/knowledge-search`

路由：[`routes_chat.go#L155-L158`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/router/routes_chat.go#L155-L158)（Viewer + `retrieve` API key）。

请求结构：[`internal/handler/session/types.go#L108-L123`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/session/types.go#L108-L123)

| 字段 | 类型 | 必填 | 语义 / 默认 |
|---|---|---|---|
| `query` | string | 是 | 查询文本；为空返回 400 |
| `knowledge_base_id` / `knowledge_base_ids` | string / []string | 条件 | 检索范围；与 `knowledge_ids`、tag scope 至少给一个 |
| `knowledge_ids` | []string | 否 | 限定文档 |
| `tag_ids` | []string | 否 | 标签过滤（**过滤**，不是查询扩展） |
| `mentioned_items` | []object | 否 | 带 KB 范围的标签提及 |
| `vector_threshold` | `*float64` | 否 | 0–1；**省略**时用空间配置（默认 0.15）；显式 `0` 表示不设阈值（全保留） |
| `keyword_threshold` | `*float64` | 否 | 0–1；省略用空间配置（默认 0.3）；显式 `0` 表示不设阈值 |
| `match_count` | int | 否 | 返回条数；0/省略用空间 `rerank_top_k`（默认 10）；上限 200；会把召回深度抬到不小于它 |
| `disable_keywords_match` | bool | 否 | 只走向量；与 `disable_vector_match` 同时为 true 返回 400 |
| `disable_vector_match` | bool | 否 | 只走关键词 |
| `rerank` | object | 否 | `enabled` / `model_id` / `top_k` / `threshold`；`knowledge-search` 不传时默认启用（用空间配置的 rerank 模型） |

校验与覆盖逻辑：

- [`knowledgeSearchOptions`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/session/qa.go#L1003-L1026)：`match_count` 不能为负、不能超过 `types.MaxRequestedResults`（200）；两个 disable 不能同时为 true；`rerank` 走 `Validate()`。
- [`applyKnowledgeSearchOverrides`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/session_knowledge_qa.go#L941-L963)：阈值只在请求给了指针时覆盖；`match_count > 0` 时把 `RerankTopK` 设为它；`EmbeddingTopK = max(EmbeddingTopK, RerankTopK)`。
- `match_count` 的 200 上限来自 [`MaxRequestedResults`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L46-L50)。

> 注意指针语义：请求里**不传** `vector_threshold` 与传 `0` 是两件事——前者沿用空间配置，后者把阈值压到 0（保留全部召回）。CLI 要区分「未指定」和「显式 0」。

官方文档同样写明「省略时用空间检索配置（默认 0.15 / 0.3）」「`match_count` 上限 200，召回深度自动加大」：[`website-docs/04-api/02-api-chat.md#L449-L456`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/website-docs/04-api/02-api-chat.md#L449-L456)。

### 2.2 `POST /api/v1/knowledge-bases/:id/hybrid-search`

底层召回接口，请求体是 [`SearchParams`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/search.go#L259-L291)：

- 同样支持 `vector_threshold`、`keyword_threshold`、`match_count`、`disable_keywords_match`、`disable_vector_match`、`knowledge_ids`、`tag_ids`、`rerank`。
- `match_count` 在 `SearchParams` 里还有 agent schema 的 `x-agent-maximum=30`（只是暴露给 Agent 工具的上限，不是 HTTP 校验上限）。
- **同样没有 RRF 权重/`rrf_k` 字段**。

### 2.3 结论

`search` 能透传给 WeKnora 的只有：**阈值、`match_count`、两路开关、rerank**。RRF 权重不在逐请求协议里，票面「向量/关键词权重或阈值」中的「权重」不能按请求透传。

---

## 3. 融合算法与权重

### 3.1 实现

[`fuseWithRRF`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search_fusion.go#L170-L205)：

```
rrfScore = vectorWeight / (k + vectorRank)
         + keywordWeight / (k + keywordRank)
maxRRF   = (vectorWeight + keywordWeight) / (k + 1)
score    = rrfScore / maxRRF        # 归一化到 [0,1]
```

- `k`、两路权重来自租户 `RetrievalConfig`；`rank` 在每个检索结果列表内按各自分数计算（1-indexed），同一 chunk 取最好名次。
- 只有一路结果时不跑 RRF：仅向量保留原始相似度；仅关键词把 BM25 按本列表最高分缩放到 [0,1]（[`fuseOrDeduplicate`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search_fusion.go#L58-L90)）。

### 3.2 默认值

[`RetrievalConfig`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L12-L38) + 有效值方法：

| 字段 | JSON 键 | 默认（未配置/≤0） | 出处 |
|---|---|---|---|
| `embedding_top_k` | `embedding_top_k` | 50 | [`GetEffectiveEmbeddingTopK`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L52-L58) |
| `vector_threshold` | `vector_threshold` | 0.15 | [`GetEffectiveVectorThreshold`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L60-L66) |
| `keyword_threshold` | `keyword_threshold` | 0.3 | [`GetEffectiveKeywordThreshold`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L68-L74) |
| `rerank_top_k` | `rerank_top_k` | 10 | [`GetEffectiveRerankTopK`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L76-L82) |
| `rerank_threshold` | `rerank_threshold` | 0.2 | [`GetEffectiveRerankThreshold`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L84-L90) |
| `rrf_k` | `rrf_k` | 60 | [`GetEffectiveRRFK`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L92-L98) |
| `rrf_vector_weight` | `rrf_vector_weight` | 0.7 | [`GetEffectiveRRFWeights`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/retrieval_config.go#L100-L115) |
| `rrf_keyword_weight` | `rrf_keyword_weight` | 0.3 | 同上 |

> 因此 spec §7.3 的「默认 0.7/0.3」**不是自拟**，是 WeKnora 的默认；但它是**租户级融合配置**，不是单次检索参数。

### 3.3 与 `knowledge-search` 的关系

`knowledge-search` 在 [`session_knowledge_qa.go#L837-L841`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/session_knowledge_qa.go#L837-L841) 读取租户 `RetrievalConfig`，随后 [`#L886-L896`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/session_knowledge_qa.go#L886-L896) 把阈值/TopK 放进 `ChatManage`；`fuseOrDeduplicate` 拿同一份配置做 RRF。请求层无法覆盖权重。

---

## 4. 租户检索配置（唯一能改权重/`k` 的地方）

### 4.1 端点与权限

- `GET /api/v1/tenants/kv/retrieval-config`：Viewer+；API key 需 `manage_tenant_settings` 或 full。
- `PUT` 同路径：Admin；API key 同上。
- 路由：[`routes_auth_tenant.go#L87-L88`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/router/routes_auth_tenant.go#L87-L88)。

### 4.2 形状与更新语义

- GET 返回 `{"success":true,"data":{RetrievalConfig}}`（[`GetTenantRetrievalConfig`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/tenant.go#L1719-L1736)）。
- PUT 把请求体绑定到一个**全新的** `RetrievalConfig`，再 `tenant.RetrievalConfig = &cfg`（[`updateTenantRetrievalConfigInternal`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/tenant.go#L1738-L1794)）——即**整体替换**，不是局部合并。只发 `rrf_*` 会把其他字段清零（清零后按默认生效，已有的自定义阈值会丢）。
- 校验：`vector_threshold`/`keyword_threshold` 0–1；`rerank_threshold` -10–10；`embedding_top_k`/`rerank_top_k` 0–200。**`rrf_k` 与两路权重没有范围校验**（`<=0` 时回退默认）。
- 作用域：租户（空间）级，聊天问答、Agent 工具、`knowledge-search`、`hybrid-search` 共用；改一次影响所有检索入口。
- 文档也写明 RRF 参数在「检索设置」里可调，且「只有一路结果时不执行双路 RRF」：[`website-docs/03-features/05-retrieval-engines.md#L16-L20`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/website-docs/03-features/05-retrieval-engines.md#L16-L20)。

### 4.3 线上实例只读实测（2026-11-06）

用 052 现有 `weknora.WeKnoraClient` 登录后只读 GET（未打印凭据、未写任何配置）：

```json
{
  "data": {
    "embedding_top_k": 0,
    "vector_threshold": 0,
    "keyword_threshold": 0,
    "rerank_top_k": 0,
    "rerank_threshold": 0,
    "rerank_model_id": ""
  },
  "success": true
}
```

`0` 表示未显式配置；由于 `GetEffective*` 把 `<=0` 当默认，实际生效值就是 §3.2 的默认表。`rrf_*` 字段因 `omitempty` 未出现，同样走默认 0.7/0.3、k=60。

> 风险提示：这次只读探测只能证明「当前没有显式覆盖」，不能证明我们的账号有 PUT 权限（PUT 要 Admin）。**不要**在 #22 里让 CLI 随手 PUT 空间配置：它会改掉所有入口的检索行为。

---

## 5. 同义词 / 查询扩展能力

### 5.1 没有同义词词典

- `website-docs` 全库检索「同义词 / synonym / query expansion」没有同义词配置章节；`knowledge-search` / `hybrid-search` 的请求结构里也没有 synonym 字段。
- 源码里 `synonym` 的出现都在无关处：知识库描述提示词要求「合并同义词」、Wiki 概念抽取的 `aliases` 字段、topic 归一化注释——都不是检索期同义词表。
- `tag_ids` 在下推到引擎时是 `tag_id IN (...)`（[`postgres/repository.go#L253-L260`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/repository/retriever/postgres/repository.go#L253-L260)），是**过滤/收窄**，不会把「连杆」扩展到「连接杆」。

### 5.2 WeKnora 自带的「查询扩展」是什么

[`query_expansion.go`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/chat_pipeline/query_expansion.go#L17-L50)：

- 触发条件：`EnableQueryExpansion` 为真 **且** 初次召回数 `< max(1, EmbeddingTopK)`；
- 变体是**本地规则**：去停用词、关键短语、分隔符切分、去疑问词（[`expandQueries`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/chat_pipeline/query_expansion.go#L123-L186)）；
- 只走**关键词**召回（`DisableVectorMatch: true`），关键词阈值 ×0.8，TopK ×2；
- 不调用 LLM，也没有同义词词典。

关键：这是**聊天管线**的行为。`knowledge-search` 构造 `ChatManage` 时没有设置 `EnableQueryExpansion`（[`session_knowledge_qa.go#L880-L900`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/session_knowledge_qa.go#L880-L900)），零值 false；它只跑 `CHUNK_SEARCH / CHUNK_RERANK / CHUNK_MERGE / FILTER_TOP_K`（[`#L978-L984`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/session_knowledge_qa.go#L978-L984)）。所以 052 现在用的检索接口**不做**查询扩展，更不做同义词。

### 5.3 结论

同义词/别名表只能由 052 在客户端维护和应用。

---

## 6. 客户端查询扩展的可行路径（基于 WeKnora 的行为）

关键词侧语义是 `content ||| ?`，ParadeDB 的 `|||` 是「匹配任一词」（[`postgres/repository.go#L262-L265`](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/repository/retriever/postgres/repository.go#L262-L265)）。这决定了拼词不会像 AND 那样把召回压到 0。

| 方案 | 做法 | 优点 | 代价/风险 |
|---|---|---|---|
| A. 单查询拼词 | 把同义词并入一个 query，例如 `连杆 连接杆 间隙`，一次 `knowledge-search` | 改动最小；BM25 是 OR，能扩大关键词召回；只花一次 rerank | 向量侧是对整串做 embedding，语义可能被稀释；排序解释变难 |
| B. 多查询合并 | 每个变体各跑一次 `knowledge-search`，客户端按 `knowledge_id + chunk` 去重合并 | 每个变体独立 rerank，效果可控 | N 次 rerank 成本；跨查询分数不可直接比较，需要定义合并规则（取 max？RRF？） |
| C. 关键词补召回 | 原查询正常跑；变体用 `disable_vector_match: true` + 降低 `keyword_threshold` 再跑，合并到结果 | 模仿 WeKnora 自己的扩展思路，避免向量稀释 | 只补关键词；合并后的分数与 rerank 结果不同源；可能引入低分噪声 |
| D. 别名→项目标签兜底 | 查询命中项目别名时，映射到规范项目名，用 `tag_ids` 过滤重试 | 与现有 `项目/<名>` 标签天然契合；适合「项目简称↔全称」 | `tag_ids` 是收窄不是扩展；只覆盖项目名，不覆盖通用术语；可能把结果限死在错误项目 |

> 这是设计选项，不是结论。A 最省，B 最可控，C 最像 WeKnora，D 只能算「同义词标签兜底」的字面实现。

---

## 7. 052 真相源里同义词表可以放哪

### 7.1 现状

- `projects(id, name UNIQUE)`，没有别名列（`src/docarchive/db.py#L11-L14`）。
- 文档主归属 `documents.project_id`；关联项目在 `document_project_tags`（`db.py#L96-L100`）。
- WeKnora 标签由项目名生成：`项目/<name>`（`src/docarchive/index.py#L160-L182`）。
- 目前没有任何 synonym/alias 表，也没有配置文件。

### 7.2 可选存法

| 方案 | 形状 | 适合 | 代价 |
|---|---|---|---|
| 1. 全局同义词表 | `synonyms(term TEXT, expansion TEXT, PRIMARY KEY(term, expansion))` | 通用术语（连杆↔连接杆）、跨项目 | 需要人工维护；没有项目语义；要防自反/环 |
| 2. 项目别名表 | `project_aliases(project_id, alias UNIQUE)` | 项目简称↔全称；可同时服务 `--tag` 与查询扩展 | 只覆盖项目名；要决定别名是否也进 WeKnora 标签（进标签就要重建索引） |
| 3. 配置文件 | `data/synonyms.yaml` / `config/synonyms.yaml` | 想手改、想进 git diff、不想动 schema | 不在真相源事务里；没有校验；多机部署要同步 |
| 4. 塞进 `projects.name` | 名称里写别名 | 零新表 | 污染标签名与精确匹配；不推荐 |

### 7.3 对索引的影响

- 若扩展只在**查询时**做（A/B/C/D 都是），同义词表变化**不需要重建 WeKnora 索引**。
- 若把别名写进 WeKnora 标签（例如 `项目/浮标项目` 之外再加 `别名/浮标`）或写进文档正文，才需要重建索引；这会改变标签词表与检索口径，应单独决策。

---

## 8. 对 #22 的落地含义

### 8.1 CLI 能透传什么

基于 §2：

- 可以加：`--vector-threshold`、`--keyword-threshold`（0–1，缺省不传=用空间配置；显式 0=不设阈值）、`--match-count`（0–200，替代/补充现有 `--limit`）、`--no-rerank`、`--rerank-threshold`、`--rerank-top-k`、`--vector-only`、`--keyword-only`。
- 不能逐请求透传：`rrf_k`、`rrf_vector_weight`、`rrf_keyword_weight`、`embedding_top_k`。这些只能改空间配置（PUT，Admin，整体替换，影响所有入口）。
- 如果确实要让用户「调权重」，可选做法是加一个**独立的、显式的**管理命令（先 GET、再合并、再 PUT），并明确提示「改的是整个空间」；不建议把它做成 `search` 的隐式副作用。是否要做，交给 `/grill-with-docs` 拍板。

### 8.2 默认行为

- 票面要求「不传参数时与现在一致」。现有 CLI 总是发 `match_count`（默认 5），所以「默认」其实已经覆盖了空间的 `rerank_top_k`。新增阈值/rerank 参数应保持「不传即不发」，避免悄悄改口径。
- 阈值要用 `None` 表示未指定，不能用 `0.0` 兜底，否则会把「用空间配置」变成「关闭阈值」。

### 8.3 同义词

- 必须在 052 客户端做（§5）。
- 表放哪、谁维护、扩展用 A/B/C/D 哪种，需要 grill 决定；票面要求写 ADR。
- 推荐在 grill 时先确认三个问题：
  1. 同义词是「替换」（只发规范词）还是「扩展」（原词+变体都发）？
  2. 扩展走单查询拼词（A）还是多查询合并（B/C）？前者便宜但向量稀释，后者贵但可控。
  3. 别名是否只服务项目名（`project_aliases`），还是同时支持通用术语（全局 `synonyms`）？
- 还要定维护入口：手工 SQL？CLI 子命令？YAML + 导入？以及「同义词表为空/命中环」的降级（退回原查询）。

### 8.4 建议先做的一次实测（进 #22 实现前）

在临时 KB 上用同一份文档跑三组 `knowledge-search`，确认：

1. 只改 `vector_threshold` / `keyword_threshold` 是否真能看出结果差异；
2. 单查询拼同义词（A）是否比原查询多召回、有没有明显掉精度；
3. `disable_vector_match: true` + 低关键词阈值（C）是否能把「换个说法」的文档捞回来。

这三组实验不需要改 WeKnora 配置，只用逐请求参数，风险低。

---

## 9. 未核实 / 风险清单

| 项 | 状态 | 说明 |
|---|---|---|
| 线上实际检索驱动的关键词算子 | 推断 | 本文按 spec 的 ParadeDB/Postgres 路径解释 `|||`；未从线上实例确认 `RETRIEVE_DRIVER`。若换 Qdrant，关键词也是 token OR（文档表），结论方向一致，但分词/打分不同。 |
| `knowledge-search` 不做查询扩展 | 已验证（源码），未做运行时反证 | 依据是 `ChatManage` 未设 `EnableQueryExpansion` + 事件列表；没有构造低召回场景做对照实验。 |
| 同义词拼进单查询的向量稀释 | 未实测 | 需要 §8.4 的实验。 |
| PUT retrieval-config 的权限 | 未实测 | 只读 GET 可用；PUT 要 Admin，本文没有写。 |
| `rrf_*` 的 PUT 行为 | 已验证（源码），未实测 | 无范围校验；`<=0` 回退默认；PUT 整体替换。 |
| 多查询合并的成本/排序 | 未实测 | rerank 调用次数与分数可比性需要实测。 |
| 版本漂移 | 低风险 | 线上 v0.9.x 与 v0.9.0 的检索代码结构一致；升级后重核本文链接。 |

---

## 10. 参考（固定版本）

- WeKnora v0.9.0：<https://github.com/Tencent/WeKnora/tree/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7>
- 检索 API：`website-docs/04-api/02-api-chat.md`、`website-docs/04-api/01-api-overview.md`
- 检索引擎与 RRF：`website-docs/03-features/05-retrieval-engines.md`
- RAG 管线（查询扩展、rerank、复合分）：`website-docs/02-architecture/04-rag-pipeline.md`
- 本仓库：`docs/spec.md` §7.3、`docs/weknora-api.md`、`src/docarchive/__main__.py`（`cmd_search`）、`src/docarchive/db.py`、`src/docarchive/index.py`
