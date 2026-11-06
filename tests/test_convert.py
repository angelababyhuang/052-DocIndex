"""票 #13：文档转 md + 图片抽取。

样例文件全部在 tmp_path 里现造（不提交二进制 fixture）：
  docx 用 pandoc 由 md 生成，pptx 用 python-pptx，pdf 用 PyMuPDF，
  xlsx 用 openpyxl，图片用 Pillow。缺哪个工具就跳过对应的用例。
"""
import hashlib
import importlib.util
import re
import shutil
import subprocess

import pytest
from support import make_pending, make_png

from docarchive import archive, convert, imagelib

HAS_PANDOC = shutil.which("pandoc") is not None
HAS_PPTX = importlib.util.find_spec("pptx") is not None
HAS_FITZ = importlib.util.find_spec("fitz") is not None
HAS_MARKITDOWN = importlib.util.find_spec("markitdown") is not None
HAS_OCR = importlib.util.find_spec("rapidocr_onnxruntime") is not None
HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_TEXTUTIL = shutil.which("textutil") is not None

needs_pandoc = pytest.mark.skipif(not HAS_PANDOC, reason="需要 pandoc")
needs_pptx = pytest.mark.skipif(not HAS_PPTX, reason="需要 python-pptx")
needs_fitz = pytest.mark.skipif(not HAS_FITZ, reason="需要 PyMuPDF")
needs_markitdown = pytest.mark.skipif(not HAS_MARKITDOWN, reason="需要 markitdown")
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="需要 rapidocr-onnxruntime")
needs_pil = pytest.mark.skipif(not HAS_PIL, reason="需要 Pillow")
needs_textutil = pytest.mark.skipif(not HAS_TEXTUTIL, reason="需要 macOS textutil")


# ---------- 样例文件工厂 ----------

def _text_image(path, text):
    """白底黑字大号字，给 OCR 用。"""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for cand in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(cand, 40)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(size=40)
    im = Image.new("RGB", (1100, 200), "white")
    ImageDraw.Draw(im).text((30, 70), text, fill="black", font=font)
    im.save(path)
    return path


def _make_docx(dirpath, image_text="EMBEDDED"):
    """docx：标题 + 正文 + OMML 公式 + 表格 + 内嵌图片。"""
    img = make_png(dirpath / "pic.png", image_text)
    md = dirpath / "src.md"
    md.write_text(
        "# 连杆方案\n\n"
        "正文段落，含公式 $E = mc^2$。\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 单元1 | 单元2 |\n\n"
        "![](pic.png)\n",
        encoding="utf-8",
    )
    docx = dirpath / "sample.docx"
    subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True, cwd=dirpath,
                   capture_output=True)
    return docx, img.read_bytes()


def _make_pptx(path, png_path, title="项目汇报"):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text = title

    box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(6), Inches(1))
    tf = box.text_frame
    tf.text = "第一点"
    p = tf.add_paragraph()
    p.text = "第二点"
    p.level = 1

    shape = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1))
    table = shape.table
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "数值"
    table.cell(1, 0).text = "进度"
    table.cell(1, 1).text = "80%"

    slide.shapes.add_picture(str(png_path), Inches(1), Inches(4.5), width=Inches(1))
    prs.save(str(path))
    return path


