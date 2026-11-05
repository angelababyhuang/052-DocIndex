# 云端部署方案调研：团队文档归档 + 语义搜索系统

调研对象：微信存档 / PDF / Word / PPT / 扫描件 / 图片 / 视频 → Markdown → 统一图片库 → 向量库 → 飞书机器人或局域网检索。

调研日期：2026-11-05。所有结论均核对一手来源（腾讯云官方文档、WeKnora 仓库文件、各厂商定价页），未能核实处已显式标注。

**硬件前提**：MacBook Pro M5 Pro 64GB（无 Docker）、Mac mini M6 16GB、Windows 桌面 i7 / 32GB / RTX 3070 8GB VRAM。

**一句话结论**：腾讯云官方在 2026 年 6 月**已经上线了 WeKnora 应用镜像（腾讯云轻量应用服务器）**——这是唯一"官方云路径"、开箱即用、且 Docker 由云端托管的方案。Tencent Cloud VectorDB 原生支持稠密+稀疏混合检索（自带 jieba BM25 工具包），WeKnora 有 `tencent_vectordb` 驱动可直接对接。而 **WeKnora Lite 单二进制没有任何官方预编译产物**（42 个 Release 全部 0 asset），且 Lite 模式下 **PDF/Word/PPT 仍强制依赖 Python docreader 服务**，因此"不用 Docker = 纯单进程"这条路的性价比远低于预期。

---

## 0. 决策速览（先看这 5 条）

