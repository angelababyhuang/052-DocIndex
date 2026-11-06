## 完成

014 → 052 全量只读迁移已实现（TDD，12 测试绿），并对真实 014 数据实测通过。

**迁移方案**：只迁元数据（track.db 的数据库记录），**不复制、不移动任何文档文件**。archive_path 保留 014 的相对路径（如 `archive/doc-1/v1-x.docx`），文件原地不动，新库通过路径指针找到它们。磁盘只耗 3.1MB 元数据（可用 393GB）。

**实测结果**（真实 014 数据）：

| 项 | 结果 | 预期 |
|---|---|---|
| projects | 14 | 14 ✅ |
| documents | 559 | 559 ✅ |
| versions | 820 | 820 ✅ |
| occurrences | 5316 | 5316 ✅ |
| 指纹复算 | 0 差异（688 个可复算文件） | ✅ |

**实现要点**：
- 只读打开源库（`file:...?mode=ro`），不碰 014 原库字节
- 幂等：重跑清空目标库重来（可回退）
- 指纹算法与 014 完全一致（sha256_stream / norm_text / name_key / text_fingerprint）
- CLI：`python -m docarchive migrate <src.db> <dst.db> [--verify]`

**TDD 抓到的 bug**：RED 测试暴露「重跑不幂等」（projects.name UNIQUE 冲突），补上开头清空逻辑；复算脚本的相对路径 bug 也被实测当场抓住修正。

位置：`src/docarchive/migrate.py`、`src/docarchive/__main__.py`、`tests/test_migrate.py`。
