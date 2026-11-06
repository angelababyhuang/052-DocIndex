"""真相源 → WeKnora 派生索引。

WeKnora 里的知识库、标签、分块、向量全是**派生**的：删了能从真相源重建，
所以这里不保存任何真相，只负责推。

两点设计取舍（见 docs/adr/0003）：
1. **切「知识条目」而不是切分块**：一份文档（现行版本的 md）、一张图（caption）、
   一个视频（简介）各成一条；真正分块交给 WeKnora——它要做父子块和上下文补齐，
   我们在外面先切碎只会破坏它的检索上下文。
2. **标签四维**（项目/类别/图片/视频）编码在标签名里：`项目/浮标项目`、
   `类别/测试`、`图片`、`视频`。WeKnora 的标签是平的，维度靠命名约定表达。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from docarchive.archive import CATEGORIES

# 标签维度前缀
TAG_PROJECT = "项目"
TAG_CATEGORY = "类别"
TAG_IMAGE = "图片"
TAG_VIDEO = "视频"


@dataclass(frozen=True)
class Unit:
    """一条要推进 WeKnora 的知识条目。"""

    kind: str          # document | image | video
    key: str           # 真相源里的稳定标识：doc-<document_id> / IMG-0001 / VID-0001
    title: str
    content: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class IndexReport:
    kb_id: str
    units: int
    tags: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()


class IndexExistsError(RuntimeError):
    """同名知识库已存在：要么换名字，要么显式 --rebuild 重建。"""


class IndexClient(Protocol):
    """推索引要的 WeKnora 能力。"""

    def find_kb(self, name: str) -> str | None: ...

    def create_kb(self, name: str, embedding_model_id: str | None = None) -> str: ...

    def delete_kb(self, kb_id: str) -> None: ...

    def ensure_tag(self, kb_id: str, name: str) -> str: ...

    def create_knowledge(self, kb_id: str, title: str, content: str,
                         tag_ids: Iterable[str] = ()) -> str: ...


class SearchClient(Protocol):
    """检索要的 WeKnora 能力（比推索引少得多）。"""

    def find_kb(self, name: str) -> str | None: ...

    def tag_ids_for(self, kb_id: str, names: Iterable[str]) -> list[str]: ...

    def search(self, query: str, kb_ids: Iterable[str],
               tag_ids: Iterable[str] = (), match_count: int = 5) -> list[dict]: ...


def collect_units(conn: sqlite3.Connection, archive_dir: str | Path) -> list[Unit]:
    """把真相源切成知识条目（文档现行版本 / 图片 / 视频）。

    没有 md 的文档跳过（没正文可索引）；图片和视频即使没有 caption/简介也会进，
    正文退化成「编号 + 名称」，这样按编号/项目仍能命中（spec §7.1 的承诺）。
    """
    archive_dir = Path(archive_dir)
    units: list[Unit] = []
    units += _document_units(conn, archive_dir)
    units += _image_units(conn)
    units += _video_units(conn)
    return units


def plan_units(units: Iterable[Unit]) -> IndexReport:
    """只算「会推什么、会建哪些标签」，不碰网络（CLI 的 --dry-run）。"""
    units = list(units)
    return IndexReport(kb_id="", units=len(units), tags=planned_tags(units))


def planned_tags(units: Iterable[Unit]) -> tuple[str, ...]:
    """真实运行会建的全套标签：五个类别 + 图片/视频两个维度 + 条目自己的标签。"""
    seen: list[str] = [f"{TAG_CATEGORY}/{c}" for c in CATEGORIES]
    seen += [TAG_IMAGE, TAG_VIDEO]
    for u in units:
        for tag in u.tags:
            if tag not in seen:
                seen.append(tag)
    return tuple(seen)


def index_units(units: Iterable[Unit], client: IndexClient, *, kb_name: str,
                embedding_model_id: str | None = None,
                rebuild: bool = False) -> IndexReport:
    """把知识条目推进 WeKnora：建 KB、建标签、逐条创建知识。

    同名 KB 已存在时默认报错（防止重复推送把库塞脏），要重来就 rebuild=True
    （删掉同名 KB 从零重建——索引本来就是派生物）。
    """
    units = list(units)
    kb_id = client.find_kb(kb_name)
    if kb_id is not None:
        if not rebuild:
            raise IndexExistsError(
                f"知识库「{kb_name}」已存在（{kb_id}）；要重建请加 --rebuild"
            )
        client.delete_kb(kb_id)
        kb_id = None
    if kb_id is None:
        kb_id = client.create_kb(kb_name, embedding_model_id)

    tag_ids = {name: client.ensure_tag(kb_id, name) for name in planned_tags(units)}
    kids = [
        client.create_knowledge(kb_id, unit.title, unit.content,
                                [tag_ids[t] for t in unit.tags])
        for unit in units
    ]
    return IndexReport(kb_id=kb_id, units=len(units), tags=tuple(tag_ids),
                       knowledge_ids=tuple(kids))


# ---------------- 真相源 → 条目 ----------------

def _project_tags(conn: sqlite3.Connection, tag_table: str, column: str,
                  entity_id: int, primary: int | None) -> tuple[str, ...]:
    """主归属 + 关联项目标签 → 统一的 `项目/<名>` 标签（去重、保序）。

    table / column 只由本模块的调用点传字面量，不接用户输入。
    """
    names: list[str] = []
    if primary is not None:
        row = conn.execute("SELECT name FROM projects WHERE id=?", (primary,)).fetchone()
        if row:
            names.append(row["name"])
    for row in conn.execute(
        f"SELECT p.name FROM {tag_table} t JOIN projects p ON p.id = t.project_id "
        f"WHERE t.{column} = ? ORDER BY p.name",
        (entity_id,),
    ):
        names.append(row["name"])
    seen: list[str] = []
    for name in names:
        tag = f"{TAG_PROJECT}/{name}"
        if tag not in seen:
            seen.append(tag)
    return tuple(seen)


def _document_units(conn: sqlite3.Connection, archive_dir: Path) -> list[Unit]:
    rows = conn.execute(
        "SELECT d.id, d.title, d.category, d.current_version_id, d.project_id, "
        "       v.markdown_path "
        "FROM documents d JOIN versions v ON v.id = d.current_version_id "
        "WHERE v.markdown_path IS NOT NULL ORDER BY d.id"
    ).fetchall()
    units: list[Unit] = []
    for r in rows:
        md_file = archive_dir / r["markdown_path"]
        if not md_file.is_file():
            continue
        content = md_file.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue
        tags = list(_project_tags(conn, "document_project_tags",
                                  "document_id", r["id"], r["project_id"]))
        if r["category"]:
            tags.append(f"{TAG_CATEGORY}/{r['category']}")
        units.append(Unit("document", f"doc-{r['id']}", r["title"], content,
                          tuple(tags)))
    return units


def _image_units(conn: sqlite3.Connection) -> list[Unit]:
    return _media_units(conn, table="images", tag_table="image_project_tags",
                        tag_column="image_id", tag=TAG_IMAGE, kind="image",
                        label="图片", text_column="caption", empty_note="无描述")


def _video_units(conn: sqlite3.Connection) -> list[Unit]:
    return _media_units(conn, table="videos", tag_table="video_project_tags",
                        tag_column="video_id", tag=TAG_VIDEO, kind="video",
                        label="视频", text_column="intro", empty_note="暂无简介")


def _media_units(conn: sqlite3.Connection, *, table: str, tag_table: str,
                 tag_column: str, tag: str, kind: str, label: str,
                 text_column: str, empty_note: str) -> list[Unit]:
    """图片/视频共用的条目构造：主文本缺失时退化成「名称（编号，说明）」。

    table / column 只由本模块的调用点传字面量，不接用户输入。
    """
    rows = conn.execute(
        f"SELECT id, code, name, original_name, {text_column} AS text, project_id "
        f"FROM {table} ORDER BY id"
    ).fetchall()
    units: list[Unit] = []
    for r in rows:
        tags = list(_project_tags(conn, tag_table, tag_column, r["id"], r["project_id"]))
        tags.append(tag)
        code = r["code"]
        text = (r["text"] or "").strip()
        if not text:
            who = r["name"] or r["original_name"] or code
            text = f"{who}（{label} {code}，{empty_note}）"
        units.append(Unit(kind, code, f"{label} {code}", text, tuple(tags)))
    return units
