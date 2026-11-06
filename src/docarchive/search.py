"""票 #21：检索命中 → 内嵌图回指。

命中结果回到图片有两条路，按精度从高到低：

1. **分块正文里的标记**：md 里图片就写成 `{{IMG-####}}`，WeKnora 分块时原样保留
   （实测），所以命中片段里出现的标记 = 这条结果带回来的图。WeKnora 的上下文补齐
   会把相邻块并进 `content`，其中的标记也一并算上——它们仍是同一知识条目的上下文。
2. **知识条目图片清单**：索引时把文档里全部 IMG 编号写进 knowledge 的
   `custom_metadata.图片`，检索结果以 `knowledge_custom_metadata` 文本带回。
   它只到「文档级」——分块边界刚好把标记切走时，靠它兜底（#21 的降级路径）。

另外，命中「图片条目」（caption 知识，标题 `图片 IMG-0001`）时条目本身就是那张图，
直接给编号走同一条母本解析。

编号 → 母本的解析在 `media.resolve_image`（走真相源 `images.path`），不依赖
WeKnora 返回图片 URL：图片二进制只在真相源一份（ADR-0005）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from docarchive import imagelib, manifest

# 图片条目的标题格式由 index._media_units 生成；这里只认它，避免把文档标题里的
# 编号误当图片。
_IMAGE_UNIT_TITLE_RE = re.compile(r"^图片 (IMG-\d{4,})$")
_VIDEO_UNIT_TITLE_RE = re.compile(r"^视频 (VID-\d{4,})$")


@dataclass(frozen=True)
class HitImages:
    """一条检索命中涉及的内嵌图。

    chunk：命中正文里 `{{IMG-####}}` 引用的图（分块级，最准）。
    knowledge：knowledge 的图片清单（文档级，可能包含本段之外的图）。
    knowledge_total：清单被截断时是原始张数，否则为 None。
    unit_image：命中本身就是图片条目时的编号（标题 `图片 IMG-0001`）。
    """

    chunk: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    knowledge_total: int | None = None
    unit_image: tuple[str, ...] = ()

    def extra(self) -> tuple[str, ...]:
        """清单里有、本段正文却没引用的图（文档级补充，打印时单独标）。"""
        return tuple(code for code in self.knowledge if code not in self.chunk)


def document_ref(hit: Mapping) -> str | None:
    """命中条目对应的真相源文档（`custom_metadata.文档 = doc-<id>`）。

    旧索引没有该字段，返回 None；调用方据此只提示、不报错（#22）。
    """
    return manifest.document_ref(hit.get("knowledge_custom_metadata"))


def hit_image_codes(hit: Mapping) -> HitImages:
    """从一条 WeKnora 检索结果里取图片编号；字段缺失/形状不对都退化成空。"""
    content = hit.get("content")
    decoded = manifest.decode(hit.get("knowledge_custom_metadata"))
    return HitImages(
        chunk=imagelib.ref_codes(content if isinstance(content, str) else ""),
        knowledge=decoded.codes,
        knowledge_total=decoded.total,
        unit_image=_unit_image(hit.get("knowledge_title")),
    )


def is_unit_hit(hit: Mapping) -> bool:
    """命中是不是图片/视频条目本身（标题是 `图片 IMG-…` / `视频 VID-…`）。

    严格匹配 index 生成的标题格式；普通文档（哪怕标题以“图片”开头）不算。
    """
    title = hit.get("knowledge_title")
    if not isinstance(title, str):
        return False
    title = title.strip()
    return bool(_IMAGE_UNIT_TITLE_RE.match(title) or _VIDEO_UNIT_TITLE_RE.match(title))


def _unit_image(title: object) -> tuple[str, ...]:
    if not isinstance(title, str):
        return ()
    match = _IMAGE_UNIT_TITLE_RE.match(title.strip())
    return (match.group(1),) if match else ()
