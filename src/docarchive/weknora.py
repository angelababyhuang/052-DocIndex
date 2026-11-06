"""WeKnora HTTP 客户端（只包 #15 用得到的几个动作）。

认证用账号密码换 JWT（`.env` 里的 WEKNORA_EMAIL / WEKNORA_PASSWORD），
不引第三方 HTTP 库，和 caption.py 一样走 stdlib。

所有写操作都在派生索引上：删库重建是正常操作，不碰真相源。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, Mapping


class WeKnoraError(RuntimeError):
    """WeKnora 接口失败（网络、鉴权、业务错误）。"""


@dataclass
class WeKnoraClient:
    base_url: str
    email: str | None = None
    password: str | None = field(default=None, repr=False)  # 别在 repr/日志里漏凭据
    token: str | None = field(default=None, repr=False)
    timeout: int = 60

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    # ---------------- 底层 ----------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "ignore")
            raise WeKnoraError(f"{method} {path} → {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise WeKnoraError(f"{method} {path} 请求失败: {e}") from e
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise WeKnoraError(
                f"{method} {path} 返回的不是 JSON: {raw[:200]!r}"
            ) from e

    def login(self) -> None:
        """账号密码换 token。"""
        if not (self.email and self.password):
            raise WeKnoraError("缺少 WEKNORA_EMAIL / WEKNORA_PASSWORD")
        data = self._request("POST", "/api/v1/auth/login",
                             {"email": self.email, "password": self.password})
        token = _dig(data, "data", "token") or _dig(data, "token")
        if not token:
            raise WeKnoraError(f"登录没拿到 token: {str(data)[:200]}")
        self.token = token

    # ---------------- 知识库 ----------------

    def find_kb(self, name: str) -> str | None:
        data = self._request("GET", "/api/v1/knowledge-bases?page=1&page_size=100")
        for kb in _items(data):
            if kb.get("name") == name:
                return kb.get("id")
        return None

    def create_kb(self, name: str, embedding_model_id: str | None = None) -> str:
        payload: dict = {"name": name, "type": "document"}
        if embedding_model_id:
            payload["embedding_model_id"] = embedding_model_id
        data = self._request("POST", "/api/v1/knowledge-bases", payload)
        kb_id = _dig(data, "data", "id")
        if not kb_id:
            raise WeKnoraError(f"建知识库没拿到 id: {str(data)[:200]}")
        return kb_id

    def delete_kb(self, kb_id: str) -> None:
        self._request("DELETE", f"/api/v1/knowledge-bases/{kb_id}")

    def embedding_model_id(self, preferred: str | None = None) -> str | None:
        """挑 embedding 模型：给了 preferred 就只认名字匹配的（找不到返回 None）。

        spec 说 embedding 模型「从第一天定死」，所以不静默换成别的模型。
        不给 preferred 时退回空间里第一个 embedding 模型。
        """
        data = self._request("GET", "/api/v1/models?page=1&page_size=100")
        embedding = [m for m in _items(data) if _is_embedding(m)]
        if preferred:
            for m in embedding:
                if preferred in (m.get("name") or ""):
                    return m.get("id")
            return None
        return embedding[0].get("id") if embedding else None

    # ---------------- 标签 ----------------

    def ensure_tag(self, kb_id: str, name: str) -> str:
        """按名取标签，没有就建。返回标签 id。"""
        tag_id = self._find_tag(kb_id, name)
        if tag_id:
            return tag_id
        data = self._request("POST", f"/api/v1/knowledge-bases/{kb_id}/tags",
                             {"name": name})
        tag_id = _dig(data, "data", "id") or _dig(data, "data", "tag_id")
        if not tag_id:
            raise WeKnoraError(f"建标签没拿到 id: {str(data)[:200]}")
        return tag_id

    def tag_ids_for(self, kb_id: str, names: Iterable[str]) -> list[str]:
        """按名取标签 id（只查不建）；缺哪个就报错，免得搜索条件被悄悄忽略。"""
        out: list[str] = []
        for name in names:
            tag_id = self._find_tag(kb_id, name)
            if not tag_id:
                raise WeKnoraError(f"知识库里没有标签「{name}」")
            out.append(tag_id)
        return out

    def _find_tag(self, kb_id: str, name: str) -> str | None:
        data = self._request("GET", f"/api/v1/knowledge-bases/{kb_id}/tags")
        for tag in _items(data):  # 实测是分页信封 data.data
            if tag.get("name") == name:
                return tag.get("id")
        return None

    # ---------------- 知识 ----------------

    def create_knowledge(self, kb_id: str, title: str, content: str,
                         tag_ids: Iterable[str] = ()) -> str:
        payload = {"title": title, "content": content, "status": "publish",
                   "tag_ids": list(tag_ids)}
        data = self._request("POST", f"/api/v1/knowledge-bases/{kb_id}/knowledge/manual",
                             payload)
        kid = _dig(data, "data", "id")
        if not kid:
            raise WeKnoraError(f"建知识没拿到 id: {str(data)[:200]}")
        return kid

    def set_knowledge_metadata(self, knowledge_id: str,
                               metadata: Mapping[str, object]) -> None:
        """写 knowledge 的 custom_metadata（整体覆盖）。

        manual 接口没有 metadata 字段，只能建完再 PUT（#21）。WeKnora 校验：
        最多 20 个字段、键 1–64 字符、值 ≤1000 字符，超了整条写入失败。
        检索结果里这份元数据以 `knowledge_custom_metadata` 文本返回
        （`键: 值`，多字段换行；见 docs/weknora-api.md）。
        """
        self._request("PUT", f"/api/v1/knowledge/{knowledge_id}",
                      {"custom_metadata": dict(metadata)})

    def knowledge_status(self, kid: str) -> str:
        """解析状态：pending / processing / completed / failed。"""
        data = self._request("GET", f"/api/v1/knowledge/{kid}")
        return (_dig(data, "data", "parse_status")
                or _dig(data, "data", "status") or "unknown")

    def wait_knowledge(self, kids: Iterable[str], *, timeout: float = 300,
                       interval: float = 2.0) -> dict[str, str]:
        """等一批知识解析完，返回 {kid: 最终状态}（超时的按当时状态记）。"""
        pending = list(kids)
        deadline = time.monotonic() + timeout
        final: dict[str, str] = {}
        while pending and time.monotonic() < deadline:
            for kid in list(pending):
                status = self.knowledge_status(kid)
                if status in ("completed", "failed"):
                    final[kid] = status
                    pending.remove(kid)
            if pending:
                time.sleep(interval)
        for kid in pending:  # 超时的也报出来，别假装成功
            final[kid] = self.knowledge_status(kid)
        return final

    # ---------------- 检索 ----------------

    def search(self, query: str, kb_ids: Iterable[str],
               tag_ids: Iterable[str] = (), match_count: int = 5) -> list[dict]:
        payload = {"query": query, "knowledge_base_ids": list(kb_ids),
                   "match_count": match_count}
        if tag_ids:
            payload["tag_ids"] = list(tag_ids)
        data = self._request("POST", "/api/v1/knowledge-search", payload)
        return _items(data)


def _items(data: Mapping) -> list:
    """取列表：兼容 `data:[…]` 和分页信封 `data:{data:[…]}`（实测 tags 是后者）。"""
    value = _dig(data, "data")
    if isinstance(value, Mapping):
        value = value.get("data")
    return value if isinstance(value, list) else []


def _dig(obj: Mapping, *path: str):
    cur = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _is_embedding(model: Mapping) -> bool:
    kind = (model.get("type") or model.get("model_type") or "").lower()
    return "embed" in kind
