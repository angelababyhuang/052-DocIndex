"""摄入与去重：扫描目录登记出现，三层指纹去重给建议。"""
from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path

from docarchive import migrate

SKIP_NAMES = {".DS_Store"}


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


def suggestion(conn: sqlite3.Connection, byte_hash: str | None,
               text_fp: str | None, file_name: str,
               exclude_id: int | None = None) -> str:
    """三层指纹去重建议。

    返回字符串之一：
      - 'duplicate_byte'：字节相同（byte_hash 命中其他出现）
      - 'duplicate_content'：内容相同字节不同（text_fp 命中其他出现）
      - 'new_version'：名称归一化相似但指纹不同
      - 'new'：疑似新文档

    exclude_id：排除当前这条出现自身，避免自匹配误报（默认 None 不过滤，
    但扫描时传自身 id）。
    """
    if byte_hash:
        q = "SELECT id FROM occurrences WHERE byte_hash=? "
        params = [byte_hash]
        if exclude_id is not None:
            q += "AND id != ?"
            params.append(exclude_id)
        q += " LIMIT 1"
        if conn.execute(q, params).fetchone():
            return "duplicate_byte"
    if text_fp:
        q = "SELECT id FROM occurrences WHERE text_fp=? "
        params = [text_fp]
        if exclude_id is not None:
            q += "AND id != ?"
            params.append(exclude_id)
        q += " LIMIT 1"
        if conn.execute(q, params).fetchone():
            return "duplicate_content"
    key = migrate.name_key(file_name)
    if key:
        for row in conn.execute(
            "SELECT id, file_name FROM occurrences WHERE id != ?" if exclude_id is not None
            else "SELECT id, file_name FROM occurrences",
            [exclude_id] if exclude_id is not None else [],
        ):
            if migrate.name_key(row["file_name"]) == key:
                return "new_version"
    return "new"


def scan_dir(conn: sqlite3.Connection, src: Path, source_type: str = "wechat",
             since: str | None = None) -> int:
    """扫描目录登记出现（不含 zip 展开）。返回新登记数。"""
    if not src.is_dir():
        raise FileNotFoundError(f"目录不存在: {src}")
    n = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.name in SKIP_NAMES or p.name.startswith((".", "~$")):
            continue
        rel = p.relative_to(src).parts
        if not rel:
            continue
        if since is not None:
            try:
                from datetime import datetime
                if datetime.strptime(rel[0], "%Y-%m-%d").strftime("%Y-%m-%d") < since:
                    continue
            except ValueError:
                continue
        if p.suffix.lower() == ".zip":
            n += _register_zip_entries(conn, p)
        else:
            with p.open("rb") as f:
                bh = migrate.sha256_stream(f)
            fp = migrate.compute_text_fp(p, p.suffix.lower())
            if register(conn, source_type, str(p), None, p.name, p.stat().st_size, bh, fp):
                n += 1
    conn.commit()
    return n


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
            with zf.open(info) as f:
                bh = migrate.sha256_stream(f)
            raw = zf.read(info)
            ext = Path(name).suffix.lower()
            fp = migrate.compute_text_fp_from_bytes(raw, ext)
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
