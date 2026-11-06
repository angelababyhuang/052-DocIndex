# WeKnora API 契约与实测坑（052 用到的部分）

改 `src/docarchive/weknora.py` / `index.py` 之前先读这份，别重新去翻网页文档。
内容来自 2026-11-06 的真机实测（WeKnora v0.9.0，部署在腾讯云 Lighthouse）。

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
| 推 markdown | `POST /api/v1/knowledge-bases/:id/knowledge/manual` | body `{title, content, status: "publish", tag_ids: []}`；返回 `data.id` |
| 查解析状态 | `GET /api/v1/knowledge/:id` | `data.parse_status` ∈ `pending/processing/completed/failed`；**不要**用 `/knowledge-bases/:kb/knowledge/:kid`（404） |
| 检索 | `POST /api/v1/knowledge-search` | body `{query, knowledge_base_ids, tag_ids, match_count}`；返回项含 `content`、`score`、`knowledge_id`、`knowledge_title` |

## 需要知道的限制

- **手动 markdown 是异步解析的**：实测 2–3 秒从 `pending` 到 `completed`。`index --wait` 就是轮询这个状态；不要假装同步成功。
- **没有「描述这一张图」的接口**：视觉模型只在 WeKnora 自己的索引管线里跑。所以真相源的 caption 由独立的 OpenAI 兼容 VLM 生成（ADR-0002），OCR 由本机 RapidOCR（ADR-0001）。
- **不要上传原文件让它重新解析**：真相源里已经有转好的 md，用 `knowledge/manual` 推文本；上传 docx/pdf 会让 WeKnora 再解析一遍，口径还可能与我们的转换不一致。
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
