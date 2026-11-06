## 完成

摄入与去重管道已实现（TDD，22 测试全绿），真实数据实测通过。

**五个验收标准全部达成**：

1. ✅ 扫描目录登记出现（source_type/source_path/file_name/size/byte_hash/text_fp）
2. ✅ 字节相同 → duplicate_byte（实测两个相同文件正确检出）
3. ✅ 内容相同字节不同 → duplicate_content（text_fp 命中）
4. ✅ 名称归一化相似但指纹不同 → new_version
5. ✅ 永不按文件名去重（微信 `(1)` 后缀归一化成不同 name_key，不误判）

**TDD 抓到的关键 bug**：suggestion 的自匹配——扫描时每条出现查自己，byte_hash 必命中自己，导致所有文件误报 duplicate_byte。真实数据当场暴露（两个不同 jpg 都报重复），补 exclude_id 排除自身后修复。这是「先写测试 + 真数据验证」才能抓到的。

**去重语义**：byte_hash 优先于 name_key。`方案.txt` 和 `方案(1).txt` 被判重复是因为 byte_hash 相同（内容同），不是因为文件名——符合"微信 (1) 是同名避让，但内容相同时仍是重复"的正确语义。

实现：`src/docarchive/ingest.py`（register/scan_dir/suggestion），CLI `python -m docarchive scan`。
