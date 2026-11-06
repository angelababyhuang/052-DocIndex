"""票 #21：检索命中 → 内嵌图回指（分块标记 + 知识条目图片清单）。"""
from docarchive import search


def test_hit_image_codes_extracts_chunk_markers():
    hit = {"content": "前 {{IMG-0001}} 中 ![图]({{IMG-0003}}) 后 {{IMG-0001}} {{IMG-0012}}"}
    got = search.hit_image_codes(hit)
    assert got.chunk == ("IMG-0001", "IMG-0003", "IMG-0012")


def test_hit_image_codes_ignores_non_reference_codes():
    """正文里裸写的编号不算「本段引用了这张图」——只认 {{IMG-####}} 标记。"""
    hit = {"content": "图片 IMG-0009 的说明，占位 {{IMG-1}} 不是合法编号"}
    assert search.hit_image_codes(hit).chunk == ()


def test_hit_image_codes_reads_manifest_from_search_field():
    """WeKnora 把 custom_metadata 拍平成 `键: 值` 文本返回。"""
    hit = {"content": "正文没有标记", "knowledge_custom_metadata": "图片: IMG-0001,IMG-0002"}
    got = search.hit_image_codes(hit)
    assert got.chunk == ()
    assert got.knowledge == ("IMG-0001", "IMG-0002")


def test_hit_image_codes_parses_multiline_manifest():
    hit = {"knowledge_custom_metadata": "来源: 052\n图片: IMG-0007, IMG-0008"}
    assert search.hit_image_codes(hit).knowledge == ("IMG-0007", "IMG-0008")


def test_hit_image_codes_accepts_mapping_manifest():
    hit = {"knowledge_custom_metadata": {"图片": "IMG-0003"}}
    assert search.hit_image_codes(hit).knowledge == ("IMG-0003",)


def test_hit_image_codes_without_manifest_is_empty():
    assert search.hit_image_codes({"content": "纯文本命中"}).knowledge == ()
    assert search.hit_image_codes({}).knowledge == ()
    assert search.hit_image_codes({"knowledge_custom_metadata": ""}).knowledge == ()
    assert search.hit_image_codes({"knowledge_custom_metadata": "来源: 052"}).knowledge == ()


def test_hit_images_extra_excludes_chunk_codes():
    hit = {
        "content": "本段提到 {{IMG-0001}}",
        "knowledge_custom_metadata": "图片: IMG-0001,IMG-0002,IMG-0003",
    }
    got = search.hit_image_codes(hit)
    assert got.chunk == ("IMG-0001",)
    assert got.extra() == ("IMG-0002", "IMG-0003")


def test_hit_image_codes_reads_truncated_total():
    hit = {"knowledge_custom_metadata": "图片: IMG-0001,IMG-0002\n图片数: 150"}
    got = search.hit_image_codes(hit)
    assert got.knowledge == ("IMG-0001", "IMG-0002")
    assert got.knowledge_total == 150


def test_hit_image_codes_reads_image_unit_title():
    """命中图片条目（caption）时，条目本身就是那张图，标题直接给出编号。"""
    hit = {"knowledge_title": "图片 IMG-0007", "content": "一张连杆间隙测量截图"}
    got = search.hit_image_codes(hit)
    assert got.unit_image == ("IMG-0007",)
    assert got.chunk == () and got.knowledge == ()


def test_hit_image_codes_ignores_non_image_titles():
    assert search.hit_image_codes({"knowledge_title": "方案里的 IMG-0001 说明"}).unit_image == ()
    assert search.hit_image_codes({"knowledge_title": "图片 IMG-1"}).unit_image == ()
    assert search.hit_image_codes({"knowledge_title": None}).unit_image == ()


def test_document_ref_reads_flattened_metadata():
    assert search.document_ref({"knowledge_custom_metadata": "文档: doc-12"}) == "doc-12"
    assert search.document_ref({"knowledge_custom_metadata": {"文档": "doc-7"}}) == "doc-7"
    assert search.document_ref(
        {"knowledge_custom_metadata": "文档: doc-3\n图片: IMG-0001"}
    ) == "doc-3"


def test_document_ref_is_none_for_old_index_or_other_metadata():
    assert search.document_ref({}) is None
    assert search.document_ref({"knowledge_custom_metadata": None}) is None
    assert search.document_ref({"knowledge_custom_metadata": "图片: IMG-0001"}) is None
    assert search.document_ref({"knowledge_custom_metadata": "来源: 052"}) is None
