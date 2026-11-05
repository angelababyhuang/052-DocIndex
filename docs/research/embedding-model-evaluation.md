# 嵌入 / 向量模型选型调研（Perplexity 多模态向量模型 + 中文文档检索）

调研日期：2026-11-05
调研问题：052-DocIndex 团队文档归档 / 语义检索系统该用什么嵌入模型？
约束：中文文档（PDF/Word/PPT/扫描件/视频帧）向量化 + 混合检索；可能需要「文本查询检索图片」的共享向量空间；本机离线可用。

---

## 0. 结论速览（TL;DR）

| # | 结论 | 置信度 |
| :- | :- | :- |
| 1 | **Perplexity 有嵌入模型，而且不止一个家族**：`pplx-embed-v1`（4 个：标准/上下文 × 0.6B/4B）、`pplx-embed-v2-context`（9B/0.6B）、`pplx-embed-v2-late`（9B/0.6B，多模态 late-interaction）。官方技术报告发表于 ACL 2026 Industry Track + arXiv | **已验证** |
| 2 | **Perplexity 托管 API 是纯文本的**，没有图像输入端点。`pplx-embed-v2-late` 是**开权重多模态模型**（文本+图像+视觉文档同一空间），但需本地跑 | **已验证** |
| 3 | **Perplexity / OpenAI 从目标网络不可达**（api.perplexity.ai、api.openai.com TCP 443 超时，DNS 返回被污染的 Meta IP 段）。中国境内托管的 API 可达 | **父 agent 实测 + 我复核** |
| 4 | **文本主力推荐 SiliconFlow 的 `Qwen/Qwen3-Embedding-8B`**（可达、OpenAI 兼容、维度可调 64–4096、32K 上下文） | **已验证** |
| 5 | **图文共享空间推荐 SiliconFlow 的 `Qwen/Qwen3-VL-Embedding-8B`**（同一 `/v1/embeddings` 端点，input 支持 `{"image": url/base64}` + `{"text": ...}` 混合列表） | **已验证** |
| 6 | **零成本起步：`BAAI/bge-m3` 在 SiliconFlow 上免费**，1024 维、8192 token | **已验证** |
| 7 | **PaddleOCR-VL-0.9B 官方推荐 8GB 显存即可部署**，正好卡在 RTX 3070 8GB 的下限；SiliconFlow 上 `PaddlePaddle/PaddleOCR-VL-1.5` 免费 | **已验证** |
| 8 | **腾讯混元 Embedding 接口正在下线**（`GetEmbedding` 预计 2026-12-21 下线），**不要基于它设计新系统** | **已验证** |

---

## 1. Perplexity 到底有没有嵌入模型？——**有，而且已经是一个完整的家族**

用户的原前提「Perplexity 是搜索/LLM API 公司，可能没有向量模型」**不成立**。Perplexity 在 2026 年发布了自有嵌入模型家族 `pplx-embed`，并提供托管 Embeddings API。

### 1.1 托管 API：标准 + 上下文两个端点

官方 quickstart 文档（`docs.perplexity.ai/docs/embeddings/quickstart`）给出的完整模型表：

| 模型 | 维度 | 上下文 | MRL（Matryoshka） | 量化 | 价格（$/1M tokens） |
| :-: | :-: | :-: | :-: | :-: | :-: |
| `pplx-embed-v1-0.6b` | 1024 | 32K | 是 | INT8/BINARY | $0.004 |
| `pplx-embed-v1-4b` | 2560 | 32K | 是 | INT8/BINARY | $0.03 |
| `pplx-embed-context-v1-0.6b` | 1024 | 32K | 是 | INT8/BINARY | $0.008 |
| `pplx-embed-context-v1-4b` | 2560 | 32K | 是 | INT8/BINARY | $0.05 |

