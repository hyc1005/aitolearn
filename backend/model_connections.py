"""Teacher chat connections; the existing provider setting remains a background default."""
from dataclasses import dataclass, field
from hashlib import sha256
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx
import keyring
from fastapi import APIRouter, HTTPException
from keyring.errors import KeyringError
from pydantic import BaseModel, Field, SecretStr

from .models import KEY_SERVICE, PROVIDERS, active_provider, complete, model_headers, provider_config, provider_key
from .store import all_of, db, get, new_id, put

router = APIRouter(prefix="/api/models")
LEVELS = {"minimal", "low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ChatModel:
    connection_id: str
    name: str
    adapter: str
    base_url: str
    model_id: str
    effort: str
    mode: str
    key: str = field(repr=False)

    def parameters(self):
        if self.effort == "default":
            return {}
        if self.mode in {"deepseek", "switch"}:
            result = {"thinking": {"type": "disabled" if self.effort == "off" else "enabled"}}
            if self.mode == "deepseek" and self.effort in LEVELS:
                result["reasoning_effort"] = self.effort
            return result
        return {"reasoning_effort": self.effort}

    def metadata(self):
        return {"connection_id": self.connection_id, "connection_name": self.name,
                "model_id": self.model_id, "effort": self.effort}


def address(value):
    try:
        if re.search(r"[\s\\?#]", value):
            raise ValueError
        parts = urlsplit(value)
        if not parts.hostname or parts.username is not None or parts.port == 0:
            raise ValueError
        loopback = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
            raise ValueError
        return str(httpx.URL(value)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise HTTPException(422, "API 地址须使用 HTTPS；本机开发可显式使用回环 HTTP 地址") from None


class BoundTransport(httpx.HTTPTransport):
    """Resolve once and connect to that address while retaining the original TLS identity."""
    def handle_request(self, request):
        hostname = request.url.host
        local = hostname in {"localhost", "127.0.0.1", "::1"}
        try:
            ips = sorted({row[4][0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)},
                         key=lambda ip: (":" in ip, ip))
            if not ips or not all(ipaddress.ip_address(ip).is_loopback if local else
                                  ipaddress.ip_address(ip).is_global and not ipaddress.ip_address(ip).is_multicast
                                  for ip in ips):
                raise ValueError
        except (OSError, ValueError):
            raise httpx.ConnectError("地址无法解析或不允许访问", request=request) from None
        request.extensions["sni_hostname"] = hostname
        original = request.url
        request.url = original.copy_with(host=ips[0])
        try:
            return super().handle_request(request)
        finally:
            request.url = original


def credential_id(connection):
    return connection["id"] + ":" + sha256(connection["base_url"].encode()).hexdigest()


def connection_key(connection):
    if connection["adapter"] != "custom":
        provider = connection["adapter"]
        if connection["base_url"] == provider_config(provider)["base_url"].rstrip("/"):
            return provider_key(provider)
        return ""
    try:
        return keyring.get_password(KEY_SERVICE, connection["credential"]) or ""
    except (KeyringError, OSError):
        return ""


def capability(adapter, model_id, catalog_capabilities=None):
    if adapter == "opencode" and catalog_capabilities and model_id in catalog_capabilities:
        entry = catalog_capabilities[model_id]
        return entry["mode"], entry["efforts"]
    if adapter == "deepseek" and model_id in {"deepseek-flash", "deepseek-v4-pro"}:
        return "deepseek", ["default", "off", "low", "high", "max"]
    if adapter == "deepseek" and model_id in {"deepseek-chat", "deepseek-reasoner"}:
        return "switch", ["default", "off", "on"]
    return "unknown", ["default"]


def opencode_capabilities(c, rows):
    entries = {}
    for row in rows:
        protocol = (row.get("api") or {}).get("chat-completions") or {}
        if protocol.get("base_url") != c["base_url"] or not isinstance(row.get("id"), str):
            continue
        reasoning = (row.get("capabilities") or {}).get("reasoning") or {}
        if reasoning.get("supported") is not True:
            continue
        levels = [e for e in reasoning.get("effort", []) if isinstance(e, str) and e in LEVELS]
        if levels:
            entries[row["id"]] = {"mode": "effort", "efforts": ["default", *levels]}
        elif reasoning.get("toggle") is True:
            entries[row["id"]] = {"mode": "switch", "efforts": ["default", "off", "on"]}
    return entries


def models_for(connection):
    return connection.get("models", [])


def migrate(conn):
    for provider in PROVIDERS:
        existing = get(conn, "model_connection", provider)
        if existing:
            continue
        config = provider_config(provider)
        model_id = config["model"]
        mode, efforts = capability(provider, model_id)
        connection = {"id": provider, "adapter": provider, "name": config["name"],
                      "base_url": config["base_url"].rstrip("/"), "catalog": [], "legacy": True,
                      "models": [{"id": model_id, "enabled": True, "mode": mode, "efforts": efforts,
                                  "verified": ["default"], "legacy": True}]}
        put(conn, "model_connection", provider, connection)


def chat_options(conn):
    connections = all_of(conn, "model_connection")
    choices = [(item, model) for item in connections if connection_key(item) for model in models_for(item)
               if model["enabled"] and "default" in model["verified"]]
    saved = get(conn, "settings", "chat_default") or {}
    default = next(((item, model) for item, model in choices if (item["id"], model["id"]) ==
                    (saved.get("connection_id"), saved.get("model_id"))), None)
    default = default or next(((item, model) for item, model in choices if item["id"] == active_provider(conn)), None)
    return choices, default or (choices[0] if choices else None)


def selection(conn, request, session):
    choices, default = chat_options(conn)
    chosen = (session or {}).get("selection") or {}
    expected = (request.connection_id, request.model_id) if request.connection_id or request.model_id else (
        chosen.get("connection_id"), chosen.get("model_id"))
    effort = request.effort if request.effort is not None else (
        chosen.get("effort", "default") if not request.connection_id else "default")
    if not any(expected):
        if request.effort is not None:
            raise HTTPException(422, "请同时选择连接和模型")
        if request.provider or (session or {}).get("provider"):
            default = next(((c, m) for c, m in choices if c["id"] == (request.provider or session["provider"])), None)
        pair = default
    else:
        pair = next(((c, m) for c, m in choices if (c["id"], m["id"]) == expected), None)
        if pair is None:
            raise HTTPException(422, "此模型不再可用，请为下一条消息重选模型")
    if pair is None:
        return None
    c, m = pair
    if effort not in m["efforts"] or (c["adapter"] == "custom" and effort not in m["verified"]):
        raise HTTPException(422, "该模型不支持此思考强度，或该档位尚未通过测试")
    return target(c, m, effort)


def target(connection, model, effort):
    return ChatModel(connection["id"], connection["name"], connection["adapter"], connection["base_url"],
                     model["id"], effort, model["mode"], connection_key(connection))


def view(conn):
    _, default = chat_options(conn)
    return {"background": active_provider(conn), "default": {"connection_id": default[0]["id"], "model_id": default[1]["id"]} if default else None,
            "connections": [{**{k: v for k, v in c.items() if k not in {"credential", "legacy", "catalog_capabilities"}},
                             "configured": bool(connection_key(c)),
                             "env": PROVIDERS.get(c["adapter"], {}).get("env", "")}
                            for c in all_of(conn, "model_connection")]}


def require(conn, cid):
    connection = get(conn, "model_connection", cid)
    if not connection:
        raise HTTPException(404, "模型连接不存在")
    return connection


def persist_result(before, change):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require(conn, before["id"])
        if before != current:
            raise HTTPException(409, "连接已修改，请刷新页面并重试")
        change(current)
        put(conn, "model_connection", current["id"], current)
        return view(conn)


class ConnectionSettings(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=500)
    api_key: SecretStr | None = None


class ModelSettings(BaseModel):
    model_id: str = Field(min_length=1, max_length=180)
    enabled: bool = False
    mode: str = "unknown"
    efforts: list[str] = Field(default_factory=list, max_length=8)


class Probe(BaseModel):
    model_id: str = Field(max_length=180)
    effort: str = Field(default="default", max_length=16)


@router.get("/connections")
def connections():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        migrate(conn)
        return view(conn)


@router.put("/connections/{cid}")
def save_connection(cid: str, setting: ConnectionSettings):
    key = setting.api_key.get_secret_value().strip() if setting.api_key else ""
    if (key and (len(key) > 2048 or any(not 33 <= ord(ch) <= 126 for ch in key))) or not setting.name.strip():
        raise HTTPException(422, "连接名称或密钥格式无效")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        migrate(conn)
        c = {"id": new_id(), "adapter": "custom", "catalog": [], "models": []} if cid == "new" else require(conn, cid)
        url = address(setting.base_url) if c["adapter"] == "custom" else c["base_url"]
        changed = c.get("base_url") != url
        if changed and not key:
            raise HTTPException(422, "新建或修改地址时，请重新填写该地址的密钥")
        c.update(name=setting.name.strip(), base_url=url)
        if key:
            cred = (credential_id(c) + ":" + new_id()) if c["adapter"] == "custom" else c["id"]
            try:
                keyring.set_password(KEY_SERVICE, cred, key)
            except (KeyringError, OSError):
                raise HTTPException(503, "无法写入系统凭据库") from None
            if c["adapter"] == "custom":
                c["credential"] = cred
        if changed:
            c["catalog"] = []
            c.pop("catalog_capabilities", None)
        if changed or key:
            for m in models_for(c):
                m.update(verified=[])
        put(conn, "model_connection", c["id"], c)
        return {**view(conn), "saved_id": c["id"]}


@router.post("/connections/{cid}/catalog")
def refresh_catalog(cid: str):
    with db() as conn:
        c = require(conn, cid)
    key = connection_key(c)
    if not key:
        raise HTTPException(422, "请先保存 API 密钥")
    url = c["base_url"] + "/models"
    if c["adapter"] == "opencode" and c["base_url"] == PROVIDERS["opencode"]["base_url"]:
        url = "https://opencode.ai/inference/go/v1/models"
    try:
        with httpx.Client(transport=BoundTransport() if c["adapter"] == "custom" else None,
                          follow_redirects=False, timeout=12) as client:
            response = client.get(url, headers={**model_headers(c["adapter"], new_id()), "Authorization": "Bearer " + key})
            response.raise_for_status()
        rows = response.json()["data"]
        catalog = sorted({row["id"] for row in rows if isinstance(row.get("id"), str)
                          and 0 < len(row["id"]) <= 180})
        capabilities = opencode_capabilities(c, rows) if c["adapter"] == "opencode" else {}
    except httpx.HTTPStatusError as error:
        raise HTTPException(502, f"模型目录返回 HTTP {error.response.status_code}；已保留配置，可手动登记模型 ID") from None
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(502, "无法读取模型目录；已保留配置，可手动登记模型 ID") from None
    def update(current):
        current["catalog"] = catalog
        if current["adapter"] == "opencode":
            current["catalog_capabilities"] = capabilities
            for model in models_for(current):
                mode, efforts = capability("opencode", model["id"], capabilities)
                model.update(verified=[e for e in model["verified"] if e in efforts
                                       and (e == "default" or mode == model["mode"])], mode=mode, efforts=efforts)
    return persist_result(c, update)


@router.put("/connections/{cid}/model")
def save_model(cid: str, setting: ModelSettings):
    name = setting.model_id.strip()
    if not name or any(ch.isspace() for ch in name):
        raise HTTPException(422, "模型 ID 不可为空或包含空白")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        c = require(conn, cid)
        mode, efforts = capability(c["adapter"], name, c.get("catalog_capabilities"))
        if c["adapter"] == "custom":
            mode = setting.mode
            if mode not in {"unknown", "switch", "effort"} or set(setting.efforts) - LEVELS:
                raise HTTPException(422, "无效的思考能力或档位")
            if mode == "effort" and not setting.efforts:
                raise HTTPException(422, "至少登记一个档位")
            efforts = ["default"] + (["off", "on"] if mode == "switch" else
                                    list(dict.fromkeys(setting.efforts)) if mode == "effort" else [])
        old = next((m for m in models_for(c) if m["id"] == name), {})
        verified = [e for e in old.get("verified", []) if e in efforts and (e == "default" or mode == old.get("mode"))]
        model = {"id": name, "mode": mode, "efforts": efforts, "enabled": setting.enabled, "verified": verified}
        c["models"] = [m for m in models_for(c) if m["id"] != name] + [model]
        put(conn, "model_connection", cid, c)
        return view(conn)


@router.post("/connections/{cid}/test")
def probe_model(cid: str, probe: Probe):
    with db() as conn:
        c = require(conn, cid)
    model = next((m for m in models_for(c) if m["id"] == probe.model_id), None)
    if not model or probe.effort not in model["efforts"]:
        raise HTTPException(422, "请先登记模型和待测档位")
    try:
        complete(target(c, model, probe.effort), [{"role": "user", "content": "只回复 OK"}], new_id(), max_tokens=512)
    except ValueError as error:
        raise HTTPException(502, f"{probe.model_id} / {probe.effort}：{error}") from None

    def verify(current):
        for m in models_for(current):
            if m["id"] == probe.model_id:
                m["verified"] = sorted(set(m["verified"]) | {probe.effort})
    return persist_result(c, verify)


@router.put("/chat-default/{cid}")
def chat_default(cid: str, probe: Probe):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        choices, _ = chat_options(conn)
        if not any(c["id"] == cid and m["id"] == probe.model_id for c, m in choices):
            raise HTTPException(422, "请选择已勾选且通过测试的模型")
        put(conn, "settings", "chat_default", {"connection_id": cid, "model_id": probe.model_id})
        return view(conn)