def _make_text_pdf(path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Text PDF body line", fontsize=14)
    page.insert_text((72, 132), "Second paragraph here", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def _make_scanned_pdf(path, img_path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(50, 50, 650, 200), filename=str(img_path))
    doc.save(str(path))
    doc.close()
    return path


def _make_xlsx(path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "进度"
    ws.append(["姓名", "分数"])
    ws.append(["张三", 90])
    ws.append(["李四", 85])
    wb.save(str(path))
    return path


# ---------- 纯文本 / 不支持的格式 ----------

def test_plain_text_passthrough(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("纯文本内容\n第二行", encoding="utf-8")
    c = convert.convert_file(src)
    assert "纯文本内容" in c.markdown
    assert c.images == []


def test_unsupported_format_raises(tmp_path):
    src = tmp_path / "a.zip"
    src.write_bytes(b"PK\x03\x04")
    with pytest.raises(convert.UnsupportedFormat):
        convert.convert_file(src)


# ---------- ① docx → md：表格 / 图片 / OMML 公式 ----------

@needs_pandoc
@needs_pil
def test_docx_keeps_table_formula_and_image(tmp_path):
    docx, png_bytes = _make_docx(tmp_path)
    c = convert.convert_file(docx)

    assert "# 连杆方案" in c.markdown
    # 表格：表头与单元格都在，且是 md 表格（有分隔行）
    for cell in ("列A", "列B", "单元1", "单元2"):
        assert cell in c.markdown
    assert "|" in c.markdown
    # OMML 公式保留成 TeX 数学（$...$ 里带上标），不是被降级成纯文本或行内代码
    assert re.search(r"\$[^$\n]*mc\s*\^\s*\{?2\}?[^$\n]*\$", c.markdown), c.markdown
    # 图片：抽出来了且在 md 里是占位引用，不是临时路径
    assert len(c.images) == 1
    assert c.images[0].data == png_bytes
    assert "{{IMG-" in c.markdown
    assert "pic.png" not in c.markdown


# ---------- ② 文本型 PDF → md 正文可读 ----------

@needs_fitz
@needs_markitdown
def test_text_pdf_markdown_is_readable(tmp_path):
    c = convert.convert_file(_make_text_pdf(tmp_path / "text.pdf"))
    assert "Text PDF body line" in c.markdown
    assert "Second paragraph here" in c.markdown
    assert c.images == []


# ---------- ③ 扫描版 PDF：OCR 出文本 ----------

@needs_fitz
@needs_ocr
@needs_pil
def test_scanned_pdf_goes_through_ocr(tmp_path):
    img = _text_image(tmp_path / "scan.png", "Scanned Document 2026")
    pdf = _make_scanned_pdf(tmp_path / "scan.pdf", img)
    assert convert.is_scanned_pdf(pdf)
    c = convert.convert_file(pdf)
    assert "Scanned" in c.markdown or "Document" in c.markdown
    assert len(c.markdown.strip()) > 10


@needs_fitz
@needs_markitdown
def test_text_pdf_is_not_treated_as_scanned(tmp_path):
    assert not convert.is_scanned_pdf(_make_text_pdf(tmp_path / "text.pdf"))


# ---------- ④ pptx：文本 / 表格 / 图片 ----------

@needs_pptx
@needs_pil
def test_pptx_extracts_text_table_and_image(tmp_path):
    png = make_png(tmp_path / "logo.png", "LOGO")
    pptx = _make_pptx(tmp_path / "deck.pptx", png)
    c = convert.convert_file(pptx)

    assert "项目汇报" in c.markdown
    assert "第一点" in c.markdown and "第二点" in c.markdown
    for cell in ("指标", "数值", "进度", "80%"):
        assert cell in c.markdown
    assert len(c.images) == 1
    assert c.images[0].data == png.read_bytes()
    assert "{{IMG-" in c.markdown


# ---------- xlsx → md ----------

@needs_markitdown
def test_xlsx_to_markdown(tmp_path):
    c = convert.convert_file(_make_xlsx(tmp_path / "a.xlsx"))
    for cell in ("姓名", "分数", "张三", "90"):
        assert cell in c.markdown
    assert "|" in c.markdown


# ---------- ⑤ 内嵌图片进图片库 + IMG-ID + 同图只分配一次 ----------

@needs_pandoc
@needs_pil
def test_convert_version_writes_md_and_links_images(conn, archive_dir, tmp_path):
    docx, png_bytes = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    doc_id, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)

    res = convert.convert_version(conn, vid, archive_dir)

    v = conn.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
    assert v["markdown_path"] == f"doc-{doc_id}/v{vid}-sample.md"
    md_file = archive_dir / v["markdown_path"]
    assert md_file.exists()
    md = md_file.read_text(encoding="utf-8")
    assert "连杆方案" in md
    assert "{{IMG-0001}}" in md

    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["code"] == "IMG-0001"
    assert img["sha256"] == hashlib.sha256(png_bytes).hexdigest()
    assert img["byte_size"] == len(png_bytes)
    assert (archive_dir / img["path"]).read_bytes() == png_bytes
    assert img["source"] and f"doc-{doc_id}" in img["source"]

    di = conn.execute("SELECT * FROM document_images").fetchall()
    assert len(di) == 1
    assert di[0]["document_id"] == doc_id
    assert di[0]["image_id"] == img["id"]
    assert di[0]["kind"] == "embedded"
    assert di[0]["position"]

    assert res.images_total == 1
    assert res.images_new == 1


@needs_pandoc
@needs_pil
def test_same_image_across_documents_gets_one_code(conn, archive_dir, tmp_path):
    """同一张图在不同文档里出现 → 只分配一次 IMG-ID，两个文档都挂关联。"""
    docx1, _ = _make_docx(tmp_path, "SAME")
    # 第二份 docx 用同一张图（重新生成，图片字节相同）
    d2 = tmp_path / "d2"
    d2.mkdir()
    docx2, _ = _make_docx(d2, "SAME")

    occ1 = make_pending(conn, docx1, file_name="方案一.docx")
    doc1, vid1 = archive.new_doc(conn, occ1["id"], "浮标项目", archive_dir)
    occ2 = make_pending(conn, docx2, file_name="方案二.docx")
    doc2, vid2 = archive.new_doc(conn, occ2["id"], "浮标项目", archive_dir)

    r1 = convert.convert_version(conn, vid1, archive_dir)
    r2 = convert.convert_version(conn, vid2, archive_dir)

    images = conn.execute("SELECT * FROM images").fetchall()
    assert len(images) == 1
    assert images[0]["code"] == "IMG-0001"
    assert r1.images_new == 1 and r2.images_new == 0
    assert conn.execute("SELECT COUNT(*) c FROM document_images").fetchone()["c"] == 2

    md2 = (archive_dir / conn.execute(
        "SELECT markdown_path FROM versions WHERE id=?", (vid2,)
    ).fetchone()["markdown_path"]).read_text(encoding="utf-8")
    assert "{{IMG-0001}}" in md2


@needs_pptx
@needs_pil
def test_two_distinct_images_get_sequential_codes(conn, archive_dir, tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    a = make_png(tmp_path / "a.png", "AAA")
    b = make_png(tmp_path / "b.png", "BBB")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    slide.shapes.add_picture(str(a), Inches(1), Inches(1), width=Inches(1))
    slide.shapes.add_picture(str(b), Inches(3), Inches(1), width=Inches(1))
    deck = tmp_path / "deck.pptx"
    prs.save(str(deck))

    occ = make_pending(conn, deck)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)
    res = convert.convert_version(conn, vid, archive_dir)

    codes = [r["code"] for r in conn.execute("SELECT code FROM images ORDER BY id")]
    assert codes == ["IMG-0001", "IMG-0002"]
    assert res.images_new == 2
    assert res.images_total == 2


@needs_fitz
@needs_ocr
@needs_pil
def test_scanned_pdf_conversion_adds_no_images(conn, archive_dir, tmp_path):
    """OCR 出的文本不产生图片实体（页面图不是文档内嵌图）。"""
    img = _text_image(tmp_path / "scan.png", "Archive 2026")
    pdf = _make_scanned_pdf(tmp_path / "scan.pdf", img)
    occ = make_pending(conn, pdf)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)
    convert.convert_version(conn, vid, archive_dir)
    assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 0


# ---------- 图片库原语 ----------

def test_register_image_dedups_and_assigns_codes(conn, archive_dir):
    data = b"fake-png-bytes"
    r1 = imagelib.register_image(conn, archive_dir, data, ext=".png",
                                 source="doc-1/v1-a.docx", original_name="a.png")
    r2 = imagelib.register_image(conn, archive_dir, data, ext=".png",
                                 source="doc-2/v2-b.docx", original_name="b.png")
    r3 = imagelib.register_image(conn, archive_dir, b"other", ext=".jpg")
    conn.commit()

    assert (r1.code, r1.created) == ("IMG-0001", True)
    assert (r2.code, r2.created) == ("IMG-0001", False)
    assert (r3.code, r3.created) == ("IMG-0002", True)
    assert (archive_dir / "images" / "IMG-0001.png").read_bytes() == data
    assert (archive_dir / "images" / "IMG-0002.jpg").read_bytes() == b"other"
    # 复用时不新建图片行、不改来源
    assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 2


def test_convert_version_without_archive_file_raises(conn, tmp_path):
    conn.execute("INSERT INTO documents(title) VALUES ('无归档文件')")
    conn.execute("INSERT INTO versions(document_id, archive_path) VALUES (1, NULL)")
    conn.commit()
    with pytest.raises(ValueError):
        convert.convert_version(conn, 1, tmp_path)


@needs_pptx
def test_pptx_chart_data_becomes_table(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "销量"
    data = CategoryChartData()
    data.categories = ["一月", "二月"]
    data.add_series("销量", (10, 20))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2),
                           Inches(5), Inches(3), data)
    path = tmp_path / "chart.pptx"
    prs.save(str(path))

    md = convert.convert_file(path).markdown
    assert "销量" in md
    assert "一月" in md and "二月" in md
    assert "20" in md


@needs_pandoc
@needs_pil
def test_reconvert_is_idempotent(conn, archive_dir, tmp_path):
    """重跑 convert 不重复分配 IMG-ID，也不重复挂关联。"""
    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    doc_id, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)

    r1 = convert.convert_version(conn, vid, archive_dir)
    r2 = convert.convert_version(conn, vid, archive_dir)

    assert (r1.images_new, r2.images_new) == (1, 0)
    assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM document_images").fetchone()["c"] == 1
    md = (archive_dir / r2.markdown_path).read_text(encoding="utf-8")
    assert "{{IMG-0001}}" in md and "{{IMG-1}}" not in md


