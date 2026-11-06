"""CLI 入口：python -m docarchive <命令> ...

命令：
  migrate <014-track.db> <目标.db> [--verify]   014 → 052 全量只读迁移
  scan <目录> [--db 路径] [--source wechat] [--since YYYY-MM-DD]   扫描登记出现
  new-doc <出现ID> --project <项目> [--title 标题] [--category 类别]  确认为新文档
  new-version <出现ID> --doc <文档ID>   确认为某文档新版本
  merge <出现ID> --doc <文档ID> [--force]   确认为重复
  ignore <出现ID>   确认为忽略
  convert <版本ID>...   把版本转成 md（抽内嵌图入图片库）
"""
import argparse
from pathlib import Path

from docarchive import archive, convert, db, ingest, migrate


def cmd_migrate(args):
    conn = db.connect(args.dst_db)
    stats = migrate.migrate(args.src_db, conn)
    print("迁移完成:", stats)
    if args.verify:
        diffs = migrate.verify_fingerprints(args.src_db)
        print(f"指纹复算差异: {len(diffs)} 条")
        for d in diffs:
            print(f"  occ#{d['occurrence_id']} [{d['field']}] {d['path']}")
    conn.close()


def cmd_scan(args):
    conn = db.connect(args.db)
    n = ingest.scan_dir(conn, args.dir, source_type=args.source, since=args.since)
    print(f"扫描 {args.dir} → 新登记 {n} 条出现")
    # 给每条 pending 出现打印去重建议
    for row in conn.execute(
        "SELECT id, file_name, byte_hash, text_fp FROM occurrences "
        "WHERE status='pending' ORDER BY id"
    ):
        s = ingest.suggestion(conn, row["byte_hash"], row["text_fp"],
                              row["file_name"], exclude_id=row["id"])
        print(f"  #{row['id']} {row['file_name']} → {s}")
    conn.close()


def _archive_dir(db_path: Path) -> Path:
    return db_path.parent / "archive"


def cmd_new_doc(args):
    conn = db.connect(args.db)
    doc_id, vid = archive.new_doc(conn, args.id, args.project, _archive_dir(args.db),
                                  title=args.title, category=args.category)
    print(f"出现 #{args.id} → 新文档 #{doc_id}（现行版本 v{vid}）")
    conn.close()


def cmd_new_version(args):
    conn = db.connect(args.db)
    vid = archive.new_version(conn, args.id, args.doc, _archive_dir(args.db))
    print(f"出现 #{args.id} → 文档 #{args.doc} 的新版本 v{vid}（现行）")
    conn.close()


def cmd_merge(args):
    conn = db.connect(args.db)
    vid = archive.merge(conn, args.id, args.doc, force=args.force)
    print(f"出现 #{args.id} → 已并（文档 #{args.doc} 版本 v{vid} 的重复）")
    conn.close()


def cmd_ignore(args):
    conn = db.connect(args.db)
    archive.ignore(conn, args.id, note=args.reason)
    print(f"出现 #{args.id} → 已忽略")
    conn.close()


def cmd_convert(args):
    conn = db.connect(args.db)
    adir = _archive_dir(args.db)
    for vid in args.version_id:
        r = convert.convert_version(conn, vid, adir)
        print(f"版本 #{vid} → {r.markdown_path}（图片 {r.images_total} 张，"
              f"新入图片库 {r.images_new} 张）")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="团队文档归档系统 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("migrate", help="014 → 052 全量只读迁移")
    p.add_argument("src_db", type=Path, help="014 的 track.db 路径")
    p.add_argument("dst_db", type=Path, help="目标真相源数据库路径")
    p.add_argument("--verify", action="store_true", help="迁移后复算指纹并报告差异")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("scan", help="扫描目录登记出现")
    p.add_argument("dir", type=Path, help="要扫描的目录")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"),
                   help="目标真相源数据库路径")
    p.add_argument("--source", default="wechat", help="来源类型（默认 wechat）")
    p.add_argument("--since", default=None, help="只处理日期子目录 ≥ 此日期（YYYY-MM-DD）")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("new-doc", help="确认一条出现为新文档")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--project", required=True, help="项目名")
    p.add_argument("--title", default=None, help="文档标题（默认取文件名去扩展名）")
    p.add_argument("--category", default=None, help=f"类别：{'/'.join(archive.CATEGORIES)}")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"))
    p.set_defaults(func=cmd_new_doc)

    p = sub.add_parser("new-version", help="确认一条出现为某文档新版本")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--doc", type=int, required=True, help="目标文档 ID")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"))
    p.set_defaults(func=cmd_new_version)

    p = sub.add_parser("merge", help="确认一条出现为重复")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--doc", type=int, required=True, help="目标文档 ID")
    p.add_argument("--force", action="store_true", help="跳过指纹校验强制并入")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"))
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("ignore", help="确认一条出现为忽略")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--reason", default=None, help="忽略原因")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"))
    p.set_defaults(func=cmd_ignore)

    p = sub.add_parser("convert", help="版本转 md + 抽内嵌图入图片库")
    p.add_argument("version_id", type=int, nargs="+", help="版本 ID（可多个）")
    p.add_argument("--db", type=Path, default=Path("data/truth.db"))
    p.set_defaults(func=cmd_convert)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

