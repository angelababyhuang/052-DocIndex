## 完成

文档转 md + 图片抽取已实现（TDD，本机 54 测试全绿；CI Python 3.12 上 53 passed / 1 skipped——跳过的只有依赖 textutil 的 .doc 用例），端到端 CLI 跑通。

**验收标准全部达成**：

1. ✅ docx → md：表格（普通表出管道表，合并单元格自动退回 HTML table 保 colspan）、内嵌图、OMML 公式（转 TeX `$...$`，测试用正则卡住「不是被降级成纯文本/行内代码」）
2. ✅ 文本型 PDF → md：markitdown 出正文，PyMuPDF 判文字层、补页面文字
3. ✅ 扫描版 PDF → OCR：本机 RapidOCR 逐页渲染识别；图文混排时只 OCR 无文字的页
4. ✅ pptx → 文本/表格/图表/图片：python-pptx 读，含图片占位符（shape_type=PLACEHOLDER）和组合形状递归
5. ✅ 内嵌图进图片库：sha256 去重分配 `IMG-####`，母本落 `archive/images/`，`document_images` 记 embedded + 位置；同一张图跨文档复用同一 IMG-ID，重跑幂等

**转换器分工**：docx→pandoc、.doc→textutil→pandoc（仅 macOS）、文本 PDF→markitdown(+PyMuPDF)、扫描 PDF→RapidOCR、pptx→python-pptx、xlsx→markitdown、纯文本直读。

**落盘约定**（ADR-0001）：md 与受管文件同位（`doc-<ID>/v<vid>-*.md`），路径记 `versions.markdown_path`；md 里图片引用只留 `{{IMG-####}}`，不留 pandoc 的临时媒体路径。

**code review 两轴发现并修掉的问题**：

- pptx 图片占位符里的图被整张丢掉（只判 PICTURE 不判 PLACEHOLDER）→ 改按 `.image` 属性取图
- pptx 组合形状里的文本/图片丢失 → 递归展开 group
- 文本型 PDF 的插图从不入库（AC⑤ 只覆盖了 docx/pptx）→ PDF 文本页的内嵌图按页抽出
- pandoc 图片路径兜底原来相对源文件解析 → 改按「绝对/相对输出目录/相对媒体目录」依次找
- 扫描版判定有两个阈值口径 → 统一成「一页都没有文字层才算扫描版」
- 缩略公式断言太弱、测试名误导、`Conversion`/`ConvertResult` 命名过近等一并修掉

**顺手修的基建**：

- CI 一直是红的：`anydoc` 的 PyPI 版只支持到 3.10，CI 用 3.12 装不上 → requirements 加环境标记，指纹跨平台兜底改成 anydoc→markitdown 两级（textutil 主路径不动，014 一致性不受影响）
- `db.connect` 自动建父目录（默认 `data/truth.db` 首次直接跑会报 unable to open database file）

实现：`src/docarchive/convert.py`（转换器 + `convert_version`）、`src/docarchive/imagelib.py`（去重登记 + `image_ref`），CLI 加 `convert <版本ID>...`。
