## 完成

飞书查询入口已通：独立自建应用 + WeKnora 原生飞书渠道（websocket），私聊 `/search` 返回原文片段 + 匹配度，自然语言返回 AI 总结。本机 **249 passed / 1 skipped**，CI **248 passed / 2 skipped**。

**验收标准全部达成**：

1. ✅ 私聊 `/search 浮标` 返回最多 5 条原文片段 + 匹配度（实际 5/9 条，36%）
2. ✅ 自然语言「找一下有没有关于浮标的总体设计方案？」返回 AI 总结（WeKnora session `f0002acc…` 的 assistant 消息 `is_completed=true`，内容完整）
3. ✅ 建应用/开权限/事件订阅/发布/验收/排障清单写入 `docs/feishu-setup.md`（含批量导入 JSON、共享 app 的坑、`chat model` 排障）

**线上配置**：

- 新独立应用 `cli_aa7c80bdd038dc06`，bot「文档全量库搜索weknora助手」
- scopes：`im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly`、`im:message:send_as_bot`、`im:resource`、`cardkit:card:write`、`im:chat:read`、`application:application:self_manage`
- 事件：线上版本已订阅 `im.message.receive_v1`，长连接模式
- 渠道 `15de54fd-8814-4729-8d44-25f724a95aa8`，enabled；Agent「052 文档助手」绑「项目资料库」
- 飞书长连接数：1（只有 WeKnora）

**实现**：

- `src/docarchive/feishu.py`：`setup` 幂等地找/建 Agent、建/更新飞书渠道，按 `feishu:<app_id>` 判 bot 占用；`status` 用 `channel_view` 补 v0.8.0 摘要缺失的 KB/凭据字段。
- `weknora.py`：Agent 列表/详情/创建/配置更新、IM 渠道列表/创建/更新；`create_agent` 复制内置「快速问答」模板并钉 KnowledgeQA `model_id`。
- CLI：`python -m docarchive feishu setup|status`；凭据只读 `.env`，不打印 secret。
- 文档：`docs/feishu-setup.md`、`docs/weknora-api.md` Agent/IM 渠道契约、spec §8.1、AGENTS.md 指针。

**真机踩到的坑（都已修/记）**：

1. **不能复用已在跑别的机器人的飞书 app**：首次误用 Cola 的 app，`online_instance_cnt=2`；飞书长连接是集群推送、不广播，`/search` 被 Cola 那条连接收走。删除共用渠道后连接数 2→1；改用独立 app 后稳定为 1。
2. **事件配置看线上版本**：控制台「当前草稿」的 `events` 可能为空，但已发布版本的 `event_infos` 有 `im.message.receive_v1`；以线上版本为准。
3. **自建 Agent 必须显式设 chat model**：只传 `agent_mode + KB` 会报 `chat model is not configured`；已复制内置快速问答的 prompt/context 模板并设 `model_id=builtin-llm-minimax`。`create_agent` 已修，避免下次重装再踩。
4. **v0.8.0 的 IM 渠道摘要不回 `knowledge_base_id`/`credentials_configured`**：`feishu status` 从绑定 Agent 的 KB 补，凭据看 `bot_identity`。
5. **websocket 不需要回调/加密**：回调配置「未配置」、Encrypt Key 未开都正常；Verification Token 不影响长连接。

**两轴 code review 后修掉的问题**：

- 按 app_id 判 bot 占用（不是按 platform 一刀切），允许不同飞书应用各自建渠道；先查占用再建 Agent，避免孤儿 Agent。
- 改 Agent 配置前取详情，避免列表截断 config 被整体覆盖。
- 渠道更新空响应报错；`status` 的 KB/凭据兜底与 `platform` 展示。
- `docs/weknora-api.md` 补 `PUT /agents/:id` 必须带 `name` 的 v0.8.0 坑；spec 修正 websocket 只需 `app_id`/`app_secret`。
- `create_agent` 默认模板/模型；补批量导入 JSON 与共享 app 清理步骤。

**已知限制**：

- 当前渠道绑的是「项目资料库」（唯一有数据的 KB）。`052 文档库` 建好并 `index --wait` 后，重跑 `feishu setup --kb-name "052 文档库"` 会自动改绑 Agent 与渠道。
- 一个飞书 app 只能给一个 client 用；不要和 Cola/OpenClaw/Hermes 共用（用 `GET /open-apis/event/v1/connection` 查 `online_instance_cnt`，应为 1）。
- 飞书权限/事件/发布只能在开发者后台操作；`application:application:self_manage` 只让本工具用 API 核对配置，不能替代人工开通。
- `/search` 是斜杠命令，不创建 WeKnora 会话；自然语言问答会创建会话。
- 旧 Cola 的聊天记录/机器人历史仍在该飞书 app 里，不影响新入口。

实现：`feishu.py`、`weknora.py` Agent/IM 渠道方法、CLI `feishu setup/status`、`docs/feishu-setup.md`、`docs/weknora-api.md`、spec §8.1、AGENTS.md。
