# Perplexity 模型可下载性核验

核验日期：2026-11-05。结论均核一手来源（arXiv 论文全文、HuggingFace 模型卡、ModelScope 镜像 API、hf-mirror 镜像 API）。

## 一句话结论

**`pplx-embed-v2-late` 是开源模型（MIT 许可），权重已经能从你的网络下载**——不走 HuggingFace 直连（被墙），走 **hf-mirror.com 或 modelscope.cn 两个国内可达镜像**。但它是 **late-interaction（ColBERT 式多向量）模型**，不是普通的 dense 单向量模型，这直接影响它能不能塞进 WeKnora。

## 事实核验

### 1. 许可：MIT（已确认）

| 来源 | 证据 |
|---|---|
| HuggingFace 模型卡 | `perplexity-ai/pplx-embed-v2-late-0.6b` 与 `-9b` 均 `license: mit`，tags 含 `license:mit` |
| ModelScope 镜像 | `License: 'mit'`（API 字段确认） |
| arXiv 论文 | arXiv:2610.29523（CC BY-NC-ND 4.0，仅论文文本） |

论文（[arXiv:2610.29523](https://arxiv.org/abs/2610.29523)）明确写：

> Both models are available at https://huggingface.co/collections/perplexity-ai/pplx-embed-v2

### 2. 模型是什么（论文原文 + 模型卡）

- **两个尺寸**：`pplx-embed-v2-late-0.6b`（594M 参数，其中文本编码 ~240M、图像编码 ~340M、token 表 ~254M）与 `-9b`（7.4B 活跃参数）。
- **架构**：Qwen3.5 骨干（hybrid bidirectional-causal），18B 教师模型 representation distillation 得到。
- **多模态 late-interaction**：文本 + 图像 + 视觉文档（PDF 页渲染图）**同一向量空间**；**每个 token 一个 128 维向量**，用 **MaxSim** 打分。
- **⚠️ 关键限制：它不是 dense 单向量模型**，是 ColBERT 式多向量（每 token 128 维）。**不能直接塞进 WeKnora 的 pgvector HNSW / 腾讯云 VectorDB 的 cosine 单向量检索**。
- **非对称部署**：0.6B 和 9B 共享空间，可"9B 建索引 + 0.6B 发查询"。
- 模型卡：**`Mixed text+image inputs are not supported`**（文本和图像要分开 encode 调用）。
- 需要 `sentence-transformers >= 6.0.0` + `transformers >= 5.4.0`。

### 3. 权重文件与大小（已确认）

0.6b（ModelScope 记录）：

| 文件 | 大小 |
|---|---|
| `model.safetensors` | 2,377,321,184 B ≈ 2.2 GB（F32） |
| `1_Dense/model.safetensors` | 524,376 B |

9b 结构相同（17 个文件，含 `1_Dense`/`2_MultiVectorMask`/`3_Normalize` 三个子模块）。

### 4. 下载通道（国内可达，已实测）

| 通道 | 状态 | 说明 |
|---|---|---|
| `huggingface.co` | ❌ 000（被墙） | DNS 返回被污染 IP |
| `hf-mirror.com` | ✅ 200 | API 已返回 0.6b/9b 两个仓库的完整文件列表 |
| `modelscope.cn` | ✅ 200 | `perplexity-ai/pplx-embed-v2-late-0.6b` 已在（FromSite: github，模型源 USER_UPLOAD），Downloads=125 |
| `arxiv.org` | ✅ 200 | 论文全文可读 |

**下载命令（hf-mirror）**：
```bash
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download perplexity-ai/pplx-embed-v2-late-0.6b --local-dir ./models/pplx-embed-v2-late-0.6b
# 或 git lfs clone
git clone https://hf-mirror.com/perplexity-ai/pplx-embed-v2-late-0.6b
```

## 对架构的关键影响

**它与 WeKnora 不兼容。** WeKnora 的检索引擎是 dense（pgvector/VectorDB 的 HNSW cosine）+ BM25 稀疏的 RRF 融合；而 pplx-embed-v2-late 是每 token 128 维的多向量 MaxSim（ColBERT 式），需要 ColBERT 式的存储与检索（类似 RAGatouille / Qdrant 的 late-interaction 索引 / Elasticsearch 新特性）。

**三选一（留给架构票决策）**：

1. **主检索用 WeKnora 原生（dense bge-m3/Qwen3-Embedding + BM25）**，pplx-embed-v2-late 单独做一个"图像/视觉文档"专用索引（只对图片和扫描件页做 ColBERT 索引），双检索后端。
2. **放弃 pplx-embed-v2-late，统一用 dense 多模态模型**（SiliconFlow `Qwen3-VL-Embedding-8B` 图文同空间，或本地 WeMM-Embedding-2B）——单向量，能和 WeKnora 的 pgvector 兼容。
3. **自建 ColBERT 检索层**（放弃 WeKnora 的检索，只用它做问答），全部用 pplx-embed-v2-late。

## 未决

- `pplx-embed-v1`（dense）和 `pplx-embed-v2-context`（contextual dense）是否同样开源、能否 hf-mirror 下载——本次只核了 `v2-late`（用户点名的是"多模态"）。v1 权重论文标注 Apache-2.0（前次研究），但未逐一核下载通道。
- 0.6b 在 8GB VRAM 的实际显存占用与吞吐（F32 权重 2.2GB，理论可跑，但多向量索引的存储放大是 dense 的数倍——每 token 一个向量）。
