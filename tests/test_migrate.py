"""票 #11：014 数据全量只读迁移。"""
import sqlite3

import pytest

from docarchive import db as appdb
from docarchive import migrate


@pytest.fixture
def src_db(tmp_path):
    """构造一个模拟 014 结构的小型源库。"""
    p = tmp_path / "src_track.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT);
    CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT NOT NULL, project_id INTEGER,
      current_version_id INTEGER, category TEXT, created_at TEXT);
    CREATE TABLE versions(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
      byte_hash TEXT, text_fp TEXT, archive_path TEXT, note TEXT, created_at TEXT);
    CREATE TABLE occurrences(id INTEGER PRIMARY KEY, source_type TEXT NOT NULL,
      source_path TEXT NOT NULL, source_entry TEXT, file_name TEXT NOT NULL, ext TEXT,
      size INTEGER, byte_hash TEXT, text_fp TEXT, status TEXT NOT NULL DEFAULT 'pending',
      document_id INTEGER, version_id INTEGER, note TEXT, registered_at TEXT, triaged_at TEXT);
    """)
    conn.execute("INSERT INTO projects(name) VALUES ('浮标项目')")
    conn.execute("INSERT INTO projects(name) VALUES ('RCS项目')")
    conn.execute(
        "INSERT INTO documents(title, project_id, category) VALUES ('连杆方案', 1, '研发')"
    )
    conn.execute(
        "INSERT INTO versions(document_id, byte_hash, text_fp, archive_path) "
        "VALUES (1, 'abc', 'def', 'archive/doc-1/v1-x.docx')"
    )
    conn.execute(
        "UPDATE documents SET current_version_id=1 WHERE id=1"
    )
    conn.execute(
        "INSERT INTO occurrences(source_type, source_path, file_name, ext, size, "
        "byte_hash, text_fp, status, document_id, version_id) "
        "VALUES ('wechat', '001/2026-09/x.docx', 'x.docx', '.docx', 100, 'abc', 'def', "
        "'archived', 1, 1)"
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def dst_conn(tmp_path):
    c = appdb.connect(tmp_path / "dst.db")
    yield c
    c.close()


def test_migrate_counts_and_mapping(src_db, dst_conn):
    stats = migrate.migrate(src_db, dst_conn)
    assert stats == {"projects": 2, "documents": 1, "versions": 1, "occurrences": 1}

    # 数据正确性：文档挂到正确项目、版本挂到正确文档、出现挂到正确文档+版本
    d = dst_conn.execute("SELECT * FROM documents").fetchone()
    assert d["title"] == "连杆方案"
    assert d["category"] == "研发"
    p = dst_conn.execute("SELECT name FROM projects WHERE id=?", (d["project_id"],)).fetchone()
    assert p["name"] == "浮标项目"

    v = dst_conn.execute("SELECT * FROM versions").fetchone()
    assert v["document_id"] == d["id"]
    assert v["archive_path"] == "archive/doc-1/v1-x.docx"

    o = dst_conn.execute("SELECT * FROM occurrences").fetchone()
    assert o["document_id"] == d["id"]
    assert o["version_id"] == v["id"]
    assert o["status"] == "archived"

    # current_version_id 回填正确
    d2 = dst_conn.execute("SELECT current_version_id FROM documents").fetchone()
    assert d2["current_version_id"] == v["id"]


def test_migrate_is_idempotent(src_db, dst_conn):
    """重跑迁移应清空重来（可回退），而非叠加。"""
    migrate.migrate(src_db, dst_conn)
    migrate.migrate(src_db, dst_conn)
    assert dst_conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    assert dst_conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 1


def test_migrate_does_not_modify_source(src_db, dst_conn):
    before = sqlite3.connect(str(src_db))
    before_count = before.execute("SELECT count(*) FROM documents").fetchone()[0]
    before.close()

    migrate.migrate(src_db, dst_conn)

    after = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    after_count = after.execute("SELECT count(*) FROM documents").fetchone()[0]
    # 源库只读打开，未改动
    after.execute("SELECT name FROM projects")
    after.close()
    assert after_count == before_count == 1


def test_sha256_and_name_key_match_014_semantics():
    import hashlib
    from docarchive import fingerprint
    assert fingerprint.sha256_bytes(b"hello") == hashlib.sha256(b"hello").hexdigest()
    assert fingerprint.norm_text("a   b\n c") == "a b c"
    assert fingerprint.name_key("连杆固定方案 (副本).docx") == "连杆固定方案"
    assert fingerprint.name_key("方案V2.docx") == "方案"


def test_verify_fingerprints_resolves_relative_path(tmp_path):
    """复算时把相对 archive_path 解析到 archive_root。"""
    # 构造源库 + 一个真实归档文件
    src = tmp_path / "src_track.db"
    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE versions(id INTEGER PRIMARY KEY, document_id INTEGER,
      byte_hash TEXT, text_fp TEXT, archive_path TEXT, note TEXT, created_at TEXT);
    CREATE TABLE occurrences(id INTEGER PRIMARY KEY, source_type TEXT, source_path TEXT,
      source_entry TEXT, file_name TEXT, ext TEXT, size INTEGER, byte_hash TEXT,
      text_fp TEXT, status TEXT, document_id INTEGER, version_id INTEGER, note TEXT,
      registered_at TEXT, triaged_at TEXT);
    """)
    # 写一个真实文件
    f = tmp_path / "archive" / "doc-1" / "v1-x.txt"
    f.parent.mkdir(parents=True)
    f.write_text("hello world")
    import hashlib
    bh = hashlib.sha256(b"hello world").hexdigest()
    tfp = hashlib.sha256("hello world".encode()).hexdigest()
    conn.execute(
        "INSERT INTO versions(id, document_id, byte_hash, archive_path) "
        "VALUES (1, 1, ?, 'archive/doc-1/v1-x.txt')", (bh,)
    )
    conn.execute(
        "INSERT INTO occurrences(id, source_type, source_path, file_name, byte_hash, "
        "text_fp, status, version_id) VALUES (1, 'wechat', 'x', 'x.txt', ?, ?, "
        "'archived', 1)", (bh, tfp)
    )
    conn.commit()
    conn.close()

    diffs = migrate.verify_fingerprints(src, archive_root=tmp_path)
    # 哈希一致 → 无差异
    assert diffs == []
