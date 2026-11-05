## Destination

一份能直接进入开发的《团队文档归档系统（全量文档库）技术方案 + spec》：覆盖「微信手动存入 → 去重 → 建全量原文索引 → 统一转 md → 统一图片库 → 向量库 → 分类（项目/管理…）→ 飞书/局域网查询」完整链路，明确硬件拓扑（**开发测试期全云端，测试靠谱后本地采购部署**）、软件架构、数据模型、检索/嵌入选型、部署形态。

本轮 = **全量文档库**（团队工作流第一步），目标是"快速找到记得处理过但忘了存在哪的资料"（把 40 分钟的查找压到秒级）。后续的「项目事实库 + 专人信息提取」是下一轮，另开图。

## Notes

**已定决策（grilling 13 问，见 `docs/research/` 与 `docs/agents/`）**：

- **复用 014 不重造**：三层身份模型（出现→文档→版本）+ 双哈希去重（`byte_hash`→`text_fp`→`name_key`），014 已跑 559 文档/5316 出现。需新增：图片/视频实体、多项目归属。
- **微信手动存**：延续 014 的 `001-按日期缓存` 另存模式，不解容器 DB（TCC + 法律风险，chatlog 已因法务函删除）。
- **分类沿用 014 五类**（研发/测试/管理/商务/资料，ADR-0002 论证过）+ 项目单归属；**全部建成 WeKnora 标签**（`custom_metadata` 不能检索过滤，已核源码）。图片/视频独立成类。
- **真相源 = 图片库 + 元数据**；向量库/WeKnora 索引是派生可重建。图片库照抄 031 的 ADR-0001/0002（内容 sha256 去重、`IMG-0001` 永不改号、单一母本 + `索引.tsv`、引用只认 `{{IMG-0001}}`）。
- **图片双入口**：文档内嵌图走 WeKnora chunk↔图片关联；独立图走图片库 + 共享向量空间。**图片概述用免费 GLM-4V-Flash，OCR 用免费 PaddleOCR-VL-1.5 + RapidOCR 兜底**。
- **视频由人写简介**：不抽帧、不自动 VLM 概述（#5 决议）。
- **模糊搜索 = 混合检索 + 同义词**，不做编辑距离（WeKnora 无此能力，已核源码）。
- **查询入口 = WeKnora 原生飞书渠道 + 局域网 Web UI**，不自建机器人。
- **迁移 014 可回退**：新建库导入，用 014 指纹算法重算校验，对不上的人工看，不用脚本静默重建。

**硬件拓扑（分阶段）**：**开发测试期 = 全云端**——图片/视频存储走对象存储（COS），图片/视频概述与 OCR 走云端 VLM API，向量库/检索走云端 WeKnora。现有三台机器（MBP M5 Pro 64G / Mac mini M6 16G / Windows i7+3070 8G）在开发测试期只做开发与客户端，不承担推理/存储职责。**测试靠谱后 = 本地采购硬件自建部署**（届时是否复用现有三台、还是新购 GPU 服务器，另定）。

**每会话必读**：`docs/research/` 四份报告 + `docs/research/perplexity-model-verification.md` + 031 图片库 ADR + 014 的 CONTEXT.md/ADR。

**已知硬约束（实测）**：无 Docker（连容器运行时都没有）；ARM64 不兼容 ParadeDB x86-64-v2 镜像；SQLite FTS5 中文 2 字查询失效（trigram/unicode61 均 0 命中）；Perplexity/OpenAI API 网络不通（DNS 污染）；腾讯混元 GetEmbedding 2026-12-21 下线；pplx-embed-v2-late 是 ColBERT 式多向量，不兼容 WeKnora。

## Decisions so far

- [WeKnora 部署形态：云端 Lighthouse 镜像 vs 本地](https://github.com/angelababyhuang/052-DocIndex/issues/6): 开发测试期全云端（腾讯云 Lighthouse 官方 WeKnora 镜像 + COS + 云端 VLM API），本地采购部署进 Out of scope（第二阶段）。
- [检索与嵌入架构：pplx-embed-v2-late 三选一](https://github.com/angelababyhuang/052-DocIndex/issues/4): 第一阶段纯文本检索（SiliconFlow bge-m3 免费 + WeKnora BM25），图片后置 caption、pplx 与图文同空间留第二阶段。
- [视频与图片的云端 VLM/OCR 选型与视频抽帧策略](https://github.com/angelababyhuang/052-DocIndex/issues/5): 图片概述用免费 GLM-4V-Flash，OCR 用免费 PaddleOCR-VL-1.5 + RapidOCR 兜底，**视频由人写简介、不抽帧**。
- [飞书查询入口：WeKnora 原生飞书渠道 vs 自建](https://github.com/angelababyhuang/052-DocIndex/issues/7): WeKnora 原生飞书渠道（websocket），/search 定位 + 问答附赠，先私聊后群聊，建应用清单后置。
- [014 数据迁移与图片库落地的数据模型](https://github.com/angelababyhuang/052-DocIndex/issues/8): 图片/视频独立顶层实体（IMG/VID 各自 ID），文档单归属+关联项目标签，SQLite 真相源，014 全量只读迁移可回退。

## Not yet specified

<!-- 前沿已空：所有决策票已关闭。以下不是待 grill 的决策，而是 spec 编写时要填的字段级细节，属 handoff 内容。 -->

- 数据模型字段契约：`Image`/`Video`/`Document` 完整字段（继承 031 图片库字段 + caption/尺寸/时长等）
- 关联项目标签在 SQLite 的存法（JSON 列 vs tag 表）与 WeKnora 标签映射
- WeKnora 标签 → 检索过滤映射（项目/类别/图片/视频四类标签命名与多值语义）
- 014 指纹复算校验脚本
- 飞书建应用的开权限步骤清单（#7 决议的后置执行项）

## Out of scope

- 项目事实库 / 项目经理人工信息提取（团队工作流第二步，另开图）
- 编辑距离模糊搜索（Q11 明确不做）
- 企业微信会话内容存档（需 Windows/Linux 采集机，本机无企业微信）
- 直接解微信容器 DB（TCC + 法律风险）
- 视频音轨转写 ASR（Q6 延后）
- **本地硬件采购与自建部署**（测试靠谱后的第二阶段，届时另开图；开发测试期一律云端）
