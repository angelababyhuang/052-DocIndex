# ADR-0001：文档转 md 的落盘位置与图片抽取约定

- 状态：已接受
- 日期：2026-11-06
- 相关票：#13（文档转 md + 图片抽取）、#14（媒体实体登记）、#15（WeKnora 索引）

## 背景

真相源是 SQLite + 图片文件；md 和向量索引都是从真相源派生的可重建物。但
「可重建」不等于「可以每次现算」：转一份扫描版 PDF 要跑 OCR，转一份大 docx 要
起 pandoc 子进程，重复转换纯属浪费。因此 md 落盘保存，并记下位置，让 #15 的索引
任务能直接读，不必自己再调一遍转换器。

同时有两个问题必须在转换时就定下来：

1. pandoc `--extract-media` 会往 md 里写**临时媒体路径**，这些路径随后就失效，
   写进索引就是脏数据。
2. 同一张图在多份文档里出现时，必须共用同一个 `IMG-####`（031 的 ADR-0001/0002：
   编号与内容绑定、永不改号）。

## 决策

**1. md 与受管文件同位落盘。**
`doc-1/v3-方案.docx` → `doc-1/v3-方案.md`，相对路径记在 `versions.markdown_path`。
md 与它的源文件同生共死，看得见、删得掉、能重建；`documents` 层面不另存。

**2. 图片在转换时抽出，md 里只留 `{{IMG-####}}`。**
转换阶段先在 md 里写 `{{IMG-1}}` 占位，登记进图片库（sha256 去重）后替换成真实
编号。md 里不出现任何临时媒体路径；图片母本统一存 `archive/images/IMG-####.<ext>`，
`images.path` 存相对受管目录的路径。

**3. 内嵌图与文档的关联记 `document_images`（kind=embedded, position）。**
position 尽量记人能看懂的位置（docx「第 N 张图」、pptx「第 N 页」）。同一张图在
同一文档里出现多次只挂一条（表主键就是 `document_id + image_id`），position 取首次。

**4. 转换器分工（spec §6.1 的落地细节）。**
docx 走 pandoc；文本型 PDF 走 markitdown；扫描/图文混排 PDF 走 PyMuPDF 逐页判定 +
RapidOCR 离线识别；pptx 走 python-pptx；xlsx 走 markitdown。PDF 用 PyMuPDF 而不是
pdfplumber 来判断有无文字层，因为渲染页面喂 OCR 也要它，一个库干两件事。

**5. OCR 一期只用本机 RapidOCR，云端 VLM 押后。**
spec §2.1 的目标态是 qwen3.8-flash VLM 主用、RapidOCR 兜底；但转换必须能离线跑，
且 WeKnora 接入（#15/#16）还没落地，没有可用的 VLM 通路。所以一期就只接
RapidOCR，判定为扫描版/无文字层的页逐页渲染后识别；VLM 作为增强在接入 WeKnora
时再开，不改转换器的调用契约（`_ocr_page` 一个函数）。

**6. PDF 的内嵌图按页抽出。**
判定为文本页的页，用 PyMuPDF `page.get_images()` 取原始字节（`extract_image`
保留原格式），边长 < 32px 的当图标忽略；整页无文字的扫描页不抽——那一页本身就是
一张页面图，抽进图片库只会污染图片库。抽出的图收集在 md 末尾「## 内嵌图片」
一节，按页顺序给 `{{IMG-####}}`（正文里的精确插入位置由 PDF 排版决定，取不到可靠
对应关系，如实记 `document_images.position = 第 N 页`）。

**7. pandoc 的表格只留 pipe table。**
普通表格输出紧凑的 `| a | b |`；遇到合并单元格，pandoc 会自动退回 HTML
`<table>`（保住 colspan），比 pandoc 默认的 grid 表更好读、更好 grep。公式用
`+tex_math_dollars` 保住 OMML → TeX（gfm 会把公式降级成行内代码，等于丢公式）。

## 后果

- md 是**派生物**，不是真相源：删了能重建，`versions.markdown_path` 只能当线索，
  取不到时应重跑 `convert` 而不是报错终止。
- 图片母本一旦落盘，编号就跟着内容绑死；同一张图永远只有一个 `IMG-####`，
  但每份引用它的文档各有一条 `document_images`。
- 转换器有系统/三方依赖（pandoc、PyMuPDF、RapidOCR、python-pptx、markitdown）。
  缺依赖时给出可读的 `RuntimeError`，测试按依赖可用性跳过，不让整套测试挂掉。
- `.doc` 依赖 macOS 的 textutil，Linux 上暂不支持，属于已知缺口。
- **caption / 宽高留给 #14**：转换只管「抽出图片 + 分配 IMG-ID + 记来源/位置」。
  caption（VLM 生成）和尺寸是媒体实体登记的活，等 #14 落库时补齐，
  md 的检索价值先靠正文。
- **不另存 031 式的 `索引.tsv`**：052 的图片登记表就是真相源的 `images` 表。
  多写一份 tsv 就是双源，迟早漂移（spec 开篇点名的头号失败原因）。
- pptx 的**图表**数据会转成 md 表（spec §6.1 要求读图表），**演讲者备注**也随
  正文一起落到 `> 备注：` 一行——都是幻灯片里人真正写下的内容，丢了可惜。
