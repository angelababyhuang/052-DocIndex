"""CLI 入口：python -m docarchive <命令> ...

各子命令与参数见 `python -m docarchive --help`；领域词汇见 GLOSSARY.md。
"""
import argparse
import contextlib
import sqlite3
from pathlib import Path

from docarchive import (
    archive,
    caption,
    convert,
    db,
    index,
    ingest,
    media,
    migrate,
    search,
    weknora,
)
from docarchive.env import merged

DEFAULT_DB = Path("data/truth.db")
DEFAULT_KB_NAME = "052 文档库"
# spec §7.2：embedding 模型从第一天定死，别让「取第一个」悄悄换模型
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"


# ---------------- 公共小工具 ----------------

@contextlib.contextmanager
def _open_db(path):
    """打开真相源并用完关闭（命令里不再手写 connect/close 配对）。"""
    conn = db.connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _add_db_option(parser):
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="真相源数据库路径")


def _archive_dir(db_path: Path) -> Path:
    return db_path.parent / "archive"


def _open_truth_readonly(db_path: Path):
    """只读打开真相源；文件不存在返回 None（搜索不该凭空建库）。"""
    if not Path(db_path).is_file():
        return None
    try:
        return db.connect_readonly(db_path)
    except sqlite3.Error:  # 库损坏/无权限：降级成纯文本检索
        return None


def _captioner(args):
    """--no-caption 时不给模型；否则按 .env 配一个（没有密钥就返回 None）。"""
    return None if args.no_caption else caption.captioner_from_env()


# ---------------- 命令 ----------------

def cmd_migrate(args):
    with _open_db(args.dst_db) as conn:
        stats = migrate.migrate(args.src_db, conn)
    print("迁移完成:", stats)
    if args.verify:
        diffs = migrate.verify_fingerprints(args.src_db)
        print(f"指纹复算差异: {len(diffs)} 条")
        for d in diffs:
            print(f"  occ#{d['occurrence_id']} [{d['field']}] {d['path']}")


def cmd_scan(args):
    cap = _captioner(args)
    with _open_db(args.db) as conn:
        try:
            stats = ingest.scan_dir(
                conn, args.dir, source_type=args.source, since=args.since,
                all_files=args.all_files, archive_dir=_archive_dir(args.db),
                project=args.project, tags=args.tag or (), captioner=cap,
            )
        except (ValueError, FileNotFoundError) as e:
            raise SystemExit(str(e))
        print(f"扫描 {args.dir} → 文档 {stats.occurrences} / "
              f"图片 {stats.images_seen}（新 {stats.images_new}）/ "
              f"视频 {stats.videos_seen}（新 {stats.videos_new}）")
        # 媒体进了库但描述没生成：逐条报出来（和 convert/register-image 同一句话）
        for err in stats.caption_errors:
            print(f"  ⚠ 描述生成失败：{err}")
        nothing = not (stats.occurrences or stats.images_seen or stats.videos_seen)
        if nothing and args.since and not args.all_files:
            skipped = [d.name for d in sorted(args.dir.iterdir())
                       if d.is_dir() and not ingest.is_date_dir(d.name)]
            if skipped:
                print(f"  提示：{'、'.join(skipped)} 不是标准日期目录，--since 不扫它们；"
                      f"要全量请加 --all")
        # 给每条 pending 出现打印去重建议（媒体不进这里，已直接入库）
        for row in conn.execute(
            "SELECT id, file_name, byte_hash, text_fp FROM occurrences "
            "WHERE status='pending' ORDER BY id"
        ):
            s = ingest.suggestion(conn, row["byte_hash"], row["text_fp"],
                                  row["file_name"], exclude_id=row["id"])
            print(f"  #{row['id']} {row['file_name']} → {s}")


def cmd_new_doc(args):
    with _open_db(args.db) as conn:
        try:
            doc_id, vid = archive.new_doc(conn, args.id, args.project,
                                          _archive_dir(args.db), title=args.title,
                                          category=args.category)
        except ValueError as e:  # 媒体文件 / 非法类别：给操作建议，不甩 traceback
            raise SystemExit(str(e))
    print(f"出现 #{args.id} → 新文档 #{doc_id}（现行版本 v{vid}）")


def cmd_new_version(args):
    with _open_db(args.db) as conn:
        vid = archive.new_version(conn, args.id, args.doc, _archive_dir(args.db))
    print(f"出现 #{args.id} → 文档 #{args.doc} 的新版本 v{vid}（现行）")


def cmd_merge(args):
    with _open_db(args.db) as conn:
        vid = archive.merge(conn, args.id, args.doc, force=args.force)
    print(f"出现 #{args.id} → 已并（文档 #{args.doc} 版本 v{vid} 的重复）")


