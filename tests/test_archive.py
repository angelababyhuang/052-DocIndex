"""票 #18：归档落库 + triage 人工确认。"""
import hashlib

import pytest

from docarchive import archive, ingest


def _make_occ(conn, source_type="wechat", source_path="/tmp/x.txt",
              file_name="x.txt", content=b"hello", status="pending"):
    bh = hashlib.sha256(content).hexdigest()
    ingest.register(conn, source_type, source_path, None, file_name,
                    len(content), bh, None)
    return conn.execute("SELECT * FROM occurrences ORDER BY id DESC LIMIT 1").fetchone()


def test_new_doc_creates_document_version_and_copies(conn, archive_dir, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    occ = _make_occ(conn, source_path=str(src), file_name="方案.txt")
    doc_id, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir, title="连杆方案")

    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert d["title"] == "连杆方案"
    assert d["current_version_id"] == vid

    v = conn.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
    assert v["document_id"] == doc_id
    # 归档文件按 doc-<ID>/v<版本ID>-<文件名> 组织
    assert v["archive_path"] == f"doc-{doc_id}/v{vid}-方案.txt"

    o = conn.execute("SELECT * FROM occurrences WHERE id=?", (occ["id"],)).fetchone()
    assert o["status"] == "archived"
    assert o["document_id"] == doc_id

    # 文件真的复制了（不是移动），原件还在
    copied = archive_dir / f"doc-{doc_id}" / f"v{vid}-方案.txt"
    assert copied.exists()
    assert src.exists()  # 原件不动


def test_new_version_updates_current(conn, archive_dir, tmp_path):
    src1 = tmp_path / "a.txt"
    src1.write_text("v1")
    occ1 = _make_occ(conn, source_path=str(src1), file_name="a.txt")
    doc_id, vid1 = archive.new_doc(conn, occ1["id"], "P", archive_dir, title="A")

    src2 = tmp_path / "b.txt"
    src2.write_text("v2")
    occ2 = _make_occ(conn, source_path=str(src2), file_name="b.txt")
    vid2 = archive.new_version(conn, occ2["id"], doc_id, archive_dir)

    d = conn.execute("SELECT current_version_id FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert d["current_version_id"] == vid2  # 现行版本更新
    assert vid2 != vid1


def test_merge_records_without_copying(conn, archive_dir, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    occ1 = _make_occ(conn, source_path=str(src), file_name="a.txt")
    doc_id, vid = archive.new_doc(conn, occ1["id"], "P", archive_dir, title="A")

    # 第二条相同内容的出现（不同路径），merge 到 doc
    src2 = tmp_path / "a_copy.txt"
    src2.write_text("hello")  # 相同字节
    occ2 = _make_occ(conn, source_path=str(src2), file_name="a_copy.txt")
    merged_vid = archive.merge(conn, occ2["id"], doc_id)

    assert merged_vid == vid
    o = conn.execute("SELECT status FROM occurrences WHERE id=?", (occ2["id"],)).fetchone()
    assert o["status"] == "merged"
    # 不复制文件：archive 目录里只有一条版本文件
    files = list((archive_dir / f"doc-{doc_id}").glob("v*"))
    assert len(files) == 1


def test_ignore_does_not_copy(conn, archive_dir, tmp_path):
    src = tmp_path / "x.txt"
    src.write_text("skip")
    occ = _make_occ(conn, source_path=str(src), file_name="x.txt")
    archive.ignore(conn, occ["id"])
    o = conn.execute("SELECT status FROM occurrences WHERE id=?", (occ["id"],)).fetchone()
    assert o["status"] == "ignored"
    # 没建文档、没复制文件
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert list(archive_dir.rglob("v*")) == []


def test_status_flow_pending_to_terminal(conn, archive_dir, tmp_path):
    """status 只从 pending 流转；已处理的不能再处理。"""
    src = tmp_path / "x.txt"
    src.write_text("x")
    occ = _make_occ(conn, source_path=str(src), file_name="x.txt")
    archive.ignore(conn, occ["id"])
    # 再次处理已 ignored 的出现 → 报错
    with pytest.raises(ValueError, match="已是"):
        archive.ignore(conn, occ["id"])


def test_zip_occurrence_copies_expanded_entry(conn, archive_dir, tmp_path):
    import zipfile
    zp = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("inner/doc.txt", "zip content")
    # 登记 zip 内 entry
    ingest._register_zip_entries(conn, zp, archive_dir=archive_dir)
    occ = conn.execute("SELECT * FROM occurrences WHERE source_type='zip'").fetchone()
    doc_id, vid = archive.new_doc(conn, occ["id"], "P", archive_dir, title="Doc")
    v = conn.execute("SELECT archive_path FROM versions WHERE id=?", (vid,)).fetchone()
    copied = archive_dir / v["archive_path"]
    assert copied.read_text() == "zip content"


def test_schema_has_media_storage_columns(conn):
    """spec §4.3/§4.2：images/videos 有 path 列，videos 有 project_id。"""
    for col in ("path",):
        assert col in [r[1] for r in conn.execute("PRAGMA table_info(images)")]
    for col in ("path", "project_id"):
        assert col in [r[1] for r in conn.execute("PRAGMA table_info(videos)")]


# ---------- 票 #20：媒体文件不能被 new-doc 收编 ----------

def test_new_doc_refuses_image_occurrence(conn, archive_dir, tmp_path):
    """图片出现走 new-doc 只会产出「convert 才发现不支持」的半成品，直接拒绝。"""
    src = tmp_path / "shot.png"
    src.write_bytes(b"fake png bytes")
    occ = _make_occ(conn, source_path=str(src), file_name="shot.png")

    with pytest.raises(ValueError, match="register-image"):
        archive.new_doc(conn, occ["id"], "P", archive_dir)

    # 拒绝要彻底：不建文档、不复制、出现仍是 pending
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM versions").fetchone()[0] == 0
    assert list(archive_dir.rglob("v*")) == []
    assert conn.execute("SELECT status FROM occurrences").fetchone()["status"] == "pending"


def test_new_doc_refuses_video_occurrence(conn, archive_dir, tmp_path):
    src = tmp_path / "demo.mp4"
    src.write_bytes(b"fake mp4 bytes")
    occ = _make_occ(conn, source_path=str(src), file_name="demo.mp4")

    with pytest.raises(ValueError, match="register-video"):
        archive.new_doc(conn, occ["id"], "P", archive_dir)


def test_new_doc_media_message_names_the_real_remedies(conn, archive_dir, tmp_path):
    """拒绝信息要给出真能用的补救：重扫该目录（媒体不再走出现）或手动登记。

    这些出现只可能来自 #20 之前的老库，所以提示里不能再说「去掉 --no-media 重扫」
    这种已经不存在的开关。
    """
    src = tmp_path / "shot.png"
    src.write_bytes(b"fake png bytes")
    occ = _make_occ(conn, source_path=str(src), file_name="shot.png")

    with pytest.raises(ValueError) as ei:
        archive.new_doc(conn, occ["id"], "P", archive_dir)

    msg = str(ei.value)
    assert "register-image" in msg and "register-video" in msg
    assert "重新扫描" in msg
    assert "--no-media" not in msg


def test_new_doc_still_accepts_documents(conn, archive_dir, tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"docx bytes")
    occ = _make_occ(conn, source_path=str(src), file_name="a.docx")
    doc_id, vid = archive.new_doc(conn, occ["id"], "P", archive_dir)
    assert doc_id and vid
