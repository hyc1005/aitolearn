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


def stream_with_tools(provider, messages, session, **options):
    """Collect a streamed tool response while exposing only provider reasoning chunks."""
    import json
    from .chat_stream import emit
    selected = None if isinstance(provider, str) else provider
    provider = selected.connection_id if selected else provider
    key = selected.key if selected else provider_key(provider)
    if not key:
        raise ModelRequestError('credentials')
    config = {'model': selected.model_id, 'base_url': selected.base_url} if selected else provider_config(provider)
    body = {'model': config['model'], 'messages': messages, **options, 'stream': True}
    direct = None
    if selected:
        body.update(selected.parameters())
        if selected.adapter == 'custom':
            from .model_connections import BoundTransport
            direct = BoundTransport()
    elif provider == 'deepseek':
        body['thinking'] = {'type': 'disabled'}
    content, reasoning, calls = [], [], {}
    try:
        with httpx.Client(transport=ModelTransport(provider, config['model'], session, transport=direct,
                                                   base_url=config['base_url']),
                          follow_redirects=False, timeout=httpx.Timeout(45, connect=8)) as client:
            with client.stream('POST', config['base_url'].rstrip('/') + '/chat/completions',
                               headers={**model_headers(selected.adapter if selected else provider, session),
                                        'Authorization': f'Bearer {key}', 'Accept': 'text/event-stream'}, json=body) as response:
                response.raise_for_status()
                if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                    raise ToolsUnavailable('当前模型未返回流式响应')
                for line in response.iter_lines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if not data or data == '[DONE]':
                        continue
                    event = json.loads(data)
                    for choice in event.get('choices', []):
                        if choice.get('index', 0) != 0:
                            continue
                        delta = choice.get('delta') or {}
                        if isinstance(delta.get('reasoning_content'), str):
                            chunk = delta['reasoning_content']
                            reasoning.append(chunk)
                            emit('reasoning', text=chunk)
                        if isinstance(delta.get('content'), str):
                            content.append(delta['content'])
                        for call in delta.get('tool_calls') or []:
                            index = call.get('index', 0)
                            item = calls.setdefault(index, {'id': '', 'type': 'function',
                                                            'function': {'name': '', 'arguments': ''}})
                            if call.get('id'):
                                item['id'] += call['id']
                            for field in ('name', 'arguments'):
                                item['function'][field] += (call.get('function') or {}).get(field) or ''
        result = {'role': 'assistant', 'content': ''.join(content) or None}
        if reasoning:
            result['reasoning_content'] = ''.join(reasoning)
        if calls:
            result['tool_calls'] = [calls[index] for index in sorted(calls)]
        if not result['content'] and not calls:
            raise ModelRequestError('response')
        return result
    except ToolsUnavailable:
        raise
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {400, 422}:
            raise ToolsUnavailable('当前模型不支持流式工具调用') from None
        raise ModelRequestError(response_code(error.response)) from None
    except httpx.TimeoutException:
        raise ModelRequestError('timeout') from None
    except httpx.RequestError:
        raise ModelRequestError('connection') from None
    except (ValueError, TypeError, KeyError):
        raise ModelRequestError('response') from None


def model_name(provider):
    return provider_config(provider)["model"] if isinstance(provider, str) else provider.model_id


def _complete(provider, messages, session, structured, **options):
    selected = None if isinstance(provider, str) else provider
    provider = selected.connection_id if selected else provider
    key = selected.key if selected else provider_key(provider)
    if not key:
        raise ModelRequestError("credentials")
    config = {"model": selected.model_id, "base_url": selected.base_url} if selected else provider_config(provider)
    body = {"model": config["model"], "messages": messages, **options}
    direct = None
    if selected:
        body.update(selected.parameters())
        if selected.adapter == "custom":
            from .model_connections import BoundTransport
            direct = BoundTransport()
    elif provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
    try:
        with httpx.Client(transport=ModelTransport(provider, config["model"], session, transport=direct,
                                                   base_url=config["base_url"]),
                          follow_redirects=False, timeout=httpx.Timeout(45, connect=8)) as client:
            response = client.post(config["base_url"].rstrip("/") + "/chat/completions",
                                   headers={**model_headers(selected.adapter if selected else provider, session),
                                            "Authorization": f"Bearer {key}"},
                                   json=body)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        content = message.get("content")
        if structured and (message.get("tool_calls") or isinstance(content, str) and content.strip()):
            return {"role": "assistant", "content": content, **{
                name: message[name] for name in ("tool_calls", "reasoning_content") if message.get(name)}}
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        return content
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if 300 <= status < 400:
            raise ValueError(f"平台返回 HTTP {status} 重定向；请配置最终 API 地址") from None
        detail = error.response.text[:2000].lower()
        if selected and status in {400, 422}:
            field = next((p for p in ("reasoning_effort", "thinking") if p in detail), None)
            if field:
                raise ValueError(f"平台拒绝思考参数 {field}（HTTP {status}），请检查模型档位配置") from None
        if structured and status in {400, 422} and "tool" in detail:
            raise ToolsUnavailable("当前模型不支持工具调用") from None
        failure = ModelRequestError(response_code(error.response))
        if selected:
            raise ValueError(f"{failure}（HTTP {status}）") from None
        raise failure from None
    except ModelRequestError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ModelRequestError("response") from None