def cmd_ignore(args):
    with _open_db(args.db) as conn:
        archive.ignore(conn, args.id, note=args.reason)
    print(f"出现 #{args.id} → 已忽略")


def cmd_convert(args):
    cap = _captioner(args)
    adir = _archive_dir(args.db)
    with _open_db(args.db) as conn:
        for vid in args.version_id:
            r = convert.convert_version(conn, vid, adir, captioner=cap,
                                        recaption=args.recaption)
            print(f"版本 #{vid} → {r.markdown_path}（图片 {r.images_total} 张，"
                  f"新入图片库 {r.images_new} 张，新描述 {r.captions_new} 条）")
            for err in r.caption_errors:
                print(f"  ⚠ 描述生成失败：{err}")


def cmd_register_image(args):
    cap = _captioner(args)
    with _open_db(args.db) as conn:
        r = media.register_standalone_image(
            conn, _archive_dir(args.db), args.file,
            project=args.project, tags=args.tag or (), captioner=cap,
            recaption=args.recaption,
        )
    state = "新入库" if r.created else "已存在，复用原编号"
    print(f"图片 {args.file} → {r.code}（{state}）")
    if r.caption:
        print(f"  描述：{r.caption}")
    elif r.caption_error:
        print(f"  ⚠ 描述生成失败：{r.caption_error}")
    elif cap is None:
        print("  （没生成描述：未配置 CAPTION_API_KEY / ZHIPU_API_KEY，或用 --no-caption 跳过）")


def cmd_register_video(args):
    with _open_db(args.db) as conn:
        r = media.register_video(
            conn, _archive_dir(args.db), args.file,
            project=args.project, tags=args.tag or (), intro=args.intro,
        )
    state = "新入库" if r.created else "已存在，复用原编号"
    print(f"视频 {args.file} → {r.code}（{state}）")
    if r.duration_seconds is not None:
        print(f"  时长：{r.duration_seconds:g} 秒")
    if r.intro:
        print(f"  简介：{r.intro}")


def cmd_set_intro(args):
    with _open_db(args.db) as conn:
        media.set_video_intro(conn, args.code, args.intro)
    print(f"{args.code} 简介已更新")


def cmd_media(args):
    with _open_db(args.db) as conn:
        ov = media.overview(conn, project=args.project, tag=args.tag)
    print(f"图片 {len(ov['images'])} 张")
    _print_media(ov["images"], "caption", "（无描述）")
    print(f"视频 {len(ov['videos'])} 个")
    _print_media(ov["videos"], "intro", "（无简介）")


def _print_media(rows: list[dict], text_key: str, empty_note: str) -> None:
    for r in rows:
        tags = f"  [{r['tags']}]" if r["tags"] else ""
        print(f"  {r['code']}  {r['name'] or '-'}  {r['project'] or '-'}{tags}"
              f"  {r[text_key] or empty_note}")


def _weknora_client():
    """按 .env 连 WeKnora 并登录（凭据只在内存里用，不落盘、不回显）。"""
    env = merged()
    base = env.get("WEKNORA_BASE_URL")
    if not base:
        raise SystemExit("缺少 WEKNORA_BASE_URL（写在 .env 里）")
    client = weknora.WeKnoraClient(
        base, email=env.get("WEKNORA_EMAIL"), password=env.get("WEKNORA_PASSWORD"),
    )
    try:
        client.login()
    except weknora.WeKnoraError as e:
        raise SystemExit(f"连 WeKnora 失败：{e}")
    return client


def cmd_index(args):
    with _open_db(args.db) as conn:
        units = index.collect_units(conn, _archive_dir(args.db))
    if args.dry_run:
        plan = index.plan_units(units)
        print(f"（dry-run）将推送 {plan.units} 条知识，标签 {len(plan.tags)} 个：")
        for u in units:
            imgs = f"  图片 {len(u.images)}" if u.images else ""
            print(f"  {u.kind:<8} {u.key:<12} {u.title}{imgs}")
        for t in plan.tags:
            print(f"  标签 {t}")
        return

    client = _weknora_client()
    model = args.embedding_model or DEFAULT_EMBEDDING_MODEL
    try:
        emb = client.embedding_model_id(model)
        if not emb:
            raise SystemExit(f"空间里没有名为「{model}」的 embedding 模型；"
                             f"用 --embedding-model 指定实际模型名")
        report = index.index_units(units, client, kb_name=args.kb_name,
                                   embedding_model_id=emb, rebuild=args.rebuild)
    except (index.IndexExistsError, weknora.WeKnoraError) as e:
        raise SystemExit(str(e))
    print(f"推送 {report.units} 条知识 → 知识库「{args.kb_name}」({report.kb_id})，"
          f"标签 {len(report.tags)} 个")
    for w in report.parse_warnings:
        print(f"  ⚠ {w}")
    for w in report.metadata_warnings:
        print(f"  ⚠ 知识元数据写入失败：{w}")
    if args.wait and report.knowledge_ids:
        print("等 WeKnora 解析…")
        try:
            statuses = client.wait_knowledge(report.knowledge_ids)
        except weknora.WeKnoraError as e:
            raise SystemExit(f"查解析状态失败：{e}")
        done = sum(1 for s in statuses.values() if s == "completed")
        failed = [k for k, s in statuses.items() if s == "failed"]
        print(f"解析完成 {done}/{len(statuses)}" + (f"，失败 {len(failed)} 条" if failed else ""))


