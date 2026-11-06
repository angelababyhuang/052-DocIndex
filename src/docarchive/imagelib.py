"""图片库：内容 sha256 去重 → 分配 IMG-#### → 单一母本落盘。

纪律继承 031 的 ADR-0001/0002：编号与内容绑定，同一张图（字节 sha256 相同）
永远只有一个 IMG-ID，永不改号、永不复用；元数据存真相源，二进制存文件系统，
记录里只认 `{{IMG-0001}}` 这种引用。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from docarchive import db, fingerprint

# 图片母本放在受管目录下的 images/（与 doc-<ID>/ 平级）
IMAGES_SUBDIR = "images"


@dataclass(frozen=True)
class RegisteredImage:
    """登记结果：id 是 images.id，code 是 IMG-####，created 表示这次是否新建实体。"""

    id: int
    code: str
    created: bool


def image_ref(code: str) -> str:
    """图片在文本里的引用写法（md、简报、索引都用这一种）：`{{IMG-0001}}`。"""
    return "{{" + code + "}}"


def _norm_ext(ext: str | None) -> str:
    if not ext:
        return ".bin"
    ext = ext.lower()
    return ext if ext.startswith(".") else f".{ext}"


def register_image(conn: sqlite3.Connection, archive_dir: str | Path, data: bytes,
                   *, ext: str | None = None, source: str | None = None,
                   original_name: str | None = None) -> RegisteredImage:
    """登记一张图片（去重 + 落盘母本）。

    同样的字节第二次登记直接复用原 IMG-ID，不新建行、不改来源（来源是首次
    抽出的位置）。不 commit：由调用方在自己的事务边界内统一提交。
    """
    archive_dir = Path(archive_dir)
    sha = fingerprint.sha256_bytes(data)
    row = conn.execute("SELECT id, code FROM images WHERE sha256=?", (sha,)).fetchone()
    if row:
        return RegisteredImage(row["id"], row["code"], False)

    ext = _norm_ext(ext or (Path(original_name).suffix if original_name else None))
    code = db.next_code(conn, "IMG")
    rel = f"{IMAGES_SUBDIR}/{code}{ext}"
    dest = archive_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    cur = conn.execute(
        "INSERT INTO images(code, sha256, byte_size, ext, original_name, source, path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, sha, len(data), ext, original_name, source, rel),
    )
    return RegisteredImage(cur.lastrowid, code, True)
