"""CLI 冒烟：命令能跑通、落库、有输出（#14 的 register-image/register-video/set-intro/media）。"""
import sys

import pytest
from support import (
    FakeCaptioner,
    make_pending,
    make_png,
    make_video,
    register_fake_image,
)

from docarchive import __main__ as cli


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["docarchive", *argv])
    cli.main()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    # 别让测试真的去调视觉模型
    monkeypatch.setattr(cli.caption, "captioner_from_env", lambda *a, **k: None)
    return tmp_path / "data" / "truth.db"


def test_cli_media_commands_end_to_end(tmp_path, monkeypatch, capsys, db_path):
    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "shot.png"
    Image.new("RGB", (80, 60), "white").save(img)
    vid = tmp_path / "demo.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")

    run_cli(monkeypatch, "register-image", str(img), "--project", "浮标项目",
            "--tag", "测试", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "IMG-0001" in out and "新入库" in out

    run_cli(monkeypatch, "register-video", str(vid), "--project", "浮标项目",
            "--intro", "现场录像", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "VID-0001" in out and "现场录像" in out

    run_cli(monkeypatch, "set-intro", "VID-0001", "--intro", "改过的简介",
            "--db", str(db_path))
    assert "已更新" in capsys.readouterr().out

    run_cli(monkeypatch, "media", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "IMG-0001" in out and "浮标项目" in out and "[测试]" in out
    assert "改过的简介" in out

    run_cli(monkeypatch, "media", "--project", "别的项目", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "IMG-0001" not in out


def test_cli_scan_since_skips_non_date_dirs_and_all_includes_them(tmp_path, monkeypatch, capsys, db_path):
    inbox = tmp_path / "inbox"
    (inbox / "2026-11-05").mkdir(parents=True)
    (inbox / "2026-11-05" / "b.txt").write_text("y")
    (inbox / "备份").mkdir()
    (inbox / "备份" / "a.txt").write_text("x")

    run_cli(monkeypatch, "scan", str(inbox), "--since", "2026-11-04", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "文档 1" in out and "a.txt" not in out

    run_cli(monkeypatch, "scan", str(inbox), "--all", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "文档 1" in out and "a.txt" in out


def test_cli_scan_since_and_all_are_mutually_exclusive(db_path, monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "scan", str(tmp_path), "--since", "2026-11-04",
                "--all", "--db", str(db_path))


def test_cli_scan_rejects_malformed_since(tmp_path, monkeypatch, db_path):
    with pytest.raises(SystemExit) as ei:
        run_cli(monkeypatch, "scan", str(tmp_path), "--since", "2026-1-5", "--db", str(db_path))
    assert "YYYY-MM-DD" in str(ei.value)


def test_cli_scan_hints_when_only_non_date_dirs_matched(tmp_path, monkeypatch, capsys, db_path):
    inbox = tmp_path / "inbox"
    (inbox / "备份").mkdir(parents=True)
    (inbox / "备份" / "a.txt").write_text("x")

    run_cli(monkeypatch, "scan", str(inbox), "--since", "2026-11-04", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "文档 0" in out
    assert "备份" in out and "--all" in out


def test_cli_scan_date_dir_as_root_works(tmp_path, monkeypatch, capsys, db_path):
    d = tmp_path / "2026-11-05"
    d.mkdir(parents=True)
    (d / "a.txt").write_text("x")
    run_cli(monkeypatch, "scan", str(d), "--since", "2026-11-04", "--db", str(db_path))
    assert "文档 1" in capsys.readouterr().out


# ---------- #20：scan 媒体分流 ----------

def _mixed_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "2026-11-05").mkdir(parents=True)
    (inbox / "2026-11-05" / "方案.docx").write_bytes(b"docx bytes")
    make_png(inbox / "2026-11-05" / "shot.png")
    make_video(inbox / "2026-11-05" / "demo.mp4")
    return inbox


def test_cli_scan_routes_media_and_prints_breakdown(tmp_path, monkeypatch, capsys, db_path):
    inbox = _mixed_inbox(tmp_path)

    run_cli(monkeypatch, "scan", str(inbox), "--project", "浮标项目", "--tag", "测试",
            "--db", str(db_path))
    out = capsys.readouterr().out
    assert "文档 1" in out
    assert "图片 1（新 1）" in out
    assert "视频 1（新 1）" in out

    # 截图/录屏真的进了媒体库，且带上了本次 scan 的项目/标签
    run_cli(monkeypatch, "media", "--project", "浮标项目", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "IMG-0001" in out and "VID-0001" in out and "[测试]" in out


def test_cli_scan_no_longer_has_no_media_flag(tmp_path, monkeypatch, db_path):
    """--no-media 已删除：媒体总是进媒体库，没有退回出现的开关。"""
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "scan", str(tmp_path), "--no-media", "--db", str(db_path))


def test_cli_scan_reports_caption_failures(tmp_path, monkeypatch, capsys, db_path):
    """caption 失败要逐条打印，和 convert/register-image 同一句话。"""
    monkeypatch.setattr(cli.caption, "captioner_from_env",
                        lambda *a, **k: FakeCaptioner(error=RuntimeError("模型超时")))
    inbox = tmp_path / "inbox"
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")

    run_cli(monkeypatch, "scan", str(inbox), "--db", str(db_path))

    out = capsys.readouterr().out
    assert "⚠ 描述生成失败：" in out
    assert "模型超时" in out
    assert "图片 1（新 1）" in out  # 失败不挡入库


def test_cli_new_doc_on_media_exits_with_guidance(tmp_path, monkeypatch, capsys, db_path):
    """#20 之前留下的媒体出现：new-doc 拒绝，并告诉操作者真正的补救办法。"""
    from docarchive import db as appdb

    png = make_png(tmp_path / "shot.png")
    conn = appdb.connect(db_path)
    make_pending(conn, png)  # 老库里遗留的媒体出现
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit) as ei:
        run_cli(monkeypatch, "new-doc", "1", "--project", "浮标项目", "--db", str(db_path))
    msg = str(ei.value)
    assert "register-image" in msg and "register-video" in msg
    assert "重新扫描" in msg


def test_cli_scan_no_caption_skips_model(tmp_path, monkeypatch, capsys, db_path):
    """--no-caption 时不拿 captioner（KEY 配了也不调模型）。"""
    calls = []
    monkeypatch.setattr(cli.caption, "captioner_from_env",
                        lambda *a, **k: calls.append(1) or None)
    inbox = tmp_path / "inbox"
    (inbox / "2026-11-05").mkdir(parents=True)
    make_png(inbox / "2026-11-05" / "shot.png")

    run_cli(monkeypatch, "scan", str(inbox), "--no-caption", "--db", str(db_path))
    assert calls == []
    assert "IMG-0001" not in capsys.readouterr().out  # scan 的自述里不逐条列媒体


# ---------- #15：index / search ----------

def _seed_converted_doc(db_path, archive_dir, md_text=None):
    from docarchive import db as appdb

    conn = appdb.connect(db_path)
    conn.execute("INSERT INTO projects(name) VALUES ('浮标项目')")
    conn.execute(
        "INSERT INTO documents(title, project_id, category, current_version_id) "
        "VALUES ('连杆方案', 1, '测试', 1)"
    )
    md = archive_dir / "doc-1" / "v1-a.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(md_text or "# 连杆方案\n\n连杆间隙复测 0.02mm。", encoding="utf-8")
    conn.execute(
        "INSERT INTO versions(document_id, archive_path, markdown_path) "
        "VALUES (1, 'doc-1/v1-a.docx', 'doc-1/v1-a.md')"
    )
    conn.commit()
    conn.close()


def test_cli_index_dry_run_counts_units(tmp_path, monkeypatch, capsys, db_path):
    _seed_converted_doc(db_path, db_path.parent / "archive")  # CLI 的受管目录 = db 同级 archive/
    run_cli(monkeypatch, "index", "--dry-run", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "将推送 1 条知识" in out
    assert "项目/浮标项目" in out and "类别/测试" in out


class _FakeWeKnora:
    def __init__(self):
        self.searched = None
        self.tag_names = None

    def find_kb(self, name):
        return "kb-1"

    def tag_ids_for(self, kb_id, names):
        self.tag_names = list(names)
        return ["t-1"]

    def search(self, query, kb_ids, tag_ids=(), match_count=5):
        self.searched = (query, tuple(kb_ids), tuple(tag_ids), match_count)
        return [{"knowledge_title": "连杆方案", "score": 0.87,
                 "content": "连杆间隙复测 0.02mm。"}]


def test_cli_search_prints_hits(tmp_path, monkeypatch, capsys, db_path):
    fake = _FakeWeKnora()
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)
    run_cli(monkeypatch, "search", "连杆间隙", "--tag", "项目/浮标项目", "--limit", "3")
    out = capsys.readouterr().out
    assert "连杆方案" in out and "0.870" in out and "连杆间隙复测" in out
    assert fake.searched == ("连杆间隙", ("kb-1",), ("t-1",), 3)


def test_cli_search_missing_kb_exits(tmp_path, monkeypatch, capsys, db_path):
    class NoKb(_FakeWeKnora):
        def find_kb(self, name):
            return None

    monkeypatch.setattr(cli, "_weknora_client", lambda: NoKb())
    with pytest.raises(SystemExit) as ei:
        run_cli(monkeypatch, "search", "x")
    assert "先跑 index" in str(ei.value)


def test_cli_search_tag_accepts_bare_project_name(tmp_path, monkeypatch, capsys, db_path):
    """`--tag 浮标项目` 与 `--tag 项目/浮标项目` 等价（和 media --tag 同一口径）。"""
    fake = _FakeWeKnora()
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)
    run_cli(monkeypatch, "search", "连杆间隙", "--tag", "浮标项目")
    assert fake.tag_names == ["项目/浮标项目"]


# ---------- #21：检索结果回指内嵌图 ----------

def _seed_image(db_path, archive_dir, data=b"fake-png-bytes"):
    """CLI 用例里登记一张假图并返回编号（连接管理留在测试侧）。"""
    from docarchive import db as appdb

    conn = appdb.connect(db_path)
    try:
        return register_fake_image(conn, archive_dir, data).code
    finally:
        conn.close()


class _ImageHitWeKnora(_FakeWeKnora):
    def __init__(self, hit):
        super().__init__()
        self.hit = hit

    def search(self, query, kb_ids, tag_ids=(), match_count=5):
        return [self.hit]


def test_cli_search_prints_image_master_path(tmp_path, monkeypatch, capsys, db_path):
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir)
    code = _seed_image(db_path, archive_dir)
    fake = _ImageHitWeKnora({
        "knowledge_title": "连杆方案", "score": 0.9,
        "content": f"见 {{{{{code}}}}}",
        "knowledge_custom_metadata": f"图片: {code}",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(db_path))

    out = capsys.readouterr().out
    assert code in out
    assert str((archive_dir / f"images/{code}.png").resolve()) in out


def test_cli_search_prints_manifest_only_images(tmp_path, monkeypatch, capsys, db_path):
    """分块正文没带标记时，靠知识条目的图片清单补文档级图片（标成「清单图片」）。"""
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir)
    code = _seed_image(db_path, archive_dir)
    fake = _ImageHitWeKnora({
        "knowledge_title": "连杆方案", "score": 0.9,
        "content": "这一段没有图片标记",
        "knowledge_custom_metadata": f"图片: {code}",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(db_path))

    out = capsys.readouterr().out
    assert f"清单图片 {code}" in out
    assert str((archive_dir / f"images/{code}.png").resolve()) in out


def test_cli_search_degrades_when_master_missing(tmp_path, monkeypatch, capsys, db_path):
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir)
    code = _seed_image(db_path, archive_dir)
    (archive_dir / f"images/{code}.png").unlink()
    fake = _ImageHitWeKnora({
        "knowledge_title": "连杆方案", "score": 0.9,
        "content": f"见 {{{{{code}}}}}",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(db_path))

    out = capsys.readouterr().out
    assert "母本缺失" in out and code in out
    assert "连杆方案" in out  # 文本命中照常打印


def test_cli_search_without_truth_source_still_prints_hits(tmp_path, monkeypatch, capsys, db_path):
    fake = _ImageHitWeKnora({
        "knowledge_title": "连杆方案", "score": 0.9,
        "content": "见 {{IMG-0001}}",
        "knowledge_custom_metadata": "图片: IMG-0001",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)
    missing = tmp_path / "nope" / "truth.db"

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(missing))

    out = capsys.readouterr().out
    assert "连杆方案" in out and "IMG-0001" in out
    assert "真相源不可用" in out  # 每条图给原因，不额外打一行全局提示
    assert not missing.exists()  # 搜索不该凭空建库


def test_cli_index_reports_manifest_write_failure(tmp_path, monkeypatch, capsys, db_path):
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir,
                        md_text="# 连杆方案\n\n{{IMG-0001}} 间隙 0.02mm。")

    class _IndexFake(_FakeWeKnora):
        def embedding_model_id(self, preferred=None):
            return "emb-1"

        def find_kb(self, name):
            return None

        def create_kb(self, name, embedding_model_id=None):
            return "kb-1"

        def delete_kb(self, kb_id):
            pass

        def ensure_tag(self, kb_id, name):
            return f"t-{name}"

        def create_knowledge(self, kb_id, title, content, tag_ids=()):
            return "k-1"

        def set_knowledge_metadata(self, knowledge_id, metadata):
            raise RuntimeError("WeKnora 500")

    monkeypatch.setattr(cli, "_weknora_client", lambda: _IndexFake())
    run_cli(monkeypatch, "index", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "图片清单写入失败" in out and "WeKnora 500" in out
    assert "推送 1 条知识" in out  # 清单失败不挡入库


def test_cli_index_dry_run_shows_document_image_count(tmp_path, monkeypatch, capsys, db_path):
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir,
                        md_text="# 连杆方案\n\n{{IMG-0001}} 与 {{IMG-0002}}。")
    run_cli(monkeypatch, "index", "--dry-run", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "图片 2" in out


def test_cli_search_prints_image_unit_own_master(tmp_path, monkeypatch, capsys, db_path):
    """命中图片条目（caption）时，条目本身就是这张图，直接给母本。"""
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir)
    code = _seed_image(db_path, archive_dir)
    fake = _ImageHitWeKnora({
        "knowledge_title": f"图片 {code}", "score": 0.9,
        "content": "一张连杆间隙测量截图",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(db_path))

    out = capsys.readouterr().out
    assert f"本图 {code}" in out
    assert str((archive_dir / f"images/{code}.png").resolve()) in out


def test_cli_search_reports_truncated_manifest(tmp_path, monkeypatch, capsys, db_path):
    archive_dir = db_path.parent / "archive"
    _seed_converted_doc(db_path, archive_dir)
    code = _seed_image(db_path, archive_dir)
    fake = _ImageHitWeKnora({
        "knowledge_title": "连杆方案", "score": 0.9,
        "content": "这一段没有图片标记",
        "knowledge_custom_metadata": f"图片: {code}\n图片数: 150",
    })
    monkeypatch.setattr(cli, "_weknora_client", lambda: fake)

    run_cli(monkeypatch, "search", "连杆间隙", "--db", str(db_path))

    out = capsys.readouterr().out
    assert "只列了前 1 张，共 150 张" in out
