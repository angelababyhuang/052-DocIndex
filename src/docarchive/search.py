"""票 #21：检索命中 → 内嵌图回指。

命中结果回到图片有两条路，按精度从高到低：

1. **分块正文里的标记**：md 里图片就写成 `{{IMG-####}}`，WeKnora 分块时原样保留
   （实测），所以命中片段里出现的标记 = 这一段正文真正引用的图。
2. **知识条目图片清单**：索引时把文档里全部 IMG 编号写进 knowledge 的
   `custom_metadata.图片`，检索结果以 `knowledge_custom_metadata` 文本带回。
   它只到「文档级」——分块边界刚好把标记切走时，靠它兜底（#21 的降级路径）。

编号 → 母本的解析在 `media.resolve_image`（走真相源 `images.path`），不依赖
WeKnora 返回图片 URL：图片二进制只在真相源一份（ADR-0005）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from docarchive import imagelib, index


@dataclass(frozen=True)
class HitImages:
    """一条检索命中涉及的内嵌图。

    chunk：命中正文里 `{{IMG-####}}` 引用的图（分块级，最准）。
    knowledge：knowledge 的图片清单（文档级，可能包含本段之外的图）。
    """

    chunk: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()

    def extra(self) -> tuple[str, ...]:
        """清单里有、本段正文却没引用的图（文档级补充，打印时单独标）。"""
        return tuple(code for code in self.knowledge if code not in self.chunk)


def hit_image_codes(hit: Mapping) -> HitImages:
    """从一条 WeKnora 检索结果里取图片编号；字段缺失/形状不对都退化成空。"""
    content = hit.get("content")
    return HitImages(
        chunk=imagelib.ref_codes(content if isinstance(content, str) else ""),
        knowledge=index.manifest_codes(hit.get("knowledge_custom_metadata")),
    )
