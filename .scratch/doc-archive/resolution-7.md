## 决议

| 项 | 决策 |
|---|---|
| 飞书机器人路线 | **WeKnora 原生飞书渠道**（`platform: feishu`，websocket 长连接），零自建代码 |
| 交互形态 | **检索 + 问答都要**：第一阶段先用 `/search`（直接混合检索返回原文片段，不经 AI 总结）定位资料；问答能力随渠道免费附带 |
| 服务范围 | **先私聊，跑通后再群聊**（群聊涉及 @机器人、话题回复） |
| 建应用 | **用户执行**，agent 给精确步骤清单；**执行后置**——飞书机器人不阻塞主线，等主线跑通再建 |

**所需凭证**（飞书开放平台自建应用）：`app_id`、`app_secret`、`verification_token`、`encrypt_key`。

**关键事实**（已核源码）：WeKnora 内置 `/search` 命令直接对 Agent 可达知识库做混合检索（向量+关键词），返回原文片段、最多 5 条、每条 200 rune、附匹配度百分比，**不经 AI 总结**——完全匹配"快速定位资料"。文件/图片消息会作为 QA 附件处理。

**与 lark-cli 的关系**：本机 lark-cli（现有应用 `cli_a9424281a5389bb6`，bot ready，有 im:message/chat:read/group_msg 权限）与 WeKnora 飞书渠道是**不同身份**——WeKnora 要应用凭证（app_secret），lark-cli 用用户授权（user token），不互用。lark-cli 保留作开发期手动调试工具。

**执行后置**：飞书建应用的开权限清单写进 spec，作为主线跑通后的接入步骤，不是本轮阻塞。
