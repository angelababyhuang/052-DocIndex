"""图片库：内容 sha256 去重 → 分配 IMG-#### → 单一母本落盘。

纪律继承 031 的 ADR-0001/0002：编号与内容绑定，同一张图（字节 sha256 相同）
永远只有一个 IMG-ID，永不改号、永不复用；元数据存真相源，二进制存文件系统，
记录里只认 `{{IMG-0001}}` 这种引用。

内嵌图（convert.py）与独立图（media.py）共用这里的登记、落盘和 caption 补写。
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docarchive import db, fingerprint
from docarchive.caption import Captioner

# 图片母本放在受管目录下的 images/（与 doc-<ID>/、videos/ 平级）
IMAGES_SUBDIR = "images"

# 编号：四位起（超过 9999 张自然变宽），与 db.next_code 的 width=4 对齐
_CODE_PATTERN = r"IMG-\d{4,}"
_CODE_RE = re.compile(_CODE_PATTERN)
# 正文里唯一合法的引用写法：{{IMG-0001}}（imagelib.image_ref 的正向定义）
_REF_RE = re.compile(r"\{\{(" + _CODE_PATTERN + r")\}\}")


@dataclass(frozen=True)
class RegisteredImage:
    """登记结果：id 是 images.id，code 是 IMG-####，created 表示这次是否新建实体。"""

    id: int
    code: str
    created: bool


@dataclass(frozen=True)
class CaptionResult:
    """caption 补写结果：text 是最终描述（可能来自旧记录），generated 表示这次真的调了模型。"""

    text: str | None
    error: str | None = None
    generated: bool = False


def image_ref(code: str) -> str:
    """图片在文本里的引用写法（md、简报、索引都用这一种）：`{{IMG-0001}}`。"""
    return "{{" + code + "}}"


def ref_codes(text: str) -> tuple[str, ...]:
    """正文里 `{{IMG-####}}` 标记引用的编号（去重、保序）。

    只认带花括号的引用：正文里裸写的编号是「提到」不是「引用这张图」，
    拿来当 chunk↔图片映射会把无关的图挂上去（#21）。
    """
    return unique_codes(_REF_RE.findall(text or ""))


def all_codes(text: str) -> tuple[str, ...]:
    """文本里任意位置的 IMG 编号（去重、保序）。

    给「图片清单」这种只有编号、没有花括号的地方用；正文回指请用 ref_codes。
    """
    return unique_codes(_CODE_RE.findall(text or ""))


def unique_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """编号去重、保序（图片清单与正文回指共用）。"""
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return tuple(seen)


def normalize_ext(ext: str | None) -> str:
    if not ext:
        return ".bin"
    ext = ext.lower()
    return ext if ext.startswith(".") else f".{ext}"


def _master_path(archive_dir: str | Path, subdir: str, code: str,
                 ext: str | None) -> tuple[str, Path]:
    rel = f"{subdir}/{code}{normalize_ext(ext)}"
    dest = Path(archive_dir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    return rel, dest


def store_master(archive_dir: str | Path, subdir: str, code: str,
                 ext: str | None, data: bytes) -> str:
    """把媒体母本写到 `<archive>/<subdir>/<code><ext>`，返回相对受管目录的路径。

    编号与文件名一旦定下就不改（031 的「单一母本」纪律）；图片和视频共用这段。
    """
    rel, dest = _master_path(archive_dir, subdir, code, ext)
    dest.write_bytes(data)
    return rel


def store_master_file(archive_dir: str | Path, subdir: str, code: str,
                      ext: str | None, src: str | Path) -> str:
    """同 store_master，但源是文件——按流复制，避免把大视频整个读进内存。"""
    rel, dest = _master_path(archive_dir, subdir, code, ext)
    shutil.copyfile(src, dest)
    return rel


def _image_size(data: bytes) -> tuple[int | None, int | None]:
    """读图片宽高；Pillow 不可用或解不开就留空（不影响入库）。"""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.width, im.height
    except Exception:
        return None, None


def register_image(conn: sqlite3.Connection, archive_dir: str | Path, data: bytes,
                   *, ext: str | None = None, source: str | None = None,
                   original_name: str | None = None,
                   name: str | None = None) -> RegisteredImage:
    """登记一张图片（去重 + 落盘母本）。

    同样的字节第二次登记直接复用原 IMG-ID，不新建行、不改来源（来源是首次
    抽出的位置）。不 commit：由调用方在自己的事务边界内统一提交。
    """
    archive_dir = Path(archive_dir)
    sha = fingerprint.sha256_bytes(data)
    row = conn.execute("SELECT id, code FROM images WHERE sha256=?", (sha,)).fetchone()
    if row:
        return RegisteredImage(row["id"], row["code"], False)

    ext = normalize_ext(ext or (Path(original_name).suffix if original_name else None))
    code = db.next_code(conn, "IMG")
    rel = store_master(archive_dir, IMAGES_SUBDIR, code, ext, data)
    width, height = _image_size(data)
    cur = conn.execute(
        "INSERT INTO images(code, name, sha256, byte_size, ext, original_name, source, "
        "path, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, name or (Path(original_name).stem if original_name else None), sha,
         len(data), ext, original_name, source, rel, width, height),
    )
    return RegisteredImage(cur.lastrowid, code, True)


def ensure_caption(conn: sqlite3.Connection, image_id: int, data: bytes,
                   ext: str | None = None, captioner: Captioner | None = None,
                   force: bool = False) -> CaptionResult:
    """给图片补一条 caption：已有就复用，没有（或 force）才调模型。

    模型/网络失败只回执错误，不抛——描述是增强字段，不能挡住图片入库。
    不 commit：事务边界归调用方（和其他写函数一致）。批量补描述时记得每张提交，
    否则下一张的 HTTP 会落在写事务里、一直占着 SQLite 写锁。
    """
    row = conn.execute("SELECT caption FROM images WHERE id=?", (image_id,)).fetchone()
    existing = (row["caption"] or "").strip() if row else ""
    if existing and not force:
        return CaptionResult(existing)
    if captioner is None:
        return CaptionResult(existing or None)
    try:
        text = (captioner.describe(data, ext=ext) or "").strip()
    except Exception as e:  # 模型/网络问题不该挡住图片入库
        return CaptionResult(existing or None, f"{type(e).__name__}: {e}")
    if not text:
        return CaptionResult(existing or None, "模型返回了空描述")
    conn.execute("UPDATE images SET caption=? WHERE id=?", (text, image_id))
    return CaptionResult(text, generated=True)
