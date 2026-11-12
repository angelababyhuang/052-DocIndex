# 飞书查询入口接入步骤（票 #16）

飞书入口走 **WeKnora 原生飞书渠道**（`platform: feishu`，WebSocket 长连接），052 不写机器人代码。
本机只要把「自建应用」建好、把凭据填进 `.env`，再用一个命令把渠道挂到「052 文档库」上。

- WeKnora 侧实现：`internal/im/feishu/`（长连接 + 流式卡片）、`internal/im/cmd_search.go`（`/search`）
- 052 侧命令：`python -m docarchive feishu setup` / `feishu status`
- 代码：`src/docarchive/feishu.py`、`src/docarchive/weknora.py`

## 0. 先决条件

1. 已经能登录 WeKnora，且当前账号是空间 **Admin**（建 Agent / 建渠道要 Admin）。
2. 知识库已经建好并解析完成（`052 文档库`）：
   ```bash
   python -m docarchive index --kb-name "052 文档库" --wait
   ```
3. 有一个飞书账号，并且能在飞书开放平台创建 **企业自建应用**。

## 0.1 关键：一个应用只能给 WeKnora 用

飞书长连接是**集群推送，不广播**：同一个应用如果同时被 OpenClaw / Hermes / Cola 等网关和
WeKnora 连接，用户发的消息**只会随机落到其中一个 client**——表现就是「飞书里发了消息，
WeKnora 这边完全没反应」。

- 给 052 **单独建一个自建应用**，不要复用已经在跑别的机器人的 app。
- 上线后可以在飞书开放平台用「获取长连接在线数量」接口核对：
  `GET https://open.feishu.cn/open-apis/event/v1/connection`（tenant_access_token 鉴权），
  `online_instance_cnt` 应为 **1**；大于 1 说明还有别的 client 在抢事件。
- WeKnora 侧 `feishu status` 只能看到渠道存在，**看不到飞书连接数**；这个要单独查。

## 0.2 复用旧 app 前先清干净

如果这个飞书 app 以前被 Cola / OpenClaw / Hermes 等网关用过，先把它清成「只有 WeKnora 一个 client」再配置：

1. **重置 App Secret**（推荐，能一次性踢掉所有旧 client）：
   开发者后台 → 应用 → **凭证与基础信息 → App Secret → 重置**。
   旧 client 拿旧 secret 无法再建长连接；把新 secret 填回 `.env` 的 `FEISHU_APP_SECRET`。
2. **强制断开已有长连接**：控制台若提供「停用应用/停用版本」，停用 1 分钟后重新启用；
   否则重启仍在跑旧网关的机器/服务。仅重置 secret 时，已建立的长连接可能要等对端重连才消失。
3. **清掉旧网关的残留**（可选，但建议）：
   - 跑过 Cola/OpenClaw/Hermes 的机器上停掉对应网关/服务，避免它继续自动重连；
   - 本机遗留目录 `~/.cola`、`~/.hermes`、`~/.openclaw` 只是配置/数据，没有进程时不影响；
     确认不再使用可以删除或改名。
4. **精简应用配置**：事件订阅保留 `im.message.receive_v1`（长连接）和需要的消息事件即可；
   Cola 时代的 reaction / 文档评论 / 机器人进出群事件、机器人菜单等可以删掉。
   应用名/机器人名可以改成「052 文档助手」。
5. 用 `GET /open-apis/event/v1/connection` 确认 `online_instance_cnt = 0`，再跑 `feishu setup`；
   配好后应稳定为 **1**（只有 WeKnora）。
6. 旧 chat 里的历史消息不会随 app 配置清除；不影响新机器人，介意的话在飞书客户端里单独清会话。

> 最省事的替代方案：**不复用旧 app，直接新建一个 052 专用应用**。旧 app 留着不用即可，
> 不需要做上面的 secret 重置/网关清理。

## 1. 创建飞书自建应用

1. 打开 <https://open.feishu.cn/app>，用飞书账号登录。
2. **创建企业自建应用**：填名称（例如「052 文档助手」）、描述、图标。
3. 进入应用 → **凭证与基础信息**，记下：
   - `App ID`（形如 `cli_xxxxxxxx`）
   - `App Secret`
4. 把这两个值写进本机 `.env`（**不要提交**，`.env` 已在 `.gitignore`）：
   ```dotenv
   FEISHU_APP_ID=cli_xxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxx
   # WebSocket 模式不需要下面两个；留空即可
   FEISHU_VERIFICATION_TOKEN=
   FEISHU_ENCRYPT_KEY=
   ```

> `.env.example` 已经把四个键列好；`FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` 只在
> **webhook 模式**做签名校验时用，长连接模式不填。

## 2. 开启机器人能力

应用详情 → **应用能力 → 添加应用能力** → 找到 **机器人** → 添加。
机器人名称会显示在飞书聊天里，建议改成「052 文档助手」之类。

## 3. 开通权限

应用详情 → **权限管理 → 开通权限**，至少开通：

| 权限 | 用途 | 必需 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 接收用户私聊机器人的消息 | 是 |
| `im:message:send_as_bot` | 以机器人身份回复消息 | 是 |
| `im:resource` | 下载用户发来的文件/图片 | 是（文件消息） |
| `cardkit:card:write` | WeKnora 的流式卡片更新 | 是（流式回复） |
| `im:message.group_at_msg:readonly` | 接收群里 @机器人 的消息 | 群聊阶段再开 |
| `im:chat:readonly` | 读取群信息（群聊回复兜底） | 可选 |

