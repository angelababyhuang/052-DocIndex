"""内容指纹与文本归一化原语。

与 014 tools/track.py 保持一致的指纹算法。这些是「内容指纹」「文本归一化」
「文件名归一化」的共享领域概念，被 ingest / migrate 等多处引用。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

TEXT_EXTS = {".docx", ".doc"}
ANYDOC_EXTS = {".pdf", ".xlsx", ".xls", ".pptx", ".ppt"}
PLAIN_EXTS = {".txt", ".md"}


def sha256_stream(f) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def name_key(name: str) -> str:
    """文件名归一化：去扩展名/版本尾巴/空白，用于相似簇和同名匹配。"""
    stem = Path(name).stem.lower()
    stem = re.sub(r"[\s\-_()（）\[\]【】]+", "", stem)
    stem = re.sub(r"(副本|最终版|终稿|定稿|v\d+|rev\d*)$", "", stem)
    return stem or Path(name).stem.lower()


def text_fp_of_plain(data: bytes) -> str:
    """纯文本（txt/md）的正文指纹。"""
    return hashlib.sha256(norm_text(data.decode("utf-8", "ignore")).encode()).hexdigest()


def _markdown_fp(path: Path) -> str | None:
    """跨平台兜底：先用 anydoc（与 014 降级路径一致），装不上再用 markitdown。

    anydoc 的 PyPI 版只支持到 Python 3.10，3.11+ 的环境（含 CI 的 3.12）
    没有它，此时用 markitdown 顶上，指纹同样与 014 不同（只有 textutil 那条
    主路径与 014 一致）。
    """
    try:
        import anydoc
        md = anydoc.to_markdown(str(path))
    except Exception:
        try:
            from markitdown import MarkItDown
            md = MarkItDown().convert(str(path)).text_content
        except Exception:
            return None
    if md and md.strip():
        return hashlib.sha256(norm_text(md).encode()).hexdigest()
    return None


def compute_text_fp(path: Path, ext: str) -> str | None:
    """从文件复算正文指纹（与 014 text_fingerprint 一致的逻辑，只读文件）。

    docx/doc 优先用 macOS 的 textutil（与 014 指纹一致）；textutil 不可用时
    降级 anydoc / markitdown（跨平台，但指纹会与 014 不同）。都失败才返回 None。
    """
    try:
        if ext in PLAIN_EXTS:
            return text_fp_of_plain(path.read_bytes())
        if ext in TEXT_EXTS:
            import subprocess
            try:
                r = subprocess.run(
                    ["textutil", "-convert", "txt", "-stdout", str(path)],
                    capture_output=True, timeout=60,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return text_fp_of_plain(r.stdout)
            except (FileNotFoundError, OSError):
                pass  # 非 macOS / textutil 不可用，降级
            # textutil 失败（返回非 0 或不可用）→ 降级跨平台转换器
            return _markdown_fp(path)
        if ext in ANYDOC_EXTS:
            return _markdown_fp(path)
    except Exception:
        pass
    return None


def compute_text_fp_from_bytes(data: bytes, ext: str) -> str | None:
    """从 bytes 复算正文指纹（zip 内文件用，避免落临时文件）。"""
    try:
        if ext in PLAIN_EXTS:
            return text_fp_of_plain(data)
        # 文本型（docx/pdf 等）需要落盘给 textutil/anydoc，走临时文件
        if ext in TEXT_EXTS or ext in ANYDOC_EXTS:
            import os
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
                tf.write(data)
                tmp = tf.name
            try:
                return compute_text_fp(Path(tmp), ext)
            finally:
                os.unlink(tmp)
    except Exception:
        pass
    return None
