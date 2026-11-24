import os
from pathlib import Path

import httpx
import keyring
from keyring.errors import KeyringError

from .store import db, get
from .model_transport import ModelRequestError, ModelTransport, response_code


PROVIDERS = {
    "opencode": {"name": "OpenCode Go", "model": "glm-5.3-flash",
                 "base_url": "https://opencode.ai/inference/go/openai/v1", "env": "OPENCODE_GO_API_KEY"},
    "deepseek": {"name": "DeepSeek", "model": "deepseek-flash",
                 "base_url": "https://api.deepseek.com", "env": "DEEPSEEK_API_KEY"},
}
KEY_SERVICE = "aitolearn-models"


def active_provider(conn=None):
    if conn is None:
        with db() as conn:
            return active_provider(conn)
    setting = get(conn, "settings", "model_provider") or {}
    provider = setting.get("id")
    return provider if provider in PROVIDERS else "opencode"


def provider_config(provider):
    config = PROVIDERS[provider].copy()
    if provider == "opencode":
        config["model"] = os.getenv("OPENCODE_GO_MODEL") or config["model"]
        config["base_url"] = os.getenv("OPENCODE_GO_BASE_URL") or config["base_url"]
    return config


def provider_key(provider):
    key = os.getenv(PROVIDERS[provider]["env"], "").strip()
    if key:
        return key
    try:
        key = keyring.get_password(KEY_SERVICE, provider)
    except (KeyringError, OSError):
        key = None
    if key:
        return key
    legacy = Path("goapi.txt")
    return legacy.read_text(encoding="utf-8-sig").strip() if provider == "opencode" and legacy.is_file() else ""


def model_headers(provider, session):
    headers = {"User-Agent": "aitolearn/1.0"}
    if provider == "opencode":
        headers["x-opencode-session"] = session
    return headers


class ToolsUnavailable(ValueError):
    pass


def complete(provider, messages, session, **options):
    return _complete(provider, messages, session, False, **options)


def complete_with_tools(provider, messages, session, **options):
    return _complete(provider, messages, session, True, **options)


def _complete(provider, messages, session, structured, **options):
    key = provider_key(provider)
    if not key:
        raise ModelRequestError("credentials")
    config = provider_config(provider)
    body = {"model": config["model"], "messages": messages, **options}
    if provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
    try:
        with httpx.Client(transport=ModelTransport(provider, config["model"], session, base_url=config["base_url"]),
                          timeout=httpx.Timeout(45, connect=8)) as client:
            response = client.post(config["base_url"].rstrip("/") + "/chat/completions",
                                   headers={**model_headers(provider, session), "Authorization": f"Bearer {key}"},
                                   json=body)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        content = message.get("content")
        if structured and (message.get("tool_calls") or isinstance(content, str) and content.strip()):
            return {"role": "assistant", "content": content, **(
                {"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {})}
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        return content
    except httpx.HTTPStatusError as error:
        if structured and error.response.status_code in {400, 422} and "tool" in error.response.text.lower():
            raise ToolsUnavailable("当前模型不支持工具调用") from None
        raise ModelRequestError(response_code(error.response)) from None
    except ModelRequestError:
        raise
    except (KeyError, IndexError, TypeError, ValueError):
        raise ModelRequestError("response") from None
