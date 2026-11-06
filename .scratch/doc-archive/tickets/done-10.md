## 完成

数据模型与真相源骨架已实现（TDD，7 测试绿）。

- **8 张表**：014 的 projects/documents/versions/occurrences + 新增 images/videos/document_images/document_project_tags + code_counters（号段计数器）
- **图片/视频独立实体**：`IMG-####` / `VID-####` 各自 ID 空间，单调递增、删除不回收（code_counters 与实体表解耦，避免 MAX+1 回收号段）
- **文档单归属 + 关联项目标签**：`project_id`（主归属）+ `document_project_tags`（关联）
- **文档↔图片关联**：`document_images`（embedded/standalone + 位置）

实现位置：`src/docarchive/db.py`，测试 `tests/test_db.py`。

一个实现中发现的设计补充：'永不改号/永不复用'需要独立号段计数器，不能靠 `SELECT max(code)` 从实体表算（删除会回退号段）。这个被 RED 测试当场抓住。
