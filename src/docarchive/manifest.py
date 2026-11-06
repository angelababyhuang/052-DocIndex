"""知识条目图片清单（image manifest）的编码与解析。

索引时把文档正文里的 `{{IMG-####}}` 抽成清单，写进 WeKnora knowledge 的
`custom_metadata.图片`；检索时从 `knowledge_custom_metadata` 读回（ADR-0005）。
写（index）和读（search）共用这个模块，格式只定义一次。

WeKnora 侧的两个约束（实测，见 docs/weknora-api.md）：
- `custom_metadata` 的值 ≤1000 字符，超了整条 PUT 会 500 → 清单要截断并记总数；
- 检索结果里的该字段是拍平的 `键: 值` 文本（多字段换行），不是 JSON 对象。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from docarchive import imagelib

IMAGE_MANIFEST_KEY = "图片"
IMAGE_COUNT_KEY = "图片数"
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


def decode(value: object) -> Manifest:
    """解析 `knowledge_custom_metadata`（拍平文本或 JSON 对象两种形状都兜住）。"""
    if isinstance(value, Mapping):
        raw = value.get(IMAGE_MANIFEST_KEY)
        count = value.get(IMAGE_COUNT_KEY)
        codes = imagelib.all_codes(raw) if isinstance(raw, str) else ()
        total = count if isinstance(count, int) and not isinstance(count, bool) else None
        return Manifest(codes, total)
    if not isinstance(value, str):
        return Manifest()
    codes: tuple[str, ...] = ()
    total: int | None = None
    for line in value.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key == IMAGE_MANIFEST_KEY:
            codes = imagelib.all_codes(rest)
        elif key == IMAGE_COUNT_KEY:
            try:
                total = int(rest.strip())
            except ValueError:
                total = None
    return Manifest(codes, total)
