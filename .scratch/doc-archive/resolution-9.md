## 决议

**`/find` 文件定位命令延后开发**。第一阶段「文档在不在、存在哪」用 014 现有查找（`track.py find` / 迁移后的 SQLite 直查）兜底；等主线功能开发测试完成后，再开发 `/find` 接入查询入口。

| 项 | 决策 |
|---|---|
| 第一阶段 | 不做 `/find`。文件定位走 014 `cmd_find` 命令行 + 迁移后 SQLite 直查 |
| 延后开发 | 等摄入/转换/检索/飞书渠道等主线功能开发测试完成后，再开发 `/find` |
| 返回形态（已定，将来执行） | **直接返回原始结果**（文档 #、受管位置、出现历史），AI 不加工——文件定位是确定性元数据查询，不过模型（022 ADR-0008 纪律） |
| 接入路径（已定方向，将来执行） | 倾向 MCP server 注册进 WeKnora（SSE/HTTP Streamable，stdio 禁用，已核源码）；**开发前先实测验证**「WeKnora Agent 能否直接返回 MCP 工具原始结果、不经总结」——若不能，退回独立端点 |

**关键事实**（已核 WeKnora 源码）：
- 自定义斜杠命令（`/find` 直接加进 WeKnora）＝改 Go 源码 `CommandRegistry`，非配置项，排除。
- WeKnora 作 MCP client 能连外部 MCP server（SSE / HTTP Streamable，stdio 因安全禁用），这是 `/find` 的正路。
- `/find` 数据必须来自真相源 SQLite，不能从 WeKnora 派生索引拿。

**推论**：spec §7.4 的「两种查分开」仍成立，但「文件定位」在第一阶段是**离线能力**（命令行/SQLite），不进查询入口。spec §8.1 的 `/find` 命令标注为「第二阶段开发」。
