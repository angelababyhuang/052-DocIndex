"""媒体实体登记：独立图（IMG-####）与视频（VID-####）。

独立图 = 不是从文档里抽出来的图（微信截图、照片等），直接进图片库并分配编号；
内嵌图的登记在 convert.py。两者共用 imagelib 的 sha256 去重、编号与 caption 补写：
同一张图永远只有一个 IMG-ID（重复登记复用，不新建、不改来源）。
视频第一阶段不抽帧、不自动概述，简介由人填。

项目关联沿用文档的语义：主归属（images.project_id / videos.project_id）+ 关联
项目标签表；主归属一旦定下，后来的登记不会把它改掉。
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docarchive import db, fingerprint, imagelib
from docarchive.caption import Captioner

VIDEOS_SUBDIR = "videos"


@dataclass(frozen=True)
class ImageReceipt:
    """独立图登记回执（caption_error 非空表示图进了库但描述没生成）。"""

    code: str
    created: bool
    caption: str | None = None
    caption_error: str | None = None


@dataclass(frozen=True)
class VideoReceipt:
    code: str
    created: bool
    intro: str | None = None
    duration_seconds: float | None = None


def register_standalone_image(conn: sqlite3.Connection, archive_dir: str | Path,
                              src: str | Path, *, project: str | None = None,
                              tags: Iterable[str] = (),
                              captioner: Captioner | None = None,
                              recaption: bool = False) -> ImageReceipt:
    """登记一张独立图：去重分配 IMG-ID、落盘母本、挂项目/标签、生成 caption。

    重复登记同一张图时复用原编号：已有 caption 就不再调用模型（省一次推理），
    没有（或 recaption=True）才补/重生成。caption 生成失败只回执错误，不影响入库。
    caption 的网络调用在 commit 之后，避免长时间占着 SQLite 写锁。
    """
    archive_dir = Path(archive_dir)
    src = Path(src)
    data = src.read_bytes()

    reg = imagelib.register_image(
        conn, archive_dir, data, ext=src.suffix, source=str(src),
        original_name=src.name,
    )
    _apply_project(conn, "images", "image_project_tags", "image_id", reg.id,
                   project, tags)
    conn.commit()  # 先落库释放写锁，模型调用放到事务外

    result = imagelib.ensure_caption(conn, reg.id, data, src.suffix,
                                     captioner, force=recaption)
    conn.commit()
    return ImageReceipt(reg.code, reg.created, result.text, result.error)


def register_video(conn: sqlite3.Connection, archive_dir: str | Path,
                   src: str | Path, *, project: str | None = None,
                   tags: Iterable[str] = (), intro: str | None = None) -> VideoReceipt:
    """登记一个视频：去重分配 VID-ID、落盘母本、记项目/标签/时长、填简介。

    同一份视频重复登记复用原编号；已有简介不被后来的登记覆盖（要改走 set_video_intro）。
    """
    archive_dir = Path(archive_dir)
    src = Path(src)
    with src.open("rb") as f:  # 视频可能很大：按流算哈希，不整个读进内存
        sha = fingerprint.sha256_stream(f)

    row = conn.execute("SELECT * FROM videos WHERE sha256=?", (sha,)).fetchone()
    if row:
        vid, code, created = row["id"], row["code"], False
    else:
        # 时长要跑 ffprobe（最长 30s）：放在 next_code 之前，别让它占着写锁
        duration = _video_duration(src)
        code = db.next_code(conn, "VID")
        rel = imagelib.store_master_file(archive_dir, VIDEOS_SUBDIR, code,
                                         src.suffix, src)
        cur = conn.execute(
            "INSERT INTO videos(code, name, sha256, byte_size, ext, original_name, "
            "source, path, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, src.stem, sha, src.stat().st_size,
             (src.suffix or ".bin").lower(), src.name, str(src), rel, duration),
        )
        vid, created = cur.lastrowid, True

    _apply_project(conn, "videos", "video_project_tags", "video_id", vid,
                   project, tags)
    if intro and not _current_intro(conn, vid):
        conn.execute("UPDATE videos SET intro=? WHERE id=?", (intro, vid))
    conn.commit()
    video = conn.execute("SELECT intro, duration_seconds FROM videos WHERE id=?",
                         (vid,)).fetchone()
    return VideoReceipt(code, created, video["intro"], video["duration_seconds"])


def set_video_intro(conn: sqlite3.Connection, code: str, intro: str) -> None:
    """改/补视频简介（人填字段，随时可改）。"""
    cur = conn.execute("UPDATE videos SET intro=? WHERE code=?", (intro, code))
    if cur.rowcount == 0:
        raise ValueError(f"视频 {code} 不存在")
    conn.commit()


def overview(conn: sqlite3.Connection, project: str | None = None,
             tag: str | None = None) -> dict[str, list[dict]]:
    """图片/视频总览（可按主归属项目或关联标签过滤），给 CLI 的 media 命令用。"""
    images = [
        {"code": r["code"], "name": r["name"], "caption": r["text"],
         "project": r["project"], "tags": r["tags"]}
        for r in _overview_rows(conn, "images", "image_project_tags", "image_id",
                                "caption", "name", project, tag)
    ]
    videos = [
        {"code": r["code"], "name": r["name"], "intro": r["text"],
         "project": r["project"], "tags": r["tags"]}
        for r in _overview_rows(conn, "videos", "video_project_tags", "video_id",
                                "intro", "name", project, tag)
    ]
    return {"images": images, "videos": videos}


def _overview_rows(conn, table: str, tag_table: str, tag_column: str,
                   text_column: str, name_column: str,
                   project: str | None, tag: str | None) -> list[dict]:
    sql = (
        f"SELECT t.code, t.{text_column} AS text, t.{name_column} AS name, "
        "p.name AS project, "
        f"(SELECT group_concat(p2.name, ' / ') FROM {tag_table} g "
        f"  JOIN projects p2 ON p2.id = g.project_id WHERE g.{tag_column} = t.id) AS tags "
        f"FROM {table} t LEFT JOIN projects p ON p.id = t.project_id"
    )
    where, params = [], []
    if project:
        where.append("p.name = ?")
        params.append(project)
    if tag:
        where.append(
            f"t.id IN (SELECT g.{tag_column} FROM {tag_table} g "
            "JOIN projects p3 ON p3.id = g.project_id WHERE p3.name = ?)"
        )
        params.append(tag)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.id"
    return [dict(r) for r in conn.execute(sql, params)]


# ---------------- 内部 ----------------

def _apply_project(conn: sqlite3.Connection, table: str, tag_table: str,
                   tag_column: str, entity_id: int, project: str | None,
                   tags: Iterable[str]) -> None:
    """设主归属（只在为空时设）+ 写关联项目标签。

    table / tag_table / tag_column 只由本模块的调用点传字面量，不接用户输入。
    """
    if project:
        row = conn.execute(
            f"SELECT project_id FROM {table} WHERE id=?", (entity_id,)
        ).fetchone()
        if row["project_id"] is None:  # 先判空再建项目，别为没用的归属建孤儿项目
            pid = db.get_or_create_project(conn, project)
            conn.execute(
                f"UPDATE {table} SET project_id=? WHERE id=? AND project_id IS NULL",
                (pid, entity_id),
            )
    for tag in tags:
        tid = db.get_or_create_project(conn, tag)
        conn.execute(
            f"INSERT OR IGNORE INTO {tag_table}({tag_column}, project_id) VALUES (?, ?)",
            (entity_id, tid),
        )


def _current_intro(conn: sqlite3.Connection, video_id: int) -> str | None:
    row = conn.execute("SELECT intro FROM videos WHERE id=?", (video_id,)).fetchone()
    return row["intro"] if row else None


def _video_duration(path: Path) -> float | None:
    """视频时长（秒）：先试 ffprobe；没有它就读文件头解析 MP4/MOV 的 mvhd。

    mvhd 在文件头的（faststart）能解析；moov 在尾部的老式 MP4 只能靠 ffprobe。
    两样都不行就留空——时长是展示字段，不值得为它引入 ffmpeg 依赖。
    """
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return round(float(out.stdout.strip()), 3)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    try:
        with path.open("rb") as f:
            head = f.read(8 << 20)
    except OSError:
        return None
    return _mp4_duration(head)


def _mp4_duration(data: bytes) -> float | None:
    """从 ISO-BMFF（mp4/mov）的 moov/mvhd 里读时长，解析不了返回 None。"""
    def boxes(buf: bytes, start: int, end: int):
        i = start
        while i + 8 <= end:
            size = int.from_bytes(buf[i:i + 4], "big")
            kind = buf[i + 4:i + 8]
            header = 8
            if size == 1:  # 64 位长度
                if i + 16 > end:
                    return
                size = int.from_bytes(buf[i + 8:i + 16], "big")
                header = 16
            elif size == 0:  # 一直到文件尾
                size = end - i
            if size < header:
                return
            yield kind, i + header, i + size
            i += size

    for kind, start, end in boxes(data, 0, len(data)):
        if kind != b"moov":
            continue
        for inner, s, e in boxes(data, start, end):
            if inner != b"mvhd" or s + 20 > e:
                continue
            if data[s] == 1:  # version 1：64 位时间
                timescale = int.from_bytes(data[s + 20:s + 24], "big")
                duration = int.from_bytes(data[s + 24:s + 32], "big")
            else:
                timescale = int.from_bytes(data[s + 12:s + 16], "big")
                duration = int.from_bytes(data[s + 16:s + 20], "big")
            return round(duration / timescale, 3) if timescale else None
    return None
