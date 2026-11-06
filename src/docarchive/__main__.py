"""CLI 入口：python -m docarchive <命令> ...

命令：
  migrate <014-track.db> <目标.db> [--verify]   014 → 052 全量只读迁移
  scan <目录> [--db 路径] [--source wechat] [--since YYYY-MM-DD]   扫描登记出现
"""
import argparse
from pathlib import Path

from docarchive import db, ingest, migrate


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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

