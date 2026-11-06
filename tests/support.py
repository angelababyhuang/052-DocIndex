"""测试共用的小工厂：造样例文件、登记出现。

放在 tests/support.py（不是 conftest）：conftest 只放 fixture，普通函数显式 import，
读起来比「隐式注入一堆 helper」清楚。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from docarchive import imagelib, ingest


class FakeCaptioner:
    """测试用 captioner：记录调用次数，可选择抛错；绝不联网。"""

    def __init__(self, text="一张蓝色方框的截图，上面写着 SAMPLE。", error=None):
        self.text = text
        self.error = error
        self.calls = 0

    def describe(self, data: bytes, ext: str | None = None) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.text


def make_png(path: Path, text: str = "SCREEN") -> Path:
    """白底黑字小图（给内嵌图/独立图/占位符用例）。没装 Pillow 就跳过该用例。"""
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (160, 80), "white")
    ImageDraw.Draw(im).text((12, 30), text, fill="black")
    im.save(path)
    return path


def make_video(path: Path, payload: bytes = b"\x00\x00\x00\x18ftypmp42fake") -> Path:
    """假视频字节（不需要真能解码，只用于登记/去重/时长解析用例）。"""
    path.write_bytes(payload)
    return path


def make_mp4_with_mvhd(timescale: int = 1000, duration: int = 5000) -> bytes:
    """最小可解析的 MP4：ftyp + moov/mvhd，用来测时长解析。"""
    body = (b"\x00\x00\x00\x00" + b"\x00" * 8 + timescale.to_bytes(4, "big")
            + duration.to_bytes(4, "big") + b"\x00" * 80)
    mvhd = (len(body) + 8).to_bytes(4, "big") + b"mvhd" + body
    moov = (len(mvhd) + 8).to_bytes(4, "big") + b"moov" + mvhd
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"mp42" + b"\x00\x00\x00\x00"
    return ftyp + moov


def make_pending(conn, src: Path, file_name: str | None = None,
                 source_type: str = "wechat"):
    """登记一条出现，返回它的行（测 triage/转换/索引都用这个当起点）。"""
    data = src.read_bytes()
    ingest.register(conn, source_type, str(src), None, file_name or src.name,
                    len(data), hashlib.sha256(data).hexdigest(), None)
    return conn.execute("SELECT * FROM occurrences ORDER BY id DESC LIMIT 1").fetchone()


def register_fake_image(conn, archive_dir, data=b"fake-png-bytes"):
    """登记一张假图片（不依赖 Pillow），返回编号——图片回指/母本解析用例共用。"""
    reg = imagelib.register_image(conn, archive_dir, data, ext=".png")
    conn.commit()
    return reg