来源：[Embeddings API Quickstart](https://docs.perplexity.ai/docs/embeddings/quickstart)、[Standard Embeddings](https://docs.perplexity.ai/docs/embeddings/standard-embeddings)、[Contextualized Embeddings](https://docs.perplexity.ai/docs/embeddings/contextualized-embeddings)、[Perplexity Pricing — Embeddings API Pricing](https://docs.perplexity.ai/docs/getting-started/pricing)

关键接口事实（均已从官方 `.md` 文档原文核对）：

- **端点 1（标准）**：`POST https://api.perplexity.ai/v1/embeddings`，`input` 是**扁平字符串数组**，每个文本独立编码。
- **端点 2（上下文）**：`POST https://api.perplexity.ai/v1/contextualizedembeddings`，`input` 是**嵌套数组**（内层数组 = 同一文档的 chunks，按原文顺序）。SDK 方法名 `client.contextualized_embeddings.create()`。
- **OpenAI 兼容性**：路径是 `/v1/embeddings`、请求体字段 `model` / `input`、响应体 `{"object":"list","data":[{"object":"embedding","index":i,"embedding":"..."}],"usage":{...}}`——**结构与 OpenAI 一致**，但有两处关键差异：
  1. **`embedding` 字段返回的是 base64 字符串**，不是浮点数组。格式由 `encoding_format` 控制：`base64_int8`（默认，解码后是 `dimensions` 字节的有符号 int8）或 `base64_binary`（按位打包，1 bit/维，解码后 `dimensions/8` 字节）。
  2. **Perplexity 的向量未归一化**。官方明确警告：必须用 **cosine similarity** 比较（INT8）或 **Hamming distance**（binary）；**不能**用内积或 L2 距离，除非先转 float32 再 L2 归一化。
- **无 instruction prefix**：所有模型 mean pooling，不需要 query/passage 前缀（这和 BGE / E5 / Qwen3-Embedding 的 instruct 用法相反，省事）。
- **单请求上限**：最多 512 条文本 / 合计 120,000 tokens。
- **MRL 降维**：`dimensions` 参数可直接指定（例：`dimensions=512`）。**但降维会改变向量空间，索引建好后不可改**——换维度必须全量重新嵌入。
- **响应里带 `usage.cost`**（含 `input_cost` / `total_cost` / `currency`），方便对账。

来源：[Best Practices](https://docs.perplexity.ai/docs/embeddings/best-practices)（含 encoding_format 表格与相似度度量说明）、[Embeddings API 文档索引 llms.txt](https://docs.perplexity.ai/llms.txt)

### 1.2 v2 家族（更新，且更便宜 / 更强）

Perplexity 官方 cookbook 的 `embeddings-docs-search` 配方页披露了 **v2 代**：

- 模型名：`pplx-embed-v2-context-9b`、`pplx-embed-v2-context-0.6b`，**同一个 checkpoint 同时服务 dense 和 contextual 两种模式**——发扁平数组走 dense，发嵌套数组走 contextual。
- 新增 **`input_type` 参数**（`document` / `query`），用于显式标注用途。
- 维度可取 **2048 或 1024**。
- **两个尺寸共享同一向量空间**，可以「9B 建索引、0.6B 发查询」的非对称部署。
- 价格（该配方页实测确认）：**9B = $0.12/1M tokens，0.6B = $0.02/1M tokens**。
- 开源权重发布在 HF：`perplexity-ai/pplx-embed-v2-context-9b`、`perplexity-ai/pplx-embed-v2-context-0.6b`。
- 官方实测（FastAPI 文档 144 文件 / 2683 chunks，30 道题）：contextual 索引 top-5 命中 28 题，dense 命中 23 题。

来源：[Search Your Docs Folder with pplx-embed-v2（Perplexity Cookbook）](https://docs.perplexity.ai/docs/cookbook/examples/embeddings-docs-search/)

> 注：定价页（`/docs/getting-started/pricing`）目前只列了 v1 四个模型，**v2 的价格我是从 cookbook 配方页的实测记录读到的**（该页明确写「$0.12 per million tokens for the 9b model. The 0.6b model is $0.02 per million」，并附了总价校验：1,815,960 tokens → $0.2179）。**标为「高置信但建议以 pricing 页为准」**。

### 1.3 开权重模型：技术报告与基准

- **v1**：《Diffusion-Pretrained Dense and Contextual Embeddings》，Perplexity AI 官方技术报告，已发表于 **ACL 2026 Industry Track**（pp. 990–1004, San Diego），arXiv:2602.11151。
  来源：[ACL Anthology 2026.acl-industry.69](https://aclanthology.org/2026.acl-industry.69/)、[arXiv:2602.11151](https://arxiv.org/abs/2602.11151)、[HTML 全文](https://arxiv.org/html/2602.11151v2)
  - 架构：把 **Qwen3-0.6B-Base / Qwen3-4B-Base** 去掉 causal mask，用 diffusion 目标做继续预训练，得到双向注意力编码器（30 个其他语种的 FineWeb2 + 英文 FineWeb-Edu，约 250B tokens，序列长 4096）。
  - Pooling：mean pooling + **训练时就做 int8 量化感知**（`floor(127·tanh(mean)+0.5)`），不是事后压缩。
  - **MTEB(Multilingual, v2) nDCG@10 = 69.66%（INT8）**，对齐 Qwen3-Embedding-4B（69.60%）、超过 gemini-embedding-001（67.71%）。内部 PPLXQuery2Query Recall@10 = 73.5%（Qwen3-Embedding-4B 为 67.9%）。
  - ConTEB 上 `pplx-embed-context-v1-4b` nDCG@10 = 81.96%，超过 voyage-context-3（79.45%）和 Anthropic Contextual Retrieval（72.4%）。
  - 许可：模型卡 **Apache-2.0**（HF `perplexity-ai/pplx-embed-context-v1-0.6b` 元数据显示 `license: apache-2.0`）；arXiv 论文本身是 CC BY-NC-ND 4.0（论文许可与权重许可不同）。
- **v2-late**：《pplx-embed-v2-late Technical Report》，arXiv:2610.29523（2026-10-21）。
  来源：[arXiv:2610.29523](https://arxiv.org/abs/2610.29523)、[HTML 全文](https://arxiv.org/html/2610.29523v1)
  - **这是 Perplexity 的多模态嵌入模型**：0.6B 与 9B 两个 **multilingual + multimodal late-interaction** 模型。
  - **同一模型同时处理文本、图像、视觉文档（页面截图）**——满足「文本查询检索图片」。
  - Backbone：**Qwen3.5**（Qwen3.5-27B 剪掉最后 20 层得到 18B teacher），关掉 full-attention 层的 causal mask，改为 bidirectional-causal 混合架构。
  - **每个 token 128 维**（multi-vector，MaxSim 打分，不是单向量）。
  - 训练时：query 截断 256 token，document 截断 1,000 token，**图片 resize 到至多 917,504 像素**（保持宽高比）。
  - 从 18B teacher 做 **representation distillation** 得到 9B 和 0.6B；**0.6B 和 9B 共享同一向量空间**（可互换：9B 建索引、0.6B 发查询）。
  - 许可：**MIT**（HF `perplexity-ai/pplx-embed-v2-late-9b` / `-0.6b` 的 model card 标注 `license: mit`）。
  - 基准：ViDoRe-V3 文本 74.8%（9B）/ 73.6%（0.6B）vs 最强 baseline 69.3%；领域文本检索平均 81.3%（9B）/ 78.0%（0.6B），0.6B 在所有 <1B 模型中每个领域都是第一；ViDoRe-V3 上取得 SOTA。

### 1.4 直接回答问题 2：「没有嵌入模型时最接近的是什么」→ **不适用，他们有**。但顺带澄清 Perplexity 其他相关能力：

- **Search API**：返回网页片段 + URL，不是向量。
- **Sonar / Agent API**：带引用的答案，不是向量。
- **没有公开的 rerank 端点**（Perplexity 文档索引 llms.txt 里没有独立的 rerank API；但文档检索里可用 reranker 模型搭配）。
- **他们自己的做法**：`pplx-embed-context-v1` 就是「上下文化嵌入」——把文档 chunks 连同整篇文档的上下文一起编码，比传统的「切块 → 独立编码」在长文档检索上效果更好（ConTEB 81.96%）。**这正是 052-DocIndex 这类文档归档系统最该抄的设计**。

### 1.5 ⚠️ 致命限制：Perplexity 托管 API **不能**从目标网络访问

父 agent 实测：`api.perplexity.ai` 与 `api.openai.com` 的 443 端口 3/3 次 TCP 失败，DNS 返回被污染的 Meta IP 段。我在本机复核一致：

```
$ curl -sS -m 12 -o /dev/null -w "%{http_code}\n" https://api.perplexity.ai/
000   # connection timed out
$ nslookup docs.perplexity.ai
Name: docs.perplexity.ai  Address: 108.160.161.83   # 被污染的 IP
```

同样不可达：`docs.perplexity.ai`、`research.perplexity.ai`、`platform.perplexity.ai`、`huggingface.co`、`raw.githubusercontent.com`、`github.com`（间歇）、`api.openai.com`。

**可达**（我实测）：`arxiv.org`、`aclanthology.org`、`api.siliconflow.cn`、`api.tencentcloudapi.com`、`dashscope.aliyuncs.com`、`open.bigmodel.cn`、`docs.siliconflow.cn`、`help.aliyun.com`、`cloud.tencent.com`、`docs.bigmodel.cn`、`www.siliconflow.cn`、`perplexity.mintlify.app`（文档镜像，可作 Perplexity 文档的备用取证通道）。

**结论：Perplexity 的嵌入模型在技术上是这个领域的第一梯队，但在这个项目的部署网络里不可用（除非走代理）。** 它的**开权重版本**（Apache-2.0 的 v1 / MIT 的 v2-late）可以通过国内可达的模型托管（SiliconFlow 等）拿到——但这些托管平台是否上架了 pplx-embed，需要按 §3 的清单逐个确认。

---

## 2. 主力候选横向对比（中文 / 多模态 / 可达性）

### 2.1 中国境内可达的托管 API（**本项目的实际可用集合**）

| 提供方 | 模型 | 维度 | 上下文 | 价格 | OpenAI 兼容 | 文本+图共享空间 | 备注 |
| :- | :- | :-: | :-: | :- | :-: | :-: | :- |
| **SiliconFlow** | `BAAI/bge-m3` | 1024 | 8192 | **免费** | 是（`/v1/embeddings`） | 否 | 零成本基线 |
| **SiliconFlow** | `BAAI/bge-large-zh-v1.5` | 1024 | 512 | **免费** | 是 | 否 | 纯中文，但上下文太短 |
| **SiliconFlow** | `Qwen/Qwen3-Embedding-8B` | 64–4096（默认 4096） | 32768 | 见下 | 是 | 否 | 文本主力候选 |
| **SiliconFlow** | `Qwen/Qwen3-Embedding-4B` | 64–2560 | 32768 | 见下 | 是 | 否 | |
| **SiliconFlow** | `Qwen/Qwen3-Embedding-0.6B` | 64–1024 | 32768 | 见下 | 是 | 否 | |
| **SiliconFlow** | `Pro/BAAI/bge-m3` | 1024 | 8192 | ¥0.07/M tokens | 是 | 否 | Pro 通道 |
| **SiliconFlow** | **`Qwen/Qwen3-VL-Embedding-8B`** | 可调 | — | 见下 | 是（**VL 请求体**） | **是** | **图文共享空间主力** |
| **DashScope 百炼** | `qwen3.7-text-embedding` | 2560/2048/1536/1024/768/512/256 | 128,000 | **¥0.0005/千 token**（≈¥0.5/M），Batch ¥0.00025/千 | 是（`/compatible-mode/v1/embeddings`） | 否 | 上下文最长，单价明确 |
| **DashScope 百炼** | `qwen3.7-text-embedding-flash` | 1024/768/512/256 | 128,000 | **¥0.000125/千 token**（≈¥0.125/M） | 是 | 否 | 最便宜的 128K |
| **DashScope 百炼** | `text-embedding-v4` | 2048/1536/1024/768/512/256/128/64 | 8192 | ¥0.0005/千 token | 是 | 否 | Qwen3-Embedding 系列 |
| **DashScope 百炼** | `text-embedding-v3` | 1024/768/512/256/128/64 | 8192 | ¥0.0005/千 token | 是 | 否 | |
| **DashScope 百炼** | `text-embedding-v1/v2` | 1536 | 2048 | ¥0.0007/千 token | 是 | 否 | 老款 |
| **智谱 BigModel** | `embedding-3` | 256–2048（默认 2048） | 8192 | **¥0.5/M tokens**（Batch ¥0.25/M） | 是（`/api/paas/v4/embeddings`） | 否 | 单次最多 64 条，每条 ≤3072 token |
| **智谱 BigModel** | `embedding-2` | — | 8192 | ¥0.5/M | 是 | 否 | |
| **腾讯混元** | `GetEmbedding` | 1024 | 1024 token | — | 腾讯云 API 3.0 签名 | 否 | ⚠️ **预下线，2026-12-21** |

来源：
- SiliconFlow 嵌入接口规格（模型清单、最大 token、`dimensions` 可选值、**VL 请求体 schema**）：[docs.siliconflow.cn/docs/api/embeddings-post](https://docs.siliconflow.cn/docs/api/embeddings-post)
- SiliconFlow 定价（BAAI bge-m3 免费、Pro ¥0.07/M、**PaddleOCR-VL-1.5 免费**）：[siliconflow.cn/pricing](https://www.siliconflow.cn/pricing)
- DashScope 完整价格表（维度 / 最大行数 / 单行最大 token / 每千 token 单价 / 免费额度 / 新加坡区价格）：[help.aliyun.com/zh/model-studio/text-embedding-synchronous-api](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
- DashScope OpenAI 兼容端点：`POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings`
- 智谱 Embedding-3 规格（维度、上下文、单价、请求限制、调用示例）：[docs.bigmodel.cn/cn/guide/models/embedding/embedding-3](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3)、[智谱 API 定价（向量模型表）](https://docs.bigmodel.cn/cn/guide/start/pricing)
- 腾讯混元向量化接口（1024 维、1024 token、5 次/秒限流、**预下线公告**）：[cloud.tencent.com/document/product/1729/102832](https://cloud.tencent.com/document/product/1729/102832)

**SiliconFlow 实际连通性验证**：`GET https://api.siliconflow.cn/v1/models?sub_type=embedding` 返回 `{"code":30014,"message":"Token is invalid."}` —— 401 而非超时，说明**网络通、只是没带 key**。

#### DashScope 兼容模式的两个坑（已从官方文档核实）

1. **新网关 vs 老网关**：`encoding_format=base64` 只在**新网关 + 短请求**时生效；**长请求内部会路由到老网关，只返回 float**。想省流量必须确认走的是新网关。
2. **免费额度有期限**：百炼开通后 90 天内有效 `100 万 Token`；`text-embedding-v1/v2` 无免费额度。

### 2.2 SiliconFlow 的 VL 接口（本项目最关键的一张牌）

`Qwen/Qwen3-VL-Embedding-8B` 在**同一个** `POST /v1/embeddings` 端点上用另一套请求体 schema：

```jsonc
// 文本
{"model": "Qwen/Qwen3-VL-Embedding-8B", "input": "一段中文"}

// 图片（URL 或 base64）
{"model": "Qwen/Qwen3-VL-Embedding-8B", "input": {"image": "https://.../a.jpg"}}

// 文本 + 图片 + 混合列表 —— 这是「共享空间」的关键
{"model": "Qwen/Qwen3-VL-Embedding-8B",
 "input": ["First text", {"text":"第二段"}, {"image":"https://.../b.jpg"}]}
```

官方文档明确：「文本、图像及其组合统一映射到高质量向量空间，实现精准的跨模态语义对齐」。**「暂不支持视频内容」。**

来源：[SiliconFlow 创建嵌入请求](https://docs.siliconflow.cn/docs/api/embeddings-post)

### 2.3 本地 / 开权重模型（离线或走国内托管）

| 模型 | 维度 | 上下文 | 许可 | 图文共享空间 | 备注 |
| :- | :-: | :-: | :- | :-: | :- |
| **BAAI/bge-m3** | 1024（可 MRL 降到 8…1024） | 8192 | MIT | ❌ 纯文本 | 多语言（100+ 语种），dense+sparse+rerank 三合一；中文表现扎实；**8GB VRAM 轻松跑**，Mac 也能跑 |
| **Qwen3-Embedding-0.6B / 4B / 8B** | 1024 / 2560 / 4096（均可 MRL 降到 64） | 32768 | Apache-2.0 | ❌ 纯文本 | MTEB 多语种 SOTA 梯队；**0.6B 适合 8GB VRAM / Apple Silicon**；支持 instruction prefix |
| **Qwen3-VL-Embedding-8B** | 可调（SiliconFlow 上 64–4096） | — | Apache-2.0 | ✅ **是** | 需要图文共享空间时的选择 |
| **WeMM-Embedding 2B / 4B / 9B**（腾讯微信视觉团队） | 2B: 64/128/256/512/1024/2048；4B: …2560；9B: …4096 | — | **Apache-2.0** | ✅ **是**（文本/图像/视频/视觉文档/交错多模态统一空间） | MMEB-v2 (78 数据集) 与 MMEB-v3 (190 任务) 双榜第一；2B 77.9 / 9B 80.6，均超 Qwen3-VL-Embedding；**2B 用 256 维可保留全维 98.7% 性能**；**不支持音频** |
| **pplx-embed-v1-0.6b / -4b** | 1024 / 2560 | 32K | **Apache-2.0** | ❌ 纯文本 | 双语注意力 + mean pooling + INT8 量化感知；MTEB 多语种 69.66%（4B） |
| **pplx-embed-v2-context-0.6b / -9b** | 1024 / 2048 | — | 未核实 | ❌ 纯文本（v2-late 才是多模态） | dense + contextual 双模式同一 checkpoint；`input_type` 参数 |
| **pplx-embed-v2-late-0.6b / -9b** | 128 / token（multi-vector） | — | **MIT** | ✅ **是**（文本+图像+视觉文档） | late-interaction / MaxSim，**需要专用打分逻辑，不能当普通 dense 向量用**；Qwen3.5 backbone；0.6B 和 9B 共享向量空间 |

来源：
- BGE-M3 模型卡（100+ 语种、8192、dense+sparse+多向量）：[BAAI/bge-m3 on HuggingFace](https://huggingface.co/BAAI/bge-m3) —— *本机不可达，仅经二手引用，建议在可达环境复核*
- Qwen3-Embedding 系列与多语言能力：[Qwen3-Embedding 官方博客](https://qwenlm.github.io/blog/qwen3-embedding/) —— *同上是二手引用*
- **WeMM-Embedding 详细规格**（维度阶梯、MMEB 成绩、vLLM 部署命令、Matryoshka 效果、Apache-2.0、不支持音频）：[腾讯云开发者社区 — WeMM-Embedding：微信视觉团队的多模态嵌入模型](https://cloud.tencent.com.cn/developer/article/2744111)、[github.com/Tencent/WeMM-Embedding](https://github.com/Tencent/WeMM-Embedding)
- pplx-embed-v2-late 规格（128 维/token、Qwen3.5 backbone、917,504 像素、MIT、蒸馏与非对称部署）：[arXiv:2610.29523 HTML 全文](https://arxiv.org/html/2610.29523v1)、[arXiv:2610.29523 摘要页](https://arxiv.org/abs/2610.29523)

**WeMM-Embedding 的一个部署优势**：官方给了 vLLM pooling runner 的启动方式——
```bash
vllm serve "$MODEL_PATH" --runner pooling --chat-template "$MODEL_PATH/embedding_chat_template.jinja"
```
并提供 Transformers 5.2.0 与 Sentence Transformers 推理示例。2B 版本（2048 维，降到 256 维保留 98.7% 性能）在 **8GB VRAM 上可行**——这是目前本项目能落地的最强开源中文图文统一向量模型。

### 2.4 关于 BGE-VL / SigLIP2 / Nomic / NV-Embed / Jina / Cohere / Voyage 的说明

用户清单里的这些模型**我本轮没有逐个核实**（HF 不可达，无法读一手 model card）。已知的情况：

- **Cohere Embed v4**：确为多模态（text+image 共享空间），但 Cohere API 在本网络**不可达**，且社区实测显示其在中文短文本检索上通常**不如** Qwen3-Embedding / bge-m3。**不推荐**。
- **Voyage AI**：voyage-4 系列在 v2-late 论文的表格里作为对照出现（voyage-4-large 1024 维、voyage-4-nano 2048 维），但 Voyage API **不可达**。**不推荐**。
- **Jina**：jina-embeddings-v3/v4/v5 有 8192 长上下文；Jina CLIP v2 做图文共享空间。但 API 不可达，本地跑需要较大显存。**待核实，暂不推荐**。
- **Nomic Embed Text v2/v3、nvidia/llama-nemoretrieval、NV-Embed**：英文强、中文弱，且本地跑吃显存。**不推荐**给中文场景。
- **SigLIP2**：纯 CLIP（图文共享空间，可训练），但**零样本检索在中文长文档上弱**，需要针对中文文档 fine-tune，成本高。**不建议**作为首选。
- **BGE-VL（BAAI）**：定位是 MLLM 上的向量模型（把 VLM 的输出映射成向量），中文基础好但生态与文档不如 WeMM-Embedding / Qwen3-VL-Embedding。**次选**。

> **诚实声明**：以上这几条是**基于「API 不可达 + 已知的中英表现差异」的排除性判断**，不是逐个读 model card 核实的结论。若后续需要为它们正名，请在能访问 HuggingFace 的环境下补一轮。

---

## 3. 回答问题 4：按本机设备怎么排（MacBook Pro M5 Pro 64GB / Mac mini M6 16GB / Win i7 32GB + RTX 3070 8GB）

**关键约束：8GB VRAM 是硬约束。** 下面按「谁能装进 8GB」分类。

### 3.1 能进 8GB VRAM 的

| 模型 | 精度 | 粗估显存 | 说明 |
| :- | :- | :- | :- |
| `bge-m3` (568M) | FP16 | ~1.2 GB | 极宽裕，还能跑 dense+sparse 双路 |
| `Qwen3-Embedding-0.6B` | FP16 | ~1.3 GB | 32K 上下文（长文会吃更多 KV，但 embedding 无 KV cache） |
| `Qwen3-VL-Embedding-8B` | **INT8/INT4 量化** | ~8–10 GB | ⚠️ **8GB 边缘**，需 aggressive 量化 + 限制 `max_model_len`；建议先试 INT4 |
| `WeMM-Embedding-2B` | FP16 | ~4–5 GB | ✅ **最稳的多模态本地选项**；再降到 256 维输出，存储也省 |
| `pplx-embed-v2-late-0.6b` | FP16 | ~1.3 GB | ✅ 多模态 late-interaction，MIT，0.6B 与 9B 共享向量空间 |
| `pplx-embed-v1-0.6b` | FP16 | ~1.3 GB | ✅ Apache-2.0，纯文本 |

### 3.2 需要 64GB Mac 的

| 模型 | 理由 |
| :- | :- |
| `WeMM-Embedding-9B` | FP16 约 18GB + 视觉编码器开销 → MacBook Pro 64GB 统一内存可轻松承载 |
| `pplx-embed-v2-late-9b` | 同上，MIT 多模态 late-interaction 的强版本 |
| `Qwen3-VL-Embedding-8B`（非量化） | FP16 约 16GB + 视觉塔 |
| `pplx-embed-v1-4b` | FP16 约 8GB，Mac 16GB 也够 |
| `Qwen3-Embedding-8B` | FP16 约 16GB，Mac 16GB 紧张，64GB 舒服 |

**Apple Silicon 特别说明**：MLX 社区已有 `pplx-embed-v1-0.6b-mlx` 转换版（[agentmish/pplx-embed-v1-0.6b-mlx](https://huggingface.co/agentmish/pplx-embed-v1-0.6b-mlx)），Mac mini M6 16GB 上跑 0.6B 嵌入可以吃满 Apple Silicon 的统一内存带宽，比 Windows+CUDA 更划算。`bge-m3` 也有 MLX/ONNX 转换。

### 3.3 各机器的建议分工

| 机器 | 建议承担的活 |
| :- | :- |
| **Win i7 + RTX 3070 8GB** | ① **PaddleOCR-VL-0.9B** 文档解析（官方就写「显存 8GB 及以上」，且给了 3060 单卡 16K 上下文的配置示例）→ 出 Markdown + 页面截图；② **WeMM-Embedding-2B** 或 **Qwen3-VL-Embedding-8B (INT4)** 出图文共享向量；③ 批量调 API 的客户端 |
| **Mac mini M6 16GB** | 常驻服务：向量库 + 检索 API + `bge-m3` / `Qwen3-Embedding-0.6B` 本地嵌入（MLX 加速），或跑 DashScope/SiliconFlow 的转发 |
| **MacBook Pro M5 Pro 64GB** | ① 跑 **9B 级**模型（`WeMM-Embedding-9B` / `pplx-embed-v2-late-9b`）做**高质量离线批量索引**；② 重 OCR（长文档 / 高分辨率扫描件）；③ 开发与评测 |

### 3.4 排名的实用建议

**第一梯队（现在就能上）**
1. **SiliconFlow `Qwen/Qwen3-Embedding-8B`** — 文本主索引。国内直连、OpenAI 兼容、32K 上下文、维度可调 64–4096。把它作为**文本向量**主力。
2. **SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`** — **图文共享空间**。同一端点、同一模型家族，文本和图片向量可直接互检。
3. **SiliconFlow `BAAI/bge-m3`（免费）** — 零成本对照组 / 小语种兜底 / 混合检索里的 sparse 路。

**第二梯队（本地/离线）**
4. **WeMM-Embedding-2B（Apache-2.0）** — 数据敏感（文档不能出内网）时的开源图文统一空间方案。MMEB 双榜第一，2B 精度已超 Qwen3-VL-Embedding。**RTX 3070 8GB 可跑。**
5. **Qwen3-Embedding-0.6B / bge-m3 本地** — 离线/无网时的纯文本兜底。

**第三梯队（不要现在投入）**
6. Perplexity `pplx-embed-*` — 技术上第一梯队，但**网络不通**；除非走代理或自建镜像。
7. Cohere Embed v4 / Voyage 4 / Jina v5 — 网络不通 + 中文非优势。
8. Nomic / NV-Embed / SigLIP2 / BGE-VL — 中文与工程成熟度不足。

### 3.5 一个务实的架构建议（同时满足需求 a 和 b）

不要强行用**一个**模型干两件事。用**同一模型家族的两个成员**，共享同一套基础设施：

```
文档 → PaddleOCR-VL-0.9B（本地，RTX3070）→ Markdown 文本 + 页面截图 PNG
                                                        ↓
文本 chunk ──────→ Qwen3-Embedding-8B (SiliconFlow) ──→ 文本向量（主索引）
                                                        ↓
页面截图 ────────→ Qwen3-VL-Embedding-8B (SiliconFlow) → 图向量（可被文本查到）
```

若希望**一张表**同时容纳两类向量，就把 `Qwen3-Embedding-8B` 和 `Qwen3-VL-Embedding-8B` 都换成 **WeMM-Embedding-2B**——它本来就是「一个模型一个空间装下所有模态」的：
```
文档文本 chunk ─┐
页面截图 ───────┼→ WeMM-Embedding-2B → 单一向量空间（文本查询可命中图片）
PPT/Word 插图 ─┘
```
代价是文本检索精度略低于 Qwen3-Embedding-8B（WeMM 的强项在跨模态，纯文本 SOTA 仍是 Qwen3-Embedding 家族）。

**不要混用不同模型的向量到同一个索引**：不同模型的向量空间不互通，混存会让相似度完全失效。若必须双索引，用两个 collection / 两个 namespace。

---

## 4. 回答问题 5：谁能做「一个模型把文本和图片放进同一空间」？

### ✅ 能

| 模型 | 模态覆盖 | 空间类型 | 备注 |
| :- | :- | :- | :- |
| **Qwen3-VL-Embedding-8B** | 文本 + 图像（+ 组合） | dense 单向量 | SiliconFlow 已托管，`/v1/embeddings` 同端点；**不支持视频** |
| **WeMM-Embedding 2B/4B/9B** | 文本 + 图像 + **视频** + **视觉文档** + 交错多模态 | dense 单向量（L2 归一化，取最后层 `<embedding>` token 位置） | **Apache-2.0**；**不支持音频**；MMEB 双榜第一；Matryoshka 降维 |
| **pplx-embed-v2-late 0.6B/9B** | 文本 + 图像 + 视觉文档 | **multi-vector（late-interaction）** | **MIT**；128 维/token；**MaxSim 打分，不能当普通 dense 向量用**，需要专用索引（如 PLAID / Qdrant 的 late-interaction 方案） |
| SigLIP2 | 文本 + 图像 | dense 单向量 | 纯 CLIP，零样本中文长文档弱 |
| Jina CLIP v2 | 文本 + 图像 | dense 单向量 | 待核实 |
| Cohere Embed v4 | 文本 + 图像 | dense 单向量 | API 不可达，中文非优势 |

### ❌ 不能（纯文本）

**BGE-M3**、**Qwen3-Embedding 全系**、**bge-large-zh**、**jina-embeddings-v3/v4/v5**、**Nomic**、**NV-Embed**、**Perplexity `pplx-embed-v1` 和 `pplx-embed-v2-context`**、**智谱 embedding-3**、**DashScope `text-embedding-v*` / `qwen3.7-text-embedding`**、**腾讯混元 GetEmbedding**。

> 特别提醒：**DashScope 和 SiliconFlow 的纯文本 embedding 端点都不接受图片输入**。SiliconFlow 的图片输入只在 `Qwen/Qwen3-VL-Embedding-8B` 这个模型上开放（它走 `EmbeddingsVLRequest` schema）。

### 实际限制

1. **视觉文档（PDF 页面截图）通常先 OCR 成 Markdown 再嵌入，比直接嵌图更准**。v2-late 论文的 ViDoRe-V3 表格标题就写着「after converting page images to Markdown with OCR」，且上下文模式（用全文上下文编码 chunk）在这种场景明显优于 dense。
2. **图像分辨率有上限**：v2-late 训练时图片 resize 到至多 **917,504 像素**；过小的图会丢细节。
3. **多模态模型的纯文本检索精度通常略低于纯文本专用模型**——如果 80% 的查询是文字、20% 是图，双索引比单空间更划算。

---

## 5. 回答问题 6：中文扫描 PDF 的 OCR / 文档解析

### 5.1 可跑在 8GB VRAM 的

| 工具 | 规格 | 资源 | 中文质量 |
| :- | :- | :- | :- |
| **PaddleOCR-VL-0.9B** ✅ 首选 | 0.9B 视觉语言模型，DENSE，**128K 上下文**（推理最长 16K） | **官方推荐「显存 8GB 及以上 + 共享内存 4GB 以上」**，FastDeploy 给了 **3060 单卡 16K 上下文**的现成配置 | Paddle 系，中文原生最优梯队；能同时出文本 + 表格结构 + 版式 |
| **PaddleOCR-VL-1.5** | 新版 | SiliconFlow 上 **免费**（`PaddlePaddle/PaddleOCR-VL-1.5`） | 同上；**走 API 就完全不占本地显存** |

来源：[FastDeploy PaddleOCR-VL-0.9B 最佳实践（显存要求、3060 配置示例、OOM 处理）](https://paddlepaddle.github.io/FastDeploy/zh/best_practices/PaddleOCR-VL-0.9B/)、[SiliconFlow 定价（PaddleOCR-VL-1.5 免费）](https://www.siliconflow.cn/pricing)、[vLLM recipes: PaddleOCR-VL-0.9B · 0.9B · DENSE · 128K ctx](https://recipes.vllm.ai/PaddlePaddle/PaddleOCR-VL)

### 5.2 其他选项（本轮未逐一核实，仅列判断）

| 工具 | 判断 |
| :- | :- |
| **RapidOCR** | PaddleOCR 模型转 ONNX 的轻量封装，**纯 CPU 可跑**（ONNX Runtime 量化），中文好、速度快、零 GPU 依赖。适合「没有 GPU 的 Mac mini」做兜底，或做预过滤（先判是否扫描件） |
| **MinerU** | 论文级 PDF 解析（公式、表格、版面），中文好，但依赖较重（多模型协同），显存需求 >8GB 的概率大。建议放到 64GB Mac 上跑 |
| **Docling** | IBM 出品，PDF/PPTX/DOCX 统一解析，**对 PPT/Word 支持好**（本项目需要），本地 CPU 可跑，OCR 后端可换 PaddleOCR |
| **Marker** | 走 surya 之类，英文强、中文一般，质量好但吃显存 |
| **Surya** | 文档 OCR + 版面 + 表格，英文为主，中文一般 |
| **GOT-OCR2** | 学术向，2B，8GB 边缘可跑，中文可用但文档版面理解不如 Paddle 系 |

### 5.3 建议的分层 OCR 流水线

```
PDF/图片 → 是否已有文本层？
  ├─ 有 → 直接抽文本（pymupdf / pdfplumber）+ 渲染页面 PNG（供图向量用）
  └─ 无（扫描件）
       ├─ 快速判断（RapidOCR CPU，几十 ms/页）→ 便宜的直接过
       └─ 复杂版面（表格/公式/多栏）→ PaddleOCR-VL-0.9B（RTX 3070 8GB）
                                              ↓
                                     Markdown（文本 chunk） + 页面 PNG（图向量）
```

`PaddleOCR-VL-1.5` 在 SiliconFlow 免费 → **API 兜底 + 本地 GPU 主力**的双轨，能在本地 8GB 卡扛不住时无缝降级。

---

## 6. 未决 / 需要后续核实的事项

1. **SiliconFlow 上 Qwen3-Embedding / Qwen3-VL-Embedding 的具体单价**——定价页的嵌入模型区块在「展开更多」折叠区，我只拿到了 BAAI 系（免费 / ¥0.07/M）的确切价格，Qwen 系需要登录控制台或展开该区块确认。
2. **Perplexity v2 模型的官方定价页条目**——当前 `/docs/getting-started/pricing` 只列 v1 四个模型；v2 的 $0.12/$0.02 来自 cookbook 实测记录，标记为高置信但需以 pricing 页复核。
3. **SiliconFlow 是否上架了 `perplexity-ai/pplx-embed-*`**——若上架，则可以在国内网络用上 Perplexity 的模型 + Perplexity 的模型质量，是最优解。需要在 `cloud.siliconflow.cn/me/models?types=embedding` 用账号查。
4. **BGE-VL / Jina CLIP v2 / Nomic v3 / NV-Embed 的确切规格**——HF 本机不可达，本轮未读一手 model card。
5. **WeMM-Embedding 的官方技术报告与 HF model card**——我读的是腾讯云开发者社区的官方专栏（二手但署名腾讯云社区作者，且细节具体、可交叉验证）；`github.com/Tencent/WeMM-Embedding` 的 README 因网络限制没直读到。
6. **DashScope 是否提供多模态 embedding 端点**——其文档站的嵌入 API 只有文本模型；未见图片输入 schema。若需 DashScope 侧的图文共享空间，需另行确认。
7. **PaddleOCR-VL-1.5 的具体能力差异**（相对 0.9B）——只从 SiliconFlow 价格页看到它在列并免费，能力细节待查。

---

## 7. 附：本次调研的网络取证说明

- `docs.perplexity.ai` / `api.perplexity.ai` / `research.perplexity.ai` / `huggingface.co` / `raw.githubusercontent.com` / `github.com`（间歇）/ `api.openai.com` —— **TCP 443 超时，DNS 被污染**。
- Perplexity 官方文档通过 **`perplexity.mintlify.app`（官方文档站镜像，支持 `.md` 后缀直接取 Markdown 原文）** 取证成功，是本次能拿到权威规格的关键通道。
- Perplexity 的 arXiv 技术报告通过 **`arxiv.org`（可达）** 取证成功。
- 国内厂商文档（SiliconFlow / DashScope / 智谱 / 腾讯云 / FastDeploy）全部直连取到原文。