# ---------- 评审补测：PDF 内嵌图 / pptx 占位符图 / 组合形状 / .doc / 指纹兜底 ----------

@needs_fitz
@needs_markitdown
@needs_pil
def test_text_pdf_embedded_figure_enters_image_library(tmp_path):
    """文本型 PDF 里的插图也要抽出来进图片库（spec §6.2 内嵌图）。"""
    import fitz

    png = make_png(tmp_path / "fig.png", "FIGURE")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Body text with a figure", fontsize=14)
    page.insert_image(fitz.Rect(72, 150, 272, 250), filename=str(png))
    pdf = tmp_path / "fig.pdf"
    doc.save(str(pdf))
    doc.close()

    c = convert.convert_file(pdf)
    assert "Body text with a figure" in c.markdown
    assert len(c.images) == 1
    assert "{{IMG-" in c.markdown


@needs_pptx
@needs_pil
def test_pptx_picture_placeholder_is_extracted(tmp_path):
    """图片占位符（"标题+图片"版式）里的图也要抽（shape_type 是 PLACEHOLDER 不是 PICTURE）。"""
    from pptx import Presentation

    png = make_png(tmp_path / "ph.png", "PLACEHOLDER")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[8])  # Picture with Caption
    slide.shapes.title.text = "封面"
    slide.placeholders[1].insert_picture(str(png))
    path = tmp_path / "ph.pptx"
    prs.save(str(path))

    c = convert.convert_file(path)
    assert "封面" in c.markdown
    assert len(c.images) == 1
    assert "{{IMG-" in c.markdown


