"""票 #12：摄入与去重管道；票 #20：scan 把媒体分流入库。"""
import hashlib
from pathlib import Path

import pytest
from support import make_png, make_video

from docarchive import ingest


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
    assert n.occurrences == 2
    assert n.images == n.images_new == n.videos == n.videos_new == 0
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 2


# ---------- 票 #20：媒体文件不登记出现，直接进媒体库 ----------

class FakeCaptioner:
    """测试用 captioner：只记录调用，绝不联网。"""

    def __init__(self, text="一张微信截图。"):
        self.text = text
        self.calls = 0

    def describe(self, data, ext=None):
        self.calls += 1
        return self.text


def _date_file(root: Path, name: str, text: str = "x") -> Path:
    p = root / "2026-11-05" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_scan_routes_image_straight_to_library(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images, stats.images_new) == (0, 1, 1)
    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["code"] == "IMG-0001"
    assert img["source"] == str(inbox / "2026-11-05" / "shot.png")
    assert (archive_dir / img["path"]).exists()
    # 媒体不产生出现：triage 队列里只有文档
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_scan_routes_video_straight_to_library(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_video(inbox / "2026-11-05" / "demo.mp4")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.videos, stats.videos_new) == (0, 1, 1)
    assert conn.execute("SELECT code FROM videos").fetchone()["code"] == "VID-0001"
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_scan_mixed_dir_splits_docs_and_media(conn, archive_dir, inbox):
    _date_file(inbox, "方案.docx", "docx bytes")
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images, stats.videos) == (1, 1, 1)
    assert {r["file_name"] for r in conn.execute("SELECT file_name FROM occurrences")} == {"方案.docx"}
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM videos").fetchone()[0] == 1


def test_scan_same_image_in_two_date_dirs_gets_one_img_code(conn, archive_dir, inbox):
    """同一张图出现在两个日期目录：sha256 去重，只分配一次编号，第二次 created=False。"""
    first = inbox / "2026-11-05" / "shot.png"
    first.parent.mkdir(parents=True)
    make_png(first)
    data = first.read_bytes()
    second = inbox / "2026-11-06" / "shot.png"
    second.parent.mkdir(parents=True)
    second.write_bytes(data)

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.images, stats.images_new) == (2, 1)  # 看到两次，只新入库一次
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT code FROM images").fetchone()["code"] == "IMG-0001"


def test_scan_no_media_keeps_old_occurrence_path(conn, archive_dir, inbox):
    """--no-media：媒体退回普通出现，进 triage 队列（旧行为）。"""
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir, route_media=False)

    assert (stats.occurrences, stats.images, stats.videos) == (1, 0, 0)
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 0
    assert conn.execute("SELECT ext FROM occurrences").fetchone()["ext"] == ".png"


