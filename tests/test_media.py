"""票 #14：媒体实体登记（独立图 + 视频）。"""
import hashlib
import io
import json
import os
import urllib.error
import pytest

from docarchive import caption as capmod, env, media
from support import make_mp4_with_mvhd, make_png, make_video


class FakeCaptioner:
    """测试用 captioner：记录调用次数，可选择抛错。"""

    def __init__(self, text="一张蓝色方框的截图，上面写着 SAMPLE。", error=None):
        self.text = text
        self.error = error
        self.calls = 0

    def describe(self, data: bytes, ext: str | None = None) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.text


# ---------- 独立图 ----------

def test_register_standalone_image_assigns_img_code(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    data = src.read_bytes()
    fake = FakeCaptioner()

    r = media.register_standalone_image(conn, archive_dir, src,
                                        project="浮标项目", captioner=fake)

    assert r.code == "IMG-0001"
    assert r.created is True
    assert r.caption == fake.text
    assert r.caption_error is None

    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["code"] == "IMG-0001"
    assert img["sha256"] == hashlib.sha256(data).hexdigest()
    assert img["caption"] == fake.text
    assert img["source"] == str(src)
    assert (archive_dir / img["path"]).read_bytes() == data

    pid = conn.execute("SELECT id FROM projects WHERE name='浮标项目'").fetchone()["id"]
    assert img["project_id"] == pid


def test_duplicate_image_reuses_code_and_skips_regenerating_caption(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    first = FakeCaptioner("第一版描述")
    second = FakeCaptioner("不该被用到")

    r1 = media.register_standalone_image(conn, archive_dir, src, captioner=first)
    r2 = media.register_standalone_image(conn, archive_dir, src, captioner=second)

    assert r1.code == r2.code == "IMG-0001"
    assert (r1.created, r2.created) == (True, False)
    assert second.calls == 0  # 已有 caption 不再花钱
    assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 1


def test_duplicate_image_without_caption_gets_one_later(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    media.register_standalone_image(conn, archive_dir, src)  # 先不入 caption
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] is None

    fake = FakeCaptioner("后补的描述")
    r = media.register_standalone_image(conn, archive_dir, src, captioner=fake)

    assert r.caption == "后补的描述"
    assert fake.calls == 1
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "后补的描述"


def test_caption_failure_does_not_block_registration(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    fake = FakeCaptioner(error=RuntimeError("模型超时"))

    r = media.register_standalone_image(conn, archive_dir, src, captioner=fake)

    assert r.code == "IMG-0001"
    assert r.caption is None
    assert "模型超时" in (r.caption_error or "")
    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["caption"] is None
    assert (archive_dir / img["path"]).exists()


def test_image_single_project_not_reparented(conn, archive_dir, tmp_path):
    """单归属：重复登记换个项目名，不改已定的主项目。"""
    src = make_png(tmp_path / "shot.png")
    media.register_standalone_image(conn, archive_dir, src, project="浮标项目")
    media.register_standalone_image(conn, archive_dir, src, project="别的项目")

    img = conn.execute("SELECT project_id FROM images").fetchone()
    name = conn.execute("SELECT name FROM projects WHERE id=?", (img["project_id"],)).fetchone()
    assert name["name"] == "浮标项目"


def test_image_project_tags(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    media.register_standalone_image(conn, archive_dir, src, project="浮标项目",
                                    tags=["测试", "资料"])

    tag_names = [r["name"] for r in conn.execute(
        "SELECT p.name FROM image_project_tags t JOIN projects p ON p.id=t.project_id "
        "JOIN images i ON i.id=t.image_id WHERE i.code='IMG-0001' ORDER BY p.name"
    )]
    assert tag_names == ["测试", "资料"]


def test_unknown_ext_goes_to_archive_as_bin(conn, archive_dir, tmp_path):
    src = tmp_path / "noext"
    src.write_bytes(b"raw")
    r = media.register_standalone_image(conn, archive_dir, src)
    img = conn.execute("SELECT * FROM images WHERE code=?", (r.code,)).fetchone()
    assert img["path"] == "images/IMG-0001.bin"


# ---------- 视频 ----------

def test_register_video_assigns_vid_and_intro(conn, archive_dir, tmp_path):
    src = make_video(tmp_path / "demo.mp4")

    r = media.register_video(conn, archive_dir, src, project="浮标项目", intro="现场调试录像")

    assert r.code == "VID-0001"
    assert r.created is True
    assert r.intro == "现场调试录像"
    v = conn.execute("SELECT * FROM videos").fetchone()
    assert v["code"] == "VID-0001"
    assert v["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert v["intro"] == "现场调试录像"
    assert (archive_dir / v["path"]).read_bytes() == src.read_bytes()
    assert v["project_id"] == conn.execute(
        "SELECT id FROM projects WHERE name='浮标项目'").fetchone()["id"]


def test_duplicate_video_reuses_vid(conn, archive_dir, tmp_path):
    src = make_video(tmp_path / "demo.mp4")
    r1 = media.register_video(conn, archive_dir, src, intro="第一版简介")
    r2 = media.register_video(conn, archive_dir, src, intro="不该覆盖")
    assert r1.code == r2.code == "VID-0001"
    assert r2.created is False
    assert conn.execute("SELECT intro FROM videos").fetchone()["intro"] == "第一版简介"
    assert conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"] == 1


def test_set_video_intro(conn, archive_dir, tmp_path):
    media.register_video(conn, archive_dir, make_video(tmp_path / "demo.mp4"))
    media.set_video_intro(conn, "VID-0001", "补上的简介")
    assert conn.execute("SELECT intro FROM videos").fetchone()["intro"] == "补上的简介"


def test_set_video_intro_unknown_code_raises(conn):
    with pytest.raises(ValueError):
        media.set_video_intro(conn, "VID-9999", "x")


def test_video_project_tags(conn, archive_dir, tmp_path):
    media.register_video(conn, archive_dir, make_video(tmp_path / "demo.mp4"),
                         project="浮标项目", tags=["测试"])
    tags = [r["name"] for r in conn.execute(
        "SELECT p.name FROM video_project_tags t JOIN projects p ON p.id=t.project_id "
        "JOIN videos v ON v.id=t.video_id WHERE v.code='VID-0001'")]
    assert tags == ["测试"]


# ---------- 总览 ----------

def test_overview_lists_media_with_projects(conn, archive_dir, tmp_path):
    media.register_standalone_image(conn, archive_dir, make_png(tmp_path / "a.png"),
                                    project="浮标项目", captioner=FakeCaptioner("甲图"))
    media.register_video(conn, archive_dir, make_video(tmp_path / "v.mp4"),
                         project="浮标项目", intro="乙录像")

    ov = media.overview(conn)
    assert [i["code"] for i in ov["images"]] == ["IMG-0001"]
    assert ov["images"][0]["project"] == "浮标项目"
    assert ov["images"][0]["caption"] == "甲图"
    assert [v["code"] for v in ov["videos"]] == ["VID-0001"]
    assert ov["videos"][0]["intro"] == "乙录像"

    only_other = media.overview(conn, project="不存在的项目")
    assert only_other == {"images": [], "videos": []}


# ---------- caption 后端 ----------

def test_config_from_env_uses_caption_vars_then_zhipu(monkeypatch):
    cfg = capmod.config_from_env({
        "CAPTION_API_KEY": "k1", "CAPTION_MODEL": "my-vlm",
        "CAPTION_BASE_URL": "https://example.com/v4",
    })
    assert (cfg.api_key, cfg.model, cfg.base_url) == ("k1", "my-vlm", "https://example.com/v4")

    cfg2 = capmod.config_from_env({"ZHIPU_API_KEY": "k2"})
    assert cfg2.api_key == "k2"
    assert cfg2.model == capmod.DEFAULT_MODEL
    assert cfg2.base_url == capmod.DEFAULT_BASE_URL

    assert capmod.config_from_env({}) is None


def test_dotenv_loader_reads_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# 注释\nZHIPU_API_KEY=from-file\nWEKNORA_EMAIL=  x@y.z \n", encoding="utf-8")
    loaded = env.load_dotenv(env_file)
    assert loaded["ZHIPU_API_KEY"] == "from-file"
    assert loaded["WEKNORA_EMAIL"] == "x@y.z"


def test_vlm_captioner_posts_openai_payload(monkeypatch):
    sent = {}

    def fake_post(url, payload, headers, timeout):
        sent.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"choices": [{"message": {"content": "  一张白底黑字的截图。  "}}]}

    monkeypatch.setattr(capmod, "_post_json", fake_post)
    cfg = capmod.VLMConfig(base_url="https://example.com/v4", model="m1", api_key="k1")
    out = capmod.VLMCaptioner(cfg).describe(b"\x89PNG data", ext=".png")

    assert out == "一张白底黑字的截图。"
    assert sent["url"] == "https://example.com/v4/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k1"
    assert sent["payload"]["model"] == "m1"
    content = sent["payload"]["messages"][0]["content"]
    kinds = [c["type"] for c in content]
    assert kinds == ["image_url", "text"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["text"]


def test_vlm_captioner_empty_answer_raises(monkeypatch):
    monkeypatch.setattr(capmod, "_post_json", lambda *a, **k: {"choices": []})
    cfg = capmod.VLMConfig(base_url="https://example.com/v4", model="m1", api_key="k1")
    with pytest.raises(capmod.VLMError):
        capmod.VLMCaptioner(cfg).describe(b"x", ext=".jpg")


def test_post_json_wraps_http_error(monkeypatch):
    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr(capmod.urllib.request, "urlopen", boom)
    with pytest.raises(capmod.VLMError) as ei:
        capmod._post_json("https://example.com", {}, {}, 5)
    assert "401" in str(ei.value)


def test_post_json_parses_body(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(capmod.urllib.request, "urlopen",
                        lambda req, timeout: Resp(json.dumps({"ok": 1}).encode()))
    assert capmod._post_json("https://example.com", {"a": 1}, {}, 5) == {"ok": 1}


# ---------- 元数据：名称 / 宽高 / 时长 ----------

def test_image_records_name_and_dimensions(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    media.register_standalone_image(conn, archive_dir, src)
    img = conn.execute("SELECT * FROM images").fetchone()
    assert img["name"] == "shot"
    assert (img["width"], img["height"]) == (160, 80)


def test_video_records_name_and_duration(conn, archive_dir, tmp_path, monkeypatch):
    # 强制走「没有 ffprobe」的解析路径，免得本机 ffprobe 把结果盖掉（CI 上没这问题）
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    src = tmp_path / "demo.mp4"
    src.write_bytes(make_mp4_with_mvhd(timescale=1000, duration=5000))
    r = media.register_video(conn, archive_dir, src)
    v = conn.execute("SELECT * FROM videos").fetchone()
    assert v["name"] == "demo"
    assert v["duration_seconds"] == 5.0
    assert r.duration_seconds == 5.0


def test_video_duration_none_for_unparsable_bytes(conn, archive_dir, tmp_path):
    media.register_video(conn, archive_dir, make_video(tmp_path / "x.mp4"))
    assert conn.execute("SELECT duration_seconds FROM videos").fetchone()[0] is None


# ---------- recaption / 标签读取 ----------

def test_recaption_regenerates(conn, archive_dir, tmp_path):
    src = make_png(tmp_path / "shot.png")
    media.register_standalone_image(conn, archive_dir, src, captioner=FakeCaptioner("旧描述"))
    r = media.register_standalone_image(conn, archive_dir, src,
                                        captioner=FakeCaptioner("新描述"), recaption=True)
    assert r.caption == "新描述"
    assert conn.execute("SELECT caption FROM images").fetchone()["caption"] == "新描述"


def test_overview_shows_and_filters_tags(conn, archive_dir, tmp_path):
    media.register_standalone_image(conn, archive_dir, make_png(tmp_path / "a.png"),
                                    project="浮标项目", tags=["测试", "资料"])
    media.register_standalone_image(conn, archive_dir, make_png(tmp_path / "b.png", "B"),
                                    project="浮标项目")

    tagged = media.overview(conn, tag="测试")
    assert [i["code"] for i in tagged["images"]] == ["IMG-0001"]
    assert tagged["images"][0]["tags"] == "测试 / 资料"
    assert [i["code"] for i in media.overview(conn)["images"]] == ["IMG-0001", "IMG-0002"]


# ---------- 真模型冒烟（默认跳过，RUN_LIVE_CAPTION=1 时跑） ----------

@pytest.mark.skipif(os.environ.get("RUN_LIVE_CAPTION") != "1",
                    reason="真模型冒烟需要 RUN_LIVE_CAPTION=1")
def test_live_caption_smoke(tmp_path):
    cap = capmod.captioner_from_env()
    if cap is None:
        pytest.skip("没有配置视觉模型密钥")
    img = make_png(tmp_path / "live.png", "SAMPLE 2026")
    text = cap.describe(img.read_bytes(), ext=".png")
    assert isinstance(text, str) and len(text) >= 5


def test_standalone_caption_runs_outside_write_transaction(conn, archive_dir, tmp_path):
    """登记独立图时，模型调用不能落在未提交的写事务里（会长时间占写锁）。"""
    seen = []

    class Probe:
        def describe(self, data, ext=None):
            seen.append(conn.in_transaction)
            return "描述"

    media.register_standalone_image(conn, archive_dir, make_png(tmp_path / "x.png"),
                                    captioner=Probe())
    assert seen == [False]