@needs_pptx
@needs_pil
def test_pptx_group_shapes_are_read(tmp_path):
    """组合形状里的文本和图片不能丢（group 要递归展开）。"""
    from pptx import Presentation
    from pptx.util import Inches

    png = make_png(tmp_path / "g.png", "GROUP")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text_frame.text = "组内文本"
    group.shapes.add_picture(str(png), Inches(1), Inches(2), width=Inches(1))
    path = tmp_path / "group.pptx"
    prs.save(str(path))

    c = convert.convert_file(path)
    assert "组内文本" in c.markdown
    assert len(c.images) == 1


@needs_textutil
@needs_pandoc
@needs_pil
def test_doc_via_textutil_converts(tmp_path):
    """.doc 走 textutil → docx → pandoc（仅 macOS 可用）。"""
    docx, _ = _make_docx(tmp_path)
    doc = tmp_path / "legacy.doc"
    subprocess.run(["textutil", "-convert", "doc", "-output", str(doc), str(docx)],
                   check=True, capture_output=True)
    c = convert.convert_file(doc)
    assert "连杆方案" in c.markdown


@needs_pandoc
@needs_pil
def test_text_fp_falls_back_when_textutil_missing(tmp_path, monkeypatch):
    """textutil 不可用（Linux/CI）时仍要能算出指纹：anydoc，装不上就用 markitdown。"""
    from docarchive import fingerprint

    docx, _ = _make_docx(tmp_path)

    def no_textutil(*args, **kwargs):
        raise FileNotFoundError("textutil")

    monkeypatch.setattr(subprocess, "run", no_textutil)
    fp = fingerprint.compute_text_fp(docx, ".docx")
    assert fp is not None and len(fp) == 64


