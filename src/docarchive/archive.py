"""归档落库 + triage 人工确认。

把已登记的 pending 出现，经人工判定后确认入库：复制进受管目录（不动原件）、
建文档/版本、流转 status。词汇见 GLOSSARY.md；参考 014 的 triage 语义。
"""
from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from docarchive import db

STATUS_CN = {"pending": "待处理", "archived": "已归", "merged": "已并", "ignored": "已忽略"}
CATEGORIES = ["研发", "测试", "管理", "商务", "资料"]


def copy_to_archive(occ: sqlite3.Row, archive_dir: Path, doc_id: int,
                    version_id: int) -> str | None:
    """复制出现对应的文件进受管目录（不动原件）。

    返回归档后的相对路径（相对 archive_dir），无法复制返回 None。
    - wechat/manual：直接复制源文件
    - zip：展开包内 entry 写到目标
    - feishu：无本地副本，返回 None
    """
    src_type, src_path, entry = occ["source_type"], occ["source_path"], occ["source_entry"]
    if src_type == "feishu":
        return None
    dest_dir = archive_dir / f"doc-{doc_id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"v{version_id}-{occ['file_name']}"
    if src_type == "zip":
        with zipfile.ZipFile(src_path) as zf, dest.open("wb") as out:
            out.write(zf.read(entry))
    elif src_type in ("wechat", "manual"):
        dest.write_bytes(Path(src_path).read_bytes())
    else:
        return None
    return str(dest.relative_to(archive_dir))


def get_occ(conn: sqlite3.Connection, oid: int) -> sqlite3.Row:
    """取一条 pending 出现；不存在或已处理则报错。"""
    r = conn.execute("SELECT * FROM occurrences WHERE id=?", (oid,)).fetchone()
    if not r:
        raise ValueError(f"出现 #{oid} 不存在")
    if r["status"] != "pending":
        raise ValueError(f"出现 #{oid} 已是 {STATUS_CN.get(r['status'], r['status'])} 状态")
    return r


def _finish(conn: sqlite3.Connection, occ: sqlite3.Row, status: str,
            doc_id: int | None, version_id: int | None, note: str | None = None) -> None:
    conn.execute(
        "UPDATE occurrences SET status=?, document_id=?, version_id=?, note=?, "
        "triaged_at=datetime('now','localtime') WHERE id=?",
        (status, doc_id, version_id, note, occ["id"]),
    )


def new_doc(conn: sqlite3.Connection, oid: int, project: str, archive_dir: Path,
            title: str | None = None, category: str | None = None) -> tuple[int, int]:
    """确认一条出现为新文档：建 document + version + 复制进受管目录。"""
    occ = get_occ(conn, oid)
    pid = db.get_or_create_project(conn, project)
    title = title or Path(occ["file_name"]).stem
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"类别必须是: {'/'.join(CATEGORIES)}")
    cur = conn.execute(
        "INSERT INTO documents(title, project_id, category) VALUES (?, ?, ?)",
        (title, pid, category),
    )
    doc_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO versions(document_id, byte_hash, text_fp) VALUES (?, ?, ?)",
        (doc_id, occ["byte_hash"], occ["text_fp"]),
    )
    vid = cur.lastrowid
    ap = copy_to_archive(occ, archive_dir, doc_id, vid)
    conn.execute("UPDATE versions SET archive_path=? WHERE id=?", (ap, vid))
    conn.execute("UPDATE documents SET current_version_id=? WHERE id=?", (vid, doc_id))
    _finish(conn, occ, "archived", doc_id, vid)
    conn.commit()
    return doc_id, vid


def new_version(conn: sqlite3.Connection, oid: int, doc_id: int,
                archive_dir: Path) -> int:
    """确认一条出现为某文档新版本：建新 version + 复制 + 更新现行版本。"""
    occ = get_occ(conn, oid)
    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise ValueError(f"文档 #{doc_id} 不存在")
    cur = conn.execute(
        "INSERT INTO versions(document_id, byte_hash, text_fp) VALUES (?, ?, ?)",
        (doc_id, occ["byte_hash"], occ["text_fp"]),
    )
    vid = cur.lastrowid
    ap = copy_to_archive(occ, archive_dir, doc_id, vid)
    conn.execute("UPDATE versions SET archive_path=? WHERE id=?", (ap, vid))
    conn.execute("UPDATE documents SET current_version_id=? WHERE id=?", (vid, doc_id))
    _finish(conn, occ, "archived", doc_id, vid)
    conn.commit()
    return vid


def merge(conn: sqlite3.Connection, oid: int, doc_id: int, force: bool = False) -> int | None:
    """确认一条出现为重复：仅记录出现位置，不复制，status=merged。

    返回匹配到的 version id；指纹对不上且非 force 时抛错。
    """
    occ = get_occ(conn, oid)
    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise ValueError(f"文档 #{doc_id} 不存在")
    vid = None
    if occ["text_fp"]:
        v = conn.execute(
            "SELECT id FROM versions WHERE document_id=? AND text_fp=?",
            (doc_id, occ["text_fp"]),
        ).fetchone()
        vid = v["id"] if v else None
    if vid is None and occ["byte_hash"]:
        v = conn.execute(
            "SELECT id FROM versions WHERE document_id=? AND byte_hash=?",
            (doc_id, occ["byte_hash"]),
        ).fetchone()
        vid = v["id"] if v else None
    if vid is None and force:
        vid = d["current_version_id"]
        _finish(conn, occ, "merged", doc_id, vid, note="人工确认并入(跳过指纹校验)")
        conn.commit()
        return vid
    if vid is None:
        raise ValueError(
            f"出现 #{oid} 与文档 #{doc_id} 的版本指纹都对不上，请核对（或 new-version / force）"
        )
    _finish(conn, occ, "merged", doc_id, vid)
    conn.commit()
    return vid


def ignore(conn: sqlite3.Connection, oid: int, note: str | None = None) -> None:
    """确认一条出现为忽略：status=ignored，不复制。"""
    occ = get_occ(conn, oid)
    _finish(conn, occ, "ignored", None, None, note=note or "确认无需追踪")
    conn.commit()
