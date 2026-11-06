# WeKnora API 契约与实测坑（052 用到的部分）

改 `src/docarchive/weknora.py` / `index.py` 之前先读这份，别重新去翻网页文档。
内容来自 2026-11-06 的真机实测：**线上实际是 WeKnora v0.8.0**（`GET /api/v1/system/info`，commit `1edcd54`）；下面的契约按 v0.9.0 源码记录，版本差异见「需要知道的限制」。

## 连接与认证

| 项 | 值 |
|---|---|
| Base URL | `.env` 的 `WEKNORA_BASE_URL`（例：`http://101.43.123.124:8088`） |
| 认证 | `POST /api/v1/auth/login`，body `{email, password}`（`.env` 的 `WEKNORA_EMAIL` / `WEKNORA_PASSWORD`） |
| 之后 | 请求头 `Authorization: Bearer <token>`；机器主体可用 `X-API-Key`，但本客户端只发 Bearer |
| 健康检查 | `GET /health`（**不带** `/api/v1` 前缀，免认证） |

⚠️ 登录成功的响应**没有 `data` 包裹**：`token` 在顶层
（`{"token": "...", "refresh_token": "...", "user": {...}}`），不要按 `data.token` 取。

## 响应形状的三种坑

同一个「列表」接口，返回形状有三种，`weknora._items()` 统一兜住：

| 形状 | 出现在 |
|---|---|
| `{"data": [ ... ]}` | `GET /api/v1/knowledge-bases`、`GET /api/v1/models` |
| `{"data": {"total": n, "page": 1, "page_size": 20, "data": [ ... ]}}` | `GET /api/v1/knowledge-bases/:id/tags`（分页信封） |
| `{"success": true, "data": {...}}` | 创建类接口（返回单个对象） |

`POST /api/v1/knowledge-search` 实测 `data` 是裸数组；文档写的 `meta.rerank` 实测可能为 `null`。

## 本仓库用到的端点

| 用途 | 方法与路径 | 关键字段 |
|---|---|---|
| 列模型 | `GET /api/v1/models` | `id`、`name`、`type`（**首字母大写**：`Embedding` / `Rerank` / `VLLM` / `KnowledgeQA`） |
| 找/建/删知识库 | `GET/POST /api/v1/knowledge-bases`、`DELETE /api/v1/knowledge-bases/:id` | 建库 body：`name`、`type: document`、`embedding_model_id`；返回 `data.id` |
| 列/建标签 | `GET/POST /api/v1/knowledge-bases/:id/tags` | 建标签 body 只有 `{name}`（可选 color/sort_order）；**标签是平的，没有维度字段**，四维靠命名（`项目/<名>`、`类别/<名>`、`图片`、`视频`） |
| 推 markdown | `POST /api/v1/knowledge-bases/:id/knowledge/manual` | body `{title, content, status: "publish", tag_ids: []}`；返回 `data.id`（**没有 metadata 字段**） |
| 写知识元数据 | `PUT /api/v1/knowledge/:id` | body `{custom_metadata: {...}}`；整体覆盖，见下节 |
| 读分块 | `GET /api/v1/chunks/:knowledge_id?page=1&page_size=100` | `data` 是数组，含 `content`、`start_at`、`end_at`、`chunk_index`、`image_info` |
| 查解析状态 | `GET /api/v1/knowledge/:id` | `data.parse_status` ∈ `pending/processing/completed/failed`；**不要**用 `/knowledge-bases/:kb/knowledge/:kid`（404） |
| 检索 | `POST /api/v1/knowledge-search` | body `{query, knowledge_base_ids, tag_ids, match_count}`；返回项含 `content`、`score`、`knowledge_id`、`knowledge_title` |

## knowledge 的 custom_metadata（#21/#22 实测）

`manual` 接口没有 metadata 字段，只能建完知识后单独写：

```
PUT /api/v1/knowledge/:kid
{"custom_metadata": {"图片": "IMG-0001,IMG-0002", "文档": "doc-12"}}
```

- 校验：≤20 个字段、键 1–64 字符、值 ≤1000 字符（string/number/bool/null）。
  **超了不是 400 而是 500**（`invalid custom_metadata field "…"`），所以本地先截断。