def test_scan_media_applies_project_and_tags(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")

    ingest.scan_dir(conn, inbox, archive_dir=archive_dir,
                    project="浮标项目", tags=["测试"])

    img = conn.execute("SELECT * FROM images").fetchone()
    pid = conn.execute("SELECT id FROM projects WHERE name='浮标项目'").fetchone()["id"]
    assert img["project_id"] == pid
    tag_names = [r["name"] for r in conn.execute(
        "SELECT p.name FROM image_project_tags t JOIN projects p ON p.id=t.project_id")]
    assert tag_names == ["测试"]
    vid = conn.execute("SELECT * FROM videos").fetchone()
    assert vid["project_id"] == pid
    assert [r["name"] for r in conn.execute(
        "SELECT p.name FROM video_project_tags t JOIN projects p ON p.id=t.project_id")] == ["测试"]


def test_scan_media_passes_captioner_to_images(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")  # 视频不该调 vision 模型
    fake = FakeCaptioner("一张终端截图。")

    ingest.scan_dir(conn, inbox, archive_dir=archive_dir, captioner=fake)

    assert fake.calls == 1
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "一张终端截图。"


def test_scan_since_filters_before_media_routing(conn, archive_dir, inbox):
    """--since 先筛日期目录，被筛掉的目录里的媒体也不入库。"""
    first = inbox / "2026-11-03" / "old.png"
    first.parent.mkdir(parents=True)
    make_png(first)
    second = inbox / "2026-11-05" / "new.png"
    second.parent.mkdir(parents=True)
    make_png(second, "NEW")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir, since="2026-11-04")

    assert (stats.images, stats.images_new) == (1, 1)
    assert conn.execute("SELECT source FROM images").fetchone()["source"] == str(second)


def test_scan_media_without_archive_dir_raises(conn, inbox):
    """开媒体分流却不知道受管目录 → 直接报错，别静默丢掉媒体。"""
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")
    with pytest.raises(ValueError, match="archive_dir"):
        ingest.scan_dir(conn, inbox)


# ---------- 票 #19：--since 只放行标准日期目录 ----------

def _file(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _names(conn):
    return {r["file_name"] for r in conn.execute("SELECT file_name FROM occurrences")}


def test_scan_since_skips_non_date_dirs(conn, inbox):
    _file(inbox / "2026-11-05" / "a.txt")
    _file(inbox / "备份" / "b.txt")
    _file(inbox / "待整理" / "c.txt")

    n = ingest.scan_dir(conn, inbox, since="2026-11-04")
    assert n.occurrences == 1
    assert _names(conn) == {"a.txt"}


def test_scan_since_skips_files_outside_date_dirs(conn, inbox):
    _file(inbox / "top.txt")
    _file(inbox / "2026-11-05" / "a.txt")

    ingest.scan_dir(conn, inbox, since="2026-11-01")
    assert _names(conn) == {"a.txt"}


def test_scan_since_compares_dates_as_strings(conn, inbox):
    _file(inbox / "2026-11-03" / "old.txt")
    _file(inbox / "2026-11-05" / "new.txt")

    ingest.scan_dir(conn, inbox, since="2026-11-04")
    assert _names(conn) == {"new.txt"}


def test_scan_since_skips_nonstandard_date_names(conn, inbox):
    _file(inbox / "2026-1-5" / "unpadded.txt")      # 未补零
    _file(inbox / "2026-13-45" / "impossible.txt")  # 月份日期不合法
    _file(inbox / "2026-11-05" / "ok.txt")

    ingest.scan_dir(conn, inbox, since="2026-01-01")
    assert _names(conn) == {"ok.txt"}


def test_scan_all_includes_non_date_dirs_and_root_files(conn, inbox):
    _file(inbox / "2026-11-05" / "a.txt")
    _file(inbox / "备份" / "b.txt")
    _file(inbox / "top.txt")

    n = ingest.scan_dir(conn, inbox, all_files=True)
    assert n.occurrences == 3
    assert _names(conn) == {"a.txt", "b.txt", "top.txt"}


def test_scan_rejects_since_with_all(conn, inbox):
    """同时给 since 和 all_files 是矛盾调用，直接报错，不静默忽略。"""
    _file(inbox / "备份" / "b.txt")
    with pytest.raises(ValueError):
        ingest.scan_dir(conn, inbox, since="2026-11-04", all_files=True)


def test_scan_rejects_malformed_since_at_api_level(conn, inbox):
    with pytest.raises(ValueError):
        ingest.scan_dir(conn, inbox, since="2026-1-5")


def test_scan_date_dir_itself_as_root(conn, tmp_path):
    """直接指向某个日期目录时，把目录名当日期用，别静默扫出 0 条。"""
    d = tmp_path / "2026-11-05"
    _file(d / "a.txt")
    n = ingest.scan_dir(conn, d, since="2026-11-04")
    assert n.occurrences == 1 and _names(conn) == {"a.txt"}


def test_is_date_dir_rejects_lookalikes():
    assert not ingest.is_date_dir("2026-11-05\n")
    assert not ingest.is_date_dir("２０２６-11-05")  # 全角数字
    assert not ingest.is_date_dir("2026-11-05-备份")


def test_is_date_dir():
    assert ingest.is_date_dir("2026-11-05")
    assert not ingest.is_date_dir("2026-1-5")
    assert not ingest.is_date_dir("备份")
    assert not ingest.is_date_dir("2026-13-01")
    assert not ingest.is_date_dir("2026-11-32")
