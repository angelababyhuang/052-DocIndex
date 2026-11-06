"""票 #12：摄入与去重管道；票 #20：scan 把媒体分流入库（含 zip 内媒体）。"""
import inspect
import zipfile

import pytest
from support import FakeCaptioner, make_png, make_video

from docarchive import ingest


def _file(path, text="x"):
    """在指定路径造一个文本文件（父目录自动建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _names(conn):
    return {r["file_name"] for r in conn.execute("SELECT file_name FROM occurrences")}


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


def test_scan_dir_registers_files(tmp_path, conn, archive_dir):
    d = tmp_path / "inbox"
    _file(d / "2026-09-01" / "a.txt", "hello")
    _file(d / "2026-09-01" / "b.txt", "world")
    n = ingest.scan_dir(conn, d, source_type="wechat", archive_dir=archive_dir)
    assert n.occurrences == 2
    assert n.images_seen == n.images_new == n.videos_seen == n.videos_new == 0
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 2


# ---------- 票 #20：媒体文件不登记出现，直接进媒体库 ----------

def test_scan_dir_has_no_media_switch():
    """媒体分流没有开关：--no-media / route_media 已删除，扫描总是把媒体入库。"""
    assert "route_media" not in inspect.signature(ingest.scan_dir).parameters


def test_scan_stats_requires_explicit_counts():
    """ScanStats 不再带全零默认值：漏传字段是 bug，不是「这次扫到 0 个」。"""
    with pytest.raises(TypeError):
        ingest.ScanStats()


def test_scan_routes_image_straight_to_library(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images_seen, stats.images_new) == (0, 1, 1)
    assert stats.caption_errors == ()
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

    assert (stats.occurrences, stats.videos_seen, stats.videos_new) == (0, 1, 1)
    assert conn.execute("SELECT code FROM videos").fetchone()["code"] == "VID-0001"
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_scan_mixed_dir_splits_docs_and_media(conn, archive_dir, inbox):
    _file(inbox / "2026-11-05" / "方案.docx", "docx bytes")
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images_seen, stats.videos_seen) == (1, 1, 1)
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

    assert (stats.images_seen, stats.images_new) == (2, 1)  # 看到两次，只新入库一次
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT code FROM images").fetchone()["code"] == "IMG-0001"


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


def test_scan_normalises_tags_so_a_generator_is_not_consumed(conn, archive_dir, inbox):
    """tags 只消费一次：传生成器时第二张图也得拿到同一批标签（不能第一张吃光）。"""
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "a.png")
    make_png(inbox / "2026-11-05" / "b.png", "B")

    ingest.scan_dir(conn, inbox, archive_dir=archive_dir, tags=(t for t in ("测试", "资料")))

    rows = conn.execute(
        "SELECT i.code, p.name FROM images i "
        "JOIN image_project_tags t ON t.image_id = i.id "
        "JOIN projects p ON p.id = t.project_id ORDER BY i.code, p.name"
    ).fetchall()
    assert [(r["code"], r["name"]) for r in rows] == [
        ("IMG-0001", "测试"), ("IMG-0001", "资料"),
        ("IMG-0002", "测试"), ("IMG-0002", "资料"),
    ]


def test_scan_media_passes_captioner_to_images(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")  # 视频不该调 vision 模型
    fake = FakeCaptioner("一张终端截图。")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir, captioner=fake)

    assert fake.calls == 1
    assert stats.caption_errors == ()
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "一张终端截图。"


def test_scan_collects_caption_errors(conn, archive_dir, inbox):
    """caption 失败不能被吞：回执里带上错误，CLI 才能逐条报给操作者。"""
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")
    fake = FakeCaptioner(error=RuntimeError("模型超时"))

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir, captioner=fake)

    assert stats.images_new == 1  # 图照样入库
    assert len(stats.caption_errors) == 1
    assert "模型超时" in stats.caption_errors[0]
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] is None


def test_scan_since_filters_before_media_routing(conn, archive_dir, inbox):
    """--since 先筛日期目录，被筛掉的目录里的媒体也不入库。"""
    first = inbox / "2026-11-03" / "old.png"
    first.parent.mkdir(parents=True)
    make_png(first)
    second = inbox / "2026-11-05" / "new.png"
    second.parent.mkdir(parents=True)
    make_png(second, "NEW")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir, since="2026-11-04")

    assert (stats.images_seen, stats.images_new) == (1, 1)
    assert conn.execute("SELECT source FROM images").fetchone()["source"] == str(second)


def test_scan_without_archive_dir_fails_before_registering_anything(conn, inbox):
    """scan 总要登记媒体：没有受管目录就在入口报错，别走一半才炸、留半截库。"""
    _file(inbox / "2026-11-05" / "a.docx", "docx bytes")   # 排序在 z.png 之前
    make_png(inbox / "2026-11-05" / "z.png")

    with pytest.raises(ValueError, match="受管目录"):
        ingest.scan_dir(conn, inbox)

    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 0


# ---------- 票 #20：zip 包内的媒体也进媒体库 ----------

def test_scan_routes_image_inside_zip_to_library(conn, archive_dir, inbox, tmp_path):
    """zip 里的图片不登记出现（否则 triage 时 new-doc 只能拒绝），直接进图片库。"""
    png = make_png(tmp_path / "src.png")
    (inbox / "2026-11-05").mkdir(parents=True)
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("inner/shot.png", png.read_bytes())

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images_seen, stats.images_new) == (0, 1, 1)
    img = conn.execute("SELECT code, source FROM images").fetchone()
    assert img["code"] == "IMG-0001"
    # 来源记压缩包里的位置，不记那个随后就被删掉的临时文件
    assert img["source"] == f"{inbox / '2026-11-05' / 'pkg.zip'}!inner/shot.png"
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_scan_keeps_zip_documents_as_occurrences(conn, archive_dir, inbox):
    """zip 里的文档照旧登记出现：媒体分流只针对媒体扩展名。"""
    (inbox / "2026-11-05").mkdir(parents=True)
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("inner/方案.docx", b"docx bytes")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.images_seen) == (1, 0)
    assert _names(conn) == {"方案.docx"}


def test_scan_routes_video_inside_zip_to_library(conn, archive_dir, inbox):
    (inbox / "2026-11-05").mkdir(parents=True)
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("clip.mp4", b"\x00\x00\x00\x18ftypmp42fake")

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.occurrences, stats.videos_seen, stats.videos_new) == (0, 1, 1)
    vid = conn.execute("SELECT code, source FROM videos").fetchone()
    assert vid["code"] == "VID-0001"
    assert vid["source"] == f"{inbox / '2026-11-05' / 'pkg.zip'}!clip.mp4"
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_scan_zip_media_keeps_one_img_code_across_zip_and_filesystem(
        conn, archive_dir, inbox, tmp_path):
    """sha256 去重：同一张图在 zip 里和文件系统上都出现，只占一个 IMG 编号。"""
    png = make_png(tmp_path / "src.png")
    (inbox / "2026-11-05").mkdir(parents=True)
    (inbox / "2026-11-05" / "shot.png").write_bytes(png.read_bytes())
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("inner/shot.png", png.read_bytes())

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.images_seen, stats.images_new) == (2, 1)
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT code FROM images").fetchone()["code"] == "IMG-0001"


def test_scan_same_image_in_two_zips_keeps_one_img_code(conn, archive_dir, inbox, tmp_path):
    """同一张图装进两个压缩包：去重口径是 sha256，不是「哪个包」。"""
    png = make_png(tmp_path / "src.png")
    for day, entry in (("2026-11-05", "a/shot.png"), ("2026-11-06", "b/copy.png")):
        (inbox / day).mkdir(parents=True)
        with zipfile.ZipFile(inbox / day / "pkg.zip", "w") as zf:
            zf.writestr(entry, png.read_bytes())

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir)

    assert (stats.images_seen, stats.images_new) == (2, 1)
    assert conn.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT code FROM images").fetchone()["code"] == "IMG-0001"


def test_scan_zip_media_applies_project_tags_and_captioner(conn, archive_dir, inbox, tmp_path):
    """zip 里的图与文件系统上的图走同一条登记路径：项目/标签/caption 一样生效。"""
    png = make_png(tmp_path / "src.png")
    (inbox / "2026-11-05").mkdir(parents=True)
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("inner/shot.png", png.read_bytes())
    fake = FakeCaptioner("一张终端截图。")

    ingest.scan_dir(conn, inbox, archive_dir=archive_dir,
                    project="浮标项目", tags=["测试"], captioner=fake)

    assert fake.calls == 1
    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["caption"] == "一张终端截图。"
    assert img["project_id"] == conn.execute(
        "SELECT id FROM projects WHERE name='浮标项目'").fetchone()["id"]
    assert [r["name"] for r in conn.execute(
        "SELECT p.name FROM image_project_tags t JOIN projects p ON p.id=t.project_id")] == ["测试"]


def test_scan_zip_media_collects_caption_errors(conn, archive_dir, inbox, tmp_path):
    """zip 分支的 caption 失败也要进 caption_errors（不能只在文件系统分支上报）。"""
    png = make_png(tmp_path / "src.png")
    (inbox / "2026-11-05").mkdir(parents=True)
    with zipfile.ZipFile(inbox / "2026-11-05" / "pkg.zip", "w") as zf:
        zf.writestr("inner/shot.png", png.read_bytes())

    stats = ingest.scan_dir(conn, inbox, archive_dir=archive_dir,
                            captioner=FakeCaptioner(error=RuntimeError("模型超时")))

    assert len(stats.caption_errors) == 1 and "模型超时" in stats.caption_errors[0]


# ---------- 票 #19：--since 只放行标准日期目录 ----------

def test_scan_since_skips_non_date_dirs(conn, inbox, archive_dir):
    _file(inbox / "2026-11-05" / "a.txt")
    _file(inbox / "备份" / "b.txt")
    _file(inbox / "待整理" / "c.txt")

    n = ingest.scan_dir(conn, inbox, since="2026-11-04", archive_dir=archive_dir)
    assert n.occurrences == 1
    assert _names(conn) == {"a.txt"}


def test_scan_since_skips_files_outside_date_dirs(conn, inbox, archive_dir):
    _file(inbox / "top.txt")
    _file(inbox / "2026-11-05" / "a.txt")

    ingest.scan_dir(conn, inbox, since="2026-11-01", archive_dir=archive_dir)
    assert _names(conn) == {"a.txt"}


def test_scan_since_compares_dates_as_strings(conn, inbox, archive_dir):
    _file(inbox / "2026-11-03" / "old.txt")
    _file(inbox / "2026-11-05" / "new.txt")

    ingest.scan_dir(conn, inbox, since="2026-11-04", archive_dir=archive_dir)
    assert _names(conn) == {"new.txt"}


def test_scan_since_skips_nonstandard_date_names(conn, inbox, archive_dir):
    _file(inbox / "2026-1-5" / "unpadded.txt")      # 未补零
    _file(inbox / "2026-13-45" / "impossible.txt")  # 月份日期不合法
    _file(inbox / "2026-11-05" / "ok.txt")

    ingest.scan_dir(conn, inbox, since="2026-01-01", archive_dir=archive_dir)
    assert _names(conn) == {"ok.txt"}


def test_scan_all_includes_non_date_dirs_and_root_files(conn, inbox, archive_dir):
    _file(inbox / "2026-11-05" / "a.txt")
    _file(inbox / "备份" / "b.txt")
    _file(inbox / "top.txt")

    n = ingest.scan_dir(conn, inbox, all_files=True, archive_dir=archive_dir)
    assert n.occurrences == 3
    assert _names(conn) == {"a.txt", "b.txt", "top.txt"}


def test_scan_rejects_since_with_all(conn, inbox, archive_dir):
    """同时给 since 和 all_files 是矛盾调用，直接报错，不静默忽略。"""
    _file(inbox / "备份" / "b.txt")
    with pytest.raises(ValueError):
        ingest.scan_dir(conn, inbox, since="2026-11-04", all_files=True,
                        archive_dir=archive_dir)


def test_scan_rejects_malformed_since_at_api_level(conn, inbox, archive_dir):
    with pytest.raises(ValueError):
        ingest.scan_dir(conn, inbox, since="2026-1-5", archive_dir=archive_dir)


def test_scan_date_dir_itself_as_root(conn, tmp_path, archive_dir):
    """直接指向某个日期目录时，把目录名当日期用，别静默扫出 0 条。"""
    d = tmp_path / "2026-11-05"
    _file(d / "a.txt")
    n = ingest.scan_dir(conn, d, since="2026-11-04", archive_dir=archive_dir)
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
