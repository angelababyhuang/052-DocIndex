"""摄入与去重：扫描目录登记出现，三层指纹去重给建议。

票 #20：媒体（图片/视频）不再登记成出现，scan 直接把它们送进媒体库；
出现只留给需要人工 triage 的文档。见 docs/adr/0004。
"""
from __future__ import annotations

import re
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docarchive import fingerprint, media
from docarchive.caption import Captioner

SKIP_NAMES = {".DS_Store"}

# 日期目录：YYYY-MM-DD（补零）。字典序即时间序，所以比较直接用字符串。
_DATE_DIR_RE = re.compile(r"[0-9]{4}-([0-9]{2})-([0-9]{2})")


@dataclass(frozen=True)
class ScanStats:
    """一次扫描的结果。

    出现（occurrences）进 triage 队列；媒体（images/videos）直接进媒体库，
    带 * _new 的字段表示这次真正新入库的数量（重复登记只计数、不加新实体）。
    """

    occurrences: int = 0
    images: int = 0
    images_new: int = 0
    videos: int = 0
    videos_new: int = 0


def is_date_dir(name: str) -> bool:
    """第一级子目录名是不是标准日期目录（如 2026-11-05）。

    只认补零的 YYYY-MM-DD，并且月份/日期在合理范围内；「备份」这种非日期目录
    返回 False，「2026-1-5」「2026-13-45」也返回 False。
    """
    m = _DATE_DIR_RE.fullmatch(name)
    if not m:
        return False
    month, day = int(m.group(1)), int(m.group(2))
    return 1 <= month <= 12 and 1 <= day <= 31


def _scan_date_dir(src: Path, rel: tuple[str, ...]) -> str | None:
    """这份文件归属哪个日期目录：扫描根本身是日期目录时用它，否则看第一级子目录。"""
    if is_date_dir(src.name):
        return src.name
    if len(rel) > 1 and is_date_dir(rel[0]):
        return rel[0]
    return None

# 去重建议里只参考「会进库」的出现：pending/archived 是有效去重基准，
# merged/ignored 已被人工判过（分别是重复、忽略），不该再作为新文件的重复依据。
_DEDUP_STATUSES = ("pending", "archived")


def register(conn: sqlite3.Connection, source_type: str, source_path: str,
             source_entry: str | None, file_name: str, size: int,
             byte_hash: str | None, text_fp: str | None) -> bool:
    """登记一条出现。同源（source_type+source_path+source_entry）已存在则不重复登记。"""
    if conn.execute(
        "SELECT 1 FROM occurrences WHERE source_type=? AND source_path=? "
        "AND IFNULL(source_entry,'')=IFNULL(?,'')",
        (source_type, source_path, source_entry),
    ).fetchone():
        return False
    conn.execute(
        "INSERT INTO occurrences(source_type, source_path, source_entry, file_name, "
        "ext, size, byte_hash, text_fp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source_type, source_path, source_entry, file_name,
         Path(file_name).suffix.lower(), size, byte_hash, text_fp),
    )
    return True


def _exists(conn: sqlite3.Connection, column: str, value: str,
            exclude_id: int | None) -> bool:
    """去重基准里是否有其他出现（排除 exclude_id）在该列命中 value。"""
    q = f"SELECT 1 FROM occurrences WHERE {column}=? AND status IN ('pending','archived') "
    params = [value]
    if exclude_id is not None:
        q += "AND id != ?"
        params.append(exclude_id)
    q += " LIMIT 1"
    return conn.execute(q, params).fetchone() is not None


def suggestion(conn: sqlite3.Connection, byte_hash: str | None,
               text_fp: str | None, file_name: str,
               exclude_id: int | None = None) -> str:
    """三层指纹去重建议。

    返回字符串之一：
      - 'duplicate_byte'：字节相同（byte_hash 命中其他出现）
      - 'duplicate_content'：内容相同字节不同（text_fp 命中其他出现）
      - 'new_version'：名称归一化相似但指纹不同
      - 'new'：疑似新文档

    exclude_id：排除当前这条出现自身，避免自匹配误报。
    只参考 pending/archived 状态的出现（merged/ignored 已被人工判过，不算基准）。
    """
    if byte_hash and _exists(conn, "byte_hash", byte_hash, exclude_id):
        return "duplicate_byte"
    if text_fp and _exists(conn, "text_fp", text_fp, exclude_id):
        return "duplicate_content"
    key = fingerprint.name_key(file_name)
    if key:
        q = "SELECT id, file_name FROM occurrences WHERE status IN ('pending','archived') "
        params = []
        if exclude_id is not None:
            q += "AND id != ?"
            params.append(exclude_id)
        for row in conn.execute(q, params):
            if fingerprint.name_key(row["file_name"]) == key:
                return "new_version"
    return "new"


