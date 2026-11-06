"""014 → 052 全量只读迁移。

把 014 的 track.db 元数据导入 052 新库。不动 014 原库、不复制/移动文件。
指纹复算校验：重算 byte_hash/text_fp，对不上的列差异清单。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

# 与 014 tools/track.py 保持一致的指纹算法
TEXT_EXTS = {".docx", ".doc"}
ANYDOC_EXTS = {".pdf", ".xlsx", ".xls", ".pptx", ".ppt"}
PLAIN_EXTS = {".txt", ".md"}


def sha256_stream(f) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def name_key(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[\s\-_()（）\[\]【】]+", "", stem)
    stem = re.sub(r"(副本|最终版|终稿|定稿|v\d+|rev\d*)$", "", stem)
    return stem or Path(name).stem.lower()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def migrate(src_db: Path, dst_conn: sqlite3.Connection) -> dict:
    """把 014 的 track.db 只读导入目标连接。

    返回统计 dict：{projects, documents, versions, occurrences}。
    只读打开源库（file:...?mode=ro），不动任何文件字节。
    """
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    stats = {}

    # 幂等：重跑前清空目标库实体表（可回退 = 作废重来）
    dst_conn.executescript("""
    DELETE FROM document_project_tags;
    DELETE FROM document_images;
    DELETE FROM occurrences;
    DELETE FROM versions;
    DELETE FROM images;
    DELETE FROM videos;
    DELETE FROM documents;
    DELETE FROM projects;
    DELETE FROM code_counters;
    """)

    # projects
    proj_map = {}
    for r in src.execute("SELECT * FROM projects ORDER BY id"):
        cur = dst_conn.execute(
            "INSERT INTO projects(name, created_at) VALUES (?, ?)",
            (r["name"], r["created_at"]),
        )
        proj_map[r["id"]] = cur.lastrowid
    stats["projects"] = len(proj_map)

    # documents
    doc_map = {}
    for r in src.execute("SELECT * FROM documents ORDER BY id"):
        cur = dst_conn.execute(
            "INSERT INTO documents(title, project_id, category, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                r["title"],
                proj_map.get(r["project_id"]),
                r["category"],
                r["created_at"],
            ),
        )
        doc_map[r["id"]] = cur.lastrowid
    stats["documents"] = len(doc_map)

    # versions
    ver_map = {}
    for r in src.execute("SELECT * FROM versions ORDER BY id"):
        cur = dst_conn.execute(
            "INSERT INTO versions(document_id, byte_hash, text_fp, archive_path, "
            "note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                doc_map.get(r["document_id"]),
                r["byte_hash"],
                r["text_fp"],
                r["archive_path"],
                r["note"],
                r["created_at"],
            ),
        )
        ver_map[r["id"]] = cur.lastrowid
    stats["versions"] = len(ver_map)

    # 回填 documents.current_version_id（需要 version id 映射）
    for r in src.execute(
        "SELECT id, current_version_id FROM documents WHERE current_version_id IS NOT NULL"
    ):
        if r["current_version_id"] in ver_map:
            dst_conn.execute(
                "UPDATE documents SET current_version_id=? WHERE id=?",
                (ver_map[r["current_version_id"]], doc_map[r["id"]]),
            )

    # occurrences
    for r in src.execute("SELECT * FROM occurrences ORDER BY id"):
        dst_conn.execute(
            "INSERT INTO occurrences(source_type, source_path, source_entry, "
            "file_name, ext, size, byte_hash, text_fp, status, document_id, "
            "version_id, note, registered_at, triaged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["source_type"],
                r["source_path"],
                r["source_entry"],
                r["file_name"],
                r["ext"],
                r["size"],
                r["byte_hash"],
                r["text_fp"],
                r["status"],
                doc_map.get(r["document_id"]),
                ver_map.get(r["version_id"]),
                r["note"],
                r["registered_at"],
                r["triaged_at"],
            ),
        )
    stats["occurrences"] = src.execute("SELECT count(*) c FROM occurrences").fetchone()["c"]

    dst_conn.commit()
    src.close()
    return stats


def verify_fingerprints(src_db: Path, dst_conn: sqlite3.Connection, archive_root: Path | None = None) -> list[dict]:
    """复算 014 每条出现归档文件的 byte_hash/text_fp，与库中记录对比。

    返回差异清单：每项 {occurrence_id, field, stored, recomputed, path}。
    只对 status='archived'（已归）且 archive_path 存在的出现复算。

    archive_root：archive_path 是相对路径时，用它做根解析到真实文件。
    默认取 src_db 的父目录（014 仓库根）。
    """
    root = archive_root or src_db.parent
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    diffs = []
    for r in src.execute("SELECT * FROM occurrences WHERE status='archived'"):
        # archive_path 在 versions 表里，通过 occurrence -> version 关联
        path = None
        if r["version_id"]:
            v = src.execute(
                "SELECT archive_path FROM versions WHERE id=?", (r["version_id"],)
            ).fetchone()
            if v:
                path = v["archive_path"]
        if not path:
            continue
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        if not p.exists():
            continue
        # byte_hash 复算
        with open(p, "rb") as f:
            bh = sha256_stream(f)
        if r["byte_hash"] and bh != r["byte_hash"]:
            diffs.append({
                "occurrence_id": r["id"], "field": "byte_hash",
                "stored": r["byte_hash"], "recomputed": bh, "path": str(p),
            })
        # text_fp 复算（只对支持格式）
        ext = p.suffix.lower()
        tfp = compute_text_fp(p, ext)
        if r["text_fp"] and tfp is not None and tfp != r["text_fp"]:
            diffs.append({
                "occurrence_id": r["id"], "field": "text_fp",
                "stored": r["text_fp"], "recomputed": tfp, "path": str(p),
            })
    src.close()
    return diffs


def compute_text_fp(path: Path, ext: str) -> str | None:
    """复算正文指纹（与 014 text_fingerprint 一致的逻辑，只读文件）。"""
    try:
        if ext in PLAIN_EXTS:
            raw = path.read_bytes()
            return hashlib.sha256(norm_text(raw.decode("utf-8", "ignore")).encode()).hexdigest()
        if ext in TEXT_EXTS:
            import subprocess
            r = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and r.stdout.strip():
                return hashlib.sha256(
                    norm_text(r.stdout.decode("utf-8", "ignore")).encode()
                ).hexdigest()
        if ext in ANYDOC_EXTS:
            import anydoc
            md = anydoc.to_markdown(str(path))
            if md and md.strip():
                return hashlib.sha256(norm_text(md).encode()).hexdigest()
    except Exception:
        pass
    return None