- 是**整体覆盖**：只传要保留的字段；不传 `custom_metadata` 就不改。
- `GET /api/v1/knowledge/:kid` 读回来是对象（`data.custom_metadata`）。
- **检索结果里是拍平文本**：`knowledge_custom_metadata` 为 `键: 值`、多字段按
  换行分隔、键按字典序（`CustomMetadataText()` 的输出），空元数据是 `null`。
  解析按行取 `图片:` / `文档:` 前缀，不要假设 JSON。
- 052 的约定：**文档条目**带 `文档 = doc-<id>`（回查受管文件位置），有内嵌图时
  再带 `图片` / `图片数`（#21）；图片/视频条目不写。旧索引没有 `文档` 字段，需要
  `index --rebuild` 一次。
- 元数据变化会触发一次文档摘要刷新（`summary_status` 转 `pending`），不阻塞解析。
- **必须在解析完成后写**：解析完成会回写 knowledge 行，解析过程中写的 `custom_metadata`
  可能被覆盖（真机 3 条里丢 1 条）。052 的 `index` 先 `wait_knowledge` 再 PUT。

## 内嵌图标记在分块里原样保留（#21 实测）

manual 推的 markdown 里写 `{{IMG-0001}}`，分块正文和 `knowledge-search` 返回的
`content` 里都是原样的 `{{IMG-0001}}`。`SearchResult` 还带 `id`（分块 id）、
`knowledge_id`、`start_at` / `end_at`、`chunk_type`、`knowledge_custom_metadata`。
所以 chunk ↔ 图片的映射可以做两层：正文标记（分块级）+ knowledge 元数据
（文档级兜底）。若将来标记不再保留，退路是用命中里的 `id` + `GET /api/v1/chunks/:knowledge_id`
（按分块 id 则用 `GET /api/v1/chunks/by-id/:id`）的 `start_at`/`end_at` 做偏移映射；
本仓库目前没有走这条路（ADR-0005）。

## 需要知道的限制

- **手动 markdown 是异步解析的**：实测 2–3 秒从 `pending` 到 `completed`。`index --wait` 就是轮询这个状态；不要假装同步成功。
- **没有「描述这一张图」的接口**：视觉模型只在 WeKnora 自己的索引管线里跑。所以真相源的 caption 由独立的 OpenAI 兼容 VLM 生成（ADR-0002），OCR 由本机 RapidOCR（ADR-0001）。
- **不要上传原文件让它重新解析**：真相源里已经有转好的 md，用 `knowledge/manual` 推文本；上传 docx/pdf 会让 WeKnora 再解析一遍，口径还可能与我们的转换不一致。
- **图片二进制不进 WeKnora**：图片母本只在真相源，检索回指由本仓库按编号解析
  `images.path`（ADR-0005）。WeKnora 返回的 `resource://` / 直链只适用于它自己
  收的文件，与本项目的图无关。
- **线上 v0.8.0 的 `knowledge-search` 不认逐请求参数**：`vector_threshold`、`keyword_threshold`、`match_count`、`disable_keywords_match`、`disable_vector_match`、`rerank` 都会被静默忽略；v0.8.2 仍如此，**v0.8.3 才加上**。本仓库改用客户端截断 `--limit`；要真正调阈值/开关，需升级到 v0.8.3+，或走 `hybrid-search`（v0.8.0 已支持阈值/开关/`match_count`，但没有 rerank）。
- **embedding 模型按名字钉死**（spec §7.2）：默认找 `text-embedding-v4`，找不到就报错，不静默换「空间里第一个」。
- 标签按知识库隔离；既有库可能有别的命名习惯（如 `005-雷达仿真`），所以默认建独立库 `052 文档库`。

## 怎么重新核（不要先爬渲染页）

1. 一手文档在 GitHub `Tencent/WeKnora` 仓库的 `website-docs/`（Markdown 源），直接读对应 `.md` 比抓渲染页便宜一个数量级。
2. 需要真机确认时，用下面这段脚本（只读；会打印状态码，不打印凭据）：

```python
import os, sys
sys.path.insert(0, "src")
from docarchive import weknora
from docarchive.env import merged

env = merged()
c = weknora.WeKnoraClient(env["WEKNORA_BASE_URL"], email=env["WEKNORA_EMAIL"],
                          password=env["WEKNORA_PASSWORD"])
c.login()
models = weknora._items(c._request("GET", "/api/v1/models?page=1&page_size=100"))
print([(m["id"], m["name"], m["type"]) for m in models])
```

3. 临时知识库用完就删（`delete_kb`），别把验证库留在用户空间里。
