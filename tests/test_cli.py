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