def cmd_search(args):
    client = _weknora_client()
    # 标签允许只写项目名（和 media --tag 同一个口径），补上「项目/」前缀
    wanted = [t if "/" in t else f"{index.TAG_PROJECT}/{t}" for t in (args.tag or [])]
    try:
        kb_id = client.find_kb(args.kb_name)
        if not kb_id:
            raise SystemExit(f"知识库「{args.kb_name}」不存在，先跑 index")
        tag_ids = client.tag_ids_for(kb_id, wanted)
        limit = max(1, min(args.limit, 200))
        hits = client.search(args.query, [kb_id], tag_ids=tag_ids, match_count=limit)
        # 线上 WeKnora v0.8.0 的 knowledge-search 忽略 match_count；客户端再截一次
        hits = hits[:limit]
    except weknora.WeKnoraError as e:
        raise SystemExit(str(e))
    if not hits:
        print("没搜到（索引可能还没解析完，或关键词不合适）")
        return
    conn = _open_truth_readonly(args.db)
    archive_dir = _archive_dir(args.db)
    stale_index = False
    try:
        print(f"命中 {len(hits)} 条：")
        for h in hits:
            title = h.get("knowledge_title") or h.get("title") or "-"
            score = h.get("score")
            score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
            snippet = " ".join((h.get("content") or "").split())[:160]
            print(f"  [{score_s}] {title}：{snippet}")
            doc_ref = search.document_ref(h)
            imgs = search.hit_image_codes(h)
            if doc_ref:
                _print_hit_document(conn, archive_dir, doc_ref)
            elif not search.is_unit_hit(h):
                stale_index = True
            _print_hit_images(conn, archive_dir, imgs)
        if stale_index:
            print("（部分命中没有文档位置：索引可能由旧版生成，"
                  "运行 index --rebuild 后可显示）")
    finally:
        if conn is not None:
            conn.close()


def _print_hit_document(conn, archive_dir: Path, doc_ref: str) -> None:
    """每条命中下打印真相源里的受管文件位置；取不到只标注原因。"""
    loc = archive.resolve_document(conn, archive_dir, doc_ref)
    detail = loc.path or f"（{loc.problem}）"
    print(f"    文档 {doc_ref}  {detail}")


def _print_hit_images(conn, archive_dir: Path, imgs: search.HitImages) -> None:
    """每条命中下打印图片母本位置；解析不到也打印编号+原因（文本命中不受影响）。"""
    seen: set[str] = set()
    for code in imgs.unit_image:
        print("    " + _format_image(media.resolve_image(conn, archive_dir, code), "本图"))
        seen.add(code)
    for code in imgs.chunk:
        if code not in seen:
            print("    " + _format_image(media.resolve_image(conn, archive_dir, code), "图片"))
            seen.add(code)
    for code in imgs.extra():
        if code not in seen:
            print("    " + _format_image(media.resolve_image(conn, archive_dir, code), "清单图片"))
            seen.add(code)
    if imgs.knowledge_total and imgs.knowledge_total > len(imgs.knowledge):
        print(f"    （图片清单只列了前 {len(imgs.knowledge)} 张，共 {imgs.knowledge_total} 张）")


def _format_image(ref: media.ImageLocation, label: str) -> str:
    detail = ref.location or f"（{ref.problem}）"
    return f"{label} {ref.code}  {detail}"