def scan_dir(conn: sqlite3.Connection, src: Path, source_type: str = "wechat",
             since: str | None = None, all_files: bool = False, *,
             archive_dir: str | Path | None = None, project: str | None = None,
             tags: Iterable[str] = (), captioner: Captioner | None = None,
             route_media: bool = True) -> ScanStats:
    """扫描目录登记（含 zip 展开）。返回 ScanStats（出现 / 媒体分开计数）。

    媒体分流（票 #20）：扩展名命中 media.IMAGE_EXTS / VIDEO_EXTS 的文件直接
    `media.register_standalone_image` / `media.register_video`，不登记出现。
    sha256 去重，所以重复扫、同一张图在多个日期目录里都只占一个 IMG/VID 编号。
    route_media=False（CLI 的 --no-media）退回旧行为：媒体也当普通出现登记。
    project / tags 只作用于本次登记的媒体（出现没有项目字段，triage 时才定）。
    captioner 只作用于图片；没有 VLM 密钥时传 None，扫描不会联网。
    zip 包内的媒体仍走出现登记（已知限制，见 ADR-0004）。

    日期范围（对应 014 的 --since）：
      - all_files=True：全量扫，不看目录结构（= 014 的 --since all）
      - since="YYYY-MM-DD"：只扫第一级子目录是**标准日期目录**且 >= since 的文件；
        非日期目录（如「备份」）和直接放在根下的文件一律跳过——这是 #19 修的坑，
        原来「备份」字典序大于日期，会被整个放行。
      - 两者都不给：扫所有文件。
    日期筛选在分流之前生效：被筛掉的目录里的媒体同样不入库。
    """
    if not src.is_dir():
        raise FileNotFoundError(f"目录不存在: {src}")
    if since is not None and not is_date_dir(since):
        raise ValueError(f"since 要标准日期（YYYY-MM-DD），收到: {since}")
    if since is not None and all_files:
        raise ValueError("all_files 与 since 不能同时给")
    occurrences = images = images_new = videos = videos_new = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.name in SKIP_NAMES or p.name.startswith((".", "~$")):
            continue
        rel = p.relative_to(src).parts
        if not rel:
            continue
        if since is not None and not all_files:
            # 只认日期目录下的文件；根下散文件和「备份」这类目录跳过
            date = _scan_date_dir(src, rel)
            if date is None or date < since:
                continue
        ext = p.suffix.lower()
        if route_media and ext in media.MEDIA_EXTS:
            if archive_dir is None:
                raise ValueError(
                    f"登记媒体 {p.name} 需要受管目录（archive_dir）；"
                    f"或者用 route_media=False 退回普通出现登记"
                )
            if ext in media.IMAGE_EXTS:
                r = media.register_standalone_image(
                    conn, archive_dir, p, project=project, tags=tags,
                    captioner=captioner,
                )
                images += 1
                images_new += 1 if r.created else 0
            else:
                v = media.register_video(
                    conn, archive_dir, p, project=project, tags=tags,
                )
                videos += 1
                videos_new += 1 if v.created else 0
            continue
        if ext == ".zip":
            occurrences += _register_zip_entries(conn, p)
        else:
            with p.open("rb") as f:
                bh = fingerprint.sha256_stream(f)
            fp = fingerprint.compute_text_fp(p, ext)
            if register(conn, source_type, str(p), None, p.name, p.stat().st_size, bh, fp):
                occurrences += 1
    conn.commit()
    return ScanStats(occurrences=occurrences, images=images,
                     images_new=images_new, videos=videos, videos_new=videos_new)


def _register_zip_entries(conn: sqlite3.Connection, zp: Path) -> int:
    """展开一个 zip，把内部文件各登记为一条出现。"""
    n = 0
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(zip_display_name(info)).name
            if name in SKIP_NAMES or name.startswith("."):
                continue
            # 读一次，同时算 byte_hash 和 text_fp
            raw = zf.read(info)
            bh = fingerprint.sha256_bytes(raw)
            ext = Path(name).suffix.lower()
            fp = fingerprint.compute_text_fp_from_bytes(raw, ext)
            if register(conn, "zip", str(zp), info.filename, name, info.file_size, bh, fp):
                n += 1
    return n


def zip_display_name(info) -> str:
    """zip 中文文件名兼容：无 UTF-8 标志时按 cp437→utf-8/gbk 修正乱码。"""
    n = info.filename
    if info.flag_bits & 0x800:
        return n
    raw = n.encode("cp437", "ignore")
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return n
