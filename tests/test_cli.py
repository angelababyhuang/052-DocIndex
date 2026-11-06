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
