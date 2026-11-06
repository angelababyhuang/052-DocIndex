"""图片 caption 生成器：调用多模态大模型，给图片写一句可检索的中文描述。

后端是 OpenAI 兼容的 `/chat/completions`（智谱 GLM-4V-Flash、阿里云百炼
qwen-vl 都是这个形状），所以换模型只换环境变量，不改代码：

  CAPTION_API_KEY   密钥（缺省回落到 ZHIPU_API_KEY）
  CAPTION_BASE_URL  默认 https://open.bigmodel.cn/api/paas/v4
  CAPTION_MODEL     默认 glm-4v-flash（免费）

为什么不用 WeKnora 里的 qwen3.8-flash：caption 是真相源的字段，必须能独立
重建；WeKnora 的 VLM 只在它自己的索引流程里跑，没有「描述单张图」的对外接口。
详见 docs/adr/0002。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

from docarchive.env import merged

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4v-flash"
DEFAULT_PROMPT = (
    "先用中文说清这张图是什么（截图/照片/图表/扫描件），照抄画面里能辨认的关键文字，"
    "再补一句它大概是做什么用的。看不清的不要臆测。"
)
DEFAULT_TIMEOUT = 60


class VLMError(RuntimeError):
    """调用视觉模型失败（网络、鉴权、返回格式、空描述）。"""


class Captioner(Protocol):
    """给一张图写一句描述。任何实现了 describe 的对象都能顶上来（测试用假的）。"""

    def describe(self, data: bytes, ext: str | None = None) -> str: ...


@dataclass(frozen=True)
class VLMConfig:
    base_url: str
    model: str
    api_key: str
    prompt: str = DEFAULT_PROMPT
    timeout: int = DEFAULT_TIMEOUT


def config_from_env(env: Mapping[str, str] | None = None) -> VLMConfig | None:
    """从环境变量拼出配置；没有密钥返回 None（调用方据此跳过 caption）。"""
    if env is None:
        env = merged()
    api_key = env.get("CAPTION_API_KEY") or env.get("ZHIPU_API_KEY")
    if not api_key:
        return None
    return VLMConfig(
        base_url=(env.get("CAPTION_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        model=env.get("CAPTION_MODEL") or DEFAULT_MODEL,
        api_key=api_key,
    )


def captioner_from_env(env: Mapping[str, str] | None = None) -> Captioner | None:
    """有密钥就给个真 captioner，没有就返回 None。"""
    cfg = config_from_env(env)
    return VLMCaptioner(cfg) if cfg else None


class VLMCaptioner:
    """OpenAI 兼容的视觉模型客户端。"""

    def __init__(self, config: VLMConfig):
        self.config = config

    def describe(self, data: bytes, ext: str | None = None) -> str:
        mime = mimetypes.guess_type(f"x{ext or ''}")[0] or "image/png"
        b64 = base64.b64encode(data).decode()
        payload = {
            "model": self.config.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": self.config.prompt},
                ],
            }],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        resp = _post_json(f"{self.config.base_url}/chat/completions",
                          payload, headers, self.config.timeout)
        text = _content_text(resp)
        if not text:
            raise VLMError("模型返回了空描述")
        return text


def _content_text(resp: dict) -> str:
    """从 OpenAI 兼容响应里取正文（content 可能是字符串，也可能是分段列表）。"""
    choices = resp.get("choices") or []
    if not choices:
        raise VLMError(f"模型响应里没有 choices: {str(resp)[:200]}")
    content = (choices[0] or {}).get("message", {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return ""


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "ignore")
        raise VLMError(f"模型接口返回 {e.code}: {detail}") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        raise VLMError(f"调用模型接口失败: {e}") from e
