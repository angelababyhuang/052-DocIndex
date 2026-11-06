"""票 #15：WeKnora 客户端（不联网，_request 打桩）。"""
import io
import json
import urllib.error

import pytest

from docarchive import weknora


@pytest.fixture
def client():
    return weknora.WeKnoraClient("http://weknora.local/", token="tok")


def stub(monkeypatch, client, *responses):
    calls = []

    def fake(method, path, payload=None):
        calls.append((method, path, payload))
        idx = len(calls) - 1
        return responses[idx] if idx < len(responses) else {}

    monkeypatch.setattr(client, "_request", fake)
    return calls


def test_login_sets_token(monkeypatch):
    c = weknora.WeKnoraClient("http://weknora.local", email="a@b", password="pw")
    stub(monkeypatch, c, {"success": True, "data": {"token": "jwt-1"}})
    c.login()
    assert c.token == "jwt-1"


def test_login_without_credentials_raises(client):
    with pytest.raises(weknora.WeKnoraError):
        client.login()


def test_find_kb_returns_matching_id(monkeypatch, client):
    stub(monkeypatch, client, {"data": [
        {"id": "kb-1", "name": "别的库"}, {"id": "kb-2", "name": "052 文档库"},
    ]})
    assert client.find_kb("052 文档库") == "kb-2"
    assert client.find_kb("不存在") is None


def test_create_kb_posts_embedding_model(monkeypatch, client):
    calls = stub(monkeypatch, client, {"success": True, "data": {"id": "kb-9"}})
    assert client.create_kb("052 文档库", "emb-1") == "kb-9"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/v1/knowledge-bases")
    assert payload == {"name": "052 文档库", "type": "document",
                       "embedding_model_id": "emb-1"}


def test_delete_kb(monkeypatch, client):
    calls = stub(monkeypatch, client, {})
    client.delete_kb("kb-9")
    assert calls[0] == ("DELETE", "/api/v1/knowledge-bases/kb-9", None)


def test_embedding_model_prefers_name(monkeypatch, client):
    payload = {"data": [
        {"id": "m-1", "name": "bge-m3", "type": "embedding"},
        {"id": "m-2", "name": "text-embedding-v4", "type": "embedding"},
        {"id": "m-3", "name": "qwen3.7-text-rerank", "type": "rerank"},
    ]}
    stub(monkeypatch, client, payload, payload)
    assert client.embedding_model_id("text-embedding") == "m-2"
    assert client.embedding_model_id() == "m-1"


def test_embedding_model_type_variants(monkeypatch, client):
    stub(monkeypatch, client, {"data": [
        {"id": "m-1", "name": "x", "type": "Embedding"},
        {"id": "m-2", "name": "y", "model_type": "embedding_model"},
    ]})
    assert client.embedding_model_id() == "m-1"


def test_ensure_tag_reuses_existing(monkeypatch, client):
    calls = stub(monkeypatch, client, {"data": [{"id": "t-1", "name": "项目/浮标项目"}]})
    assert client.ensure_tag("kb-1", "项目/浮标项目") == "t-1"
    assert len(calls) == 1 and calls[0][0] == "GET"


def test_ensure_tag_creates_when_missing(monkeypatch, client):
    calls = stub(monkeypatch, client,
                 {"data": []},
                 {"success": True, "data": {"id": "t-2"}})
    assert client.ensure_tag("kb-1", "类别/测试") == "t-2"
    assert calls[1][0] == "POST"
    assert calls[1][2] == {"name": "类别/测试"}


def test_create_knowledge_manual_payload(monkeypatch, client):
    calls = stub(monkeypatch, client, {"success": True, "data": {"id": "k-1"}})
    kid = client.create_knowledge("kb-1", "连杆方案", "# 正文", ["t-1", "t-2"])
    assert kid == "k-1"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual")
    assert payload["title"] == "连杆方案"
    assert payload["content"] == "# 正文"
    assert payload["status"] == "publish"
    assert payload["tag_ids"] == ["t-1", "t-2"]


