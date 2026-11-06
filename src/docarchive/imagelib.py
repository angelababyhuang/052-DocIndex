"""图片库：内容 sha256 去重 → 分配 IMG-#### → 单一母本落盘。

纪律继承 031 的 ADR-0001/0002：编号与内容绑定，同一张图（字节 sha256 相同）
永远只有一个 IMG-ID，永不改号、永不复用；元数据存真相源，二进制存文件系统，
记录里只认 `{{IMG-0001}}` 这种引用。

内嵌图（convert.py）与独立图（media.py）共用这里的登记、落盘和 caption 补写。
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from docarchive import db, fingerprint
from docarchive.caption import Captioner

# 图片母本放在受管目录下的 images/（与 doc-<ID>/、videos/ 平级）
IMAGES_SUBDIR = "images"


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


def _norm_ext(ext: str | None) -> str:
    if not ext:
        return ".bin"
    ext = ext.lower()
    return ext if ext.startswith(".") else f".{ext}"


def _master_path(archive_dir: str | Path, subdir: str, code: str,
                 ext: str | None) -> tuple[str, Path]:
    rel = f"{subdir}/{code}{_norm_ext(ext)}"
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

    ext = _norm_ext(ext or (Path(original_name).suffix if original_name else None))
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
                   force: bool = False, commit: bool = True) -> CaptionResult:
    """给图片补一条 caption：已有就复用，没有（或 force）才调模型。

    模型/网络失败只回执错误，不抛——描述是增强字段，不能挡住图片入库。
    默认生成后立刻 commit：批量补描述时，下一张图的 HTTP 不该落在写事务里
    （否则一次几十秒的请求会一直占着 SQLite 写锁）。
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
    if commit:
        conn.commit()
    return CaptionResult(text, generated=True)
