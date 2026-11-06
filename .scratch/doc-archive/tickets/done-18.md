## 完成

归档落库 + triage 人工确认已实现（TDD，31 测试全绿），端到端 CLI 实测通过。

**验收标准全部达成**：

1. ✅ 确认为新文档 → 建 document + version + 复制进 `archive/doc-<ID>/v<vid>-<文件名>`
2. ✅ 确认为某文档新版本 → 建新 version + 复制 + 更新 current_version_id
3. ✅ 确认为重复 → 仅记录出现位置（status=merged），不复制文件
4. ✅ 确认为忽略 → status=ignored，不复制
5. ✅ 原件不动（复制非移动）、扫描幂等
6. ✅ status 流转 pending → archived/merged/ignored，人工触发（已处理的再处理会报错）

**补齐 code review 发现的 schema 缺口**：images/videos 加 `path` 列，videos 加 `project_id`（幂等 ALTER，兼容已建库）。

**端到端实测**：扫描 2 个相同文件 → 去重建议都判 duplicate → new-doc 归档 → merge 去重，文档/版本/出现状态全部正确。

实现：`src/docarchive/archive.py`（copy_to_archive/new_doc/new_version/merge/ignore），CLI 加 new-doc/new-version/merge/ignore 四命令。