def test_set_knowledge_metadata_puts_custom_metadata(monkeypatch, client):
    """#21：图片清单走 custom_metadata（manual 接口没有 metadata 字段，只能事后 PUT）。"""
    calls = stub(monkeypatch, client, {"success": True})
    client.set_knowledge_metadata("k-1", {"图片": "IMG-0001,IMG-0002"})
    method, path, payload = calls[0]
    assert (method, path) == ("PUT", "/api/v1/knowledge/k-1")
    assert payload == {"custom_metadata": {"图片": "IMG-0001,IMG-0002"}}


def test_search_payload(monkeypatch, client):
    calls = stub(monkeypatch, client, {"success": True, "data": [
        {"content": "连杆间隙 0.02mm", "score": 0.9},
    ]})
    hits = client.search("连杆间隙", ["kb-1"], tag_ids=["t-1"], match_count=3)
    assert hits[0]["content"] == "连杆间隙 0.02mm"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/v1/knowledge-search")
    assert payload["query"] == "连杆间隙"
    assert payload["knowledge_base_ids"] == ["kb-1"]
    assert payload["tag_ids"] == ["t-1"]
    assert payload["match_count"] == 3


def test_request_wraps_http_error(monkeypatch, client):
    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {},
                                     io.BytesIO(b'{"error":"bad token"}'))

    monkeypatch.setattr(weknora.urllib.request, "urlopen", boom)
    with pytest.raises(weknora.WeKnoraError) as ei:
        client.find_kb("x")
    assert "401" in str(ei.value)


def test_request_parses_json_body(monkeypatch, client):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(weknora.urllib.request, "urlopen",
                        lambda req, timeout: Resp(json.dumps({"data": 1}).encode()))
    assert client._request("GET", "/x") == {"data": 1}


def test_request_rejects_non_json_body(monkeypatch, client):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(weknora.urllib.request, "urlopen",
                        lambda req, timeout: Resp(b"<html>oops</html>"))
    with pytest.raises(weknora.WeKnoraError) as ei:
        client._request("GET", "/x")
    assert "不是 JSON" in str(ei.value)


def test_find_tag_handles_paginated_envelope(monkeypatch, client):
    stub(monkeypatch, client, {"data": {"total": 1, "page": 1, "page_size": 20,
                                        "data": [{"id": "t-1", "name": "项目/浮标项目"}]}})
    assert client.ensure_tag("kb-1", "项目/浮标项目") == "t-1"


def test_tag_ids_for_returns_ids_or_raises(monkeypatch, client):
    existing = {"data": [{"id": "t-1", "name": "项目/浮标项目"}]}
    stub(monkeypatch, client, existing, existing)
    assert client.tag_ids_for("kb-1", ["项目/浮标项目"]) == ["t-1"]
    with pytest.raises(weknora.WeKnoraError):
        client.tag_ids_for("kb-1", ["不存在的标签"])


def test_embedding_model_preferred_missing_returns_none(monkeypatch, client):
    stub(monkeypatch, client, {"data": [
        {"id": "m-1", "name": "text-embedding-v4", "type": "Embedding"},
    ]})
    assert client.embedding_model_id("bge-m3") is None


def test_knowledge_status_reads_parse_status(monkeypatch, client):
    calls = stub(monkeypatch, client, {"data": {"parse_status": "completed"}})
    assert client.knowledge_status("k-1") == "completed"
    assert calls[0][1] == "/api/v1/knowledge/k-1"


def test_wait_knowledge_polls_until_done(monkeypatch, client):
    seq = {"k1": ["pending", "completed"], "k2": ["processing", "failed"]}

    def fake_status(kid):
        return seq[kid].pop(0) if len(seq[kid]) > 1 else seq[kid][0]

    monkeypatch.setattr(client, "knowledge_status", fake_status)
    monkeypatch.setattr(weknora.time, "sleep", lambda s: None)
    assert client.wait_knowledge(["k1", "k2"], timeout=5, interval=0) == {
        "k1": "completed", "k2": "failed",
    }


def test_wait_knowledge_timeout_reports_current_status(monkeypatch, client):
    monkeypatch.setattr(client, "knowledge_status", lambda kid: "processing")
    assert client.wait_knowledge(["k1"], timeout=0, interval=0) == {"k1": "processing"}
