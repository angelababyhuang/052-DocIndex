"""飞书查询入口：把 WeKnora 原生飞书渠道配置好（票 #16）。

WeKnora 自带飞书适配器（websocket 长连接），052 不写机器人代码；这里只做
「建/选一个只绑 052 文档库的 Agent → 建/更新飞书渠道 → 写凭据」。

凭据从 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 来。websocket 模式不需要
`verification_token` / `encrypt_key`（那是 webhook 签名用的）；建应用与开权限的
人工步骤见 `docs/feishu-setup.md`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

FEISHU_PLATFORM = "feishu"
DEFAULT_AGENT_NAME = "052 文档助手"
DEFAULT_CHANNEL_NAME = "052 飞书入口"


class FeishuSetupError(RuntimeError):
    """飞书渠道配置的前置条件不满足（缺凭据、缺知识库、bot 被占用等）。"""


class FeishuClient(Protocol):
    """配置飞书渠道要的 WeKnora 能力。"""

    def find_kb(self, name: str) -> str | None: ...

    def find_agent(self, name: str) -> dict | None: ...

    def get_agent(self, agent_id: str) -> dict: ...

    def create_agent(self, name: str, knowledge_base_ids: Iterable[str]) -> str: ...

    def update_agent_config(self, agent_id: str, config: Mapping,
                            name: str | None = None) -> dict: ...

    def list_im_channels(self) -> list[dict]: ...

    def create_im_channel(self, agent_id: str, platform: str, name: str,
                          credentials: Mapping[str, str], *,
                          knowledge_base_id: str = "",
                          mode: str = "websocket") -> dict: ...

    def update_im_channel(self, channel_id: str, **fields) -> dict: ...


@dataclass(frozen=True)
class SetupReport:
    agent_id: str
    channel_id: str
    knowledge_base_id: str
    created_agent: bool
    created_channel: bool
    enabled: bool
    credentials_configured: bool


def credentials_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """取飞书自建应用凭据；websocket 模式只需要 app_id / app_secret。"""
    app_id = (env.get("FEISHU_APP_ID") or "").strip()
    app_secret = (env.get("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise FeishuSetupError(
            "缺少 FEISHU_APP_ID / FEISHU_APP_SECRET；先在 .env 填飞书自建应用凭证，"
            "建应用与开权限步骤见 docs/feishu-setup.md"
        )
    return {"app_id": app_id, "app_secret": app_secret}


def feishu_channels(channels: Iterable[dict]) -> list[dict]:
    """从 IM 渠道总览里挑出飞书渠道（CLI/测试共用，别各写一份）。"""
    return [c for c in channels if c.get("platform") == FEISHU_PLATFORM]


def bot_identity(credentials: Mapping[str, str]) -> str:
    """WeKnora 的飞书 bot 身份：`feishu:<app_id>`（同 app 只能挂一个渠道）。"""
    app_id = (credentials.get("app_id") or "").strip()
    return f"{FEISHU_PLATFORM}:{app_id}" if app_id else ""


def channel_view(channel: Mapping, agents: Mapping[str, Mapping]) -> dict:
    """把 v0.8.0 的渠道摘要补成可展示的一行（KB 从绑定 Agent 补）。"""
    agent = agents.get(channel.get("agent_id") or "") or {}
    kb_id = channel.get("knowledge_base_id") or next(
        iter((agent.get("config") or {}).get("knowledge_bases") or []), "",
    )
    configured = channel.get("credentials_configured")
    if configured is None:  # v0.8.0 的摘要不带这个字段
        configured = bool(channel.get("bot_identity"))
    return {
        "id": channel.get("id") or "",
        "name": channel.get("name") or "-",
        "platform": channel.get("platform") or "",
        "agent_id": channel.get("agent_id") or "-",
        "kb_id": kb_id or "-",
        "enabled": bool(channel.get("enabled")),
        "configured": bool(configured),
    }


def setup(client: FeishuClient, *, kb_name: str, credentials: Mapping[str, str],
          agent_name: str = DEFAULT_AGENT_NAME,
          channel_name: str = DEFAULT_CHANNEL_NAME) -> SetupReport:
    """幂等地配置飞书渠道：找/建 Agent，再建/更新它的飞书渠道。

    - Agent 只绑指定知识库（`kb_selection_mode=selected`），避免搜到别的库；
    - 同一个飞书应用（`app_id`，对应 `feishu:<app_id>` bot identity）只能挂一个
      渠道；若已被别的 Agent 占用，直接报错，不偷偷改绑；
    - 先做占用检查再建 Agent，避免失败时留下孤儿 Agent。
    """
    kb_id = client.find_kb(kb_name)
    if not kb_id:
        raise FeishuSetupError(
            f"知识库「{kb_name}」不存在；先跑 index --kb-name \"{kb_name}\" 建索引"
        )

    agent = client.find_agent(agent_name)
    if agent and agent.get("is_builtin"):
        raise FeishuSetupError(
            f"Agent「{agent_name}」是内置 Agent；换一个 --agent-name，"
            f"不要改内置 Agent 的配置"
        )

    channels = feishu_channels(client.list_im_channels())
    agent_id = str(agent["id"]) if agent else ""
    existing_channel = next(
        (c for c in channels if agent_id and c.get("agent_id") == agent_id), None,
    )
    expected_identity = bot_identity(credentials)
    bot_channel = next(
        (c for c in channels
         if expected_identity and c.get("bot_identity") == expected_identity), None,
    )
    if bot_channel and (existing_channel is None
                        or bot_channel.get("id") != existing_channel.get("id")):
        raise FeishuSetupError(
            f"这个飞书应用（app_id={credentials.get('app_id')}）已绑定另一个渠道"
            f"「{bot_channel.get('name') or bot_channel.get('id')}」"
            f"（agent_id={bot_channel.get('agent_id')}）；同一个 app 只能挂一个渠道。"
            f"先在 WeKnora 里删除/改绑旧渠道，或复用它。"
        )

    if agent:
        detail = client.get_agent(agent_id)
        if detail.get("is_builtin"):
            raise FeishuSetupError(f"Agent「{agent_name}」是内置 Agent；换一个 --agent-name")
        config = dict(detail.get("config") or {})
        selected = config.get("kb_selection_mode") == "selected"
        if not selected or kb_id not in (config.get("knowledge_bases") or []):
            config["kb_selection_mode"] = "selected"
            config["knowledge_bases"] = [kb_id]
            client.update_agent_config(
                agent_id, config, name=detail.get("name") or agent_name,
            )
        created_agent = False
    else:
        agent_id = client.create_agent(agent_name, [kb_id])
        created_agent = True

    if existing_channel:
        updated = client.update_im_channel(
            str(existing_channel["id"]), name=channel_name,
            credentials=dict(credentials), knowledge_base_id=kb_id, enabled=True,
        )
        if not updated.get("id"):
            raise FeishuSetupError(f"更新飞书渠道 {existing_channel['id']} 失败（返回空）")
        channel = updated
        created_channel = False
    else:
        channel = client.create_im_channel(
            agent_id, FEISHU_PLATFORM, channel_name, dict(credentials),
            knowledge_base_id=kb_id, mode="websocket",
        )
        created_channel = True

    configured = channel.get("credentials_configured")
    if configured is None:
        configured = bool(credentials.get("app_secret"))
    return SetupReport(
        agent_id=agent_id,
        channel_id=str(channel.get("id") or ""),
        knowledge_base_id=kb_id,
        created_agent=created_agent,
        created_channel=created_channel,
        enabled=bool(channel.get("enabled", True)),
        credentials_configured=bool(configured),
    )
