"""票 #16：飞书查询入口（WeKnora 原生渠道）。"""
import pytest

from docarchive import feishu


class FakeClient:
    def __init__(self, *, kb_id="kb-1", agent=None, channels=None):
        self.kb_id = kb_id
        self.agent = agent
        self.channels = list(channels or [])
        self.calls = []

    def find_kb(self, name):
        return self.kb_id

    def find_agent(self, name):
        return self.agent

    def get_agent(self, agent_id):
        self.calls.append(("get_agent", agent_id))
        return self.agent or {"id": agent_id}

    def create_agent(self, name, knowledge_base_ids):
        self.calls.append(("create_agent", name, tuple(knowledge_base_ids)))
        return "a-1"

    def update_agent_config(self, agent_id, config, name=None):
        self.calls.append(("update_agent_config", agent_id, dict(config), name))
        return {"id": agent_id}

    def list_im_channels(self):
        return list(self.channels)

    def create_im_channel(self, agent_id, platform, name, credentials, *,
                          knowledge_base_id="", mode="websocket"):
        self.calls.append(("create_im_channel", agent_id, platform, name,
                           dict(credentials), knowledge_base_id, mode))
        return {"id": "ch-1", "enabled": True, "credentials_configured": True}

    def update_im_channel(self, channel_id, **fields):
        self.calls.append(("update_im_channel", channel_id, fields))
        return {"id": channel_id, "enabled": True, "credentials_configured": True}


def test_credentials_from_env_reads_app_credentials():
    assert feishu.credentials_from_env({
        "FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret",
    }) == {"app_id": "cli_x", "app_secret": "secret"}


def test_credentials_from_env_requires_both():
    with pytest.raises(feishu.FeishuSetupError) as ei:
        feishu.credentials_from_env({"FEISHU_APP_ID": "cli_x"})
    assert "FEISHU_APP_SECRET" in str(ei.value)


def test_setup_creates_agent_and_channel():
    client = FakeClient()
    report = feishu.setup(client, kb_name="052 文档库",
                          credentials={"app_id": "cli_x", "app_secret": "s"})
    assert report == feishu.SetupReport(
        agent_id="a-1", channel_id="ch-1", knowledge_base_id="kb-1",
        created_agent=True, created_channel=True,
        enabled=True, credentials_configured=True,
    )
    assert client.calls == [
        ("create_agent", "052 文档助手", ("kb-1",)),
        ("create_im_channel", "a-1", "feishu", "052 飞书入口",
         {"app_id": "cli_x", "app_secret": "s"}, "kb-1", "websocket"),
    ]


def test_setup_updates_existing_channel_idempotently():
    client = FakeClient(
        agent={"id": "a-1", "name": "052 文档助手"},
        channels=[{"id": "ch-1", "platform": "feishu", "agent_id": "a-1"}],
    )
    report = feishu.setup(client, kb_name="052 文档库",
                          credentials={"app_id": "cli_x", "app_secret": "s"})
    assert report.created_agent is False
    assert report.created_channel is False
    assert client.calls == [
        ("get_agent", "a-1"),
        ("update_agent_config", "a-1", {
            "kb_selection_mode": "selected", "knowledge_bases": ["kb-1"],
        }, "052 文档助手"),
        ("update_im_channel", "ch-1", {
            "name": "052 飞书入口",
            "credentials": {"app_id": "cli_x", "app_secret": "s"},
            "knowledge_base_id": "kb-1",
            "enabled": True,
        }),
    ]


def test_setup_does_not_touch_agent_already_bound_to_kb():
    client = FakeClient(
        agent={"id": "a-1", "name": "052 文档助手", "config": {
            "kb_selection_mode": "selected", "knowledge_bases": ["kb-1"],
        }},
        channels=[{"id": "ch-1", "platform": "feishu", "agent_id": "a-1"}],
    )
    feishu.setup(client, kb_name="052 文档库",
                 credentials={"app_id": "cli_x", "app_secret": "s"})
    assert not [c for c in client.calls if c[0] == "update_agent_config"]


def test_setup_requires_existing_knowledge_base():
    client = FakeClient(kb_id=None)
    with pytest.raises(feishu.FeishuSetupError) as ei:
        feishu.setup(client, kb_name="052 文档库",
                     credentials={"app_id": "cli_x", "app_secret": "s"})
    assert "index" in str(ei.value)


def test_setup_refuses_bot_already_bound_to_another_agent():
    client = FakeClient(
        channels=[{"id": "ch-old", "platform": "feishu",
                   "agent_id": "a-other", "name": "旧渠道",
                   "bot_identity": "feishu:cli_x"}],
    )
    with pytest.raises(feishu.FeishuSetupError) as ei:
        feishu.setup(client, kb_name="052 文档库",
                     credentials={"app_id": "cli_x", "app_secret": "s"})
    assert "同一个 app" in str(ei.value)
    assert not [c for c in client.calls if c[0] in ("create_agent", "create_im_channel")]


def test_setup_allows_a_different_feishu_app():
    client = FakeClient(
        channels=[{"id": "ch-old", "platform": "feishu",
                   "agent_id": "a-other", "name": "旧渠道",
                   "bot_identity": "feishu:cli_other"}],
    )
    report = feishu.setup(client, kb_name="052 文档库",
                          credentials={"app_id": "cli_x", "app_secret": "s"})
    assert report.created_agent and report.created_channel


def test_setup_refuses_builtin_agent_name():
    client = FakeClient(agent={"id": "builtin-quick-answer", "is_builtin": True})
    with pytest.raises(feishu.FeishuSetupError) as ei:
        feishu.setup(client, kb_name="052 文档库",
                     credentials={"app_id": "cli_x", "app_secret": "s"})
    assert "内置 Agent" in str(ei.value)


def test_channel_view_falls_back_to_agent_kb_and_bot_identity():
    view = feishu.channel_view(
        {"id": "ch-1", "platform": "feishu", "agent_id": "a-1",
         "bot_identity": "feishu:cli_x", "enabled": True},
        {"a-1": {"config": {"knowledge_bases": ["kb-1"]}}},
    )
    assert view == {
        "id": "ch-1", "name": "-", "platform": "feishu",
        "agent_id": "a-1", "kb_id": "kb-1", "enabled": True, "configured": True,
    }


def test_setup_raises_when_channel_update_returns_empty():
    class EmptyUpdate(FakeClient):
        def update_im_channel(self, channel_id, **fields):
            return {}

    client = EmptyUpdate(
        agent={"id": "a-1", "name": "052 文档助手"},
        channels=[{"id": "ch-1", "platform": "feishu", "agent_id": "a-1",
                   "bot_identity": "feishu:cli_x"}],
    )
    with pytest.raises(feishu.FeishuSetupError) as ei:
        feishu.setup(client, kb_name="052 文档库",
                     credentials={"app_id": "cli_x", "app_secret": "s"})
    assert "更新飞书渠道" in str(ei.value)
