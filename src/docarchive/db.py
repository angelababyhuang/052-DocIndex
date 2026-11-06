"""团队文档归档系统 — 真相源 schema 与数据访问层。

词汇见 GLOSSARY.md。设计决策见 docs/spec.md 与 014 的 docs/adr/。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  current_version_id INTEGER,
  category TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  byte_hash TEXT,
  text_fp TEXT,
  archive_path TEXT,
  markdown_path TEXT,                 -- 转换出的 md（相对受管目录，派生自 archive_path）
  note TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS occurrences(
  id INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL,          -- wechat | zip | feishu | manual
  source_path TEXT NOT NULL,
  source_entry TEXT,
  file_name TEXT NOT NULL,
  ext TEXT,
  size INTEGER,
  byte_hash TEXT,
  text_fp TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|archived|merged|ignored
  document_id INTEGER REFERENCES documents(id),
  version_id INTEGER REFERENCES versions(id),
  note TEXT,
  registered_at TEXT DEFAULT (datetime('now','localtime')),
  triaged_at TEXT);
CREATE INDEX IF NOT EXISTS ix_occ_status ON occurrences(status);
CREATE INDEX IF NOT EXISTS ix_occ_bh ON occurrences(byte_hash);
CREATE INDEX IF NOT EXISTS ix_occ_fp ON occurrences(text_fp);

-- 图片：独立顶层实体，IMG-#### 按内容 sha256 去重后顺序分配、永不改号
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,          -- IMG-0001
  name TEXT,                          -- 显示名，可空可改
  sha256 TEXT UNIQUE NOT NULL,        -- 内容哈希，去重键
  byte_size INTEGER,
  ext TEXT,
  original_name TEXT,
  source TEXT,                        -- 溯源：从哪个文件抽出/哪个微信文件
  project_id INTEGER REFERENCES projects(id),  -- 单归属项目（可空，关联项目另存标签表）
  caption TEXT,                       -- 图片描述（VLM 生成）
  width INTEGER,
  height INTEGER,
  path TEXT,                          -- 二进制存储位置（文件系统路径或 COS URL）
  created_at TEXT DEFAULT (datetime('now','localtime')));

-- 视频：独立顶层实体，VID-####，第一阶段人填简介
CREATE TABLE IF NOT EXISTS videos(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,          -- VID-0001
  name TEXT,
  sha256 TEXT UNIQUE NOT NULL,
  byte_size INTEGER,
  ext TEXT,
  original_name TEXT,
  source TEXT,
  intro TEXT,                         -- 人填简介
  duration_seconds REAL,
  project_id INTEGER REFERENCES projects(id),  -- 主归属项目
  path TEXT,                          -- 二进制存储位置（文件系统路径或 COS URL）
  created_at TEXT DEFAULT (datetime('now','localtime')));

-- 文档↔图片 关联：区分内嵌/独立，内嵌记位置
CREATE TABLE IF NOT EXISTS document_images(
  document_id INTEGER NOT NULL REFERENCES documents(id),
  image_id INTEGER NOT NULL REFERENCES images(id),
  kind TEXT NOT NULL DEFAULT 'embedded',  -- embedded | standalone
  position TEXT,                      -- 内嵌图在文档中的位置（可空）
  PRIMARY KEY (document_id, image_id));

-- 关联项目标签：文档单归属(project_id)之外的关联项目
CREATE TABLE IF NOT EXISTS document_project_tags(
  document_id INTEGER NOT NULL REFERENCES documents(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  PRIMARY KEY (document_id, project_id));

-- 图片/视频的关联项目标签：媒体实体与文档平级，单归属之外可挂多个项目
CREATE TABLE IF NOT EXISTS image_project_tags(
  image_id INTEGER NOT NULL REFERENCES images(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  PRIMARY KEY (image_id, project_id));

CREATE TABLE IF NOT EXISTS video_project_tags(
  video_id INTEGER NOT NULL REFERENCES videos(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  PRIMARY KEY (video_id, project_id));

-- 号段计数器：保证 IMG/VID 编号单调递增、删除不回收
CREATE TABLE IF NOT EXISTS code_counters(
  prefix TEXT PRIMARY KEY,            -- 'IMG' | 'VID'
  last_num INTEGER NOT NULL DEFAULT 0);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（或创建）真相源数据库，应用 schema。"""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_columns(conn)
    return conn


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """只读打开既有真相源（搜索回指图片用）。

    不应用 schema、不建库：读命令不该顺手造出一个空真相源。文件不存在时
    sqlite3 会抛 OperationalError，由调用方决定怎么降级。
    """
    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """幂等列迁移：给既有库补上后加的列（images.path/project_id、videos 两列、versions.markdown_path）。"""
    def _cols(table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    if "path" not in _cols("images"):
        conn.execute("ALTER TABLE images ADD COLUMN path TEXT")
    if "path" not in _cols("videos"):
        conn.execute("ALTER TABLE videos ADD COLUMN path TEXT")
    if "project_id" not in _cols("images"):
        conn.execute("ALTER TABLE images ADD COLUMN project_id INTEGER REFERENCES projects(id)")
    if "markdown_path" not in _cols("versions"):
        conn.execute("ALTER TABLE versions ADD COLUMN markdown_path TEXT")
    if "project_id" not in _cols("videos"):
        conn.execute("ALTER TABLE videos ADD COLUMN project_id INTEGER REFERENCES projects(id)")
    conn.commit()


def get_or_create_project(conn: sqlite3.Connection, name: str) -> int:
    """按名取项目 id，不存在则新建。不 commit（跟 next_code 一样由调用方定事务）。"""
    row = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO projects(name) VALUES (?)", (name,)).lastrowid


def set_primary_project(conn: sqlite3.Connection, table: str, entity_id: int,
                        project_name: str | None) -> None:
    """按项目名设主归属，**只在为空时设**（后来的登记不改已定的归属）。

    名不存在就建项目；但如果实体的主归属已经定了，就不白建一个孤儿项目。
    table 只由调用点传字面量，不接用户输入。不 commit。
    """
    if not project_name:
        return
    row = conn.execute(
        f"SELECT project_id FROM {table} WHERE id=?", (entity_id,)
    ).fetchone()
    if row is None or row["project_id"] is not None:
        return
    pid = get_or_create_project(conn, project_name)
    conn.execute(
        f"UPDATE {table} SET project_id=? WHERE id=? AND project_id IS NULL",
        (pid, entity_id),
    )


def next_code(conn: sqlite3.Connection, prefix: str, width: int = 4) -> str:
    """分配下一个 entity code（如 IMG-0001 / VID-0002）。

    单调递增，永不改号、永不复用（删除后不回收）。号段计数器独立于实体表，
    因此删除实体不会让号段回退。

    不 commit：由调用方在自己的事务边界内统一提交，保证「分配号段 + 插入实体」
    要么一起落库、要么一起回滚。号段与实体同事务时，实体插入失败会让号段也回滚，
    不会出现「实体没了但号段已消耗」的跳号。
    """
    # UPSERT：已有则 +1，首次插入从 1 起步
    conn.execute(
        "INSERT INTO code_counters(prefix, last_num) VALUES (?, 1) "
        "ON CONFLICT(prefix) DO UPDATE SET last_num = last_num + 1",
        (prefix,),
    )
    row = conn.execute(
        "SELECT last_num FROM code_counters WHERE prefix=?", (prefix,)
    ).fetchone()
    return f"{prefix}-{row['last_num']:0{width}d}"