# ---------------- 参数 ----------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="团队文档归档系统 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("migrate", help="014 → 052 全量只读迁移")
    p.add_argument("src_db", type=Path, help="014 的 track.db 路径")
    p.add_argument("dst_db", type=Path, help="目标真相源数据库路径")
    p.add_argument("--verify", action="store_true", help="迁移后复算指纹并报告差异")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("scan", help="扫描目录登记出现；图片/视频直接进媒体库")
    p.add_argument("dir", type=Path, help="要扫描的目录")
    _add_db_option(p)
    p.add_argument("--source", default="wechat", help="来源类型（默认 wechat）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--since", default=None,
                   help="只扫标准日期子目录（YYYY-MM-DD）且 ≥ 此日期；非日期目录跳过")
    g.add_argument("--all", dest="all_files", action="store_true",
                   help="全量扫描，不看日期目录结构（对应 014 的 --since all）")
    p.add_argument("--project", default=None, help="本次登记的媒体的主归属项目")
    p.add_argument("--tag", action="append", help="本次登记的媒体的关联项目标签（可重复）")
    p.add_argument("--no-caption", action="store_true", help="不生成图片 caption")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("new-doc", help="确认一条出现为新文档")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--project", required=True, help="项目名")
    p.add_argument("--title", default=None, help="文档标题（默认取文件名去扩展名）")
    p.add_argument("--category", default=None, help=f"类别：{'/'.join(archive.CATEGORIES)}")
    _add_db_option(p)
    p.set_defaults(func=cmd_new_doc)

    p = sub.add_parser("new-version", help="确认一条出现为某文档新版本")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--doc", type=int, required=True, help="目标文档 ID")
    _add_db_option(p)
    p.set_defaults(func=cmd_new_version)

    p = sub.add_parser("merge", help="确认一条出现为重复")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--doc", type=int, required=True, help="目标文档 ID")
    p.add_argument("--force", action="store_true", help="跳过指纹校验强制并入")
    _add_db_option(p)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("ignore", help="确认一条出现为忽略")
    p.add_argument("id", type=int, help="出现 ID")
    p.add_argument("--reason", default=None, help="忽略原因")
    _add_db_option(p)
    p.set_defaults(func=cmd_ignore)

    p = sub.add_parser("convert", help="版本转 md + 抽内嵌图入图片库")
    p.add_argument("version_id", type=int, nargs="+", help="版本 ID（可多个）")
    p.add_argument("--no-caption", action="store_true", help="不生成图片 caption")
    p.add_argument("--recaption", action="store_true", help="已有描述也重新生成")
    _add_db_option(p)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("register-image", help="独立图入库（IMG-#### + caption）")
    p.add_argument("file", type=Path, help="图片文件路径")
    p.add_argument("--project", default=None, help="主归属项目")
    p.add_argument("--tag", action="append", help="关联项目标签（可重复）")
    p.add_argument("--no-caption", action="store_true", help="不生成 caption")
    p.add_argument("--recaption", action="store_true", help="已有描述也重新生成")
    _add_db_option(p)
    p.set_defaults(func=cmd_register_image)

    p = sub.add_parser("register-video", help="视频登记（VID-####）")
    p.add_argument("file", type=Path, help="视频文件路径")
    p.add_argument("--project", default=None, help="主归属项目")
    p.add_argument("--tag", action="append", help="关联项目标签（可重复）")
    p.add_argument("--intro", default=None, help="简介（人填）")
    _add_db_option(p)
    p.set_defaults(func=cmd_register_video)

    p = sub.add_parser("set-intro", help="改/补视频简介")
    p.add_argument("code", help="视频编号，如 VID-0001")
    p.add_argument("--intro", required=True, help="简介文本")
    _add_db_option(p)
    p.set_defaults(func=cmd_set_intro)

    p = sub.add_parser("media", help="列出图片/视频")
    p.add_argument("--project", default=None, help="只看某主归属项目")
    p.add_argument("--tag", default=None, help="只看挂了某关联标签的")
    _add_db_option(p)
    p.set_defaults(func=cmd_media)

    p = sub.add_parser("index", help="把真相源推成 WeKnora 派生索引")
    _add_db_option(p)
    p.add_argument("--kb-name", default=DEFAULT_KB_NAME, help="WeKnora 知识库名")
    p.add_argument("--embedding-model", default=None,
                   help=f"embedding 模型名（默认 {DEFAULT_EMBEDDING_MODEL}，spec §7.2 钉死）")
    p.add_argument("--rebuild", action="store_true", help="删掉同名知识库重建")
    p.add_argument("--wait", action="store_true",
                   help="额外打印解析状态（index 本来就会等解析完再写元数据）")
    p.add_argument("--dry-run", action="store_true", help="只统计，不连 WeKnora")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="在 WeKnora 里做内容检索")
    p.add_argument("query", help="关键词或自然语言")
    _add_db_option(p)
    p.add_argument("--kb-name", default=DEFAULT_KB_NAME, help="WeKnora 知识库名")
    p.add_argument("--tag", action="append",
                   help="按标签过滤（可重复；写 项目/浮标项目 或只写 浮标项目）")
    p.add_argument("--limit", type=int, default=5, help="返回条数（默认 5）")
    p.set_defaults(func=cmd_search)

    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