| # | 结论 | 依据 |
|---|---|---|
| 1 | **腾讯云轻量应用服务器已有官方 WeKnora 应用镜像**（2026 年 6 月新增）。选一台 4 核 8GB 的 Lighthouse（245 元/月，或 4 核 8GB/180GB SSD 的 230 元/月锐驰型），镜像选 WeKnora 即可拉起整套 Docker Compose（app/frontend/docreader/postgres/redis），**首次开机自动生成密钥并启动**。这是"先用云端库"最省事的落地方式。 | [镜像更新记录](https://cloud.tencent.com/document/product/1207/47042)、[Lighthouse 定价](https://cloud.tencent.com/product/lighthouse) |
| 2 | **Tencent Cloud VectorDB 支持原生 BM25 混合检索**，不需要外挂 ES：稀疏向量工具包内置 jieba 分词（中/英），提供 Python/Java/Go SDK，稀疏向量字段用 `SPARSE_INVERTED` 索引。WeKnora 的 `tencent_vectordb` 驱动已支持 `HNSW COSINE` + 稀疏向量。 | [稠密与稀疏向量高阶混合检索](https://cloud.tencent.com/document/product/1709/111376)、[稀疏向量工具](https://cloud.tencent.com/document/product/1709/111372)、[检索引擎文档](https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines) |
| 3 | **VectorDB 成本极低，不值得为它单独买 CVM**。广州/南京地域标准型 52 元/GB/月；10k 文档规模的向量内存需求约 0.1–0.3 GB，单节点最小规格（标准型 1核4GB）即 **208 元/月**，存储型（1核8GB）**272 元/月**——但如果只是想要"云端向量库"，注意**默认 3 节点**是最低可用形态，实际起步约 **624 元/月**（3×标准型 4GB）。 | [计费定价](https://cloud.tencent.com/document/product/1709/103016)、[选择节点规格及数量](https://cloud.tencent.com/document/product/1709/113399) |
| 4 | **WeKnora Lite（无 Docker 路线）有两个硬伤**：(a) 官方文档明确"桌面应用**没有随 Release 提供安装包**，……当前最新 Release 未附带任何产物"——我核对 GitHub API，**42 个 Release 全部 `assets: []`**，无 Homebrew tap、无镜像站二进制；(b) Lite 的 Go 内置解析器 `simpleFormats` **只覆盖 md/markdown/txt/text/csv/json + 图片 + 音频**，**PDF/Word/PPT 一律要落到 Python docreader**，所以"单二进制跑全格式"做不到。 | [安装部署文档](https://github.com/Tencent/WeKnora/blob/main/website-docs/01-getting-started/02-installation.md)、[GitHub Releases API](https://api.github.com/repos/Tencent/WeKnora/releases)、[`builtin_converter.go`](https://github.com/Tencent/WeKnora/blob/main/internal/infrastructure/docparser/builtin_converter.go) |
| 5 | **Embedding 别用 WeMM-Embedding 的云 API——它没有云 API**。WeMM-Embedding 是腾讯开源的**自托管权重模型**（HuggingFace `tencent/WeMM-Embedding-2B` 等），官方 README 明确"Audio input is not currently supported"，embedding 由"最后一层 hidden states @ `<embedding>` token + L2 归一化"得出，无托管服务。国内可直接用的托管 embedding 最便宜的是 **SiliconFlow / 腾讯混元 0.7 元/百万 token**；需要"文本+图像同一向量空间"则用 **阿里云 `qwen3-vl-embedding`**。 | [WeMM-Embedding README](https://github.com/Tencent/WeMM-Embedding)、[混元计费](https://cloud.tencent.com/document/product/1729/97731)、[百炼文本向量](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api) |

---

## 1. 腾讯云可承载这套栈的产品

### 1.1 腾讯云向量数据库（Tencent Cloud VectorDB）

**是本栈最贴合的托管组件。** WeKnora 原生支持 `RETRIEVE_DRIVER=tencent_vectordb`，且 go.mod 依赖 `github.com/tencent/vectordatabase-sdk-go v1.8.4`（一手证据，<https://github.com/Tencent/WeKnora/blob/main/go.mod>）。

**混合检索（BM25 + 向量）：支持，且是一等公民。** 官方提供 `hybrid_search()` 接口，同时接收稠密 `ann` 与稀疏 `match` 两路：

```python
from tcvectordb.model.document import Document, AnnSearch, WeightedRerank, KeywordSearch
from tcvdb_text.encoder.bm25 import BM25Encoder

bm25 = BM25Encoder.default('zh')
client.hybrid_search(
    database_name='db-test', collection_name='book-vector',
    ann=[AnnSearch(field_name="vector", data=[0.7123, 0.83, 0.913])],
    match=[KeywordSearch(field_name="sparse_vector", data=bm25.encode(["腾讯云"]))],
)
```

来源：<https://cloud.tencent.com/document/product/1709/111376>

**稀疏向量（BM25）工具包**：腾讯云向量数据库团队出品，**集成 jieba 分词库**，区分写入/检索场景的不同词表拆分算法，当前支持中文、英文（默认中文），支持训练自定义领域语料的词频参数。提供 Python / Java / Go 三语言 SDK（`tcvdb-text`）。Go SDK 需 ≥ V1.4.7 才支持稀疏向量。

来源：<https://cloud.tencent.com/document/product/1709/111372>

> **重要**：WeKnora 自己的混合检索是**上层统一的 RRF 融合**（向量路与关键词路各自独立检索，按 rank 加权合并，默认权重 0.7/0.3）。所以即使引擎本身不提供 BM25，WeKnora 也能跑。VectorDB 驱动在 WeKnora 侧对应「HNSW COSINE + 稀疏向量 SPARSE_INVERTED」。（来源：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>）

**计费模型**：包年包月（预付费）+ 按量计费（后付费，按整点结算）。按**内存 + 磁盘**规格计价；若使用 VectorDB 自带的 Embedding，则按 **Token 流量**计价（Embedding 恒为按量计费）。

内存单价（元/GB/月，单节点；按量计费括号内为元/GB/小时）：

| 地域 | 存储型 1:8 | 标准型 1:4 | 计算型 1:2 |
|---|---|---|---|
| 上海 | 40 (0.08) | 60 (0.13) | 87 (0.18) |
| 南京 | 34 (0.07) | 52 (0.11) | 76 (0.16) |
| 广州 | 34 (0.07) | 52 (0.11) | 76 (0.16) |
| 北京 | 37 (0.08) | 56 (0.12) | 81 (0.17) |
| 成都 | 46 (0.10) | 62 (0.13) | 88 (0.18) |

来源：<https://cloud.tencent.com/document/product/1709/103016>（最近更新 2026-02-02）

**最小实例与规格**：

- 节点类型分计算型（CPU:内存 1:2）/ 标准型（1:4）/ **存储型（1:8，官方明写"适用场景：RAG 应用"是标准型）**。
- 最小单节点规格：计算型 1核2GB、存储型 1核8GB、标准型 1核4GB。
- **"默认选择 3 个节点来保证高可用"**，支持 1~50 节点。
- 内存估算公式：`单节点内存(GB) = 数据量 × (向量维度×4 + M×8) × 1.2 / 1024³`；建议磁盘容量为单节点内存的 2 倍；**磁盘暂不支持缩容**。
- 按 1536 维估算：标准型 2核8GB 支撑 100 万条；按 768 维：存储型 1核8GB 支撑 200 万条。

来源：<https://cloud.tencent.com/document/product/1709/113399>（最近更新 2025-12-03）

**免费额度**：公测已于 2024-04-10 结束商业化。仍保留 **"免费测试版实例"——单可用区单节点部署，体验时长限制 1 个月**，商业化后不产生费用，到期后需重新购买。另有 **AI 套件**（一站式文档检索解决方案）**暂不计费**。

来源：<https://cloud.tencent.com/document/product/1709/104499>

> ⚠️ **未能核实**：VectorDB 的磁盘单价在本报告抓取时该页表格被截断，未取得具体数值。因此 §5 的成本估算中磁盘按"内存×2 的 SSD"做量级估算，标注为估算而非报价。

### 1.2 腾讯云 PostgreSQL + pgvector / TDSQL

- **Tencent Cloud VectorDB 不是 PostgreSQL**。若想用 PG 路线，腾讯云提供 **TencentDB for PostgreSQL**，可用 `pgvector` 扩展做向量；`TDSQL` 系列主打 MySQL 兼容分布式，与 pgvector 无关。
- WeKnora 默认部署用的 **ParadeDB 镜像**（`paradedb/paradedb:v0.25.11-pg17`）= PostgreSQL 17 + **BM25（pg_search）+ pgvector**，即"WeKnora 默认检索引擎自带混合检索"。
- WeKnora 官方明确推荐 Postgres 路线给"绝大多数日常业务 / RAG 应用"，且这是**默认部署无需独立向量库**的路径。
- **ParadeDB 镜像的 CPU 基线是 `x86-64-v2`（含 SSE4.2、POPCNT），不强制 AVX2**——M5 Pro（M5 = ARM64）与 M6 是 Apple Silicon，**跑不了这个镜像的 x86 版本**（除非 Rosetta 模拟，性能与向量扩展均不可取）。这是选云的一个真实理由。

来源：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>、<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>（"Compose 默认 ParadeDB 镜像自 v0.22.6-pg17 起 x86 CPU 基线为 x86-64-v2……这不代表所有 ARM CPU 或其他可选服务均兼容"）

### 1.3 腾讯云对象存储 COS

- 用于存放原始文档与提取出的图片（COS 单次 PUT 上限 5GB，更大用分块上传）。
- WeKnora 支持 S3 兼容存储：go.mod 依赖 `github.com/aws/aws-sdk-go-v2/service/s3` 与 `github.com/minio/minio-go/v7`，可配 `STORAGE_TYPE=cos/s3/minio`（compose 有 `minio` profile）。
- 计费分项：流量费用、存储容量费用、请求费用、数据取回费用、管理功能费用、数据湖存储费用、数据处理费用。来源：<https://cloud.tencent.com/document/product/436/53859>
- **未能核实**：COS 各存储类型的具体单价（元/GB/月）本轮未从官方计费页取得（页面为动态渲染）。量级上 50GB 标准存储在腾讯云属"每月几元"级别，远不是成本瓶颈。

### 1.4 腾讯云 Elasticsearch

WeKnora 支持 `elasticsearch_v8` 驱动（script_score cosineSimilarity + match BM25 关键词路）与 `elasticsearch_v7`（**v7 仅支持关键词路，不支持向量**）。腾讯云 ES 是可选组件。来源：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>

**但在本题场景下没有理由用腾讯云 ES**：VectorDB 原生就带 BM25 混合检索，比 ES 便宜也少一个组件。

### 1.5 腾讯云轻量应用服务器 Lighthouse

官方定价（中国内地地域，来源：<https://cloud.tencent.com/product/lighthouse>）：

| 套餐 | 配置 | 月价 | 年价 |
|---|---|---|---|
| 入门型 | 2核2GB / 40GB SSD / 3Mbps / 200GB 流量 | **45 元/月** | 459 元/年 |
| 入门型（基础） | 2核2GB / 50GB SSD / 4Mbps / 300GB | 50 元/月 | 510 元/年 |
| 通用型（进阶） | 2核4GB / 100GB SSD / 7Mbps / 1000GB | 100 元/月 | 1020 元/年 |
| 通用型（专业） | 4核8GB / 180GB SSD / 12Mbps / 2000GB | **245 元/月** | 2499 元/年 |
| 锐驰型 | 2核2GB / 40GB / 峰值 200Mbps / 无限流量 | 45 元/月 | 459 元/年 |
| 锐驰型 | 4核8GB / 120GB / 峰值 200Mbps / 无限流量 | **230 元/月** | 2346 元/年 |

**Lighthouse vs CVM**：Lighthouse 是套餐制整体售卖（CPU+内存+SSD+流量包），运维更简单、成本更低；CVM 是独立资源计费、更灵活也更贵。

另有 **新用户免费体验 1 个月**（<https://cloud.tencent.com/act/free/personal>）。

**关键约束：WeKnora 官方建议 4 核 CPU / 8GB 内存起步**，因为 docreader 含 LibreOffice、Playwright，较吃内存。**2核2GB 的 45 元档大概率不够跑 WeKnora 全家桶**；4核8GB（230–245 元/月）是合理起点。

来源：<https://weknora.weixin.qq.com/docs/01-getting-started/02-installation>

---

## 2. WeKnora 自己的云路径

### 2.1 腾讯云 Lighthouse 应用模板（WeKnora 镜像）—— 官方确认存在

**腾讯云官方文档《轻量应用服务器 镜像更新记录》明确记载**：

> **2026年06月** | 应用镜像 | **新增 WeKnora 镜像**。

来源：<https://cloud.tencent.com/document/product/1207/47042>（最近更新 2026-10-09）

> **未能核实**：Lighthouse 的 `DescribeBlueprints` API 需要腾讯云 API 签名（SecretId/SecretId），本环境无凭据，**无法取得 WeKnora 蓝图的 BlueprintId、所在地域（是否所有中国内地地域都有）、以及镜像预置的具体 WeKnora 版本号**。文档还说明"自 2026 年 1 月起，应用镜像的更新记录仅保留'新增'与'下线'两类条目……如需查看版本详情，可在轻量应用服务器产品购买页的镜像详情中查询"。**建议直接在 <https://buy.cloud.tencent.com/lighthouse> 购买页选镜像时确认。**

### 2.2 镜像里预置了什么（从 WeKnora 仓库的云镜像脚本反推）

WeKnora 仓库内有 `scripts/cloud-image/` 目录（`prepare.sh` / `cleanup.sh` / `firstboot.sh` + systemd 单元），就是**腾讯云制作 WeKnora 镜像所用的脚本**（一手来源：<https://github.com/Tencent/WeKnora/blob/main/scripts/cloud-image/README.md>）。

`firstboot.sh` 揭示了镜像的完整行为：

- 安装路径 `/opt/WeKnora`，配置模板 `/opt/WeKnora/.env.example`。
- **开机自动生成随机密钥**并写入 `/opt/WeKnora/.env`：`DB_PASSWORD`（24 字符）、`REDIS_PASSWORD`（24 字符）、`JWT_SECRET`（48 字符）、`SYSTEM_AES_KEY`（**恰好 32 字节**，用于 AES-256）、`GIN_MODE=release`。
- 幂等策略：写完 `.env` 立刻 `touch /opt/WeKnora/.firstboot.done`（`chmod 0600`），systemd unit 用 `ConditionPathExists=!` 拦住二次生成——**因为 Postgres 数据卷已经用旧密码初始化了**。
- 从 `/opt/WeKnora/.cloud-image-meta` 恢复 `WEKNORA_VERSION`，避免镜像版本与 compose 拉到的 `:latest` 不一致。
- 执行 `docker compose up -d`。
- 凭证写到 **`/root/weknora-credentials.txt`**，并打印公网访问地址 `http://<PUB_IP>`（公网 IP 探测失败则回退内网 IP）。
- 提示注册后可用 `DISABLE_REGISTRATION=true` 关闭注册。

来源：<https://github.com/Tencent/WeKnora/blob/main/scripts/cloud-image/firstboot.sh>

**这意味着 WeKnora 镜像 = Docker Compose 全家桶（app + frontend + docreader + postgres/ParadeDB + redis）+ 自动密钥 + 自动启动。** 对"没有 Docker 的 MacBook"这个约束，这正好绕开了问题：Docker 跑在云端。

### 2.3 是否有托管版 WeKnora（WeKnora Cloud）？

**代码里有痕迹，但未找到公开的托管服务售卖页。**

- `internal/infrastructure/docparser/engine_registry.go` 的 `ReaderDeps` 含 `WeKnoraCloudCredentials func(ctx) *types.WeKnoraCloudCredentials`，注释写"Nil, or a nil return, means the tenant has not configured them"——说明**存在一个 "WeKnora Cloud" 概念，可作为解析引擎接入**（`weknoracloud_http_reader.go` 是其 reader）。
- 同理 `weknora-cloud` 是 WeKnora 的可选解析引擎之一。
- **未能核实**：是否存在面向公网的 WeKnora Cloud 托管服务（独立售卖页、定价、注册入口）。腾讯云控制台里可能有"腾讯云智能体开发平台/大模型知识引擎"等相邻产品，但不是 WeKnora 本身。**本报告不假设它可购买。**

来源：<https://github.com/Tencent/WeKnora/blob/main/internal/infrastructure/docparser/engine_registry.go>

### 2.4 官方微信公众号文章（无法直接抓取）

腾讯云官方号有《WeKnora x Lighthouse：打造云端智库，汇聚全域知识》（<http://mp.weixin.qq.com/s?__biz=MzYyMTA4OTI2OA==&mid=2247494960&idx=1&sn=4b46bfd008cf31d9393a2c1cce715731>）。抓取时被微信"环境异常"验证页拦截，**未能读取正文**。仅可从标题与 URL 推断"Lighthouse + WeKnora"是官方推广路径。

---

## 3. 可从中国大陆访问的云向量库对比

| 方案 | 混合/BM25 | 起步价（量级） | 能否与 embedding API 同域托管 | WeKnora 驱动 |
|---|---|---|---|---|
| **pgvector（自建于腾讯云 CVM）** | ✅ 若用 ParadeDB/PG17+`pg_search` 则有 BM25；纯 PG 的 `to_tsvector` 只有分词全文，无中文 jieba 质量 | CVM 2核4GB 约 100–200 元/月 + 磁盘 | ✅ 完全自控 | ✅ `postgres`（默认） |
| **腾讯云 VectorDB** | ✅ **原生**（稀疏向量 + jieba BM25 工具包 + `hybrid_search`） | 标准型 1核4GB = 208 元/月/节点；默认 3 节点 ≈ 624 元/月 | ✅ 同为腾讯云，VPC 内网互通 | ✅ `tencent_vectordb` |
| **阿里云 Elasticsearch** | ✅ ES 原生 BM25；向量走 ES 8 的 knn | ES 通用型 2核4GB 约 300–500 元/月（**未核实具体报价**） | ✅ DashScope 同在阿里云 | ✅ `elasticsearch_v8` |
| **百度智能云向量数据库 VectorDB** | ⚠️ 未核实是否支持稀疏/BM25；官方强调"多种索引类型和相似度算法" | 未核实 | ✅ 千帆同在百度智能云 | ❌ 无内置驱动，需自行适配 |
| **Zilliz Cloud（Milvus）** | ✅ Milvus 支持 BM25 Function 稀疏向量 | 有免费额度（**未核实当前额度**） | ⚠️ 境外服务为主，国内访问与合规性需自行评估 | ✅ `milvus` |
| **腾讯云 ES** | ✅ BM25 + knn | 未核实 | ✅ | ✅ `elasticsearch_v8` |

**关于"VikingDB"**：提问中提到的百度 VikingDB，在百度智能云现行文档里对应产品名是 **「向量数据库 VectorDB」**（<https://cloud.baidu.com/doc/VDB/index.html>，文档标题为"上下文数据服务 > 向量数据库 VectorDB"），另有「Agent 记忆服务」子产品。**未见" VikingDB"作为当前产品名出现**——可能是旧称或笔误。

**关于 Zilliz Cloud**：<https://zilliz.com/pricing> 本轮抓取失败（fetch failed），**其定价与免费额度未核实**。且 Milvus 走 WeKnora 的 `milvus` 驱动需要 Compose 里启用 `milvus` profile（= 自己跑 Milvus，与"托管"矛盾），或用 Milvus 官方托管 + WeKnora 驱动连远程集群——**该路径 WeKnora 侧需要额外配置验证**。

**关于"embedding API 能否与向量库同域托管"**：
- 腾讯云 VectorDB + 腾讯混元 Embedding：同厂商同内网，最干净。
- 阿里云 ES + DashScope：同厂商，最干净。
- pgvector 自建 + 任意 embedding API：embedding API 走公网 HTTPS 出站调用即可，不要求同域。

> **但要注意 WeKnora 的一个硬约束**：`SSRF_DNS_WHITELIST_ONLY` 开启后必须把所有出站地址（含 embedding API 域名）加入 `SSRF_WHITELIST`。以及 **embedding 模型更换需要重建索引**。

---

## 4. 中国大陆可用的 Embedding API

### 4.1 汇总表

| 服务 | 模型 | 维度 | 价格 | 多模态（文本+图像同一空间） |
|---|---|---|---|---|
| **腾讯混元** | Hunyuan-embedding | **1024** | **0.7 元/百万 token**（输入=输出），**首次开通赠 100 万 token**（有效期 1 年） | ❌ 未见多模态版 |
| **阿里云百炼** | `text-embedding-v3` | 1024（默认）/ 768 / 512 / 256 / 128 / 64 | 0.0005 元/千 token（即 0.5 元/百万），**v3 无免费额度** | ❌ 纯文本 |
| 阿里云百炼 | `text-embedding-v4`（Qwen3-Embedding 系） | 2048 / 1536 / 1024（默认）/ 768 / 512 / 256 / 128 / 64 | 0.0005 元/千 token；**100 万 token 免费额度（开通后 90 天内）** | ❌ 纯文本 |
| 阿里云百炼 | `qwen3.7-text-embedding` | 2560 / 2048 / 1536 / 1024（默认）/ 768 / 512 / 256 | 0.0005 元/千 token（Batch 0.00025）；**100 万 token 免费额度** | ❌ 纯文本 |
| 阿里云百炼 | `qwen3.7-text-embedding-flash` | 1024 / 768 / 512 / 256 | **0.000125 元/千 token**（≈0.125 元/百万，Batch 再半） | ❌ 纯文本 |
| 阿里云百炼 | **`qwen3-vl-embedding`** | 多档 | 见 <https://help.aliyun.com/zh/model-studio/embedding> | ✅ **文本+图片+视频可混合输入，同一空间** |
| **智谱** | Embedding-3 | **256–2048 可自定义** | **0.5 元/百万 token** | ❌ 纯文本（输入模态：文本） |
| **硅基流动 SiliconFlow** | `BAAI/bge-m3` 等 | 1024 | 走统一模型价目表（**本轮未取到 embedding 分区的确切单价**） | 取决于模型；平台有免费模型（**未核实**） |
| **百度千帆** | Embedding 系列 | 未核实 | 未核实 | 未核实 |
| **Moonshot / Kimi** | — | — | — | ❌ **模型列表中无 embedding 模型**（已核对 <https://platform.kimi.com/docs/models>） |

### 4.2 腾讯混元 Embedding 的一个坑：接口预下线

> **此接口处于预下线状态。预计下线时间：2026-12-21。** 混元大模型老控制台将于 9 月 30 日下线。

`GetEmbedding` 接口域名 `hunyuan.tencentcloudapi.com`，**向量维度 1024 维**，默认限频 **5 次/秒**，`Input` 总长不超过 1024 token（超出截断），`InputList` 数组总长不超过 50。

来源：<https://cloud.tencent.com/document/product/1729/102832>（最近更新 2026-09-23）

**并且**混元大模型整体在向 **TokenHub** 迁移：

> 为进一步提升大模型服务体验，腾讯混元大模型相关功能将逐步迁移至 TokenHub。迁移后，原平台将不再新增模型能力，并停止支持新购模型服务。

来源：<https://cloud.tencent.com/document/product/1729/97731>（最近更新 2026-06-26）

> ⚠️ **决策含义**：**不要把 WeKnora 的 embedding 长期绑定到 `hunyuan.tencentcloudapi.com` 的 `GetEmbedding`**——它已公告 2026-12-21 下线。若要用腾讯系 embedding，应确认 TokenHub 上的替代接口与 WeKnora `provider` 的兼容性。

### 4.3 WeMM-Embedding 的可用性 —— 重点澄清

**WeMM-Embedding 没有腾讯云托管 API。** 它是腾讯开源的**自托管模型**：

- 模型权重在 HuggingFace：`tencent/WeMM-Embedding-2B`（512/1024/2048 维）、`-4B`、`-9B`（4096/5120/2560 维）。
- **没有任何官方托管服务**——README 的 "Inference" 一节给的是 `AutoModel.from_pretrained` + `SentenceTransformer.encode` 的**本地 Python 调用**。
- Embedding 提取方式：取最后一层 hidden states 在专用 `<embedding>` token 位置，做 L2 归一化。
- **"Audio input is not currently supported."**
- 明确写"Embeddings are obtained from the last-layer hidden states at the dedicated `<embedding>` token position, followed by L2 normalization"。
- 评测表格里的 MTEB 分数是作者本地跑的。

来源：<https://github.com/Tencent/WeMM-Embedding>（一手，README 原文）

**在 WeKnora 里的位置**：WeKnora v0.8.3 起支持多模态 embedding（把文本和图片编码到同一向量空间），"含腾讯 WeMM-Embedding、Qwen3-VL-Embedding"，可选择开启图片向量召回。仓库里也有 PR "feat(models): add WeMM embedding presets and Transformers serving"（v0.9.0 release notes 内），说明 WeKnora 提供 WeMM 的**预置配置 + Transformers serving 方式自托管**（即跑在本机/自建 GPU 上），**不是调云 API**。

来源：<https://github.com/Tencent/WeKnora/blob/main/README.md>（v0.8.3 release notes 段）、<https://github.com/Tencent/WeKnora/releases>（v0.9.0 / v0.8.3 body）

**对本题的直接含义**：本机有 **RTX 3070 8GB VRAM**，但 WeMM-Embedding-2B（2B 参数）以 fp16 计约 4GB 权重，8GB VRAM 勉强能跑推理，但加上其他占用会紧张；4B/9B 不现实。**若一定要"文本+图像同一向量空间"且想省钱，可考虑在本机 Windows 机器跑 WeMM-Embedding-2B + Transformers serving（WeKnora 已内置该路径），而不是找云 API——因为不存在。**

**若只想用云的多模态 embedding**：`qwen3-vl-embedding`（阿里云百炼）是国内可直连、官方托管的多模态 embedding，接口支持"单段文本、单张图片或单个视频文件的上传，也允许不同类型组合（如文本+图片）"。（来源：<https://help.aliyun.com/zh/model-studio/embedding>）

### 4.4 SiliconFlow / 百度 / Moonshot 的核实状态

- **SiliconFlow**：<https://www.siliconflow.cn/pricing> 可访问，页面按"对话模型"等分栏展示，但**本轮抓取被截断，未取到 embedding 分区的确切单价**。页面 URL 形态确认是"输入价格（M Tokens）"口径。**单价未核实。**
- **百度千帆**：千帆有模型服务计费（<https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya>），新用户注册送 20 元代金券（有效期 1 个月）。**Embedding 的具体模型、维度与单价未核实。**
- **Moonshot**：<https://platform.kimi.com/docs/pricing/chat> 与 <https://platform.kimi.com/docs/models> 只有 K3/K2 系列对话与视觉模型，**grep 全文无 "embedding"**。**Moonshot 无 embedding API。**

---

## 5. 成本现实检查（量级估算，非报价）

### 5.1 统一假设（请先读这段）

| 参数 | 取值 | 依据 / 说明 |
|---|---|---|
| 文档数 | 10,000 | 题设 |
| 原始数据量 | 50 GB | 题设 |
| 平均每文档正文（转 Markdown 后） | 20,000 字符 ≈ 10,000 汉字 | 假设 |
| chunk 大小 | 512 字符（WeKnora 默认） | 官方默认，约 300 中文 token |
| 每文档 chunk 数 | ~40 | 20,000 / 512 |
| **总 chunk 数** | **≈ 400,000** | 推导 |
| 嵌入向量维度 | 1024（混元 / bge-m3 / text-embedding-v3 默认） | §4 |
| 单向量内存 | 1024 × 4 B = 4,096 B | fp32 |
| **向量总内存（不含索引开销）** | **400,000 × 4,096 ≈ 1.64 GB** | 推导 |
| **含 1.2–1.3× buffer + HNSW 索引** | **≈ 2.5–3 GB** | 官方建议预留 1.2–1.3 倍 |
| **文本总量** | 10,000 × 20,000 = 2×10⁸ 字符 ≈ 1.2×10⁸ token | 中文约 1.5–2 字/token |
| **一次性嵌入 token 成本** | 1.2×10⁸ token × 0.7 元/百万 ≈ **84 元** | 混元价；SiliconFlow/DashScope 更低 |
| 稳态查询量 | 500 次/日 × 30 = 15,000 次/月，每次约 500 token | 假设 |
| **月度查询 token** | 7.5×10⁶ token ≈ **5 元/月** | 混元价 |

> **关键结论先行**：**embedding 的钱可以忽略**（一次性 ~84 元，稳态每月几元）。**成本几乎全部在"常驻的计算/存储实例"上。**

### 5.2 (a) 全本地（现有三台机器）

| 项目 | 月成本 | 说明 |
|---|---|---|
| 云服务 | **0 元** | 硬件已拥有 |
| 电力 | ~30–80 元 | 三台机器部分时间开机，估 0.2 kW×8h/天×30天×0.6 元/kWh + 常开设备 |
| 网络 | ~0–30 元 | 若全在 LAN 则接近 0 |
| **合计** | **约 0–110 元/月（量级：接近零）** | |

**但有隐性成本，必须计入决策**：

1. **M5 Pro / M6 是 ARM64，WeKnora 的 ParadeDB 镜像与 docreader 镜像的 CPU 基线是 `x86-64-v2`** → macOS 上要么走 Docker Desktop 的 Rosetta 模拟（慢，且 pgvector/SQLite 的 C 扩展在模拟下易出问题），要么**自己装原生 PG + pgvector + Python docreader**（工程量大）。
2. **MacBook 上没有 Docker** → 要么装 Docker Desktop（占资源、需付费授权给大企业），要么走 Lite 路线（见 §6，代价是 PDF/Word/PPT 仍需 docreader）。
3. **RTX 3070 8GB** 可以跑本地 embedding 模型（bge-m3 / WeMM-Embedding-2B），但 WeKnora 官方文档明确 **rerank 模型不支持 Ollama**，纯本地部署需另配远程 rerank 或关闭 rerank（`"rerank":{"enabled":false}`）。
4. **需要 7×24 在线**才能让飞书机器人随时响应 → MacBook 合盖即睡，必须是常开的 Mac mini 或 Windows 桌面。
5. **三台机器的运维**：升级 WeKnora（3 个月连发 6 个 minor 版本，v0.8.2 是 Breaking、v0.8.3 要求 reindexing）意味着每台都要重做。

**结论：全本地的"现金成本"最低，但"工程成本 + 运维成本 + ARM 兼容性坑"最高。** 由于三台机器是自有资产，**总拥有成本大概率仍低于任何云方案——前提是你愿意自己趟 ARM + 无 Docker 的坑。**

### 5.3 (b) 腾讯云 VectorDB + 小型 CVM

| 项目 | 月成本 | 依据 |
|---|---|---|
| VectorDB（标准型 1核4GB × **3 节点**） | 3 × 4 × 52 = **624 元/月** | 官方"默认选择 3 个节点" + 内存单价 52 元/GB/月（广州/南京） |
| VectorDB（标准型 2核8GB × 3 节点，充裕） | 3 × 8 × 52 = **1,248 元/月** | 同上 |
| VectorDB（**单节点标准型 1核4GB**，牺牲 HA） | 4 × 52 = **208 元/月** | 同上 |
| VectorDB（单节点存储型 1核8GB，内存最省） | 8 × 34 = **272 元/月** | 广州存储型 34 元/GB/月 |
| 小型 CVM（2核4GB，跑 WeKnora app + docreader + postgres/redis） | 约 **150–300 元/月** | **未核实腾讯云 CVM 具体报价**；按 CVM 常见市场价量级估 |
| 对象存储 COS（50GB 标准存储） | **~5 元/月量级** | 官方单价未核实，按量级估 |
| 混元 embedding（一次性建库 + 稳态查询） | ~90 元（一次性）+ ~5 元/月 | §5.1 |
| **合计（3 节点标准型 + CVM + COS）** | **约 800–950 元/月** | |
| **合计（单节点 + CVM + COS）** | **约 380–580 元/月** | |

**这个价位对 10k 文档是明显过配的。** 官方规格表里，**标准型 2核8GB 能撑 100 万条（1536 维）**，而我们只有 40 万 chunk。VectorDB 的最小可购买形态（3 节点 × 4GB）就是 624 元/月起。

> **这引出本题最重要的一个架构判断**：
> **"先用云端库"在腾讯云 VectorDB 上不成立——因为 VectorDB 不按存储量卖，最低消费 624 元/月。**
> **真正划算的"云端库"是自建 PostgreSQL + pgvector（或 ParadeDB）跑在一台 CVM/Lighthouse 上**：向量只占 2.5–3 GB 内存 + 文本 ~1–2 GB，一台 4 核 8GB 的机器足够，**月成本 100–250 元**，还顺带把 docreader/postgres/redis 一起装下（WeKnora 默认就是这套）。

### 5.4 (c) WeKnora on Lighthouse 模板 + 腾讯 VectorDB

| 项目 | 月成本 | 依据 |
|---|---|---|
| Lighthouse（WeKnora 镜像，4核8GB/180GB，通用型专业） | **245 元/月**（年付 2,499 元 ≈ 208 元/月） | 官方定价页 |
| Lighthouse（4核8GB/120GB，锐驰型，峰值 200Mbps） | **230 元/月** | 官方定价页 |
| VectorDB（3 节点标准型 1核4GB） | **624 元/月** | §5.3 |
| VectorDB（单节点标准型 4GB） | 208 元/月 | §5.3 |
| COS 50GB | ~5 元/月量级 | 未核实 |
| embedding | ~90 元一次性 + ~5 元/月 | §5.1 |
| **合计（3 节点 VectorDB）** | **约 880–1,100 元/月** | |
| **合计（单节点 VectorDB）** | **约 480–700 元/月** | |
| **合计（Lighthouse 全部本地检索，不上 VectorDB）** | **约 245–250 元/月 + embedding** | ParadeDB 自带 BM25 + pgvector |

**最后一个数字是全表最优解**：**一台 4核8GB 的 Lighthouse WeKnora 镜像 = 约 245 元/月，得到完整 WeKnora（含 BM25 混合检索、OCR、飞书渠道、文档解析），零运维。** 比"VectorDB + CVM"便宜 2–3 倍，且少了跨云网络与凭据管理。

**成本排序（同一功能完备度下）**：

```
全本地（自有硬件）  ≲ 100 元/月现金，但工程/运维/ARM 坑最重
Lighthouse WeKnora 镜像（ ParadeDB 内置 BM25+pgvector ）  ≈ 245 元/月  ← 性价比最优
Lighthouse + VectorDB 单节点                          ≈ 480–700 元/月
Lighthouse + VectorDB 3 节点                           ≈ 880–1,100 元/月
```

### 5.5 未计入的成本（诚实标注）

- **流量费**：Lighthouse 有流量包（4核8GB 通用型 2000GB/月，锐驰型无限流量）；VectorDB 与 CVM 的公网出流量另计。**未核实单价。**
- **备案**：若走域名 + 80/443 对公网，**中国大陆服务器需 ICP 备案**（数工作日）。若只在局域网/飞书内网用则不涉及。
- **公网暴露风险**：WeKnora README 有 WARNING，建议部署在内网/私有网络，避免暴露公网。Lighthouse 镜像首启后 `http://<公网IP>` 直开，**上线前应加安全组白名单 + 关闭注册**（`DISABLE_REGISTRATION=true`）。
- **数据合规**：文档可能含商业敏感内容 → 云端存储需评估；全本地天然合规。
- **VectorDB 与 WeKnora 的驱动成熟度**：`tencent_vectordb` 在 WeKnora 检索引擎列表中已列出（HNSW COSINE + 稀疏向量 SPARSE_INVERTED），但**该驱动的实际生产验证程度未核实**——官方文档在腾讯系产品上通常优先推 ParadeDB。

---

## 6. WeKnora 不装 Docker 的可行性（Lite 模式 & 桌面应用）

### 6.1 (a) 有没有官方预编译 Lite 二进制？—— **没有**

**证据 1：官方文档自己承认。** 《安装部署》文档"桌面应用"一节的 warning：

> ::: warning 尚未正式发布
> 桌面应用目前**没有随 Release 提供安装包**，需要自己按下面的步骤构建。`release-lite.yml` 里已有跨平台（macOS universal/amd64/arm64、Linux amd64、Windows amd64）的构建任务，但该工作流的 tag 触发被注释掉、只能手动触发，且**当前最新 Release 未附带任何产物**。
> :::

来源：<https://github.com/Tencent/WeKnora/blob/main/website-docs/01-getting-started/02-installation.md>

**证据 2：我核对了 GitHub Releases API 全部 42 个 Release**（v0.1.2 → v0.9.0，2025-09-10 → 2026-10-29），**每一个的 `assets` 数组都是空 `[]`**。即：**没有任何 tag 上传过任何二进制、tarball 或 zip 附件**（源码 tarball/zipball 是 GitHub 自动生成的，不算 release asset）。

来源：<https://api.github.com/repos/Tencent/WeKnora/releases>

**证据 3：工作流本身。** `.github/workflows/release-lite.yml` 的触发器：

```yaml
on:
  # push:
  #   tags:
  #     - "v*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Release tag (e.g. v0.2.0)"
        required: true
```

**tag 触发被注释掉了**，只能手动 dispatch。CI 环境变量 `GO_VERSION: "1.24"`、`NODE_VERSION: "22"`，`CGO_ENABLED: 1`，构建标签 `-tags "sqlite_fts5"`（macOS 分支另加 `webkit2_41`），macOS 步骤设置 `CGO_LDFLAGS="-Wl,-no_warn_duplicate_libraries"`。

来源：<https://github.com/Tencent/WeKnora/blob/main/.github/workflows/release-lite.yml>

> ⚠️ **一个值得注意的不一致**：CI 用 Go 1.24，但 `go.mod` 声明 **`go 1.26.0`**，官方文档说"**源码编译：Go 1.26**（见 `docker/Dockerfile.app` builder 阶段 `golang:1.26-bookworm`）"。**Go 1.24 无法编译 `go 1.26.0` 的模块**（Go 的 toolchain 机制会自动下载所需 toolchain，但前提是 `GOTOOLCHAIN=auto` 且能联网）。这说明该 workflow 尚未真正跑通过完整矩阵，或已过时。

**未能核实**：**Homebrew tap、第三方镜像站、GitHub 之外的 release 镜像**——本轮未找到任何官方或可信第三方的 WeKnora Lite 预编译二进制分发渠道。`brew search weknora` 未执行（需联网 brew tap）。**结论：必须自建。**

### 6.2 (b) `make build-lite` 在 macOS 上到底需要什么？

Makefile 原文（`build-lite` target，一手来源：<https://github.com/Tencent/WeKnora/blob/main/Makefile>）：

```make
build-lite:
	@if [ -f frontend/package.json ] && [ "$${SKIP_FRONTEND:-}" != "1" ]; then \
		echo ">> Building frontend for Lite..."; \
		(cd frontend && npm ci --prefer-offline && npm run build) && \
		rm -rf web && cp -r frontend/dist web; \
	elif [ "$${SKIP_FRONTEND:-}" = "1" ]; then \
		echo ">> Skipping frontend (SKIP_FRONTEND=1)"; \
	else \
		echo ">> No frontend/package.json, skipping frontend"; \
	fi
	export EDITION=lite; \
	eval "$$(./scripts/get_version.sh env)"; \
	LDFLAGS="$$(./scripts/get_version.sh ldflags) -X 'google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn'"; \
	CGO_ENABLED=1 \
	CGO_CFLAGS="-Wno-deprecated-declarations" \
	CGO_LDFLAGS="$$(if [ "$$(uname)" = 'Darwin' ]; then echo '-Wl,-no_warn_duplicate_libraries'; fi)" \
	go build -tags "sqlite_fts5" -ldflags="-w -s $$LDFLAGS" -o $(BINARY_NAME)-lite $(MAIN_PATH)
```

**依赖清单（全部一手核实）**：

| 依赖 | 要求 | 来源 |
|---|---|---|
| **Go** | **1.26**（`go.mod`: `go 1.26.0`）；`go mod download` | `go.mod`、`docker/Dockerfile.app`（`golang:1.26-bookworm`） |
| **CGO** | `CGO_ENABLED=1`（强制） | Makefile |
| **libsqlite3** | 需要系统 SQLite 开发库（macOS 自带 `libsqlite3`，但 `CGO_ENABLED=1` 时 cgo 默认链接系统 libsqlite3；**需 Xcode Command Line Tools**） | 官方文档明写"CGO（依赖 `libsqlite3-dev`）" |
| **构建标签** | `-tags "sqlite_fts5"`（FTS5 全文检索） | Makefile |
| **Node.js + npm** | 前端构建：`npm ci --prefer-offline && npm run build`；可用 `SKIP_FRONTEND=1` 跳过（但那样二进制没有 UI） | Makefile |
| **Python** | **仅 docreader 需要**：Python **3.10+**（`docreader/pyproject.toml`: `requires-python = ">=3.10.18"`），用 `uv` 管理依赖：`uv sync --locked && bash scripts/generate_proto.sh && uv run -m docreader.main` | `docreader/pyproject.toml`、安装文档 |
| **Xcode CLT** | CGO 需要（`xcode-select --install`） | 推断（CGO on macOS 必需） |

`docreader/pyproject.toml` 的完整依赖（一手）：`beautifulsoup4`、`ebooklib`、`grpcio`、`grpcio-health-checking`、`grpcio-tools`、`lxml`、`markdownify`、`markitdown[docx,pdf,xls,xlsx,pptx]`、`numpy`、`onnxruntime`、`opencv-python-headless`、`opendataloader-pdf`、`openpyxl`、`pandas`、`xlrd`、`pillow`、`playwright`、`protobuf`、`pydantic`、`pypdf`、`pypdfium2`、`python-docx`、`rapidocr`、`requests`、`textract==1.5.0`、`trafilatura`。

> 注意 `playwright` + `rapidocr` + `onnxruntime` —— **docreader 是个重量级依赖**（Playwright 要下载浏览器二进制，rapidocr 要下 ONNX 模型）。

`make package-lite`（`scripts/package-lite.sh`）产出 `dist/WeKnora-lite_<VERSION>_<GOOS>_<GOARCH>.tar.gz`，内含二进制、`web/` 前端、`.env.lite.example`、`config/`、`migrations/sqlite/`、`deploy/weknora-lite.service`、`docs/LITE.md`（作为 README）。

### 6.3 (c) Lite 模式需要 docreader 吗？—— **PDF/Word/PPT 需要**

`.env.lite.example` 里确实有 `DOCREADER_ADDR=127.0.0.1:50051` / `DOCREADER_TRANSPORT=grpc`，安装文档也说"**文档解析：仍可选连本地 docreader**"——看似可选。

**但源码给出的真相更严格。** `internal/infrastructure/docparser/builtin_converter.go`：

```go
// simpleFormats lists file extensions that Go can handle without the Python service.
var simpleFormats = map[string]bool{
	"md": true, "markdown": true,
	"txt": true, "text": true,
	"csv":  true,
	"json": true,
}

var imageFormats = map[string]bool{
	"jpg": true, "jpeg": true, "png": true, "gif": true,
	"bmp": true, "tiff": true, "webp": true,
}

var audioFormats = map[string]bool{
	"mp3": true, "wav": true, "m4a": true, "flac": true, "ogg": true,
}

// IsSimpleFormat returns true if the file type can be handled by the Go SimpleFormatReader.
func IsSimpleFormat(fileType string) bool {
	return simpleFormats[strings.ToLower(strings.TrimPrefix(fileType, "."))]
}
```

而 `engine_registry.go` 的路由逻辑：

```go
if engine == "" && !isURL && IsSimpleFormat(fileType) {
    return &SimpleFormatReader{}, nil
}
return remoteReader(deps)   // → docreader
```

**结论**：

| 格式 | Lite 单二进制能否处理 | 说明 |
|---|---|---|
| `.md` `.markdown` `.txt` `.text` `.csv` `.json` | ✅ Go 内置 | `SimpleFormatReader` |
| `.jpg` `.jpeg` `.png` `.gif` `.bmp` `.tiff` `.webp` | ✅ Go 内置 | 图片走 `SimpleFormatReader`；但**内容理解仍需 VLM/caption 模型** |
| `.mp3` `.wav` `.m4a` `.flac` `.ogg` | ✅ Go 内置 | 音频；但需 ASR 模型 |
| **`.pdf`** | ❌ **需 docreader** | `simpleFormats` 不含 pdf；`engines.go` 里 `IsSimpleFormat(ft) \|\| ft == "pdf"` 是**另一处**逻辑（pdf 走 builtin reader = 仍是 docreader） |
| **`.docx` `.doc`** | ❌ **需 docreader** | `builtinEngine` 的 FileTypes 明确列 docx/doc/pdf/md/... |
| **`.pptx` `.ppt`** | ❌ **需 docreader** | 同上 |
| `.xlsx` `.xls` `.epub` `.html` `.xmind` | ❌ 需 docreader | 同上 |
| 扫描件 PDF 的 OCR | ❌ | docreader 自身不做 OCR，**由 Go 侧对页面图跑 OCR**，需另配 VLM/OCR 引擎 |

**所以对本题场景（微信存档 + PDF + Word + PPT + 扫描件 + 图片 + 视频）**：

- **Lite 单二进制只能"零依赖"地处理图片 + 纯文本**；PDF/Word/PPT **必须**另跑 docreader（Python gRPC，端口 50051）。
- **视频**：WeKnora **根本不支持视频摄入**（格式表只有音频 mp3/wav/m4a/flac/ogg，无视频格式）。这是标准版和 Lite 都一样的限制。
- **Lite 的其它限制**（`docs/LITE.md` 原文）：
  - **不提供共享空间**（成员邀请、跨成员共享知识库与智能体）
  - **单空间**；标准版 Lite 需注册登录，桌面应用免注册
  - **内置仅 Simple 类型解析引擎**
  - **默认仅本机访问**，可配置放行公网
  - Lite 数据完全在本地

> 🔑 **对"团队"场景的致命点**：**Lite 没有共享空间/多成员协作。** "团队文档归档 + 飞书机器人"需要多用户与空间隔离——**Lite 的单空间设计直接与需求冲突**（飞书渠道机器人本身在标准版才是完整支持的）。

**运行 Lite 的最小实际形态**（如果真要走这条路）：

```
WeKnora-lite (SQLite FTS5 + sqlite-vec + 内存队列 + 内嵌前端)
  ├─ Ollama 或远程 embedding/LLM API
  └─ docreader (Python gRPC, :50051)  ← 仅为 PDF/Word/PPT 而存在
```

即 **"Lite" 并没有真正消灭 Python 依赖**，只是消灭了 Postgres + Redis + Docker。

### 6.4 (d) 有没有 macOS 桌面应用构建？—— **有源码，无成品**

- 入口：`cmd/desktop/main.go` + `cmd/desktop/wails.json`（Wails v2）。
- `cmd/desktop/app.go` 向前端暴露 `GetAPIBaseURL`（返回 `http://127.0.0.1:PORT/api/v1`）、HTTP 端口设置、**「绑定到局域网」设置**（正好匹配本题"局域网检索"需求）、`CheckForUpdates` 自动更新检查。
- `scripts/package-mac-app.sh`：先构建前端到 `web/`，再 `wails build -tags "sqlite_fts5"`，最后组装 `.app`：
  - `Contents/MacOS/WeKnora Lite` 为主程序
  - `Contents/Resources` 内嵌 `.env`、`config`、`migrations/sqlite`、web 前端
  - 相对路径数据自动重定向到 `~/Library/Application Support/WeKnora Lite/data/`
  - 日志写 `~/Library/Logs/WeKnora Lite/`
- 触发方式：`make package-mac-app`。
- 桌面应用启动时通过原生桥接取得每进程随机凭据，调 `POST /auth/auto-setup` 自动创建本地账号并登录（该接口不接受匿名 HTTP 请求）。

来源：<https://github.com/Tencent/WeKnora/blob/main/website-docs/01-getting-started/02-installation.md>、<https://github.com/Tencent/WeKnora/blob/main/scripts/package-mac-app.sh>

**结论**：桌面应用是**文档里明确标"尚未正式发布"**的功能，需要自己装 Wails v2 + Go 1.26 + Node 并执行 `make package-mac-app`。它**只解决单机桌面体验，不解决团队协作**。

### 6.5 给"无 Docker"场景的现实建议

如果硬性约束是"MacBook 上不装 Docker"，可选路径按性价比排序：

1. **【推荐】WeKnora 跑在腾讯云 Lighthouse 的官方 WeKnora 镜像上**（245 元/月 4核8GB）。MacBook 只当浏览器和飞书客户端，Docker 在云端。**零本地构建成本。**
2. **【次选】Mac mini M6 装 Docker Desktop**跑 WeKnora Compose——但 M6 是 ARM64，**ParadeDB 镜像的 x86-64-v2 基线要留意**（官方文档明说不保证 ARM 兼容）。
3. **【最折腾】本机 build-lite + 本机 docreader**：需 Go 1.26 + Xcode CLT + Node + Python 3.10/uv + Playwright 下载 + 还要解决 ARM 上 docreader 的 wheel 可用性；且 Lite 无共享空间，飞书多人协作不成立。**不建议。**
4. **【折中】Lite 只做"个人检索"，云端 WeKnora 做"团队系统"**：不推荐，重复建设。

---

## 7. 落地建议（针对本硬件集）

**推荐方案：腾讯云 Lighthouse（WeKnora 官方镜像，4核8GB，245 元/月）+ 全部数据留在本机/网盘，必要时再挂腾讯云 VectorDB。**

理由：

1. **官方镜像 + 自动密钥 + 自动启动**（`firstboot.sh`）→ 30 分钟内可用，不需要研究 Docker/Go/Python。
2. **Docker 问题被云端消化** → 直接满足"MacBook 没有 Docker"的约束。
3. **4核8GB 满足官方起步要求**；文档解析（docreader 含 LibreOffice + Playwright）在 2核2GB 的 45 元档上大概率 OOM。
4. **默认 ParadeDB 已带 BM25 + pgvector** → 混合检索零额外成本，无需 VectorDB。
5. **成本 245 元/月 vs VectorDB 方案的 880+ 元/月**，省 3–4 倍。
6. **飞书渠道**是 WeKnora 标准版的内置能力（`im` 插件），Lite 没有。
7. **保留向云端走的路径**：Lighthouse 与腾讯云 VectorDB / COS 同在腾讯云 VPC，可用内网互联（<https://cloud.tencent.com/document/product/1207/56847>）；若后续文档量涨到百万 chunk 级，再迁 VectorDB（官方规格表：标准型 2核8GB 撑 100 万条）。

**配套建议**：

- **embedding 用阿里云 `text-embedding-v4` 或 `qwen3.7-text-embedding-flash`**（0.5 / 0.125 元每百万 token，各有 100 万 token 免费额度），或**混元 0.7 元/百万**（但注意 `GetEmbedding` 已公告 2026-12-21 预下线）。避免绑死单一供应商——**换 embedding 模型要重建索引**。
- **若确实需要"文本+图像同一向量空间"**：用 `qwen3-vl-embedding`（阿里云托管）；**不要指望 WeMM-Embedding 云 API——它没有**。若要省钱，在 Windows + RTX 3070 上跑 WeMM-Embedding-2B + Transformers serving（WeKnora 已内置该路径）。
- **扫描件 OCR**：需另配 VLM（混元 vision / 本地 Ollama VLM）或 PaddleOCR-VL。**docreader 自身不做 OCR**，不配则扫描件无文本进索引。
- **安全**：上线前关闭注册（`DISABLE_REGISTRATION=true`）、改 `JWT_SECRET` 与 `SYSTEM_AES_KEY`（32 字节，`openssl rand -hex 16`）、安全组只放行必要端口。**`SYSTEM_AES_KEY` 丢了则已加密凭据不可恢复**，升级须沿用原值。
- **锁定版本**：WeKnora 三个月连发 6 个 minor 版本且有 breaking change；设置 `WEKNORA_VERSION` 固定到具体 tag（如 `0.9.0`），升级前读 release notes。

---

## 8. 未能核实事项清单（诚实标注）

| # | 事项 | 状态 |
|---|---|---|
| 1 | Tencent Cloud VectorDB 的**磁盘单价** | 官方计费页该表格未完整抓取 |
| 2 | Lighthouse **WeKnora 蓝图的 BlueprintId / 地域 / 预置版本号** | `DescribeBlueprints` 需 API 签名，本环境无腾讯云凭据；文档称需在购买页镜像详情查看 |
| 3 | **WeKnora Cloud 托管服务**是否有公开售卖页 | 代码里有 `WeKnoraCloudCredentials` / `weknoracloud_http_reader.go`，但未找到公开产品页 |
| 4 | **COS 各存储类型单价** | 计费页为动态渲染，未取到数值 |
| 5 | **腾讯云 CVM 具体报价** | 未取到官方价格表，成本估算按市场量级 |
| 6 | **SiliconFlow embedding 的确切单价** | 价格页可访问但被截断，未取到 embedding 分区 |
| 7 | **百度千帆 embedding 的模型/维度/单价** | 仅确认有模型服务计费与 20 元代金券 |
| 8 | **百度 VectorDB 是否支持稀疏/BM25** | 官方文档未明示 |
| 9 | **Zilliz Cloud 定价与免费额度** | <https://zilliz.com/pricing> 抓取失败（fetch failed） |
| 10 | **腾讯云 ES 报价** | 未取到 |
| 11 | **Homebrew tap / 第三方镜像站的 WeKnora Lite 预编译二进制** | 未找到任何官方或可信第三方分发渠道（brew search 未执行） |
| 12 | **微信官方文章《WeKnora x Lighthouse》正文** | 微信"环境异常"验证页拦截 |
| 13 | `tencent_vectordb` 驱动在 WeKnora 中的**生产成熟度** | 官方检索引擎文档已列出，但无公开案例 |
| 14 | `release-lite.yml` 的 **Go 1.24 与 go.mod `go 1.26.0` 不一致** | 已发现矛盾（见 §6.1），未验证该 workflow 是否曾成功运行 |

---

## 9. 来源索引

**腾讯云官方文档**
- 向量数据库 计费定价：<https://cloud.tencent.com/document/product/1709/103016>
- 向量数据库 选择节点规格及数量：<https://cloud.tencent.com/document/product/1709/113399>
- 向量数据库 稠密向量与稀疏向量高阶混合检索：<https://cloud.tencent.com/document/product/1709/111376>
- 向量数据库 稀疏向量工具介绍：<https://cloud.tencent.com/document/product/1709/111372>
- 向量数据库 商业化计费公告（2024-04-10）：<https://cloud.tencent.com/document/product/1709/104499>
- 轻量应用服务器 产品页与定价：<https://cloud.tencent.com/product/lighthouse>
- 轻量应用服务器 镜像更新记录（含"新增 WeKnora 镜像"）：<https://cloud.tencent.com/document/product/1207/47042>
- 轻量应用服务器 内网互联：<https://cloud.tencent.com/document/product/1207/56847>
- 轻量应用服务器 价格总览：<https://cloud.tencent.com/document/product/1207/73452>
- 腾讯混元大模型 向量化（GetEmbedding，预下线）：<https://cloud.tencent.com/document/product/1729/102832>
- 腾讯混元大模型 混元生文计费概述：<https://cloud.tencent.com/document/product/1729/97731>
- 对象存储 计费项：<https://cloud.tencent.com/document/product/436/53859>

**WeKnora 仓库（一手）**
- 安装部署（含 Lite 运行时与桌面应用）：<https://github.com/Tencent/WeKnora/blob/main/website-docs/01-getting-started/02-installation.md>
- Makefile：<https://github.com/Tencent/WeKnora/blob/main/Makefile>
- go.mod（go 1.26.0）：<https://github.com/Tencent/WeKnora/blob/main/go.mod>
- docreader/pyproject.toml：<https://github.com/Tencent/WeKnora/blob/main/docreader/pyproject.toml>
- `.env.lite.example`：<https://github.com/Tencent/WeKnora/blob/main/.env.lite.example>
- `docs/LITE.md`：<https://github.com/Tencent/WeKnora/blob/main/docs/LITE.md>
- `scripts/package-lite.sh`：<https://github.com/Tencent/WeKnora/blob/main/scripts/package-lite.sh>
- `scripts/package-mac-app.sh`：<https://github.com/Tencent/WeKnora/blob/main/scripts/package-mac-app.sh>
- `scripts/cloud-image/firstboot.sh`：<https://github.com/Tencent/WeKnora/blob/main/scripts/cloud-image/firstboot.sh>
- `scripts/cloud-image/README.md`：<https://github.com/Tencent/WeKnora/blob/main/scripts/cloud-image/README.md>
- `.github/workflows/release-lite.yml`：<https://github.com/Tencent/WeKnora/blob/main/.github/workflows/release-lite.yml>
- `internal/infrastructure/docparser/builtin_converter.go`（simpleFormats）：<https://github.com/Tencent/WeKnora/blob/main/internal/infrastructure/docparser/builtin_converter.go>
- `internal/infrastructure/docparser/engine_registry.go`：<https://github.com/Tencent/WeKnora/blob/main/internal/infrastructure/docparser/engine_registry.go>
- GitHub Releases API（42 个 release，assets 全空）：<https://api.github.com/repos/Tencent/WeKnora/releases>
- 检索引擎与向量存储（官方文档站）：<https://weknora.weixin.qq.com/docs/03-features/05-retrieval-engines>
- 总体架构（ParadeDB 说明）：<https://weknora.weixin.qq.com/docs/02-architecture/01-overview>
- 文档解析（docreader 与 OCR 边界）：<https://weknora.weixin.qq.com/docs/03-features/03-document-parsing>

**模型厂商**
- 腾讯 WeMM-Embedding README：<https://github.com/Tencent/WeMM-Embedding>
- 阿里云百炼 通用文本向量同步接口：<https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api>
- 阿里云百炼 文本与多模态向量化：<https://help.aliyun.com/zh/model-studio/embedding>
- 智谱 Embedding-3：<https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3>
- 硅基流动 价格中心：<https://www.siliconflow.cn/pricing>
- Moonshot/Kimi 模型列表（无 embedding）：<https://platform.kimi.com/docs/models>
- 百度智能云 向量数据库 VectorDB：<https://cloud.baidu.com/doc/VDB/index.html>
- 百度千帆 模型服务计费：<https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya>
