"""票 #15：WeKnora 派生索引接入。"""
import pytest
from support import make_pending, make_video

from docarchive import archive, index, media


def _archived_doc(conn, archive_dir, tmp_path, *, title="连杆方案", category="测试",
                  body=None):
    text = body or "# 连杆方案\n\n连杆间隙复测 0.02mm，周五出报告。\n"
    src = tmp_path / "src.md"
    src.write_text(text, encoding="utf-8")
    occ = make_pending(conn, src, "连杆方案.md")
    doc_id, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir,
                                  title=title, category=category)
    # 受管文件换成 md 并登记（模拟已转换）
    md_rel = f"doc-{doc_id}/v{vid}-连杆方案.md"
    (archive_dir / md_rel).parent.mkdir(parents=True, exist_ok=True)
    (archive_dir / md_rel).write_text(text, encoding="utf-8")
    conn.execute("UPDATE versions SET markdown_path=?, archive_path=? WHERE id=?",
                 (md_rel, md_rel, vid))
    conn.commit()
    return doc_id, vid, md_rel


class FakeClient:
    """记录调用顺序的假 WeKnora 客户端。"""

    def __init__(self, existing_kb=None, metadata_error=None):
        self.calls = []
        self.kb_id = "kb-1"
        self.existing_kb = existing_kb
        self.metadata_error = metadata_error

    def find_kb(self, name):
        self.calls.append(("find_kb", name))
        return self.existing_kb

    def create_kb(self, name, embedding_model_id=None):
        self.calls.append(("create_kb", name, embedding_model_id))
        return self.kb_id

    def delete_kb(self, kb_id):
        self.calls.append(("delete_kb", kb_id))

    def ensure_tag(self, kb_id, name):
        self.calls.append(("ensure_tag", kb_id, name))
        return f"tag-{name}"

    def create_knowledge(self, kb_id, title, content, tag_ids=()):
        self.calls.append(("create_knowledge", kb_id, title, content, tuple(tag_ids)))
        return f"k-{title}"

    def set_knowledge_metadata(self, knowledge_id, metadata):
        self.calls.append(("set_knowledge_metadata", knowledge_id, dict(metadata)))
        if self.metadata_error:
            raise self.metadata_error

    def search(self, query, kb_ids, tag_ids=(), match_count=5):
        self.calls.append(("search", query, tuple(kb_ids), tuple(tag_ids), match_count))
        return [{"content": "连杆间隙复测 0.02mm", "score": 0.9}]


def test_collect_units_builds_document_image_video(conn, archive_dir, tmp_path):
    doc_id, vid, _ = _archived_doc(conn, archive_dir, tmp_path)

    pytest.importorskip("PIL")
    from PIL import Image
    img = tmp_path / "shot.png"
    Image.new("RGB", (80, 60), "white").save(img)
    media.register_standalone_image(conn, archive_dir, img, project="浮标项目",
                                    tags=["海试项目"], captioner=None)
    conn.execute("UPDATE images SET caption='一张连杆间隙测量截图' WHERE code='IMG-0001'")
    media.register_video(conn, archive_dir, make_video(tmp_path / "v.mp4"),
                         project="浮标项目", intro="现场调试录像")
    conn.commit()

    units = index.collect_units(conn, archive_dir)
    by_kind = {u.kind: u for u in units}
    assert set(by_kind) == {"document", "image", "video"}

    doc = by_kind["document"]
    assert doc.title == "连杆方案"
    assert "连杆间隙复测 0.02mm" in doc.content
    assert doc.tags == ("项目/浮标项目", "类别/测试")

    image = by_kind["image"]
    assert image.title == "图片 IMG-0001"
    assert "连杆间隙测量截图" in image.content
    # 主归属 + 关联项目标签都进「项目」维度，再挂「图片」维度
    assert image.tags == ("项目/浮标项目", "项目/海试项目", "图片")

    video = by_kind["video"]
    assert video.title == "视频 VID-0001"
    assert "现场调试录像" in video.content
    assert video.tags == ("项目/浮标项目", "视频")


def test_collect_units_skips_documents_without_md(conn, archive_dir, tmp_path):
    src = tmp_path / "x.md"
    src.write_text("正文", encoding="utf-8")
    occ = make_pending(conn, src)
    archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)  # 没转换过，markdown_path 为 NULL
    assert index.collect_units(conn, archive_dir) == []


def test_image_without_caption_still_indexed_by_code(conn, archive_dir, tmp_path):
    """没 caption 的图也要能按编号/项目命中（spec §7.1 的承诺），正文退化成编号+名称。"""
    pytest.importorskip("PIL")
    from PIL import Image
    img = tmp_path / "shot.png"
    Image.new("RGB", (80, 60), "white").save(img)
    media.register_standalone_image(conn, archive_dir, img, project="浮标项目")

    units = [u for u in index.collect_units(conn, archive_dir) if u.kind == "image"]
    assert len(units) == 1
    assert "IMG-0001" in units[0].content
    assert units[0].tags == ("项目/浮标项目", "图片")


def test_video_without_intro_still_indexed_by_code(conn, archive_dir, tmp_path):
    media.register_video(conn, archive_dir, make_video(tmp_path / "v.mp4"), project="浮标项目")
    units = [u for u in index.collect_units(conn, archive_dir) if u.kind == "video"]
    assert len(units) == 1
    assert "VID-0001" in units[0].content


