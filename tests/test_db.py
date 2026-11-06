"""票 #10：数据模型与真相源骨架。"""
import sqlite3

import pytest

from docarchive import db


def test_schema_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {r["name"] for r in rows}
    assert tables == {
        "projects",
        "documents",
        "versions",
        "occurrences",
        "images",
        "videos",
        "document_images",
        "document_project_tags",
        "image_project_tags",
        "video_project_tags",
        "code_counters",
    }


def test_insert_and_read_document(conn):
    conn.execute("INSERT INTO projects(name) VALUES ('浮标项目')")
    pid = conn.execute("SELECT id FROM projects WHERE name='浮标项目'").fetchone()["id"]
    conn.execute(
        "INSERT INTO documents(title, project_id, category) VALUES (?, ?, ?)",
        ("连杆固定方案", pid, "研发"),
    )
    conn.commit()
    d = conn.execute("SELECT * FROM documents WHERE title='连杆固定方案'").fetchone()
    assert d["project_id"] == pid
    assert d["category"] == "研发"


def test_image_code_allocates_sequentially(conn):
    assert db.next_code(conn, "IMG") == "IMG-0001"
    assert db.next_code(conn, "IMG") == "IMG-0002"
    assert db.next_code(conn, "IMG") == "IMG-0003"


def test_video_code_allocates_sequentially(conn):
    assert db.next_code(conn, "VID") == "VID-0001"
    assert db.next_code(conn, "VID") == "VID-0002"


def test_image_code_never_reused_after_delete(conn):
    # 分配三个号，删除中间的实体，号段仍单调递增
    assert db.next_code(conn, "IMG") == "IMG-0001"
    assert db.next_code(conn, "IMG") == "IMG-0002"
    assert db.next_code(conn, "IMG") == "IMG-0003"
    conn.execute("DELETE FROM images WHERE code='IMG-0002'")
    conn.commit()
    # 删除后不回收号段，下一个是 0004（0002 永不复用）
    assert db.next_code(conn, "IMG") == "IMG-0004"


def test_next_code_does_not_commit(conn):
    """next_code 不该 commit：号段分配要留在调用方事务里，随实体一起回滚。

    这是 code review 抓出的 bug：next_code 每次分配即 commit，
    实体插入失败时号段仍前进，造成跳号。
    """
    db.next_code(conn, "IMG")
    # 没 commit → 另一个连接（或回滚后）应看不到这次分配
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM code_counters").fetchone()[0] == 0


def test_document_image_association(conn):
    conn.execute("INSERT INTO projects(name) VALUES ('P')")
    pid = conn.execute("SELECT id FROM projects WHERE name='P'").fetchone()["id"]
    conn.execute("INSERT INTO documents(title, project_id) VALUES ('D', ?)", (pid,))
    did = conn.execute("SELECT id FROM documents WHERE title='D'").fetchone()["id"]
    conn.execute("INSERT INTO images(code, sha256) VALUES ('IMG-0001', 'd' * 64)")
    iid = conn.execute("SELECT id FROM images WHERE code='IMG-0001'").fetchone()["id"]
    conn.execute(
        "INSERT INTO document_images(document_id, image_id, kind, position) VALUES (?, ?, 'embedded', '§3')",
        (did, iid),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM document_images WHERE document_id=? AND image_id=?", (did, iid)
    ).fetchone()
    assert row["kind"] == "embedded"
    assert row["position"] == "§3"


def test_document_project_tag(conn):
    conn.execute("INSERT INTO projects(name) VALUES ('A')")
    conn.execute("INSERT INTO projects(name) VALUES ('B')")
    pa = conn.execute("SELECT id FROM projects WHERE name='A'").fetchone()["id"]
    pb = conn.execute("SELECT id FROM projects WHERE name='B'").fetchone()["id"]
    conn.execute("INSERT INTO documents(title, project_id) VALUES ('D', ?)", (pa,))
    did = conn.execute("SELECT id FROM documents WHERE title='D'").fetchone()["id"]
    # 关联项目标签：文档主归属 A，额外关联 B
    conn.execute(
        "INSERT INTO document_project_tags(document_id, project_id) VALUES (?, ?)",
        (did, pb),
    )
    conn.commit()
    tags = [
        r["project_id"]
        for r in conn.execute(
            "SELECT project_id FROM document_project_tags WHERE document_id=?", (did,)
        )
    ]
    assert tags == [pb]


def test_connect_creates_missing_parent_dir(tmp_path):
    """首次用默认路径 data/truth.db 时，父目录不存在也要能建库。"""
    conn = db.connect(tmp_path / "data" / "truth.db")
    assert (tmp_path / "data" / "truth.db").exists()
    conn.close()


def test_versions_have_markdown_path(conn):
    """#13：版本要能记住转换出的 md 位置。"""
    conn.execute("INSERT INTO documents(title) VALUES ('D')")
    conn.execute("INSERT INTO versions(document_id, markdown_path) VALUES (1, 'doc-1/v1-a.md')")
    row = conn.execute("SELECT markdown_path FROM versions WHERE id=1").fetchone()
    assert row["markdown_path"] == "doc-1/v1-a.md"


def test_connect_readonly_does_not_create_and_reads(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        db.connect_readonly(missing)
    assert not missing.exists()

    conn = db.connect(tmp_path / "real.db")
    conn.execute("INSERT INTO projects(name) VALUES ('浮标项目')")
    conn.commit()
    conn.close()

    ro = db.connect_readonly(tmp_path / "real.db")
    try:
        assert ro.execute("SELECT name FROM projects").fetchone()["name"] == "浮标项目"
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO projects(name) VALUES ('x')")
    finally:
        ro.close()
