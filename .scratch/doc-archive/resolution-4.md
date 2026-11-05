## 决议

**第一阶段（开发测试期，全云端）走纯文本检索；图文同空间与 pplx ColBERT 后置到第二阶段（本地部署）。**

| 项 | 决策 | 依据 |
|---|---|---|
| 图片检索 | **后置**。图片先做 VLM caption，caption 是文本，走普通 dense+BM25 检索 | 核心痛点"忘了存在哪"靠正文+caption 文本即可覆盖；图文同空间是唯一未实测且有兼容风险的路径 |
| pplx-embed-v2-late | **第一阶段不用**。第二阶段本地 GPU 自托管时再评估；现在先把权重下载回来跑通 ColBERT 小样验证，不进主线 | ColBERT 多向量不兼容 WeKnora；无云端 API；自托管需 GPU，撞上"全云端" |
| 文本 embedding | **SiliconFlow `BAAI/bge-m3`（1024 维，免费）** | 免费、WeKnora 默认就是 bge-m3、1024 维零迁移成本、中文语义检索实测有效 |
| 检索后端 | **WeKnora dense(bge-m3) + BM25（ParadeDB 自带 jieba）**，图片只进 caption 文本 | 纯文本路零未实测风险，主线最快跑通 |

**硬事实基础**（已核源码）：WeKnora 多模态向量检索官方只支持 vLLM 自托管（WeMM/Qwen3-VL-Embedding），不直接支持云端多模态 embedding API；pplx-embed-v2-late 是 ColBERT 多向量，与 pgvector/VectorDB 的 dense 单向量不兼容。

**推论**：
- 第二阶段的技术路线预留：本地 vLLM 部署 WeMM-Embedding-2B 或 Qwen3-VL-Embedding 做图文同空间，pplx-embed-v2-late 做 ColBERT 召回（可选）。
- 换 embedding 模型需重建索引，所以 bge-m3 的 1024 维从第一天就定死。
- SiliconFlow Qwen3-VL-Embedding 接 WeKnora 的 `image_format=object` 兼容性**未实测**，第二阶段若要云端图文同空间，需先验证这点再决定是否用云端还是本地 vLLM。
