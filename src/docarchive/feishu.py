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


def setup(client: FeishuClient, *, kb_name: str, credentials: Mapping[str, str],
          agent_name: str = DEFAULT_AGENT_NAME,
          channel_name: str = DEFAULT_CHANNEL_NAME) -> SetupReport:
    """幂等地配置飞书渠道：找/建 Agent，再建/更新它的飞书渠道。

    - Agent 只绑指定知识库（`kb_selection_mode=selected`），避免搜到别的库；
    - 同一个 bot（同一 app_id）只能有一个 WeKnora 渠道；若已被别的 Agent 占用，
      直接报错，不偷偷改绑。
    """
    kb_id = client.find_kb(kb_name)
    if not kb_id:
        raise FeishuSetupError(
            f"知识库「{kb_name}」不存在；先跑 index --kb-name \"{kb_name}\" 建索引"
        )

    agent = client.find_agent(agent_name)
    if agent:
        if agent.get("is_builtin"):
            raise FeishuSetupError(
                f"Agent「{agent_name}」是内置 Agent；换一个 --agent-name，"
                f"不要改内置 Agent 的配置"
            )
        agent_id = str(agent["id"])
        created_agent = False
        config = dict(agent.get("config") or {})
        selected = config.get("kb_selection_mode") == "selected"
        if not selected or kb_id not in (config.get("knowledge_bases") or []):
            config["kb_selection_mode"] = "selected"
            config["knowledge_bases"] = [kb_id]
            client.update_agent_config(agent_id, config, name=agent.get("name") or agent_name)
    else:
        agent_id = client.create_agent(agent_name, [kb_id])
        created_agent = True

    feishu_channels = [c for c in client.list_im_channels()
                       if c.get("platform") == FEISHU_PLATFORM]
    same_agent = next((c for c in feishu_channels
                       if c.get("agent_id") == agent_id), None)
    if same_agent:
        channel = client.update_im_channel(
            str(same_agent["id"]), name=channel_name, credentials=dict(credentials),
            knowledge_base_id=kb_id, enabled=True,
        ) or same_agent
        created_channel = False
    else:
        if feishu_channels:
            other = feishu_channels[0]
            raise FeishuSetupError(
                f"这个飞书机器人已绑定另一个渠道「{other.get('name') or other.get('id')}」"
                f"（agent_id={other.get('agent_id')}）；一个 bot 只能建一个渠道。"
                f"先在 WeKnora 里删除/改绑旧渠道，或复用它。"
            )
        channel = client.create_im_channel(
            agent_id, FEISHU_PLATFORM, channel_name, dict(credentials),
            knowledge_base_id=kb_id, mode="websocket",
        )
        created_channel = True

    return SetupReport(
        agent_id=agent_id,
        channel_id=str(channel.get("id") or ""),
        knowledge_base_id=kb_id,
        created_agent=created_agent,
        created_channel=created_channel,
        enabled=bool(channel.get("enabled", True)),
        credentials_configured=bool(channel.get("credentials_configured", True)),
    )
