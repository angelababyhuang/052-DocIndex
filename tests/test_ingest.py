"""票 #12：摄入与去重管道。"""
import hashlib
import sqlite3

import pytest

from docarchive import db as appdb
from docarchive import ingest


@pytest.fixture
def conn(tmp_path):
    c = appdb.connect(tmp_path / "test.db")
    yield c
    c.close()


def _bh(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_register_new_file_as_occurrence(conn):
    ok = ingest.register(conn, "wechat", "/tmp/001/2026-09/x.txt", None,
                         "x.txt", 5, "bh1", "fp1")
    assert ok is True
    o = conn.execute("SELECT * FROM occurrences").fetchone()
    assert o["source_type"] == "wechat"
    assert o["file_name"] == "x.txt"
    assert o["ext"] == ".txt"
    assert o["size"] == 5
    assert o["byte_hash"] == "bh1"
    assert o["text_fp"] == "fp1"


def test_register_skips_duplicate_source(conn):
    assert ingest.register(conn, "wechat", "/tmp/x.txt", None, "x.txt", 5, "b", "f") is True
    # 同源重复登记 → 返回 False，不新增
    assert ingest.register(conn, "wechat", "/tmp/x.txt", None, "x.txt", 5, "b", "f") is False
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 1


def test_suggestion_duplicate_byte(conn):
    ingest.register(conn, "wechat", "p1", None, "a.txt", 3, "bh_same", "fp1")
    assert ingest.suggestion(conn, "bh_same", "fp_different", "b.txt") == "duplicate_byte"


def test_suggestion_duplicate_content(conn):
    # 内容相同字节不同：text_fp 命中，byte_hash 不同
    ingest.register(conn, "wechat", "p1", None, "a.txt", 3, "bh_old", "fp_same")
    assert ingest.suggestion(conn, "bh_new", "fp_same", "b.txt") == "duplicate_content"


def test_suggestion_new_version_by_name(conn):
    ingest.register(conn, "wechat", "p1", None, "连杆固定方案.docx", 100, "bh1", "fp1")
    # 名称归一化相同（去掉 (副本) 尾巴），指纹不同 → 疑似新版本
    assert ingest.suggestion(conn, "bh2", "fp2", "连杆固定方案 (副本).docx") == "new_version"


def test_suggestion_new(conn):
    ingest.register(conn, "wechat", "p1", None, "a.txt", 3, "bh1", "fp1")
    assert ingest.suggestion(conn, "bhX", "fpX", "完全不同的文件.md") == "new"


def test_suggestion_excludes_self(conn):
    """自匹配 bug：查某条出现自身时，不该因自己命中而误报 duplicate。

    这是真实数据抓出的 bug——两个不同图片各自 byte_hash 唯一，却都报
    duplicate_byte，因为 suggestion 没排除当前记录自身。
    """
    ingest.register(conn, "wechat", "p1", None, "a.jpg", 10, "bh_A", None)
    ingest.register(conn, "wechat", "p2", None, "b.jpg", 20, "bh_B", None)
    rows = conn.execute("SELECT id, file_name, byte_hash, text_fp FROM occurrences").fetchall()
    # a.jpg 自己查自己（排除自身）→ 应 new，而非 duplicate_byte
    assert ingest.suggestion(conn, "bh_A", None, "a.jpg", exclude_id=rows[0]["id"]) == "new"
    assert ingest.suggestion(conn, "bh_B", None, "b.jpg", exclude_id=rows[1]["id"]) == "new"


def test_suggestion_still_detects_real_duplicate(conn):
    """排除自身后，真正的重复仍能被检出。"""
    ingest.register(conn, "wechat", "p1", None, "a.jpg", 10, "bh_SAME", None)
    # 第二条真正的重复（不同 source_path，相同 byte_hash）
    ingest.register(conn, "wechat", "p2", None, "copy.jpg", 10, "bh_SAME", None)
    rows = conn.execute("SELECT id, byte_hash FROM occurrences ORDER BY id").fetchall()
    # 第二条（id 较大）查第一条（id 较小）→ 命中 duplicate_byte
    assert ingest.suggestion(conn, "bh_SAME", None, "copy.jpg",
                             exclude_id=rows[1]["id"]) == "duplicate_byte"


def test_suggestion_ignores_merged_and_ignored(conn):
    """merged/ignored 出现已被人工判过，不该作为新文件的去重基准。

    这是 code review 抓出的 bug：suggestion 未按 status 过滤，
    会把已 ignored/merged 的出现当作「疑似重复」依据。
    """
    # 一条已 ignored 的出现，byte_hash = bh_OLD
    ingest.register(conn, "wechat", "p1", None, "旧文件.docx", 10, "bh_OLD", None)
    conn.execute("UPDATE occurrences SET status='ignored' WHERE byte_hash='bh_OLD'")
    conn.commit()
    # 新文件恰好字节相同（bh_OLD），但旧的那条已 ignored → 不该判 duplicate
    assert ingest.suggestion(conn, "bh_OLD", None, "新文件.docx") == "new"


def test_wechat_1_suffix_is_not_a_version_signal(conn):
    """微信 (1) 后缀是同名避让，不是第二份副本、也不是新版本信号。

    name_key 应把「方案.docx」和「方案(1).docx」归一化成不同名（方案 vs 方案1），
    所以 (1) 不会被误判为同名新版本；而「(副本)」才会归一化成同名。
    """
    from docarchive import fingerprint
    assert fingerprint.name_key("方案.docx") == "方案"
    assert fingerprint.name_key("方案(1).docx") == "方案1"  # (1) 不同名
    assert fingerprint.name_key("方案(副本).docx") == "方案"  # (副本) 同名

    # (1) 后缀的文件：内容不同 → 不是重复（byte_hash 不同），也不该被 name_key 判成新版本
    ingest.register(conn, "wechat", "p1", None, "方案.docx", 100, "bh_A", "fp_A")
    result = ingest.suggestion(conn, "bh_B", "fp_B", "方案(1).docx")
    # 因为 name_key 不同，不会判 new_version；但也不是重复 → new
    assert result == "new"


def test_scan_dir_registers_files(tmp_path, conn):
    d = tmp_path / "inbox"
    (d / "2026-09-01").mkdir(parents=True)
    f1 = d / "2026-09-01" / "a.txt"
    f1.write_text("hello")
    f2 = d / "2026-09-01" / "b.txt"
    f2.write_text("world")
    n = ingest.scan_dir(conn, d, source_type="wechat")
    assert n == 2
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 2
