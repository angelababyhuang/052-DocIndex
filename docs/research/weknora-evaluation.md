# WeKnora（腾讯开源）评估报告

面向"团队文档归档 + 语义搜索"系统的选型调研。所有结论均核对一手来源：GitHub 仓库 `Tencent/WeKnora`（main 分支，v0.9.0）、仓库内 `website-docs/` 文档源码（与线上 <https://weknora.weixin.qq.com/docs> 内容一致）、以及 GitHub API 元数据。

调研日期：2026-11-05。仓库 commit 快照：`main`，最近提交 2026-11-03。

**一句话结论**：WeKnora 是一个成熟度很高（36.5k star、v0.9.0、Go 1.26 + Vue3）的开源 RAG/Agent/Wiki 平台，**完全支持本地 Ollama 部署**（embedding 与 LLM 都可指向 `http://127.0.0.1:11434`），中文 embedding 默认用 `bge-m3`（1024 维）。对"团队文档归档 + 语义搜索"场景它功能高度匹配，但需注意两个限制：**自定义元数据不可直接用于检索过滤**（见 §7），以及项目演进极快（近 3 个月连发 v0.7.1→v0.9.0），API 存在 breaking change 风险。

---

## 1. WeKnora 是什么（架构总览）

### 1.1 定位

> [WeKnora](https://weknora.weixin.qq.com) is an open-source, LLM-powered knowledge framework for enterprise document understanding, semantic retrieval and reasoning.

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>（Overview 段）

README 自述的定位已超出"文档理解与检索框架"：**三个模式共享同一批知识库**——RAG 问答（Quick Q&A）、ReAct Agent（smart reasoning）、自动 Wiki（带知识图谱）。

> Use RAG to look things up, the agent for multi-step tasks, and the wiki to organize knowledge. All three work on the same knowledge bases.

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>

### 1.2 核心组件

文档明确描述为"主服务 + 前端 + 文档解析微服务"的三进程核心架构，加 PostgreSQL 与 Redis 两个基础设施依赖。

来源：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>

| 服务 | 镜像 | 端口 | 职责 |
| --- | --- | --- | --- |
| `app` | `wechatopenai/weknora-app`（Go） | `8080` | 主后端：REST API、RAG 检索、Agent 引擎、异步任务 worker、IM/Embed 渠道 |
| `frontend` | `wechatopenai/weknora-ui`（NGINX + Vue3） | `80` | Web UI，NGINX 反代 `/api` → `app` |
| `docreader` | `wechatopenai/weknora-docreader`（Python） | `50051`（仅 compose 内网） | 文档解析微服务：gRPC，25+ 格式解析与页面渲染 |
| `postgres` | `paradedb/paradedb:v0.25.11-pg17` | `5432` | 主数据库，ParadeDB = PostgreSQL 17 + BM25 + pgvector |
| `redis` | `redis:7.0-alpine` | `6379` | Asynq 任务队列、SSE 流管理、限流、模型并发闸门 |

来源：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>

### 1.3 入库流水线（ingestion）

关键点：**解析与分块分属两个进程，分块在 Go 侧完成**。

1. **docreader（Python gRPC）** 负责把文件/URL 转成 Markdown + 原始图片引用。gRPC 协议 `ReadResponse` 返回 `markdown_content` + `repeated ImageRef image_refs`（图片以 inline bytes 内联）+ 可选 `source_blocks`（把 markdown 的字符区间映射回原文件位置，用于引用定位）。
   来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

2. **Go app** 接着完成：图片持久化 → OCR/caption → 分块 → 向量化 → 写入向量/关键词索引。

   > docreader 是独立的 Python gRPC 服务，负责将文件或 URL 转换为 Markdown 和原始图片引用。Go 主服务随后完成分块、图片存储、OCR、图片描述及向量化。

   来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

3. **分块在 Go 侧**：

   > WeKnora 的分块在 **Go 侧**完成（`internal/infrastructure/chunker` 包），采用"文档画像 → 分层策略 → 结果校验 → 逐级回退"的自适应架构

   来源：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>

4. **快速开始里的入库时序**（官方 mermaid 摘要）：

   ```
   APP->>LLM: 连通性测试 (remote/check, embedding/test)
   APP->>DB: 分块 → Embedding → 向量/关键词索引 (Asynq 异步)
   APP->>DB: 混合检索 (向量+BM25) → RRF → Rerank
   ```

   来源：<https://weknora.weixin.qq.com/docs/01-getting-started/03-quickstart>

### 1.4 检索服务（retrieval）

分层架构：**Repository → KVHybridRetrieveEngine → Composite → Registry**。

- `KVHybridRetrieveEngine`（`retriever/keywords_vector_hybrid_indexer.go`）包装 Repository，负责按支持的检索类型计算 embedding 并写入。
- `Composite` 负责融合多引擎结果。
- Registry 在启动时解析 `RETRIEVE_DRIVER`（逗号分隔多驱动），逐驱动 `registry.Register(retriever.NewKVHybridRetrieveEngine(repo, engineType))`；**单个驱动初始化失败只记日志不阻断启动**。

来源：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>

检索入口是 `HybridSearch`（`knowledgebase_search.go`），按 KB 的向量存储绑定关系选择引擎，支持多库并发 fan-out。

### 1.5 RAG 问答管线（事件驱动插件框架）

阶段序列（`internal/application/service`）：

`LOAD_HISTORY` → `MEMORY_RECALL` → `QUERY_UNDERSTAND`（查询改写+意图识别+实体抽取）→ `CHUNK_SEARCH_PARALLEL`（并行检索 chunk + 图谱实体）→ `CHUNK_RERANK`（重排、复合打分、MMR、Wiki 加权）→ `WEB_FETCH` → `CHUNK_MERGE`（八步融合合并）→ `FILTER_TOP_K` → `DATA_ANALYSIS`（DuckDB 表格分析）→ `INTO_CHAT_MESSAGE` → `CHAT_COMPLETION` / `CHAT_COMPLETION_STREAM`

来源：<https://weknora.weixin.qq.com/docs/02-architecture/04-rag-pipeline>

### 1.6 API Server

Go + Gin（`github.com/gin-gonic/gin` v1.12.0），依赖注入 `go.uber.org/dig`。文档覆盖 **~360 个 API 端点**和 **~150 个环境变量**。

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>（Documentation 段）、<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>

---

## 2. 部署

### 2.1 是 Docker-based 吗？最小部署？

**是。** 标准部署 = Docker Compose，四个默认服务：`app` + `frontend` + `docreader` + `postgres`(ParadeDB) + `redis`。

```bash
git clone https://github.com/Tencent/WeKnora.git
cd WeKnora
cp .env.example .env    # Edit .env as needed, see comments in the file
docker compose pull     # Pull the latest images
docker compose up -d    # Start core services
```

| Service | URL |
|---------|-----|
| Web UI | `http://localhost` |
| Backend API | `http://localhost:8080` |
| Langfuse Tracing | `http://localhost:3000` |

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>（Run with Docker Compose）

可选 profile：`full`（全功能）、`neo4j`（知识图谱）、`minio`（对象存储）、`langfuse`（追踪）。

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>

其他部署形态（<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>）：

| 形态 | 依赖 | 适用 |
| --- | --- | --- |
| Docker Compose | ParadeDB + Redis + Asynq | 生产/团队自托管，推荐 |
| Helm（`helm/`） | ParadeDB + Redis | Kubernetes ≥ 1.25 |
| Lite 单二进制 | SQLite（FTS5 + sqlite-vec）+ 内存队列 | 个人/离线/低资源 |
| 桌面应用 | SQLite + 内存 | 桌面单机（**未正式发布**） |

硬件要求：**建议 4 核 CPU / 8GB 内存起步**（docreader 含 LibreOffice、Playwright，较吃内存）。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>

### 2.2 是否强制要求外部 API key？

**不强制。** 官方文档明确支持完全本地的 Ollama 后端，LLM 与 embedding 可都指向本地 Ollama。

环境变量（`.env.example` D1 段原文）：

```bash
# ========== D1. LLM / VLM / Ollama ==========
# Ollama 不可用时仅告警不阻断（true 时生效，默认不阻断，避免未配 Ollama 就启动失败）。
OLLAMA_OPTIONAL=true
# 本地 Ollama 的唯一地址。source=local 的向量模型与对话模型共用这一进程，
# 不需要为 embedding 再起一个 Ollama。未设置时进程回退到 http://localhost:11434。
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

来源：<https://github.com/Tencent/WeKnora/blob/main/.env.example>

配置文档同款表述：

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | http://host.docker.internal:11434 | 唯一的本地 Ollama 地址。`source=local` 的向量与对话模型共用它；未设置时进程用 `http://localhost:11434` |
| `OLLAMA_OPTIONAL` | true | Ollama 不可用时仅告警不阻断启动 |

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>

**注意容器网络陷阱**：容器内的 `localhost` 指向容器自身，连宿主机 Ollama 必须用 `http://host.docker.internal:11434`；Linux 需确认 `extra_hosts: host.docker.internal:host-gateway` 生效。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/03-quickstart>

### 2.3 embedding 与 LLM 能否都指向本地 Ollama？

**能，两者共用同一个 Ollama 进程。** 官方快速开始的初始化请求就是纯本地 Ollama 配置（无任何 apiKey 字段）：

```bash
# 4) 初始化知识库（以本地 Ollama 为例；远程模型改 source/baseUrl/apiKey）
curl -s -X POST $BASE/initialization/initialize/$KB_ID -H "$AUTH" -H "Content-Type: application/json" -d '{
  "llm":       {"source":"local","modelName":"qwen3:8b"},
  "embedding": {"source":"local","modelName":"bge-m3","dimension":1024},
  "rerank":    {"enabled":false},
  "multimodal":{"enabled":false},
  "documentSplitting":{"chunkSize":512,"chunkOverlap":50,"separators":["\n\n","\n","。"]},
  "nodeExtract":{"enabled":false},
  "questionGeneration":{"enabled":false}}'
```

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/03-quickstart>

声明式配置写法（`config/builtin_models.yaml.example`，仓库内原文）：

```yaml
# ----- Example: one local Ollama for embedding and generation ---------------
# source: local uses OLLAMA_BASE_URL only. Embedding and chat share that
# process; a second Ollama is not required. ${EMBEDDING_MODEL_NAME} is read
# only because this file references it (see .env.example section D2).
# dimension is a literal and must match the pulled model (the CLI example
# nomic-embed-text uses 768). Uncomment this list on its own — do not also
# uncomment the remote embedding entry above, or you will register two defaults.
#
# builtin_models:
#   - id: builtin-ollama-chat
#     type: KnowledgeQA
#     source: local
#     name: ${LLM_MODEL_NAME}            # Ollama chat model, e.g. qwen2.5:7b
#   - id: builtin-ollama-embedding
#     type: Embedding
#     source: local
#     name: ${EMBEDDING_MODEL_NAME}      # Ollama embedding model name
#     parameters:
#       embedding_parameters:
#         dimension: 768
```

来源：<https://github.com/Tencent/WeKnora/blob/main/config/builtin_models.yaml.example>

模型生命周期也有 API 支撑（`internal/models/utils/ollama/ollama.go` 的 `OllamaService`）：

| 路径 | 说明 |
|------|------|
| `GET /initialization/ollama/status` | Ollama 服务可用性 |
| `GET /initialization/ollama/models` | 列出本地已有模型 |
| `POST /initialization/ollama/models/check` | 批量检查模型是否已下载 |
| `POST /initialization/ollama/models/download` | 异步下载 |
| `GET /initialization/ollama/download/progress/:taskId` | 下载进度 |

来源：<https://weknora.weixin.qq.com/docs/03-features/06-models>

> ⚠️ **重要注意点**：**rerank 模型不支持 Ollama**（"重排模型不支持 Ollama"）。纯 Ollama 部署时 rerank 需另配远程服务，或关闭 rerank（`"rerank":{"enabled":false}`）。
> 来源：<https://weknora.weixin.qq.com/docs/03-features/06-models>

### 2.4 中文 embedding 默认模型

**`bge-m3`，维度 1024。**

配置文档原文：

> 向量模型名不由环境变量决定。在模型记录里把 `type=Embedding`、`source=local` 的 `name` 设为 Ollama 模型名（CLI 示例 `nomic-embed-text`，维度 768；**快速开始用 `bge-m3`，维度 1024**）。名为空时本地 embedder 回退到 `nomic-embed-text`。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>

注意两点差异：
- **快速开始路径**用 `bge-m3`（1024 维）。
- **CLI 示例**用 `nomic-embed-text`（768 维）。
- 若 `name` 为空，本地 embedder **回退到 `nomic-embed-text`**。

`dimension` 必须与实际拉取的模型一致（写死在 YAML 里，是字面量）。

**更换 embedding 模型需要重建索引**——"更换后需要重建索引"。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/03-quickstart>

---

## 3. 支持的输入格式

### 3.1 格式总表

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

| 类别 | 格式 |
| --- | --- |
| 文档 | **PDF**、**Word（doc/docx）**、**PPT（ppt/pptx）**、Excel（xls/xlsx）、EPUB、XMind |
| 文本 | txt、Markdown、CSV、JSON |
| 网页 | 在线 URL 抓取、本地 HTML / MHTML 归档 |
| 图片 | jpg、png、gif、bmp、tiff、webp（需配置视觉模型才能理解内容） |
| 音频 | mp3、wav、m4a、flac、ogg（需配置语音识别模型） |

**逐项回答提问**：
- **PDF** ✅
- **Word (.docx)** ✅
- **PPT/PPTX** ✅
- **图片** ✅（但需配置视觉模型/VLM 才能"理解内容"；否则仅整图内联）
- **视频** ❌ **不支持视频摄入**。格式表只有音频（mp3/wav/m4a/flac/ogg），无视频格式。UI 的模型配置里 chat 模型可勾选"图像/音频/视频"输入模态，但那是模型能力声明，不是文档摄入格式。
  来源（无视频格式）：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>；（视频作为模型输入模态）：<https://weknora.weixin.qq.com/docs/03-features/06-models>

### 3.2 OCR / 扫描件 PDF

**支持扫描件 PDF 的 OCR，但 OCR 不在 docreader 里跑——它在 Go 侧。**

docreader 自身的 PDF 解析器**明确不做 OCR**：

> **依赖**：`pypdfium2`（+ Pillow）。无需任何外部服务（MinerU / Docling 等），docreader 自身不做 OCR。

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

PDF 采用**逐页路由（per-page routing）**：每页独立分类为 `"text"` 或 `"scanned"`（`_classify_page`），主信号是图片面积覆盖率（阈值 `DOCREADER_PDF_SCAN_IMAGE_RATIO=0.5`），次信号是文本层字符数 < `DOCREADER_PDF_SCAN_MIN_CHARS`（10）。扫描页渲染为 JPEG（DPI 200），markdown 中以 `![xxx_page_N.jpg](images/...)` 占位，metadata 标记 `image_source_type=scanned_pdf`，**由 Go App 对这些页面图执行 OCR**。

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

**OCR 引擎（可选）**：
- **PaddleOCR-VL**（自建或云端）——`internal/infrastructure/docparser/paddleocr_vl_converter.go` / `paddleocr_vl_cloud_converter.go`
- **VLM OCR**（用配置的视觉模型做 OCR）——`image_multimodal.go`，对 `scanned_pdf` 来源使用专门的 `ocr_prompt`
- **MinerU**（PDF 整体解析引擎，含表格/版式）
- **OpenDataLoader**（Java 版面分析，支持 hybrid 模式调独立服务）
- **MarkItDown**（Microsoft 库，`markitdown[docx,pdf,xls,xlsx]`）

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

**图片 OCR 管线可配置**（v0.9.0 引入）：`image_processing_config.image_pipeline` 可选 `default`（手动选描述与 OCR 动作）或 `smartocr`（先观察图片，再按属性决定是否 OCR）。KB 还可选 OCR 图片预处理：`off`（默认）/ `auto` / `always`。

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

> ⚠️ 关键限制：**扫描件文本完全依赖 Go 侧 OCR**。若不配视觉模型/VLM，扫描 PDF 的文字不会进索引（图片会以 `![...]` 占位留在 markdown 中，但无 OCR 文本）。docreader 的 Dockerfile 注释明确"已移除 OCR/PaddleOCR 相关依赖"。
> 来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

### 3.3 解析引擎可切换

解析结果不理想时可在 KB 解析设置里为 `pdf` 指定其他引擎（MarkItDown / OpenDataLoader / MinerU）。旧版 `.doc` 需 LibreOffice 转 `.docx` 后再解析；无 LibreOffice 时退化为 antiword 纯文本（**无图片、无表格结构**）。

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

---

## 4. 向量数据库

### 4.1 默认：PostgreSQL + pgvector（通过 ParadeDB 镜像）

**默认不需要独立向量库。** 默认 compose 的 postgres 镜像就是 `paradedb/paradedb:v0.25.11-pg17`，自带 BM25 全文检索 + pgvector：

> | `postgres` | `paradedb/paradedb:v0.25.11-pg17` | `5432`（网络内） | 主数据库。ParadeDB 发行版自带 BM25 全文检索与 pgvector 向量能力，因此**默认部署无需独立向量库**（`RETRIEVE_DRIVER=postgres`） |

来源：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>

环境变量：

```bash
# 向量存储类型（逗号分隔可多驱动）：postgres / elasticsearch_v7 / elasticsearch_v8 / ...
RETRIEVE_DRIVER=postgres
```

来源：<https://github.com/Tencent/WeKnora/blob/main/.env.example>

### 4.2 全部可选向量库

| 后端 | 驱动名 | 向量索引 | 关键词 |
|--------|--------|----------|--------|
| PostgreSQL | `postgres` | pgvector halfvec + HNSW 表达式索引 | pg_search BM25（`\|\|\|`） |
| Elasticsearch v8 | `elasticsearch_v8` | script_score cosineSimilarity | match（BM25） |
| Elasticsearch v7 | `elasticsearch_v7` | **不支持**（仅 keywords） | match（BM25） |
| OpenSearch | `opensearch` | k-NN 插件 knn（HNSW） | match（BM25） |
| Milvus | `milvus` | HNSW（IP/COSINE/L2） | BM25 Function 稀疏向量 |
| Weaviate | `weaviate` | nearVector（certainty） | 原生 BM25 |
| Qdrant | `qdrant` | — | tokenizeQuery + MatchText OR 过滤（无 BM25 打分） |
| Apache Doris | `doris` | — | — |
| 腾讯云 VectorDB | `tencent_vectordb` | HNSW COSINE | 稀疏向量 SPARSE_INVERTED |
| SQLite (Lite 模式) | `sqlite` | sqlite-vec | FTS5 |

来源：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>

**所需基础设施**：
- 标准部署：Postgres(ParadeDB) + Redis 必跑；向量库用默认 postgres 即可。
- 用 ES/Milvus/Qdrant 等需额外启对应 compose profile（`elasticsearch`、`milvus`、`qdrant`、`weaviate`、`doris`、`opensearch`）。
- **Lite 模式**可完全零外部依赖：SQLite + 内存队列。

> `RETRIEVE_DRIVER` | postgres | 检索引擎：`postgres` / `elasticsearch_v7` / `elasticsearch_v8` / `qdrant` / `milvus` / `weaviate` / `opensearch` / `doris` / `tencent_vectordb` / `sqlite`（Lite）；可逗号分隔多引擎并行

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>

### 4.3 存储引擎选择的关键设计点

无论引擎本身是否提供混合检索，**WeKnora 的混合始终是上层统一的 RRF 融合**（`knowledgebase_search_fusion.go`）——向量与关键词各自独立检索，按 rank 加权合并。因此各引擎只需分别提供两类单模检索。

来源：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>

RRF 公式（源码原文）：

```go
// fuseWithRRF
rrf   = vectorWeight/(rrfK + vectorRank) + keywordWeight/(rrfK + keywordRank)
score = rrf / ((vectorWeight + keywordWeight) / (rrfK + 1))   // 归一化到 [0,1]
```

默认权重 `vectorWeight`/`keywordWeight` = 0.7 / 0.3。

---

## 5. 检索 API

### 5.1 两个对外检索接口

> 对外有两个检索接口，都要求 API Key 有 `retrieve`（或 full）权限，都返回 `SearchResult` 列表。

| | `POST /api/v1/knowledge-search` | `POST /api/v1/knowledge-bases/{id}/hybrid-search`（兼容 GET） |
| --- | --- | --- |
| 用途 | **默认用这个**，与产品内问答同一条检索流程（召回→rerank→合并→截断） | 更底层的召回接口，默认不做 rerank，分数就是召回分 |
| rerank | 默认开（用空间配置） | 默认关，传 `rerank` 对象才开 |
| 多知识库 | 可以，**embedding 模型可以不同** | 可以，但 embedding 模型必须相同 |
| 预计算向量 | 不支持 | 支持 `query_embedding` |
| 上下文块 | 合并进结果的 `content` | 作为额外结果行返回 |
| `match_count` 默认 | 空间的 `rerank_top_k`（默认 10） | 50 |

来源：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>

### 5.2 三种检索模式是否支持？

| 提问 | 答案 | 依据 |
| --- | --- | --- |
| (a) 精确/关键词搜索 | ✅ 支持。默认 postgres 引擎用 `pg_search` BM25 —— `content \|\|\| query`（**任意 token 匹配**，非严格精确匹配）。ES/OpenSearch/Weaviate 用 `match`；Milvus 用 BM25 Function 稀疏向量 | <https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines> |
| (b) 模糊搜索 | ⚠️ **没有独立的"模糊/编辑距离"搜索模式**。关键词路是 BM25 token 匹配 + 阈值过滤（`keyword_threshold`）；向量路提供语义近似。Qdrant 驱动的关键词路是 token 化后逐 token `MatchText` OR 过滤（命中即回，无 BM25 打分）。已核对源码：全仓 `edit_distance` 命中 0 处；`levenshtein` 仅出现在 CLI 命令补全（`cli/internal/cmdutil/suggest.go`），与检索无关；`trigram` 仅用于 Wiki 去重与评测指标（ROUGE/BLEU）；retriever 层的 "fuzzy" 仅指 FTS5 的 token OR 匹配 | 同上 + <https://github.com/Tencent/WeKnora/blob/main/internal/application/repository/retriever/sqlite/repository.go> |
| (c) 自然语言语义搜索 | ✅ 支持。这是最主要路径。向量召回 + 查询改写 + 查询扩展 + rerank。另有 `POST /messages/search` 支持 `mode: keyword/vector/hybrid` | <https://weknora.weixin.qq.com/docs/02-architecture/04-rag-pipeline>、<https://weknora.weixin.qq.com/docs/04-api/02-api-chat> |

### 5.3 请求/响应格式

**`POST /api/v1/knowledge-search`**（Handler: `internal/handler/session/qa.go` 的 `SearchKnowledge`）：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 查询 |
| `knowledge_base_id` | string | 否 | 单 KB（兼容旧版） |
| `knowledge_base_ids` | []string | 否 | 多 KB |
| `knowledge_ids` | []string | 否 | 指定文件，与 `knowledge_base_id(s)` 取并集 |
| `tag_ids` | []string | 否 | 标签过滤 |
| `mentioned_items` | []object | 否 | 带 KB 范围的标签提及 |
| `vector_threshold` / `keyword_threshold` | float | 否 | 召回阈值；省略时用空间检索配置（默认 0.15 / 0.3） |
| `match_count` | int | 否 | 返回条数，上限 200；省略时用 `rerank_top_k`（默认 10） |
| `disable_keywords_match` / `disable_vector_match` | bool | 否 | 关闭某一路召回；两个都为 `true` 返回 400 |
| `parent_slot_aggregation` | bool | 否 | 同一父块下的多个子块命中只占 1 个 top-k 名额 |
| `rerank` | object | 否 | 覆盖 rerank 设置；`{"enabled":false}` 关闭 rerank |

来源：<https://weknora.weixin.qq.com/docs/04-api/02-api-chat>

调用示例（官方原文）：

```bash
curl -X POST $BASE/api/v1/knowledge-search -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' -d '{"query":"部署要求","knowledge_base_ids":["kb-1"]}'

# 只用向量召回、取 5 条，并关闭 rerank
curl -X POST $BASE/api/v1/knowledge-search -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"部署要求","knowledge_base_ids":["kb-1"],"disable_keywords_match":true,"match_count":5,"rerank":{"enabled":false}}'

# 只在指定文件里搜（不传 knowledge_base_ids）
curl -X POST $BASE/api/v1/knowledge-search -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' -d '{"query":"部署要求","knowledge_ids":["k-1","k-2"]}'
```

**`POST /api/v1/knowledge-bases/:id/hybrid-search`**（请求体 `types.SearchParams`）：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query_text` | string | 条件必填 | 查询文本（除非提供 `query_embedding`；开启 rerank 时必填） |
| `query_embedding` | []float32 | 否 | 预计算向量 |
| `vector_threshold` / `keyword_threshold` | float64 | 否 | 匹配阈值 |
| `match_count` | int | 否 | 返回条数上限（默认 50） |
| `disable_keywords_match` / `disable_vector_match` | bool | 否 | 关闭某一路召回 |
| `knowledge_base_ids` | []string | 否 | 一次检索多个知识库，路径上的 `:id` 必须在其中；embedding 模型必须相同，否则返回 400 |
| `knowledge_ids` / `tag_ids` | []string | 否 | 限定知识条目 / 标签过滤（OR） |
| `only_recommended` | bool | 否 | FAQ 仅推荐条目 |
| `skip_context_enrichment` | bool | 否 | 跳过父块/上下文补齐 |
| `rerank` | object | 否 | 传入即开启 rerank（`{}` 使用空间配置的模型） |

来源：<https://weknora.weixin.qq.com/docs/04-api/02-api-knowledge>

响应统一形态：`{"success":true,"data":[SearchResult],"meta":{"rerank":{...}}}`。

`SearchResult` 字段（源码 `internal/types/search.go` 原文注释）：

```go
type SearchResult struct {
    ID              string            `json:"id"`
    Content         string            `json:"content"`
    KnowledgeID     string            `json:"knowledge_id"`
    ChunkIndex      int               `json:"chunk_index"`
    KnowledgeTitle  string            `json:"knowledge_title"`
    StartAt         int               `json:"start_at"`
    EndAt           int               `json:"end_at"`
    Score           float64           `json:"score"`
    MatchType       MatchType         `json:"match_type"`
    SubChunkID      []string          `json:"sub_chunk_id"`
    Metadata        map[string]string `json:"metadata"`
    ChunkType       string            `json:"chunk_type"`
    ParentChunkID   string            `json:"parent_chunk_id"`
    ImageInfo       string            `json:"image_info"`
    MatchedImages   []MatchedImage    `json:"matched_images,omitempty"`
    KnowledgeFilename string          `json:"knowledge_filename"`
    // ...
}
```

来源：<https://github.com/Tencent/WeKnora/blob/main/internal/types/search.go>

### 5.4 混合检索 + 重排（rerank）

**均支持。** 混合检索是向量 + BM25 关键词双路，RRF 融合。重排流程（`internal/reranking`，与问答链路、Agent 的 `search_knowledge` 工具共用同一实现）：

1. 文本候选送给模型打分的文本 = 文档标题 + 去 Markdown 标记的分块正文 + 图片描述与 OCR 文本 + 生成的问题。
2. 保留分数不低于 `threshold` 的结果。若一条都没有、且 `threshold` > 0.3，则降到 `max(threshold×0.7, 0.3)` 再筛一次。
3. **排序分 = `0.6×模型分 + 0.3×召回分 + 0.1×来源权重`**；结果的 `metadata` 里带 `model_score` 和 `base_score`。
4. 用 **MMR（λ=0.7）** 从中挑出 `top_k` 条，降低内容重复。

来源：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>

`rerank` 对象：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 设为 `false` 关闭 rerank |
| `model_id` | string | 见下文 | rerank 模型 ID（`GET /models` 里 `type` 为 `Rerank` 的模型）。ID 不存在/未激活/非 rerank 模型返回 400 |
| `top_k` | int | 接口的返回条数 | 负数或超过 200 返回 400 |
| `threshold` | float | 空间配置的 `rerank_threshold`（未配置时 0.2） | 模型分数下限 |

来源：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>

`meta.rerank` 诊断字段让空结果可解释（`outcome` 取值：`ok` / `threshold_degraded` / `fallback_top1` / `all_below_threshold` / `model_error` / `model_unavailable` / `no_model` / `disabled` / `no_candidates`）。

来源：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>

### 5.5 其它检索相关端点

- `GET /tenants/kv/retrieval-config` —— 空间级检索配置（阈值、rerank 模型、RRF 参数）
- `POST /knowledge-bases/:id/graph-search` —— GraphRAG 图谱查询（需开 Neo4j）
- `POST /messages/search` —— 聊天历史搜索，支持 `mode: keyword/vector/hybrid`

来源：<https://weknora.weixin.qq.com/docs/04-api/02-api-chat>、<https://weknora.weixin.qq.com/docs/04-api/02-api-knowledge>

---

## 6. 分块（Chunking）

### 6.1 算法

**Go 侧实现**（`internal/infrastructure/chunker`），自适应架构："文档画像 → 分层策略 → 结果校验 → 逐级回退"。

支持策略：`auto` / `heading` / `heuristic` / `recursive` / `legacy`（`""` = legacy）。

来源：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>

**可配置项**（`ChunkingConfig`，KB 级，可被单次上传覆盖）：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `chunk_size` | int | **512**（字符） | 单块目标大小。约 100–130 英文 token / **300 中文 token** |
| `chunk_overlap` | int | **80**（约 15%） | 相邻块重叠字符数。超过 `chunk_size/2` 会被钳制到一半 |
| `separators` | []string | `["\n\n", "\n", "。"]` | 递归分块的分隔符优先级序列 |
| `strategy` | string | `""`（= legacy） | `auto` / `heading` / `heuristic` / `recursive` / `legacy` |
| `token_limit` | int | 0（不启用） | 以近似 token 数上限约束块大小 |
| `languages` | []string | 空（自动检测） | 启发式模式的语言提示，如 `["zh"]`、`["en","de"]` |
| `enable_parent_child` | bool | false | 启用父子（两级）分块 |
| `parent_chunk_size` | int | 4096 | 父块大小 |
| `child_chunk_size` | int | 384 | 子块大小，子块 overlap 固定为 `child_size/5`（约 20%） |
| `parser_engine_rules` | []ParserEngineRule | 空 | 文件类型 → 解析引擎路由 |
| `table_metadata_instructions` | string | 空 | CSV/Excel 表格摘要生成指引 |

来源：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>

**父子分块**（`enable_parent_child=true`）：
- 父块 → `ChunkTypeParentText`，**只入 DB 不进向量索引**；
- 子块 → `ChunkTypeText` + `ParentChunkID`，是**唯一被嵌入/索引的粒度**。

来源：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>

> ⚠️ 迁移注意（源码注释原文）：历史上 Go DefaultConfig 用 64、knowledge.go 用 50、Python docreader 用 100 三种 overlap 默认值，现已统一为 80。

### 6.2 是否保留文档中的图片？

**是，图片被提取并与分块保持关联，且图片语义单独可检索。**

- `chunker.ExtractImageRefs(text)` 用支持一层括号嵌套的正则提取块内图片引用，供 `processChunks` 建立 **chunk ↔ 图片关联**。
- **每张图片在多模态阶段生成 `image_caption` / `image_ocr` 两个子 Chunk**（`ParentChunkID` 指向文本块）并**单独索引**——图片语义可召回，命中后回到原文块。

来源：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>

检索结果侧：`SearchResult.ImageInfo`（JSON 字符串）、`MatchedImages`（存储引用，不含图片字节）。图片以内部句柄 `resource://<handle>` 返回，客户端需再调一次带鉴权的 `/files` 代理取内容；或用 `?resource_urls=public` 换成可直接加载的直链。

来源：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>

v0.8.3 起还支持**多模态 embedding**（把文本和图片编码到同一向量空间，含腾讯 WeMM-Embedding、Qwen3-VL-Embedding），可选择开启图片向量召回。

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>（v0.8.3 release notes 段）

---

## 7. 元数据 / 分类

### 7.1 自定义元数据（`custom_metadata`）

**可以附加。** `PUT /api/v1/knowledge/:id` 接受 `custom_metadata`。

| 约束 | 值 |
| --- | --- |
| 字段数 | ≤ 20 |
| 键长度 | 1-64 字符，不能为空白 |
| 值类型 | string / number / boolean / null |
| 值长度 | ≤ 1000 字符 |

整体**覆盖式更新**（传入的对象替换原有对象）。元数据文本会参与摘要生成与文档级模型上下文（`Knowledge.CustomMetadataText()` 按键排序生成 `键: 值` 文本）。字段由 migration `000078` 引入。

来源：<https://weknora.weixin.qq.com/docs/04-api/02-api-knowledge>、<https://weknora.weixin.qq.com/docs/03-features/02-knowledge-base>

```bash
curl -X PUT $BASE/api/v1/knowledge/k-1 -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"新标题","custom_metadata":{"部门":"研发中心","密级":"内部","版本":3}}'
```

### 7.2 标签（Tags）与文件夹

- **标签（KnowledgeTag）**：多对多关联表 `knowledge_tag_relations`（migration `000063` 换掉了旧的 `knowledges.tag_id` 单列）。一篇文档可带多个标签。过滤是 **OR 语义**（命中任一标签即返回），SQL 走 `knowledges.id IN (SELECT knowledge_id FROM knowledge_tag_relations WHERE tag_id IN (...))`。FAQ 条目例外：标签存在 `chunks.tag_id` 上（单标签）。
- **自动标签**（`auto_tag_config`）：解析完成后异步从**已有候选标签**中挑选匹配项（不创建新标签），默认每篇最多 3 个。候选标签取 KB 排序前 500 个。
- **文件夹**：唯一归属（一篇文档只在一个目录下），适合按项目/来源归档。检索时两者都能作为范围限定条件。

来源：<https://weknora.weixin.qq.com/docs/03-features/02-knowledge-base>

### 7.3 能否按自定义元数据过滤搜索？

**不能直接过滤。** 这是本报告最重要的一个负面结论。

源码 `internal/types/search.go` 的 `SearchParams` 结构体**没有 `custom_metadata` 过滤字段**——可用的范围限定字段只有：

```go
type SearchParams struct {
    QueryText           string    `json:"query_text"`
    QueryEmbedding      []float32 `json:"query_embedding,omitempty"`
    VectorThreshold     float64   `json:"vector_threshold"`
    KeywordThreshold    float64   `json:"keyword_threshold"`
    MatchCount          int       `json:"match_count"`
    DisableKeywordsMatch bool     `json:"disable_keywords_match"`
    DisableVectorMatch  bool      `json:"disable_vector_match"`
    KnowledgeIDs        []string  `json:"knowledge_ids"`
    TagIDs              []string  `json:"tag_ids"`
    ScopeTagIDs         []string  `json:"scope_tag_ids,omitempty"`
    OnlyRecommended     bool      `json:"only_recommended"`
    KnowledgeBaseIDs    []string  `json:"knowledge_base_ids,omitempty"`
    SkipContextEnrichment bool    `json:"skip_context_enrichment,omitempty"`
    Rerank              *RerankOptions `json:"rerank,omitempty"`
}
```

来源：<https://github.com/Tencent/WeKnora/blob/main/internal/types/search.go>

**可行替代方案**：
1. **用标签（`tag_ids`）而非 `custom_metadata` 做过滤维度**——标签是检索原生支持的过滤字段，OR 语义。部门、项目、密级这类维度建成标签即可。
2. **用文件夹**做唯一归属的归档维度。
3. `custom_metadata` 仍可作为**展示与文档级模型上下文**。经核对源码 `internal/reranking/passage.go`，rerank 模型实际打分的文本是 `ModelPassage` = **文档标题 + 标题面包屑（ContextHeader）+ 富化后的分块正文**（正文 + 图片 caption/OCR 文本 + 生成的问题），**不含 `custom_metadata`**。`custom_metadata` 文本只参与摘要生成与文档级模型上下文（`Knowledge.CustomMetadataText()`）。

> ⚠️ 团队文档归档系统若需要"按 category/project/date 过滤搜索"，应优先用 WeKnora 的**标签**机制，而不是 `custom_metadata`。

---

## 8. 成熟度与许可

### 8.1 许可

**MIT**（带第三方组件例外清单）。

> WeKnora is licensed under the MIT except for the third-party components listed below.

来源：<https://github.com/Tencent/WeKnora/blob/main/LICENSE>

注意：GitHub API 的 `license.spdx_id` 返回 `NOASSERTION`（因为 LICENSE 文件是 MIT + 例外清单的混合结构），但 README 和 LICENSE 本身都明确是 MIT。

### 8.2 活跃度与规模（截至 2026-11-05）

| 指标 | 值 |
| --- | --- |
| Stars | 36,509 |
| Forks | 4,990 |
| Open Issues | 820 |
| 主语言 | Go |
| 创建时间 | 2025-07-22 |
| 最新 release | **v0.9.0**，发布于 2026-10-29 |
| 最近提交 | 2026-11-03 |
| 官网 | <https://weknora.weixin.qq.com> |

Release 节奏：

| 版本 | 发布日期 |
| --- | --- |
| v0.9.0 | 2026-10-29 |
| v0.8.3 | 2026-10-14 |
| v0.8.2 | 2026-09-24 |
| v0.8.0 | 2026-09-03 |
| v0.7.2 | 2026-08-07 |
| v0.7.1 | 2026-07-24 |

来源：<https://github.com/Tencent/WeKnora/releases>、GitHub API `/repos/Tencent/WeKnora`

### 8.3 中文支持

**原生中文优先。** 依据：
- 文档站默认语言 `zh-CN`（`DEFAULT_LOCALE` 默认 `zh-CN`）；UI 支持中文/英文/日文/韩文/俄文。
- `WEKNORA_LANGUAGE` 变量专门用于"文档处理语言（问题/摘要生成）"，默认 `zh-CN`。
- chunk_size 的中文换算：512 字符 ≈ **300 中文 token**。
- Milvus 分析器对中文用内置 Jieba（`chinese`），英文用 `english`。
- 中文 embedding 默认 `bge-m3`。
- README 有简体中文/日文/韩文多语言版本；v0.8.3 起支持繁体中文。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>、<https://weknora.weixin.qq.com/docs/03-features/04-chunking>、<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>

### 8.4 已知限制与风险（源码/文档明述）

**功能性限制**：
1. **rerank 不支持 Ollama**——纯本地部署需另配远程 rerank，或关闭 rerank。
2. **扫描 PDF 的文字完全依赖 Go 侧 OCR**；不配 VLM/OCR 引擎则扫描件无文本进索引。
3. **无视频摄入**；音频摄入需配 ASR 模型。
4. **`custom_metadata` 不可用于检索过滤**（见 §7.3）。
5. **无独立"模糊/编辑距离"搜索模式**——关键词路是 BM25 token 匹配。
6. docreader 的 PDF 解析**不做表格结构识别**（文本层表格按行输出），标题识别是字号启发式。
7. MarkItDown 的 PDF 走 pdfminer 文本抽取，**对扫描件无能为力**，表格/版面还原弱于 builtin PDF 路由。
8. `.doc` 需 LibreOffice；无 LibreOffice 时退化为 antiword 纯文本（无图片、无表格结构）。
9. `pdfium` C 库**非线程安全**，所有 pdfium 操作串行在一把全局锁后——并发 PDF 上传排队。
10. 单文件大小限制 `MAX_FILE_SIZE_MB=50`（app/frontend/docreader 三处共用）。

**运维/演进风险**：
1. **版本演进极快，breaking change 频繁**。README 明确标注 v0.8.2 为 Breaking（DingTalk channels 变 Stream-only、sandbox 命令以 root 运行）；v0.8.3 的 upgrade notes 要求"reindexing"（重建索引）。
2. **embedding 模型更换需要重建索引**。
3. **生产部署安全提示**（README WARNING 原文）：建议部署在内网/私有网络，避免暴露到公网，配置防火墙与访问控制。
4. 官方建议生产部署至少改：`DB_USER/DB_PASSWORD/DB_NAME`、`REDIS_PASSWORD`、`JWT_SECRET`、`SYSTEM_AES_KEY`（后者必须 32 字节，**丢失则已加密数据不可恢复**）。
5. **SSRF 白名单**：开 `SSRF_DNS_WHITELIST_ONLY` 前必须把所有出站地址（含本机 Ollama 的 `localhost`）加入 `SSRF_WHITELIST`。
6. `SYSTEM_AES_KEY` 更换会使已加密凭据无法解密，升级须沿用原值。
7. ParadeDB 镜像 x86 CPU 基线为 `x86-64-v2`（含 SSE4.2、POPCNT）；不强制 AVX2，但不代表所有 ARM CPU 兼容。
8. 文档站版本徽章与实际 release 可能短暂不同步（README 提示 `docker compose up -d` 单独执行会复用本地缓存镜像）。

来源：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>、<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>、<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>、<https://github.com/Tencent/WeKnora/blob/main/README.md>

---

## 9. 面向"团队文档归档 + 语义搜索"的落地建议

| 需求 | WeKnora 现状 | 结论 |
| --- | --- | --- |
| 全本地部署（无外网 API key） | ✅ `OLLAMA_BASE_URL` + `source: local`，LLM/embedding 共用一个 Ollama | **满足**。用 `bge-m3`(1024) + `qwen3:8b` |
| Docker Compose 最小部署 | ✅ 默认 5 服务（app/frontend/docreader/postgres/redis） | **满足** |
| PDF/Word/PPT/Excel/图片摄入 | ✅ 全部支持 | **满足** |
| 扫描件 PDF OCR | ✅ 但需另配 VLM/PaddleOCR-VL | **有条件满足** |
| 视频摄入 | ❌ 不支持 | **不满足**（音频支持） |
| 语义搜索 | ✅ 向量 + BM25 混合 + RRF + rerank | **满足** |
| 按 category/project/date 过滤 | ⚠️ 用**标签**（`tag_ids`）实现，不要用 `custom_metadata` | **需设计调整** |
| 归档目录结构 | ✅ 文件夹树（唯一归属）+ 标签（多对多） | **满足** |
| 图片与文档关联 | ✅ chunk↔图片关联 + caption/OCR 子块单独索引 | **满足** |
| 引用定位回原文 | ✅ `source_locators`（page/bbox/block/slide/sheet/时间区间） | **满足** |
| 长期维护风险 | ⚠️ 3 个月连发 6 个 minor 版本，有 breaking change | **需锁版本 + 关注 upgrade notes** |

**建议的最小落地路径**：
1. `docker compose up -d` 起默认 5 服务；
2. 宿主机 `ollama serve`，拉 `bge-m3`（embedding）与 `qwen3:8b`（chat）；
3. `.env` 设 `OLLAMA_BASE_URL=http://host.docker.internal:11434`，生成 `JWT_SECRET` 与 `SYSTEM_AES_KEY`；
4. KB 初始化用 `"source":"local"` 的 llm + embedding，`"rerank":{"enabled":false}`（rerank 不支持 Ollama）；
5. **归档维度全部建成标签**（部门/项目/密级），不要依赖 `custom_metadata` 做过滤；
6. 需要扫描件 OCR 时，额外配 VLM（本地 Ollama 也可作为 `multimodal` 模型）。

---

## 附：来源索引

**一手来源（GitHub `Tencent/WeKnora`，main 分支）**
- README：<https://github.com/Tencent/WeKnora/blob/main/README.md>
- 中文 README：<https://github.com/Tencent/WeKnora/blob/main/README_CN.md>
- `.env.example`：<https://github.com/Tencent/WeKnora/blob/main/.env.example>
- `docker-compose.yml`：<https://github.com/Tencent/WeKnora/blob/main/docker-compose.yml>
- `config/builtin_models.yaml.example`：<https://github.com/Tencent/WeKnora/blob/main/config/builtin_models.yaml.example>
- `internal/types/search.go`：<https://github.com/Tencent/WeKnora/blob/main/internal/types/search.go>
- `LICENSE`：<https://github.com/Tencent/WeKnora/blob/main/LICENSE>
- `CHANGELOG.md`：<https://github.com/Tencent/WeKnora/blob/main/CHANGELOG.md>
- Releases：<https://github.com/Tencent/WeKnora/releases>

**一手来源（官方文档站，与仓库 `website-docs/` 同步）**
- 总体架构：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>
- 检索问答全流程：<https://weknora.weixin.qq.com/docs/02-architecture/04-rag-pipeline>
- 文档入库流程：<https://weknora.weixin.qq.com/docs/02-architecture/03-document-pipeline>
- 安装部署：<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>
- 快速上手：<https://weknora.weixin.qq.com/docs/01-getting-started/03-quickstart>
- 配置详解：<https://weknora.weixin.qq.com/docs/01-getting-started/04-configuration>
- 文档解析服务 docreader：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>
- 分块机制：<https://weknora.weixin.qq.com/docs/03-features/04-chunking>
- 检索引擎与向量存储：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>
- 模型管理：<https://weknora.weixin.qq.com/docs/03-features/06-models>
- 知识库与知识管理：<https://weknora.weixin.qq.com/docs/03-features/02-knowledge-base>
- API 总览：<https://weknora.weixin.qq.com/docs/04-api/01-api-overview>
- 会话、消息与聊天：<https://weknora.weixin.qq.com/docs/04-api/02-api-chat>
- 知识库与知识：<https://weknora.weixin.qq.com/docs/04-api/02-api-knowledge>
