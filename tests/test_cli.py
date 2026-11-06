"""CLI 冒烟：命令能跑通、落库、有输出（#14 的 register-image/register-video/set-intro/media）。"""
import sys

import pytest

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
    assert "新登记 1 条" in out and "a.txt" not in out

    run_cli(monkeypatch, "scan", str(inbox), "--all", "--db", str(db_path))
    out = capsys.readouterr().out
    assert "新登记 1 条" in out and "a.txt" in out


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
    assert "新登记 0 条" in out
    assert "备份" in out and "--all" in out


def test_cli_scan_date_dir_as_root_works(tmp_path, monkeypatch, capsys, db_path):
    d = tmp_path / "2026-11-05"
    d.mkdir(parents=True)
    (d / "a.txt").write_text("x")
    run_cli(monkeypatch, "scan", str(d), "--since", "2026-11-04", "--db", str(db_path))
    assert "新登记 1 条" in capsys.readouterr().out


# ---------- #15：index / search ----------

def _seed_converted_doc(db_path, archive_dir):
    from docarchive import db as appdb

    conn = appdb.connect(db_path)
    conn.execute("INSERT INTO projects(name) VALUES ('浮标项目')")
    conn.execute(
        "INSERT INTO documents(title, project_id, category, current_version_id) "
        "VALUES ('连杆方案', 1, '测试', 1)"
    )
    md = archive_dir / "doc-1" / "v1-a.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# 连杆方案\n\n连杆间隙复测 0.02mm。", encoding="utf-8")
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
