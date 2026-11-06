"""文档统一转 md + 内嵌图片抽取。

转换器选型见 docs/spec.md §6.1：
  docx → pandoc（表格/图片/OMML 公式/脚注）
  .doc → textutil 转 docx 再走 pandoc（macOS；Linux 上不可用）
  文本型 PDF → markitdown（正文），内嵌图按页抽出
  扫描版 PDF → OCR（RapidOCR 离线，逐页渲染；图文混排时只 OCR 无文字的页）
  pptx → python-pptx（文本/表格/图表/图片，pandoc 无 pptx reader）
  xlsx → markitdown（公式不求值）
  纯文本 → 原样

抽出的图片不在 md 里留临时路径：转换阶段写 `{{IMG-1}}` 这样的占位，登记进
图片库（sha256 去重）后再换成真实编号 `{{IMG-0001}}`（spec §6.2「引用只认」）。
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from docarchive import imagelib
from docarchive.caption import Captioner

# 转换阶段的图片占位，登记后替换成 imagelib.image_ref() 的真实引用
PLACEHOLDER = "{{IMG-%d}}"

DOCX_EXTS = {".docx"}
DOC_EXTS = {".doc"}
PDF_EXTS = {".pdf"}
PPTX_EXTS = {".pptx"}
XLSX_EXTS = {".xlsx", ".xls"}
PLAIN_EXTS = {".txt", ".md"}          # 与 fingerprint.PLAIN_EXTS 同一组格式

# 单页正文少于这么多字，认为该页没有文字层（是图片页），需要 OCR
SCANNED_PAGE_CHARS = 10
# PDF 里小于这个边长的图当图标忽略，不占图片库的号
MIN_FIGURE_PX = 32

# pandoc 的图片引用：![alt](path){width=...}
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?P<path>[^)]*)\)(?:\{[^}]*\})?")
_HTML_IMAGE_RE = re.compile(r'<img\s+src="(?P<path>[^"]+)"[^>]*/?>')


class UnsupportedFormat(ValueError):
    """没有对应转换器的文件格式。"""


@dataclass(frozen=True)
class ExtractedImage:
    """从文档里抽出来的一张图：字节 + 扩展名 + 原始文件名 + 位置。"""

    data: bytes
    ext: str
    original_name: str
    position: str


@dataclass
class Conversion:
    """一次转换的内存产物：md 文本（含图片占位）+ 抽出的图片。"""

    markdown: str = ""
    images: list[ExtractedImage] = field(default_factory=list)


@dataclass(frozen=True)
class ConvertReport:
    """一次 convert_version 的落库回执。"""

    markdown_path: str
    images_total: int
    images_new: int
    captions_new: int = 0
    caption_errors: tuple[str, ...] = ()


# ---------------- 对外入口 ----------------

def convert_file(src: str | Path) -> Conversion:
    """把一份文档转成 md，并把内嵌图片抽出来（md 里留 {{IMG-n}} 占位）。"""
    src = Path(src)
    ext = src.suffix.lower()
    if ext in PLAIN_EXTS:
        return Conversion(src.read_text(encoding="utf-8", errors="replace"), [])
    if ext in DOCX_EXTS:
        with tempfile.TemporaryDirectory() as td:
            return _pandoc_docx(src, Path(td))
    if ext in DOC_EXTS:
        with tempfile.TemporaryDirectory() as td:
            return _doc_via_textutil(src, Path(td))
    if ext in PDF_EXTS:
        return _pdf(src)
    if ext in PPTX_EXTS:
        return _pptx(src)
    if ext in XLSX_EXTS:
        return Conversion(_markitdown(src), [])
    raise UnsupportedFormat(f"没有 {ext or '（无扩展名）'} 的转换器")


def convert_version(conn: sqlite3.Connection, version_id: int,
                    archive_dir: str | Path, *,
                    captioner: Captioner | None = None,
                    recaption: bool = False) -> ConvertReport:
    """把一个版本转成 md、抽图入图片库、回写 versions.markdown_path。

    md 落在受管文件旁边（`doc-1/v3-方案.docx` → `doc-1/v3-方案.md`），
    同文档同一张图只挂一条 document_images（position 取首次出现的位置）。
    """
    archive_dir = Path(archive_dir)
    v = conn.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
    if not v:
        raise ValueError(f"版本 #{version_id} 不存在")
    if not v["archive_path"]:
        raise ValueError(
            f"版本 #{version_id} 没有受管文件（例如飞书来源没有本地副本），无法转换"
        )
    src = archive_dir / v["archive_path"]
    if not src.exists():
        raise ValueError(f"受管文件不存在: {src}")

    conv = convert_file(src)
    doc = conn.execute(
        "SELECT title, project_id FROM documents WHERE id=?", (v["document_id"],)
    ).fetchone()
    doc_project = doc["project_id"]
    md, total, new = conv.markdown, 0, 0
    registered: list[tuple[int, ExtractedImage]] = []
    for i, img in enumerate(conv.images, 1):
        reg = imagelib.register_image(
            conn, archive_dir, img.data, ext=img.ext,
            source=v["archive_path"], original_name=img.original_name,
            # pandoc 抽出来的媒体名是 rId20 这种，显示名用「文档标题 图N」更好认
            name=f"{doc['title']} 图{i}",
        )
        md = md.replace(PLACEHOLDER % i, imagelib.image_ref(reg.code))
        total += 1
        if reg.created:
            new += 1
        conn.execute(
            "INSERT OR IGNORE INTO document_images(document_id, image_id, kind, position) "
            "VALUES (?, ?, 'embedded', ?)",
            (v["document_id"], reg.id, img.position),
        )
        # 内嵌图跟随文档的主归属；已有归属的（先作为独立图登记过）不被改动
        if doc_project is not None:
            conn.execute(
                "UPDATE images SET project_id=? WHERE id=? AND project_id IS NULL",
                (doc_project, reg.id),
            )
        registered.append((reg.id, img))

    md_rel = str(Path(v["archive_path"]).with_suffix(".md"))
    md_file = archive_dir / md_rel
    md_file.parent.mkdir(parents=True, exist_ok=True)
    md_file.write_text(md, encoding="utf-8")
    conn.execute("UPDATE versions SET markdown_path=? WHERE id=?", (md_rel, version_id))
    conn.commit()  # 先落库释放写锁，caption 的网络调用放到事务外

    captions_new, caption_errors = 0, []
    if captioner is not None:
        for image_id, img in registered:
            # ensure_caption 每次生成后自己 commit：下一张图的 HTTP 不落在写事务里
            res = imagelib.ensure_caption(conn, image_id, img.data, img.ext,
                                          captioner, force=recaption)
            if res.generated:
                captions_new += 1
            if res.error:
                caption_errors.append(f"{img.original_name}: {res.error}")
    return ConvertReport(md_rel, total, new, captions_new, tuple(caption_errors))


# ---------------- docx / .doc（pandoc） ----------------

def _require_pandoc() -> None:
    if shutil.which("pandoc") is None:
        raise RuntimeError("docx 转换需要 pandoc（brew install pandoc / apt install pandoc）")


def _pandoc_docx(src: Path, workdir: Path) -> Conversion:
    """pandoc 抽 md：保留表格、OMML 公式（转 TeX 数学）、脚注、多级标题。"""
    _require_pandoc()
    media = workdir / "media"
    out = workdir / "out.md"
    subprocess.run(
        [
            "pandoc", str(src), "-f", "docx",
            # +tex_math_dollars 保住 OMML 公式（gfm 会把数学降级成行内代码）；
            # 表格只留 pipe_tables：普通表出紧凑的 | a | b |，有合并单元格的表
            # pandoc 会自动退回 HTML <table>（保住 colspan），比 grid 表更好读。
            "-t", "markdown-simple_tables-multiline_tables-grid_tables"
                  "+pipe_tables+tex_math_dollars",
            "--wrap=none", f"--extract-media={media}", "-o", str(out),
        ],
        check=True, capture_output=True, timeout=300,
    )
    md = out.read_text(encoding="utf-8")
    # 图片：抽出的文件在 media 目录里，替换成占位（别把临时路径写进 md）
    md = _HTML_IMAGE_RE.sub(lambda m: f"![]({m.group('path')})", md)
    images: list[ExtractedImage] = []

    def repl(m: re.Match) -> str:
        found = _resolve_media(m.group("path"), media, out.parent)
        if found is None:
            return m.group(0)  # 外链或找不到的图，原样保留
        images.append(ExtractedImage(
            data=found.read_bytes(), ext=found.suffix, original_name=found.name,
            position=f"第 {len(images) + 1} 张图",
        ))
        return PLACEHOLDER % len(images)

    return Conversion(_MD_IMAGE_RE.sub(repl, md), images)


def _resolve_media(ref: str, media_dir: Path, out_dir: Path) -> Path | None:
    """把 pandoc 写进 md 的图片路径解析回真实文件。

    pandoc 把 `--extract-media` 给的值原样写进 md：我们传绝对路径，所以通常是
    绝对路径直接命中；万一将来改成相对路径，再按「相对输出目录」和
    「相对媒体目录」两处找，避免把临时路径漏进 md。
    """
    p = Path(urllib.parse.unquote(ref.strip()))
    candidates = [p, out_dir / p, media_dir / p]
    if p.is_absolute():
        candidates = [p]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _doc_via_textutil(src: Path, workdir: Path) -> Conversion:
    """.doc 无 pandoc reader：先 textutil 转 docx（macOS），再走 docx 通路。"""
    if shutil.which("textutil") is None:
        raise UnsupportedFormat(".doc 需要 macOS 的 textutil 先转 docx")
    tmp_docx = workdir / "from-doc.docx"
    subprocess.run(
        ["textutil", "-convert", "docx", "-output", str(tmp_docx), str(src)],
        check=True, capture_output=True, timeout=180,
    )
    return _pandoc_docx(tmp_docx, workdir)


# ---------------- PDF（文本 / 扫描 / 混合） ----------------

def _import_fitz():
    try:
        import fitz
    except ImportError as e:  # pragma: no cover - 环境缺依赖时给可读的错
        raise RuntimeError("PDF 转换需要 PyMuPDF（pip install pymupdf）") from e
    return fitz


def _chars(text: str) -> int:
    """正文有效字数（去空白），用来判「有没有文字层」。"""
    return len(re.sub(r"\s+", "", text))


def _page_text(page) -> str:
    return re.sub(r"[ \t]+", " ", page.get_text()).strip()


def _has_text(page_text: str) -> bool:
    return _chars(page_text) >= SCANNED_PAGE_CHARS


def _is_scanned(text_pages: list[str]) -> bool:
    """一页都没有文字层 → 扫描版（整篇都要 OCR 才有内容进索引）。"""
    return not any(_has_text(t) for t in text_pages)


def is_scanned_pdf(src: str | Path) -> bool:
    """整篇几乎没有文字层 → 扫描版。判定口径与 _pdf 共用，别各说各话。"""
    fitz = _import_fitz()
    with fitz.open(str(src)) as doc:
        return _is_scanned([_page_text(page) for page in doc])


def _pdf(src: Path) -> Conversion:
    """文本页走 markitdown（md 更整齐），没有文字层的页补 OCR，内嵌图按页抽出。"""
    fitz = _import_fitz()
    with fitz.open(str(src)) as doc:
        text_pages = [_page_text(p) for p in doc]
        if _is_scanned(text_pages):
            return Conversion(_ocr_pages(doc), [])          # 全扫描版：整页是图，不算内嵌图

        images: list[ExtractedImage] = []
        figures = _pdf_figures(doc, text_pages, images)
        if all(_has_text(t) for t in text_pages):
            try:
                md = _markitdown(src).strip()
            except Exception:  # markitdown 缺依赖/解析失败时退回 PDF 文字层
                md = ""
            md = md or "\n\n".join(text_pages)
        else:
            md = _mixed_pages(doc, text_pages)               # 图文混排
    if figures:
        md = f"{md}\n\n## 内嵌图片\n\n{figures}"
    return Conversion(md, images)


def _pdf_figures(doc, text_pages: list[str], images: list[ExtractedImage]) -> str:
    """按页抽出 PDF 内嵌图（跳过整页无文字的扫描页和图标大小的图）。"""
    lines = []
    for no, page in enumerate(doc, 1):
        if not _has_text(text_pages[no - 1]):
            continue  # 这一页本身是扫描图，不当内嵌图入库
        for info in page.get_images(full=True):
            xref = info[0]
            meta = doc.extract_image(xref) or {}
            data = meta.get("image")
            if not data:
                continue
            if min(meta.get("width", 0), meta.get("height", 0)) < MIN_FIGURE_PX:
                continue
            ext = f".{meta.get('ext') or 'bin'}"
            images.append(ExtractedImage(
                data=data, ext=ext, original_name=f"p{no}-{xref}{ext}",
                position=f"第 {no} 页",
            ))
            lines.append(f"第 {no} 页：{PLACEHOLDER % len(images)}")
    return "\n\n".join(lines)


def _ocr_pages(doc) -> str:
    """逐页渲染成图再 OCR，页与页之间用空行分开。"""
    return "\n\n".join(t for t in (_ocr_page(p) for p in doc) if t)


def _mixed_pages(doc, text_pages: list[str]) -> str:
    parts = []
    for i, page in enumerate(doc):
        t = text_pages[i] if _has_text(text_pages[i]) else _ocr_page(page)
        if t:
            parts.append(t)
    return "\n\n".join(parts)


_ocr_engine = None


def _ocr_page(page) -> str:
    """单页 → 图片 → RapidOCR → 按行拼文本。"""
    global _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:  # pragma: no cover - 环境缺依赖时给可读的错
        raise RuntimeError("扫描版 PDF 需要 rapidocr-onnxruntime") from e

    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "page.png"
        page.get_pixmap(dpi=200).save(str(png))
        result, _ = _ocr_engine(str(png))
    return "\n".join(line[1] for line in (result or []))


def _markitdown(src: Path) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as e:  # pragma: no cover - 环境缺依赖时给可读的错
        raise RuntimeError("pdf/xlsx 转换需要 markitdown（pip install markitdown）") from e

    return MarkItDown().convert(str(src)).text_content or ""


# ---------------- pptx（python-pptx） ----------------

def _pptx(src: Path) -> Conversion:
    """逐页取标题/正文/表格/图表/图片，页与页之间用分隔线。"""
    try:
        from pptx import Presentation
    except ImportError as e:  # pragma: no cover - 环境缺依赖时给可读的错
        raise RuntimeError("pptx 转换需要 python-pptx（pip install python-pptx）") from e

    prs = Presentation(str(src))
    images: list[ExtractedImage] = []
    slides: list[str] = []

    for no, slide in enumerate(prs.slides, 1):
        block = [f"## 第 {no} 页"]
        title = slide.shapes.title
        if title is not None and title.has_text_frame and title.text_frame.text.strip():
            block.append(f"### {_one_line(title.text_frame.text)}")
        for shape in _iter_shapes(slide.shapes):
            if title is not None and shape == title:
                continue
            pic = _shape_picture(shape)
            if pic is not None:
                images.append(ExtractedImage(
                    data=pic.blob, ext=f".{pic.ext}",
                    original_name=f"slide{no}.{pic.ext}", position=f"第 {no} 页",
                ))
                block.append(PLACEHOLDER % len(images))
                continue
            text = _shape_text(shape)
            if text:
                block.append(text)
        notes = _notes_text(slide)
        if notes:
            block.append(f"> 备注：{notes}")
        slides.append("\n\n".join(b for b in block if b))

    return Conversion("\n\n---\n\n".join(slides), images)


def _iter_shapes(shapes):
    """递归展开组合形状（group 里的文本和图片也要读到）。"""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


def _shape_picture(shape):
    """图片形状 → 其 image；普通形状返回 None。

    图片既可能是 PICTURE，也可能是被插入图片的图片占位符（PLACEHOLDER），
    两者都靠 `.image` 暴露字节。
    """
    try:
        return shape.image
    except (AttributeError, ValueError):
        return None


def _shape_text(shape) -> str:
    if shape.has_table:
        return _table_md(shape.table)
    if shape.has_chart:
        return _chart_md(shape.chart)
    if shape.has_text_frame and shape.text_frame.text.strip():
        return _text_frame_md(shape.text_frame)
    return ""


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text_frame_md(tf) -> str:
    """文本框：段落按层级变列表项，空段落跳过。"""
    lines = []
    for p in tf.paragraphs:
        text = _one_line(p.text)
        if text:
            lines.append(f"{'  ' * p.level}- {text}")
    return "\n".join(lines)


def _md_table(head: list[str], rows: list[list[str]]) -> str:
    """管道表：表头一行 + 分隔行 + 数据行（单元格里的竖线要转义）。"""
    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    out = [line(head), line(["---"] * len(head))]
    out += [line(row) for row in rows]
    return "\n".join(out)


def _table_md(table) -> str:
    """pptx 表格：首行当表头。"""
    cells = [[_one_line(c.text) for c in row.cells] for row in table.rows]
    if not cells:
        return ""
    return _md_table(cells[0], cells[1:])


def _chart_md(chart) -> str:
    """图表：标题 + 分类/系列数据表（图表图片本身不进图片库）。"""
    try:
        title = ""
        if chart.has_title:
            title = _one_line(chart.chart_title.text_frame.text)
        plot = chart.plots[0]
        cats = [str(c) for c in plot.categories]
        series = list(plot.series)
        rows = []
        for i, cat in enumerate(cats):
            vals = []
            for s in series:
                try:
                    vals.append(_num(s.values[i]))
                except (IndexError, TypeError):
                    vals.append("")
            rows.append([cat] + vals)
        head = ["分类"] + [_one_line(s.name) for s in series]
        heading = f"### 图表：{title}" if title else "### 图表"
        return f"{heading}\n\n{_md_table(head, rows)}"
    except (AttributeError, IndexError, ValueError):
        return "### 图表"


def _num(value) -> str:
    """图表数值：整数不带小数点，其余原样。"""
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _notes_text(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    tf = slide.notes_slide.notes_text_frame
    return _one_line(tf.text) if tf is not None else ""
