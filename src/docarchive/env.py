"""读 `.env`（KEY=VALUE）。不引 python-dotenv：格式够简单，自己解析更少依赖。"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """读 KEY=VALUE 形式的 .env；文件不存在返回空 dict。"""
    p = Path(path)
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def merged(dotenv_path: str | Path = ".env") -> dict[str, str]:
    """进程环境变量 + .env 文件（进程环境优先）。"""
    return {**load_dotenv(dotenv_path), **os.environ}
