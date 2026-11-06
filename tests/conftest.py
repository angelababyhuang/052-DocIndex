"""公共 fixture：临时真相源库、受管目录、扫描源目录。

每个测试文件只留自己特有的 fixture（如 test_weknora 的 client、test_cli 的 db_path）。
"""
import pytest

from docarchive import db as appdb


@pytest.fixture
def conn(tmp_path):
    c = appdb.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    return d


@pytest.fixture
def inbox(tmp_path):
    """扫描源单独一个目录：测试库 test.db 在 tmp_path 根上，别被扫进来。"""
    d = tmp_path / "inbox"
    d.mkdir()
    return d