@needs_pandoc
@needs_pil
def test_convert_version_captions_embedded_images(conn, archive_dir, tmp_path):
    """内嵌图也要有 caption（spec §6.2），已有描述的第二次不再调模型。"""
    class FakeCapture:
        def __init__(self):
            self.calls = 0

        def describe(self, data, ext=None):
            self.calls += 1
            return "一张连杆示意图"

    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)
    cap = FakeCapture()

    r1 = convert.convert_version(conn, vid, archive_dir, captioner=cap)
    assert r1.captions_new == 1
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "一张连杆示意图"

    r2 = convert.convert_version(conn, vid, archive_dir, captioner=cap)
    assert r2.captions_new == 0
    assert cap.calls == 1


@needs_pandoc
@needs_pil
def test_embedded_image_inherits_document_project(conn, archive_dir, tmp_path):
    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)
    convert.convert_version(conn, vid, archive_dir)
    row = conn.execute(
        "SELECT p.name FROM images i JOIN projects p ON p.id = i.project_id"
    ).fetchone()
    assert row["name"] == "浮标项目"


@needs_pandoc
@needs_pil
def test_embedded_image_gets_readable_display_name(conn, archive_dir, tmp_path):
    """pandoc 的媒体名（rId20）不适合当显示名，用「文档标题 图N」。"""
    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)
    convert.convert_version(conn, vid, archive_dir)
    # 文档标题来自文件名（sample.docx → sample），不是 docx 里的 H1
    assert conn.execute("SELECT name FROM images").fetchone()["name"] == "sample 图1"


@needs_pandoc
@needs_pil
def test_convert_reports_caption_errors(conn, archive_dir, tmp_path):
    """caption 失败要回执到报告里（CLI 会逐条打印），不能静默。"""
    class BadCapturer:
        def describe(self, data, ext=None):
            raise RuntimeError("模型接口返回 401")

    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)

    r = convert.convert_version(conn, vid, archive_dir, captioner=BadCapturer())
    assert r.captions_new == 0
    assert r.caption_errors and "401" in r.caption_errors[0]
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] is None


@needs_pandoc
@needs_pil
def test_convert_recaption_regenerates_embedded(conn, archive_dir, tmp_path):
    class FixedCaptioner:
        def __init__(self, text):
            self.text = text

        def describe(self, data, ext=None):
            return self.text

    docx, _ = _make_docx(tmp_path)
    occ = make_pending(conn, docx)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)

    convert.convert_version(conn, vid, archive_dir, captioner=FixedCaptioner("旧描述"))
    r = convert.convert_version(conn, vid, archive_dir,
                                captioner=FixedCaptioner("新描述"), recaption=True)
    assert r.captions_new == 1
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "新描述"


@needs_pptx
@needs_pil
def test_caption_http_happens_outside_write_transaction(conn, archive_dir, tmp_path):
    """补描述时不能占着 SQLite 写事务：否则第 2..N 张图的 HTTP 会一直锁库。"""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i, name in enumerate(("a.png", "b.png")):
        pic = make_png(tmp_path / name, name)
        slide.shapes.add_picture(str(pic), Inches(1 + i * 2), Inches(1), width=Inches(1))
    deck = tmp_path / "deck.pptx"
    prs.save(str(deck))
    occ = make_pending(conn, deck)
    _, vid = archive.new_doc(conn, occ["id"], "浮标项目", archive_dir)

    seen = []

    class Probe:
        def describe(self, data, ext=None):
            seen.append(conn.in_transaction)
            return "描述"

    convert.convert_version(conn, vid, archive_dir, captioner=Probe())
    assert len(seen) == 2
    assert not any(seen), "调用模型时仍有未提交的写事务"