可以直接在「权限管理 → 批量导入/导出权限」粘贴这段 JSON（`user` 留空）：

```json
{
  "scopes": {
    "tenant": [
      "im:message.p2p_msg:readonly",
      "im:message.group_at_msg:readonly",
      "im:message:send_as_bot",
      "im:resource",
      "cardkit:card:write",
      "im:chat:read",
      "application:application:self_manage"
    ],
    "user": []
  }
}
```

`application:application:self_manage` 只用于之后用 API 核对应用配置；不加也能收发消息，只是我无法直接读 scopes 列表。

飞书权限的判定规则见官方文档 [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)：
私聊消息必须要有 `im:message.p2p_msg` 或 `im:message.p2p_msg:readonly`；群聊 @ 消息需要
`im:message.group_at_msg` 系列权限。改完权限后要**创建新版本并发布**才生效。

## 4. 配置事件订阅（长连接）

应用详情 → **事件与回调 → 事件配置**：

1. 订阅方式选 **使用长连接接收事件**（WebSocket）。选择后不需要填请求地址。
2. 在「消息与群组」分类下订阅 **接收消息 v2.0**（`im.message.receive_v1`）。
3. 加密策略里的 Verification Token / Encrypt Key 只在 webhook 模式使用；长连接模式留空。

> WeKnora 的飞书适配器由 `internal/im/feishu/longconn.go` 主动连飞书网关；它不需要公网回调地址，
> 也不校验 `verification_token` / `encrypt_key`。本机/内网部署只要出网能到 `open.feishu.cn` 即可。

## 5. 发布应用

应用详情 → **版本管理与发布 → 创建版本**：

- 可用范围至少包含你自己（测试阶段建议只勾自己）。
- 提交发布，按企业设置走管理员审核；测试企业可免审。
- 发布后，机器人才会真正收到事件。

## 6. 挂到 WeKnora

```bash
python -m docarchive feishu setup --kb-name "052 文档库"
```

它会：

1. 按名找 `052 文档库`；没有就报错并提示先 `index`；
2. 找名为「052 文档助手」的 Agent，没有就建一个（`quick-answer`，只绑这个 KB）；
3. 找这个 Agent 的飞书渠道：没有就建，有就更新凭据/KB/启用状态；
4. 打印 channel id、agent id、KB 和凭据状态。

查看状态：

```bash
python -m docarchive feishu status
```

预期能看到 `platform=feishu`、`enabled=是`、`credentials=已配置`、`kb=<052 文档库 id>`。

> 一个飞书应用（同一 `app_id`，bot identity 为 `feishu:<app_id>`）只能挂一个 WeKnora 渠道。若返回「已绑定另一个渠道」，
> 先在 WeKnora 的「设置 → IM 集成」里删掉/改绑旧渠道，或直接复用它。

## 7. 验收

在飞书里私聊机器人（第一次先点开应用/添加机器人）：

1. **内容检索**：发 `/search 连杆间隙`
   - 预期：最多 5 条原文片段，每条带匹配度百分比；**不经 AI 总结**。
2. **自然语言问答**：发「浮标在海上怎么上报数据？」
   - 预期：基于知识库的 AI 总结回答（带引用）。
3. `/help`、`/info` 可用；`/info` 会显示当前 Agent 绑定的知识库。

群聊阶段（跑通私聊后）：把机器人拉进群，@机器人 提问；需要 `im:message.group_at_msg:readonly`。

## 8. 排障

| 现象 | 先查 |
|---|---|
| 私聊发消息完全没反应 | 先查这个 app 是否被别的网关共用：`GET /open-apis/event/v1/connection` 的 `online_instance_cnt` 是否 >1（长连接事件随机分发）；再查应用是否已发布、机器人能力、事件订阅 `im.message.receive_v1`、权限审批、`feishu status` 的 `enabled` |
| 回复「权限不足 / 99991672」 | 权限没开或没发布新版本；对照 §3 重开并重新发布 |
| 收不到群消息 | 群聊权限 `im:message.group_at_msg:readonly`；是否 @ 了机器人 |
| 流式卡片不更新/报 `cardkit` 错误 | `cardkit:card:write` 没开；WeKnora 会退回普通消息 |
| `/search` 永远 0 条 | `052 文档库` 是否已 `index --wait`；Agent 是否绑错 KB；知识是否 `completed` |
| `feishu setup` 报「知识库不存在」 | 先跑 `python -m docarchive index --kb-name "052 文档库"` |
| `feishu setup` 报「已绑定另一个渠道」 | 一个 bot 只能一个渠道；去 WeKnora「设置 → IM 集成」删旧渠道 |
| WeKnora 连不上飞书 | 服务器能否访问 `open.feishu.cn`；如走反代，给渠道凭据加 `api_base_url` |

## 9. 安全与运维

- `FEISHU_APP_SECRET` 只放 `.env` / WeKnora 渠道凭据；不要写进文档、日志或提交。
- `feishu status` 不会打印凭据，只显示 `credentials_configured`。
- 换应用/换 bot：先删旧渠道（或改绑），再改 `.env` 重新 `feishu setup`；同一个 bot 不能双挂。
- 升级 WeKnora 后重跑 `feishu status`，确认渠道仍 `enabled`；长连接由 WeKnora 的 Supervisor 自动重连。
