"""配置读取：进程环境变量优先，.env 兜底。"""
from docarchive import env


def test_load_dotenv_parses_and_skips_noise(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# 注释\nA=1\n\nB = x@y.z \n', encoding="utf-8")
    assert env.load_dotenv(f) == {"A": "1", "B": "x@y.z"}


def test_load_dotenv_missing_file_is_empty(tmp_path):
    assert env.load_dotenv(tmp_path / "nope.env") == {}


def test_merged_prefers_process_env(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=from-file\nB=from-file\n", encoding="utf-8")
    monkeypatch.setenv("B", "from-process")
    merged = env.merged(f)
    assert merged["A"] == "from-file"
    assert merged["B"] == "from-process"
