"""CLI 入口：python -m docarchive <命令> ...

命令：
  migrate <014-track.db> <目标.db> [--verify]   014 → 052 全量只读迁移
"""
import argparse
from pathlib import Path

from docarchive import db, migrate


def cmd_migrate(args):
    conn = db.connect(args.dst_db)
    stats = migrate.migrate(args.src_db, conn)
    print("迁移完成:", stats)
    if args.verify:
        diffs = migrate.verify_fingerprints(args.src_db, conn)
        print(f"指纹复算差异: {len(diffs)} 条")
        for d in diffs:
            print(f"  occ#{d['occurrence_id']} [{d['field']}] {d['path']}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="团队文档归档系统 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("migrate", help="014 → 052 全量只读迁移")
    p.add_argument("src_db", type=Path, help="014 的 track.db 路径")
    p.add_argument("dst_db", type=Path, help="目标真相源数据库路径")
    p.add_argument("--verify", action="store_true", help="迁移后复算指纹并报告差异")
    p.set_defaults(func=cmd_migrate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

