"""知识条目 custom_metadata 的编码与解析。

两类字段共用一份 WeKnora `custom_metadata`：
- `图片` / `图片数`：文档正文里的内嵌图清单（#21，ADR-0005）；
- `文档`：知识条目对应的真相源文档（`doc-<id>`），让检索结果能回到受管文件（#22）。

写（index）和读（search）共用这个模块，格式只定义一次。WeKnora 的约束：
`custom_metadata` 的值 ≤1000 字符，超了整条 PUT 会 500；检索结果里的字段是
拍平的 `键: 值` 文本（多字段换行），不是 JSON 对象。

图片清单的值超长时截断并记 `图片数`（见 docs/weknora-api.md 的实测约束）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from docarchive import imagelib

IMAGE_MANIFEST_KEY = "图片"
IMAGE_COUNT_KEY = "图片数"
DOCUMENT_KEY = "文档"
MAX_MANIFEST_CHARS = 1000


@dataclass(frozen=True)
class Manifest:
    """解析结果：codes 是清单里的编号，total 是截断前的原始张数（没截断为 None）。"""

    codes: tuple[str, ...] = ()
    total: int | None = None


def encode(codes: Iterable[str]) -> dict[str, object]:
    """把 IMG 编号拼成 custom_metadata 字段（空清单返回 {}，不写）。

    值超长就按顺序截断到装得下为止，并带 `图片数 = 原始总数`；截断只影响
    「文档级清单」，chunk 正文里的标记不受影响。
    """
    codes = imagelib.unique_codes(codes)
    if not codes:
        return {}
    kept: list[str] = []
    length = 0
    for code in codes:
        extra = len(code) + (1 if kept else 0)
        if length + extra > MAX_MANIFEST_CHARS:
            break
        kept.append(code)
        length += extra
    fields: dict[str, object] = {IMAGE_MANIFEST_KEY: ",".join(kept)}
    if len(kept) < len(codes):
        fields[IMAGE_COUNT_KEY] = len(codes)
    return fields


def parse_fields(value: object) -> dict[str, str]:
    """把 `knowledge_custom_metadata` 解析成 `{键: 值}`（拍平文本或 JSON 对象）。"""
    if isinstance(value, Mapping):
        out: dict[str, str] = {}
        for key, val in value.items():
            if not isinstance(key, str) or isinstance(val, bool):
                continue
            if isinstance(val, (str, int, float)):
                out[key.strip()] = str(val).strip()
        return out
    if not isinstance(value, str):
        return {}
    out = {}
    for line in value.splitlines():
        key, sep, rest = line.partition(":")
        if sep:
            out[key.strip()] = rest.strip()
    return out


def document_ref(value: object) -> str | None:
    """取 `文档` 字段（`doc-<id>`）；旧索引/其他元数据返回 None。"""
    ref = parse_fields(value).get(DOCUMENT_KEY, "")
    return ref or None


def decode(value: object) -> Manifest:
    """解析 `knowledge_custom_metadata` 里的图片清单（兼容文本与 JSON 对象）。"""
    fields = parse_fields(value)
    codes = imagelib.all_codes(fields.get(IMAGE_MANIFEST_KEY, ""))
    raw_total = fields.get(IMAGE_COUNT_KEY)
    total: int | None = None
    if raw_total is not None:
        try:
            total = int(raw_total)
        except ValueError:
            total = None
    return Manifest(codes, total)