def test_index_units_creates_tags_and_knowledge(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    units = index.collect_units(conn, archive_dir)
    client = FakeClient()
    report = index.index_units(units, client, kb_name="052 文档库")

    assert report.kb_id == "kb-1"
    assert report.units == 1
    # 先建 KB，再按需建标签（同标签只建一次），最后建知识
    ensure_tags = [c[2] for c in client.calls if c[0] == "ensure_tag"]
    # 五个类别 + 图片/视频两个维度先建齐，再建条目自己的项目标签
    assert ensure_tags[:7] == [f"类别/{c}" for c in archive.CATEGORIES] + ["图片", "视频"]
    assert "项目/浮标项目" in ensure_tags
    assert ensure_tags.count("类别/测试") == 1
    created = [c for c in client.calls if c[0] == "create_knowledge"]
    assert len(created) == 1
    assert created[0][2] == "连杆方案"      # title
    assert "连杆间隙复测" in created[0][3]  # content


def test_index_units_refuses_existing_kb_without_rebuild(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    units = index.collect_units(conn, archive_dir)
    client = FakeClient(existing_kb="kb-old")
    with pytest.raises(index.IndexExistsError):
        index.index_units(units, client, kb_name="052 文档库")
    assert [c for c in client.calls if c[0] == "create_knowledge"] == []


def test_index_units_rebuild_deletes_then_recreates(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    units = index.collect_units(conn, archive_dir)
    client = FakeClient(existing_kb="kb-old")
    report = index.index_units(units, client, kb_name="052 文档库", rebuild=True)
    kinds = [c[0] for c in client.calls]
    assert kinds[:2] == ["find_kb", "delete_kb"]
    assert report.kb_id == "kb-1"


def test_plan_units_lists_units_and_tags_without_network(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    units = index.collect_units(conn, archive_dir)
    plan = index.plan_units(units)
    assert plan.units == 1
    # dry-run 的标签清单要和真实运行一致：五个类别 + 图片/视频 + 条目自己的标签
    assert plan.tags[:7] == tuple(f"类别/{c}" for c in archive.CATEGORIES) + ("图片", "视频")
    assert "项目/浮标项目" in plan.tags and "类别/测试" in plan.tags


# ---------- #21：内嵌图 → 知识条目图片清单 ----------

def test_document_unit_carries_image_codes_from_markdown(conn, archive_dir, tmp_path):
    """清单从「实际推送的 md」里取：取了哪张图，正文里就有哪个标记。"""
    _archived_doc(
        conn, archive_dir, tmp_path,
        body="# 方案\n\n先看 {{IMG-0001}}，再看 ![图]({{IMG-0002}})，重复 {{IMG-0001}}。\n",
    )
    units = index.collect_units(conn, archive_dir)
    doc = [u for u in units if u.kind == "document"][0]
    assert doc.images == ("IMG-0001", "IMG-0002")


def test_document_without_images_has_empty_manifest(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    doc = index.collect_units(conn, archive_dir)[0]
    assert doc.images == ()
    assert index.manifest_fields(doc.images) == {}


def test_manifest_fields_join_codes_for_custom_metadata():
    assert index.manifest_fields(("IMG-0001", "IMG-0002")) == {
        "图片": "IMG-0001,IMG-0002",
    }


def test_manifest_fields_truncate_long_lists_and_record_total():
    """custom_metadata 的值有 1000 字符上限：截断并带总数，别让整条写入失败。"""
    codes = tuple(f"IMG-{i:04d}" for i in range(1, 200))
    fields = index.manifest_fields(codes)
    assert len(fields["图片"]) <= index.MAX_MANIFEST_CHARS
    assert fields["图片"].startswith("IMG-0001,IMG-0002")
    assert fields["图片数"] == 199
    assert "IMG-0199" not in fields["图片"]


def test_manifest_codes_roundtrips_search_field():
    assert index.manifest_codes("图片: IMG-0001,IMG-0002") == ("IMG-0001", "IMG-0002")
    assert index.manifest_codes({"图片": "IMG-0003"}) == ("IMG-0003",)
    assert index.manifest_codes("来源: 052") == ()  # 别的字段里出现编号不算清单
    assert index.manifest_codes(None) == ()
    assert index.manifest_codes({"图片": 3}) == ()


def test_index_units_writes_image_manifest_after_create(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path,
                  body="# 方案\n\n{{IMG-0001}} 与 {{IMG-0002}}\n")
    units = index.collect_units(conn, archive_dir)
    client = FakeClient()
    report = index.index_units(units, client, kb_name="052 文档库")

    created = [c for c in client.calls if c[0] == "create_knowledge"]
    metas = [c for c in client.calls if c[0] == "set_knowledge_metadata"]
    assert len(metas) == 1
    assert metas[0][1] == "k-连杆方案"
    assert metas[0][2] == {"图片": "IMG-0001,IMG-0002"}
    # 写清单排在 create 之后（要拿到 knowledge_id）
    assert client.calls.index(metas[0]) > client.calls.index(created[0])
    assert report.metadata_warnings == ()


def test_index_units_skips_manifest_when_document_has_no_images(conn, archive_dir, tmp_path):
    _archived_doc(conn, archive_dir, tmp_path)
    client = FakeClient()
    index.index_units(index.collect_units(conn, archive_dir), client, kb_name="052 文档库")
    assert [c for c in client.calls if c[0] == "set_knowledge_metadata"] == []


def test_index_units_degrades_when_manifest_write_fails(conn, archive_dir, tmp_path):
    """清单写失败只记警告：图回指降级，文本入索引不受影响。"""
    _archived_doc(conn, archive_dir, tmp_path, body="# 方案\n\n{{IMG-0001}}\n")
    client = FakeClient(metadata_error=RuntimeError("WeKnora 500"))
    report = index.index_units(index.collect_units(conn, archive_dir), client,
                               kb_name="052 文档库")
    assert report.units == 1
    assert len(report.metadata_warnings) == 1
    assert "WeKnora 500" in report.metadata_warnings[0]
    assert [c for c in client.calls if c[0] == "create_knowledge"]


