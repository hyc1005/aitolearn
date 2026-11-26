import base64
import csv
import hashlib
import hmac
import io
import json
import time
import queue
import threading
from zipfile import BadZipFile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import keyring
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from keyring.errors import KeyringError
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from .agent import api_key
from .attachment_flow import plan_attachment_tools
from .materials import ocr_executable
from .attachments import MATERIALS, archive_is_small, excerpt, material_signature
from .chat_agent import run_chat
from .cleaning_agent import structured_review
from .conversation import (CORRECTION_FIELDS, cleaning_card, deterministic, latest_clean, make_proposal, material_version, onboarding,
                           proposal_card, resolve, resolve_recommendation, save_session, session_summary, session_view,
                           settings_get, settings_put)
from .data import REQUIRED, import_headers, profile, read_file, student_scores
from .diagnosis import overview as diagnosis_overview, zero_evidence
from .follow_up import router as follow_up_router
from .learning_chat import ChatRequest, ChatTurn, collect_sources, reply
from .learning_progress import answer_progress
from .model_connections import migrate as migrate_connections, router as model_router, selection as chat_selection
from .models import KEY_SERVICE, PROVIDERS, ToolsUnavailable, active_provider, complete, provider_config, provider_key
from .recommendation_review import preview_text
from .search import backfill_index, search
from .snapshots import current_snapshot, legacy_batches, queue_cleaning, queue_job, table_data
from .store import all_of, db, get, new_id, put, save_file
from . import student_query


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(student_query.backfill, db)
    await run_in_threadpool(backfill_index, db)
    with db() as conn:
        for item in all_of(conn, "delivery"):
            if item["state"] == "sending":
                put(conn, "delivery", item["id"], {**item, "state": "unknown"})
    yield


app = FastAPI(title="智慧学伴", lifespan=lifespan)
app.include_router(follow_up_router)
app.include_router(model_router)


@app.middleware("http")
async def local_origin(request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0]
    origin = urlparse(request.headers.get("origin", "")).hostname
    if host not in {"127.0.0.1", "localhost", "testserver"} or (
        origin and origin not in {"127.0.0.1", "localhost", "testserver"}
    ):
        return Response(status_code=403)
    return await call_next(request)


def require(conn, kind, key):
    item = get(conn, kind, key)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def tables(conn):
    return table_data(current_snapshot(conn))


def job(conn, kind, payload):
    return queue_job(conn, kind, payload)


async def read_upload(file):
    raw = await file.read(70_000_001)
    if not raw or len(raw) > 70_000_000:
        raise HTTPException(400, "文件为空或超过 70 MB")
    return raw


@app.get("/api/health")
def health():
    return {"status": "ready", "model_configured": bool(api_key()),
            "ocr_configured": ocr_executable().is_file()}


class ModelSettings(BaseModel):
    api_key: SecretStr | None = None


@app.get("/api/models/settings")
def model_settings():
    return {"active": active_provider(), "providers": [
        {"id": provider, **provider_config(provider), "configured": bool(provider_key(provider))}
        for provider in PROVIDERS
    ]}


@app.put("/api/models/settings/{provider}")
def save_model_settings(provider: str, setting: ModelSettings):
    if provider not in PROVIDERS:
        raise HTTPException(404, "模型平台不存在")
    key = setting.api_key.get_secret_value().strip() if setting.api_key else ""
    if key:
        if len(key) > 2048 or any(char.isspace() for char in key):
            raise HTTPException(400, "API 密钥格式无效")
        try:
            keyring.set_password(KEY_SERVICE, provider, key)
        except KeyringError:
            raise HTTPException(503, "无法写入系统凭据库，可通过环境变量配置 API 密钥") from None
    if not provider_key(provider):
        raise HTTPException(422, "请先填写该平台的 API 密钥")
    with db() as conn:
        put(conn, "settings", "model_provider", {"id": provider})
    return model_settings()


@app.post("/api/models/test/{provider}")
def test_model_connection(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "模型平台不存在")
    if not provider_key(provider):
        raise HTTPException(422, "请先保存该平台的 API 密钥")
    try:
        complete(provider, [{"role": "user", "content": "只回复 OK"}], new_id(), max_tokens=32)
    except ValueError as error:
        raise HTTPException(502, str(error)) from None
    return {"model": provider_config(provider)["model"]}


@app.post("/api/learning-chat")
def learning_chat(request: ChatRequest):
    started = time.monotonic()
    if not request.question.strip() or any(isinstance(turn, str) and len(turn) > 1200 for turn in request.history):
        raise HTTPException(422, "请输入问题，单条最多 1200 字")
    with db() as conn:
        session = get(conn, "chat_session", request.session_id) if request.session_id else None
        if session and not request.history:
            request.history = [ChatTurn(question=turn["question"], answer=turn.get("answer", "")[:1200])
                               for turn in session.get("turns", [])[-5:] if turn.get("answer")]
        if session and not request.context_changed and not (request.student or request.group or request.node):
            context = session.get("context", {})
            request.student = context.get("student", "")
            request.group = context.get("group", "")
            request.node = context.get("node", "")
        if session and not request.snapshot_id and not request.context_changed:
            request.snapshot_id = session.get("context", {}).get("snapshot_id", "")
        active = current_snapshot(conn)
        snapshot = require(conn, "snapshot", request.snapshot_id) if request.snapshot_id else active
        request.snapshot_id = snapshot["id"] if snapshot else ""
        if snapshot and not student_query.is_indexed(conn, request.snapshot_id):
            raise HTTPException(404, "该快照尚未确认")
        if not get(conn, "model_connection", "opencode"):
            migrate_connections(conn)
        selected = chat_selection(conn, request, session)
        provider = request.provider or (session or {}).get("provider") or active_provider(conn)
        if not selected and request.provider and (provider not in PROVIDERS or not provider_key(provider)):
            raise HTTPException(422, "请选择已配置的模型")
        request.provider = provider if request.provider or (session or {}).get("provider") else None
        result = None if request.attachments else answer_progress(conn, snapshot, request)
    called = False
    mode = "data" if result else "buttons"
    if result is None and request.use_tools and (selected or (not request.use_configured_models and provider in PROVIDERS and provider_key(provider))):
        try:
            called = True
            result = run_chat(request, snapshot, selected or provider)
            mode = "tools"
        except ToolsUnavailable:
            mode = "without_tools"
        except ValueError as error:
            raise HTTPException(502, str(error)) from None
    if result is None:
        with db() as conn:
            result = None if request.attachments else deterministic(conn, request, snapshot)
        if result is None:
            if request.use_configured_models and not selected:
                raise HTTPException(422, "请先在设置中勾选并测试一个聊天模型")
            try:
                with db() as conn:
                    sources = collect_sources(conn, snapshot, request)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            try:
                called = True
                result = reply(request, sources, new_id(), selected)
            except ValueError as error:
                raise HTTPException(502, str(error)) from None
            proposal = result.pop("proposal", None) if isinstance(result, dict) else None
            if proposal and snapshot:
                data = table_data(snapshot)
                resolved = resolve(data, request.question,
                                   {"student": request.student, "group": request.group, "node": request.node})
                resolved.update({key: proposal.get(key) or resolved.get(key, "")
                                 for key in ("student", "group", "node")})
                payload, card = resolve_recommendation(data, snapshot["id"], resolved, request.anchor_event)
                if payload:
                    with db() as conn:
                        item = make_proposal(conn, "generate_recommendation", payload, title="生成学习建议")
                    result["cards"] = [proposal_card(item)]
                elif card:
                    result["cards"] = [card]
    result.setdefault("parts", [])
    result.setdefault("cards", [])
    result["snapshot_id"] = request.snapshot_id
    result["mode"] = mode
    if called:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    if selected:
        result["selection"] = selected.metadata()
        if called:
            result["model_call"] = selected.metadata()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        saved = save_session(conn, request.session_id, request, result)
        return {**result, "session_id": saved["id"], "context": saved["context"]}


@app.post('/api/learning-chat/stream')
def learning_chat_stream(request: ChatRequest):
    """Stream transient reasoning/tool events; only the verified final turn is stored."""
    from .chat_stream import chat_events
    events = queue.Queue()
    closed = threading.Event()

    def emit_event(event):
        if not closed.is_set():
            events.put(event)

    def run():
        token = chat_events.set(emit_event)
        try:
            emit_event({'type': 'complete', 'result': learning_chat(request)})
        except HTTPException as error:
            emit_event({'type': 'error', 'message': str(error.detail), 'status': error.status_code})
        except Exception:
            emit_event({'type': 'error', 'message': '对话处理失败，请重试', 'status': 500})
        finally:
            chat_events.reset(token)
            events.put(None)

    def body():
        threading.Thread(target=run, daemon=True).start()
        try:
            while True:
                try:
                    event = events.get(timeout=15)
                except queue.Empty:
                    yield ': keepalive\n\n'
                    continue
                if event is None:
                    break
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
        finally:
            closed.set()

    return StreamingResponse(body(), media_type='text/event-stream', headers={'Cache-Control': 'no-store',
                                                                              'X-Accel-Buffering': 'no'})


@app.get("/api/chat/sessions")
def chat_sessions():
    with db() as conn:
        sessions = [session_summary(item) for item in all_of(conn, "chat_session") if item.get("turns")]
        return sorted(sessions, key=lambda item: item["last_active_at"] or 0, reverse=True)[:30]


class SessionTitle(BaseModel):
    title: str | None


@app.put("/api/chat/sessions/{key}/title")
def update_chat_title(key: str, request: SessionTitle):
    title = request.title.strip() if request.title is not None else None
    if title is not None and not 1 <= len(title) <= 72:
        raise HTTPException(422, "会话名称须为 1–72 个字")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = require(conn, "chat_session", key)
        if title is None:
            session.pop("custom_title", None)
        else:
            session["custom_title"] = title
        put(conn, "chat_session", key, session)
        return session_summary(session)


class SessionSelection(BaseModel):
    connection_id: str = Field(min_length=1, max_length=40)
    model_id: str = Field(min_length=1, max_length=180)
    effort: str = Field(max_length=16)
    provider: str | None = None


@app.put("/api/chat/sessions/{key}/selection")
def update_chat_selection(key: str, selection: SessionSelection):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = require(conn, "chat_session", key)
        selected = chat_selection(conn, selection, session)
        session["selection"] = selected.metadata()
        put(conn, "chat_session", key, session)
        return session["selection"]


@app.get("/api/chat/sessions/{key}")
def chat_session(key: str):
    with db() as conn:
        session = session_view(conn, key)
        if session is None:
            raise HTTPException(404, "对话不存在")
        return {**session, **session_summary(session)}


@app.get("/api/chat/proposals/{key}")
def chat_proposal(key: str):
    with db() as conn:
        item = require(conn, "proposal", key)
        return proposal_card(item)


@app.get("/api/chat/tasks/{key}")
def chat_task(key: str):
    with db() as conn:
        run = require(conn, "job", key)
        draft_id = (run.get("stages", {}).get("generate") or {}).get("draft_id")
        draft = get(conn, "draft", draft_id) if draft_id else None
        return {"state": run["state"], "stage": run.get("stage"), "kind": run["kind"],
                "draft_id": draft_id if draft else None, "error": run.get("error"),
                "model_retrying": run.get("model_retrying", False)}


@app.post("/api/chat/proposals/{key}/confirm")
def confirm_proposal(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        proposal = require(conn, "proposal", key)
        if proposal.get("status") == "confirmed" and proposal.get("result") is not None:
            return {"proposal": proposal_card(proposal), "result": proposal["result"]}
        payload = proposal["payload"]
        if proposal["kind"] == "confirm_import":
            run = require(conn, "job", payload["run_id"])
            batch = require(conn, "import", payload["batch_id"])
            if run.get("stage") == "published":
                result = run.get("result") or {}
                proposal.update(status="confirmed", result=result, confirmed_at=time.time())
                put(conn, "proposal", key, proposal)
                return {"proposal": proposal_card(proposal), "result": result}
            active = current_snapshot(conn)
            baseline = run["payload"]["base_snapshot_id"]
            if baseline != (active["id"] if active else None) or (
                    active is None and run["payload"]["legacy_baseline"] != legacy_batches(conn)):
                raise HTTPException(409, {"message": "基础数据已变更，请按当前数据重新运行清洗",
                                          "snapshot_changed": True,
                                          "current_snapshot_id": active["id"] if active else None})
            result = confirm_batch(conn, payload["batch_id"], payload["run_id"])
            proposal.update(status="confirmed", result=result, confirmed_at=time.time())
            put(conn, "proposal", key, proposal)
            return {"proposal": proposal_card(proposal), "result": result}
        if proposal["kind"] == "generate_recommendation":
            active = current_snapshot(conn)
            if not active or payload.get("snapshot_id") != active["id"]:
                raise HTTPException(409, {
                    "message": "数据快照已更新，请按新快照重新确认",
                    "snapshot_changed": True,
                    "current_snapshot_id": active["id"] if active else None,
                })
            run = create_run(conn, Run(student=payload.get("student", ""), group=payload.get("group", ""),
                                       node=payload.get("node", ""), anchor_event=payload.get("anchor_event", ""),
                                       variant=payload.get("variant", False), snapshot_id=payload.get("snapshot_id", "")))
            result = {"run_id": run["id"], "snapshot_id": active["id"]}
            proposal.update(status="confirmed", result=result, confirmed_at=time.time())
            put(conn, "proposal", key, proposal)
            return {"proposal": proposal_card(proposal), "result": result}
        if proposal["kind"] == "approve_draft":
            active = current_snapshot(conn)
            draft = require(conn, "draft", payload["draft_id"])
            if not active or active["id"] != payload["snapshot_id"] or material_version(draft) != payload["version"]:
                raise HTTPException(409, "数据快照或建议版本已变化，请重新核对。")
            review = draft.get("final_review") or {}
            if review.get("decision") != "pass" or review.get("input_version") != draft["revision"]:
                raise HTTPException(409, "当前建议版本尚未通过终审")
            saved = edit_draft(conn, draft["id"], Edit(revision=payload["revision"], approved=True,
                message=draft.get("message", draft.get("follow_up", "")),
                exercises=[item["question_id"] for item in draft["exercises"]]))
            result = {"draft_id": saved["id"], "revision": saved["revision"], "approved": saved["approved"]}
            proposal.update(status="confirmed", result=result, confirmed_at=time.time())
            put(conn, "proposal", key, proposal)
            return {"proposal": proposal_card(proposal), "result": result}
        if proposal['kind'] == 'assign_follow_up':
            from .follow_up import Assignment, assign_in
            draft = require(conn, 'draft', payload['draft_id'])
            active = current_snapshot(conn)
            if not active or active['id'] != payload['snapshot_id'] or material_version(draft) != payload['version']:
                raise HTTPException(409, '数据快照或建议版本已变化，请重新核对。')
            result = assign_in(conn, payload['draft_id'], Assignment(revision=payload['revision'],
                baseline_node=payload['baseline_node'], directions=payload['directions']))
            proposal.update(status='confirmed', result=result, confirmed_at=time.time())
            put(conn, 'proposal', key, proposal)
            return {'proposal': proposal_card(proposal), 'result': result}
        if proposal["kind"] == "publish_material":
            doc = require(conn, "material", payload["material_id"])
            if material_version(doc) != payload["version"]:
                raise HTTPException(409, "教学资料已变化，请重新核对后发起发布。")
            run = publish_document(conn, doc)
        elif proposal["kind"] == "retry_job":
            item = require(conn, "job", payload["run_id"])
            if material_version(item) != payload["version"]:
                raise HTTPException(409, "任务状态已变化，请重新查询后发起重试。")
            run = retry_run(conn, item)
        elif proposal["kind"] == "stop_job":
            item = require(conn, "job", payload["run_id"])
            if item["state"] != payload["state"] or item["kind"] != payload["kind"]:
                raise HTTPException(409, "任务状态已变化，请重新查询。")
            result = manage_job_in(conn, item, "stop")
            proposal.update(status="confirmed", result=result, confirmed_at=time.time())
            put(conn, "proposal", key, proposal)
            return {"proposal": proposal_card(proposal), "result": result}
        elif proposal["kind"] in {"revise_material", "revise_draft", "revise_exercises", "correct_attempt"}:
            result = confirm_chat_revision(conn, proposal["kind"], payload)
            proposal.update(status="confirmed", result=result, confirmed_at=time.time())
            put(conn, "proposal", key, proposal)
            return {"proposal": proposal_card(proposal), "result": result}
        else:
            raise HTTPException(400, "不支持的操作类型")
        result = {"run_id": run["id"]}
        proposal.update(status="confirmed", result=result, confirmed_at=time.time())
        put(conn, "proposal", key, proposal)
        return {"proposal": proposal_card(proposal), "result": result}


def confirm_chat_revision(conn, action, payload):
    if action == "revise_material":
        doc = require(conn, "material", payload["material_id"])
        if material_version(doc) != payload["version"]:
            raise HTTPException(409, "资料版本已变化，请重新核对。")
        return edit_document(conn, doc["id"], Revision(changes={payload["block_id"]: payload["after"]}))
    if action == "revise_draft":
        draft = require(conn, "draft", payload["draft_id"])
        if material_version(draft) != payload["version"]:
            raise HTTPException(409, "草稿版本已变化，请重新核对。")
        saved = edit_draft(conn, draft["id"], Edit(revision=payload["revision"], approved=False,
            message=payload["after"], exercises=[item["question_id"] for item in draft["exercises"]]))
        return {"run_id": saved["review_run_id"], "draft_id": saved["id"]}
    if action == 'revise_exercises':
        draft = require(conn, 'draft', payload['draft_id'])
        if material_version(draft) != payload['version']:
            raise HTTPException(409, '草稿版本已变化，请重新核对。')
        saved = edit_draft(conn, draft['id'], Edit(revision=payload['revision'], approved=False,
            message=draft.get('message', draft.get('follow_up', '')), exercises=payload['order'], hints=payload['hints']))
        return {'run_id': saved.get('review_run_id'), 'draft_id': saved['id']}
    active = current_snapshot(conn)
    if not active or active["id"] != payload["snapshot_id"]:
        raise HTTPException(409, "数据快照已变化，请重新核对作答。")
    event = payload["event_id"]
    attempts = [row for key in active["batch_ids"] for row in require(conn, "import", key)["rows"]
                if row["table"] == "作答记录" and row["values"].get("作答事件编号") == event
                and row["values"].get("修订版本", "").isdigit()]
    if not attempts:
        raise HTTPException(409, "找不到可修订的原始作答。")
    revision = max(int(row["values"]["修订版本"]) for row in attempts)
    latest = [row for row in attempts if int(row["values"]["修订版本"]) == revision]
    if len(latest) != 1 or latest[0]["values"].get(payload["field"], "") != payload["before"]:
        raise HTTPException(409, "作答记录已变化或存在歧义，请在数据导入中选择原始记录。")
    row = latest[0]
    return correct_record_in(conn, Correction(source=row["source"], sheet=row["sheet"], line=row["line"],
        file_id=row.get("file_id"), changes={payload["field"]: payload["after"]}))


@app.post("/api/chat/proposals/{key}/refresh")
def refresh_proposal(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        proposal = require(conn, "proposal", key)
        if proposal.get("status") == "confirmed":
            return proposal_card(proposal)
        if proposal["kind"] != "generate_recommendation":
            raise HTTPException(409, "该操作无法自动换成新快照，请重新发起")
        active = current_snapshot(conn)
        if not active:
            raise HTTPException(409, "尚未确认数据快照")
        data = table_data(active)
        payload = proposal["payload"]
        resolved = resolve(data, "", {"student": payload.get("student", ""),
                                      "group": payload.get("group", ""), "node": payload.get("node", "")})
        fresh, card = resolve_recommendation(data, active["id"], resolved, payload.get("anchor_event", ""))
        if not fresh:
            raise HTTPException(409, card.get("prompt", "对象已不存在"))
        proposal["payload"] = fresh
        proposal["status"] = "pending"
        put(conn, "proposal", key, proposal)
        return proposal_card(proposal)


@app.get("/api/onboarding")
def onboarding_state():
    with db() as conn:
        return onboarding(conn)


class OnboardingChoice(BaseModel):
    permanent: bool = False


@app.post("/api/onboarding/dismiss")
def onboarding_dismiss(choice: OnboardingChoice):
    with db() as conn:
        settings_put(conn, "onboarding", {"dismissed": choice.permanent, "completed": choice.permanent,
                                          "at": time.time()})
        return onboarding(conn)


@app.post("/api/onboarding/reopen")
def onboarding_reopen():
    with db() as conn:
        settings_put(conn, "onboarding", {"dismissed": False, "completed": False, "reopened": True,
                                          "at": time.time()})
        return onboarding(conn)


@app.get("/api/jobs")
def jobs():
    with db() as conn:
        return [{field: value for field, value in item.items() if field not in {"stages", "failures"}}
                for item in sorted(all_of(conn, "job"), key=lambda job: -job["created"])]


@app.get("/api/agent/runs/{key}")
def run_detail(key: str):
    with db() as conn:
        run = require(conn, "job", key)
        if run["kind"] not in {"clean", "generate", "recommendation_review", "extract", "publish"}:
            raise HTTPException(404, "任务不存在")
        stages = run.get("stages", {})
        if run["kind"] == "clean" and "python" in stages:
            snapshot = require(conn, "snapshot", stages["python"]["snapshot_id"])
            active = current_snapshot(conn)
            run.update(report=snapshot["report"], manifest=snapshot["manifest"],
                       outdated=(active["id"] if active else None) != snapshot["base_snapshot_id"],
                       correction=require(conn, "import", run["payload"]["batch_id"]).get("correction", False))
        if run["kind"] == "generate" and run["payload"].get("snapshot_id"):
            snapshot = require(conn, "snapshot", run["payload"]["snapshot_id"])
            data = table_data(snapshot)
            run["snapshot_uploads"] = list(dict.fromkeys(source["name"] for batch in snapshot.get("manifest", [])
                                                          for source in batch.get("sources", [])))
            run["source_index_examples"] = list(dict.fromkeys(
                row["来源或资料文件"] for row in data["题目与材料"].values()
                if row.get("来源或资料文件") and Path(row["来源或资料文件"]).suffix.lower() in MATERIALS))[:5]
        if run["kind"] == "generate" and "interpret" in stages:
            scope = profile(data, *(run["payload"].get(field, "") for field in ("student", "group", "node")))
            run["interpret_indicators"] = {axis["id"]: {key: axis[key] for key in ("label", "coverage", "population", "n")}
                                           for axis in scope["axes"]}
            refs = {ref for claim in stages["interpret"]["claims"] for ref in claim["refs"][1:]}
            run["linked_evidence"] = {ref: {**data["作答记录"][ref],
                                          "题目": data["题目与材料"][data["作答记录"][ref]["材料编号"]]}
                                      for ref in refs}
        run["stop_requested"] = run["state"] == "running" and bool(get(conn, "job_stop", key))
        return run


def diagnosis_source(key):
    with db() as conn:
        run = require(conn, "job", key)
        payload = run.get("payload", {})
        if run["kind"] != "generate" or not payload.get("snapshot_id"):
            raise HTTPException(404, "这次运行没有诊断快照")
        return payload, table_data(require(conn, "snapshot", payload["snapshot_id"]))


@app.get("/api/agent/runs/{key}/diagnosis")
def run_diagnosis(key: str):
    payload, tables = diagnosis_source(key)
    return diagnosis_overview(tables, payload)


@app.get("/api/agent/runs/{key}/diagnosis/zero-events")
def run_zero_events(key: str, indicator: str = "", node: str = "", item: str = "",
                    offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50), event: str = ""):
    payload, tables = diagnosis_source(key)
    result = zero_evidence(tables, payload, indicator, node, item, offset, limit, event)
    if result is None:
        raise HTTPException(404, "该作答不在本次诊断范围内")
    return result


@app.get("/api/agent/cases/q015")
def q015_case():
    draft_id = "2809aa96e95e4568a990e4f10828b44a"
    review_id = "9cbafe0ffab2448090a8776ed9ae7229"
    with db() as conn:
        conn.execute("BEGIN")
        initial_run = get(conn, "job", draft_id) or {}
        final_run = get(conn, "job", review_id) or {}
        first = get(conn, "draft_revision", f"{draft_id}:1")
        second = get(conn, "draft_revision", f"{draft_id}:2")
        snapshot_id = (initial_run.get("payload") or {}).get("snapshot_id")
        snapshot_id = snapshot_id or (first or second or {}).get("snapshot_id")
        snapshot = get(conn, "snapshot", snapshot_id) if snapshot_id else None
        alternatives = [draft for draft in all_of(conn, "draft")
                        if snapshot and draft.get("snapshot_id") == snapshot_id and draft.get("student")
                        and draft["student"] != "D01" and draft.get("evidence") and draft.get("exercises")]

    evidence = None
    indicator = None
    returned_question = None
    if snapshot:
        data = table_data(snapshot)
        row = data["作答记录"].get("D01-N01-Q015")
        if row:
            evidence = {**row, "题目": data["题目与材料"].get("Q015", {})}
        indicator = data["课程要求"].get("T01I2")
        returned_question = data["题目与材料"].get("Q004", {}).get("题干或内容")

    def suggestion(draft):
        if draft is None:
            return None
        return {field: draft.get(field) for field in
                ("revision", "observation", "message", "exercises", "citations", "student",
                 "node", "approved", "review_state", "evidence", "questions")}

    def review(run, draft, revision):
        if (run.get("result") or {}) != {"draft": draft_id, "revision": revision}:
            return None
        result = (run.get("stages") or {}).get("final_review")
        if not result or result.get("input_version") != revision:
            return None
        return {"review": result, "confirmed": bool(
            draft and draft.get("approved") and run.get("confirmed_at")), "run_id": run["id"]}

    accepted = review(final_run, second, 2)
    main_questions = [item["question_id"] for item in (second or {}).get("exercises", [])]
    comparisons = sorted((draft for draft in alternatives
                          if [item["question_id"] for item in draft["exercises"]] != main_questions),
                         key=lambda draft: (draft.get("node") != "N01", draft["student"], draft["id"]))
    by_student = {}
    for draft in comparisons:
        by_student.setdefault(draft["student"], draft)
    return {"snapshot_id": snapshot_id, "indicator": indicator, "returned_question": returned_question,
            "comparisons": [suggestion(draft) for draft in by_student.values()],
            "student_preview": preview_text(second) if accepted and accepted["confirmed"] else None,
            "steps": [
        {"title": "D01 · N01 · Q015 作答", "evidence": evidence},
        {"title": "初版建议", "before": suggestion(first)},
        {"title": "初次终审", "result": review(initial_run, first, 1)},
        {"title": "教师修订", "before": suggestion(first), "after": suggestion(second)},
        {"title": "修订 2 终审", "result": accepted},
    ]}


@app.post("/api/jobs/{key}/control/{action}")
def manage_job(key: str, action: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = require(conn, "job", key)
        return manage_job_in(conn, item, action)


def manage_job_in(conn, item, action):
    key = item["id"]
    if action == "stop" and item["kind"] not in {"clean", "generate", "recommendation_review"} and item["state"] == "running":
        raise HTTPException(409, "当前任务不支持阶段停止")
    if action == "stop" and item["state"] == "running":
        put(conn, "job_stop", key, {"requested": True})
        return {"state": "running", "stop_requested": True}
    if action == "stop" and item["state"] == "queued":
        item["state"] = "stopped"
    elif action in {"archive", "unarchive"} and item["state"] in {"done", "failed", "stopped"}:
        item["archived"] = action == "archive"
    else:
        raise HTTPException(409, "当前任务状态不支持此操作")
    put(conn, "job", key, item)
    return item


@app.post("/api/jobs/{key}/retry")
def retry(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return retry_run(conn, require(conn, "job", key))


def retry_run(conn, item):
    if item["state"] not in {"failed", "stopped"}:
        raise HTTPException(409, "仅失败或已停止的任务可以恢复")
    if item["kind"] not in {"clean", "generate", "recommendation_review", "extract"}:
        return job(conn, item["kind"], item["payload"])
    item["state"] = "queued"
    item.pop("archived", None)
    item.pop("last_model_request", None)
    conn.execute("DELETE FROM records WHERE kind = 'job_stop' AND id = ?", (item["id"],))
    item.pop("error", None)
    item.pop("model_error", None)
    put(conn, "job", item["id"], item)
    return item


async def inspect_import(files, mapping):
    try:
        columns = json.loads(mapping)
        if not isinstance(columns, dict) or not all(isinstance(v, str) for v in columns.values()):
            raise ValueError("列映射必须是 JSON 对象")
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    inspected = []
    for file in files:
        name = Path(file.filename or "").name
        raw = await read_upload(file)
        info = {"name": name, "tables": {}, "status": "ready", "message": ""}
        try:
            if name.lower().endswith(".xlsx") and not archive_is_small(raw):
                raise ValueError("工作簿超过安全解压上限")
            if name.lower().endswith(".csv"):
                try:
                    contents = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    contents = raw.decode("gb18030")
                headers = next(csv.reader(io.StringIO(contents, newline="")), [])
                headers = [columns.get(header.strip(), header.strip()) for header in headers]
                if {"数据表", "字段名", "含义"} <= set(headers):
                    info.update(status="skipped", message="字段说明表，将跳过")
                    inspected.append((info, raw, []))
                    continue
                expected = Path(name).stem
                if expected in REQUIRED:
                    missing = sorted(set(REQUIRED[expected]) - set(headers))
                    if missing:
                        raise ValueError("缺少必需列：" + "、".join(missing))
            rows = read_file(name, raw, columns)
            for row in rows:
                info["tables"][row["table"]] = info["tables"].get(row["table"], 0) + 1
        except (ValueError, OSError, UnicodeError, csv.Error, BadZipFile) as error:
            info.update(status="error", message=str(error))
            rows = []
        inspected.append((info, raw, rows))
    return inspected


@app.post("/api/chat/attachments/inspect")
async def inspect_attachment(file: UploadFile = File(...), reference: bool = Form(False),
                             mapping: str = Form("{}")):
    name = Path(file.filename or "").name[:180]
    raw = await read_upload(file)
    suffix = Path(name).suffix.lower()
    if reference:
        try:
            text = await run_in_threadpool(excerpt, name, raw)
        except Exception:  # Uploaded documents can fail in several parser libraries.
            text = ""
        return {"name": name, "classification": "reference" if text else "unknown",
                "excerpt": text, "reason": "本轮参考摘录，不自动入库" if text else "未能提取文字；扫描件请走资料导入"}
    try:
        if suffix in {".xlsx", ".csv"}:
            if suffix == ".xlsx" and not archive_is_small(raw):
                return {"name": name, "classification": "unknown", "reason": "工作簿超过安全解压上限"}
            [(info, _, rows)] = await inspect_import([UploadFile(io.BytesIO(raw), filename=name)], mapping)
            return {"name": name, "classification": "data" if rows or info["status"] == "skipped" else "unknown",
                    "reason": info["message"] or "业务表结构与所需列匹配", "tables": info["tables"]}
        if suffix in MATERIALS and material_signature(suffix, raw):
            return {"name": name, "classification": "material", "reason": "文档或图像格式匹配；需解析和人工校对后发布"}
    except (BadZipFile, OSError, ValueError):
        pass
    return {"name": name, "classification": "unknown", "reason": "未识别到有效的业务表或教材格式，不会提交"}


@app.post("/api/chat/attachments/submit")
async def submit_chat_attachments(files: list[UploadFile] = File(...),
                                  batch_id: str = Form(..., pattern=r"^[a-f0-9-]{36}$"),
                                  session_id: str = Form("", max_length=100),
                                  continue_target: str = Form("", max_length=180)):
    if not 1 <= len(files) <= 20:
        raise HTTPException(422, "请选择 1–20 个文件")
    continuation = parse_continuation(continue_target)
    with db() as conn:
        if prior := get(conn, "attachment_receipt", batch_id):
            return prior
        session = require(conn, "chat_session", session_id) if session_id else None
        try:
            provider = chat_selection(conn, ChatRequest(question="处理上传文件"), session) or active_provider(conn)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            provider = active_provider(conn)
    tables_for_import, documents, skipped, seen = [], [], [], set()
    size = 0
    for file in files:
        name = Path(file.filename or "").name[:180]
        suffix = Path(name).suffix.lower()
        if name in seen:
            raise HTTPException(422, "同一批文件不可重名")
        seen.add(name)
        raw = await read_upload(file)
        size += len(raw)
        if size > 100_000_000:
            raise HTTPException(422, "单批附件不能超过 100 MB")
        if suffix in {".csv", ".xlsx"}:
            [entry] = await inspect_import([UploadFile(io.BytesIO(raw), filename=name)], "{}")
            info, _, rows = entry
            if rows and info["status"] == "ready":
                tables_for_import.append(entry)
            else:
                skipped.append({"name": name, "reason": info["message"] or "没有可导入的业务数据"})
        else:
            try:
                recognized = suffix in MATERIALS and material_signature(suffix, raw)
            except (BadZipFile, ValueError, OSError):
                recognized = False
            if recognized:
                documents.append((name, raw))
            else:
                skipped.append({"name": name, "reason": "未识别格式，不会导入"})
    counts = {"queue_student_import": len(tables_for_import), "queue_material_extract": len(documents)}
    started = time.monotonic()
    plan = await run_in_threadpool(plan_attachment_tools, provider, batch_id, counts) if any(counts.values()) else []
    paths, cards, trace = [], [], []
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if prior := get(conn, "attachment_receipt", batch_id):
                return prior
            for tool, origin in plan:
                if tool == "queue_student_import":
                    run, _ = queue_inspected_import(conn, tables_for_import, {}, continuation, paths)
                    card, _ = cleaning_card(conn, run)
                    cards.append(card)
                elif tool == "queue_material_extract":
                    for name, raw in documents:
                        path = save_file(raw, Path(name).suffix.lower())
                        paths.append(path)
                        extract = queue_material(conn, name, path)
                        cards.append({"kind": "material_extract", "run_id": extract["id"], "material_id": extract["payload"]["material"],
                                      "name": name, "state": "queued"})
                trace.append({"tool": tool, "status": "ok", "count": counts[tool], "origin": origin})
            if skipped:
                cards.append({"kind": "attachment_skipped", "files": skipped})
            ctx = (session or {}).get("context", {})
            answer = {"answer": "附件已进入后台处理；需要确认或校对时会在任务卡中显示。" if plan else "没有可以自动处理的文件。",
                      "cards": cards, "trace": trace, "parts": [], "snapshot_id": ctx.get("snapshot_id", ""),
                      "elapsed_ms": round((time.monotonic() - started) * 1000) if plan else None}
            request = ChatRequest(question=("上传附件：" + "、".join(seen_in_order(files)))[:1200],
                                  **{key: ctx.get(key, "") for key in ("student", "group", "node")})
            saved = save_session(conn, session_id or None, request, answer)
            result = {**answer, "question": request.question, "session_id": saved["id"]}
            put(conn, "attachment_receipt", batch_id, result)
            return result
    except BaseException:
        for path in paths:
            Path(path).unlink(missing_ok=True)
        raise


def seen_in_order(files):
    return [Path(file.filename or "").name[:180] for file in files]


@app.post("/api/imports/preview")
async def preview_import(files: list[UploadFile] = File(...), mapping: str = Form("{}")):
    inspected = await inspect_import(files, mapping)
    return {"files": [info for info, _, _ in inspected],
            "can_import": any(rows for _, _, rows in inspected) and not any(
                info["status"] == "error" for info, _, _ in inspected)}


class ColumnMapping(BaseModel):
    mapping: dict[str, str]


@app.post("/api/imports/suggest-mapping")
async def suggest_import_mapping(files: list[UploadFile] = File(...), mapping: str = Form("{}")):
    inspected = await inspect_import(files, mapping)
    existing = json.loads(mapping)
    expected = {field for fields in REQUIRED.values() for field in fields} | {"测验总分"}
    unknown = set()
    for info, raw, _ in inspected:
        if info["status"] != "error" or not info["message"].startswith(("缺少必需列", "未识别到")):
            continue
        for headers in import_headers(info["name"], raw):
            unknown.update(str(value).strip() for value in headers if value is not None
                           and 0 < len(str(value).strip()) <= 100)
    candidates = sorted(unknown - expected - existing.keys())[:100]
    if not candidates:
        return {"mapping": {}}
    try:
        result, _ = await run_in_threadpool(
            structured_review, ColumnMapping,
            "建议数据表表头映射。仅映射 supplied 中含义明确且与 required 一致的表头。无法确定时省略。",
            {"supplied": candidates, "required": sorted(expected), "tables": REQUIRED}, new_id(),
        )
    except ValueError as error:
        raise HTTPException(502, str(error)) from None
    suggested = {key: value for key, value in result.mapping.items() if key in candidates and value in expected}
    return {"mapping": suggested}


@app.post("/api/imports")
async def stage(files: list[UploadFile] = File(...), mapping: str = Form("{}"),
                chat_session_id: str | None = Form(None), continue_target: str = Form("", max_length=180)):
    continuation = parse_continuation(continue_target)
    inspected = await inspect_import(files, mapping)
    paths = []
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run, batch = queue_inspected_import(conn, inspected, json.loads(mapping), continuation, paths)
            result = cleaning_run(run)
            result["skipped"] = [info["name"] for info, _, _ in inspected if info["status"] == "skipped"]
            if chat_session_id is not None:
                existing = get(conn, "chat_session", chat_session_id) or {}
                context = existing.get("context", {})
                card, _ = cleaning_card(conn, run)
                answer = {"answer": "文件已接收，正在启动本批清洗。", "cards": [card],
                          "snapshot_id": context.get("snapshot_id", "")}
                request = ChatRequest(question=("导入 " + batch["label"])[:1200],
                                      **{key: context.get(key, "") for key in ("student", "group", "node")})
                saved = save_session(conn, chat_session_id, request, answer)
                result.update(session_id=saved["id"], card=card)
            return result
    except (ValueError, OSError, csv.Error) as exc:
        for path in paths:
            Path(path).unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc


def parse_continuation(target):
    if not target:
        return None
    kind, sep, entity = target.partition(":")
    if not sep or kind not in {"student", "group"} or not entity.strip():
        raise HTTPException(422, "请指定续跑建议的学生或班级")
    return {kind: entity.strip()}


def queue_inspected_import(conn, inspected, columns, continuation, paths):
    rows, sources, pending = [], [], []
    for info, raw, parsed in inspected:
        name = info["name"]
        if info["status"] == "skipped":
            continue
        if info["status"] == "error":
            raise ValueError(f"{name}：{info['message']}")
        file_id = new_id()
        rows.extend({**row, "file_id": file_id} for row in parsed)
        pending.append((file_id, name, raw))
    if not rows:
        raise ValueError("没有可导入的业务数据，请选择学生、课程、测验、题目和作答文件。")
    for file_id, name, raw in pending:
        path = save_file(raw, Path(name).suffix.lower())
        paths.append(path)
        sources.append({"id": file_id, "name": name, "hash": hashlib.sha256(raw).hexdigest(), "path": path})
    batch = {"id": new_id(), "rows": rows, "sources": sources, "mapping": columns,
             "label": "、".join(source["name"] for source in sources), "confirmed": False, "continuation": continuation}
    return queue_cleaning(conn, batch), batch


def cleaning_run(item):
    return {"batch_id": item["payload"]["batch_id"], "run_id": item["id"]}


@app.post("/api/imports/migrate")
def migrate_imports():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        baseline = legacy_batches(conn)
        if current_snapshot(conn) or not baseline:
            raise HTTPException(409, "没有待迁移的历史批次")
        for item in all_of(conn, "job"):
            if item["kind"] == "clean" and item["payload"].get("label") == "迁移历史批次":
                return cleaning_run(item)
        batch = {"id": new_id(), "rows": [], "sources": [], "label": "迁移历史批次", "confirmed": False}
        return cleaning_run(queue_cleaning(conn, batch))


@app.post("/api/imports/{key}/rebase")
def rebase_import(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require(conn, "import", key)
        if batch.get("confirmed") or batch.get("correction"):
            raise HTTPException(409, "已确认批次不可重建，教师修订请从当前异常重新发起")
        active = current_snapshot(conn)
        active_id = active["id"] if active else None
        for item in reversed(all_of(conn, "job")):
            payload = item["payload"]
            if (item["kind"] == "clean" and payload["batch_id"] == key
                    and payload["base_snapshot_id"] == active_id):
                return cleaning_run(item)
        return cleaning_run(queue_cleaning(conn, batch))


def confirm_batch(conn, key, run_id):
    item = require(conn, "import", key)
    run = require(conn, "job", run_id)
    if run["kind"] != "clean" or run["payload"]["batch_id"] != key:
        raise HTTPException(409, "任务与批次不匹配")
    if run.get("stage") == "published":
        return run["result"]
    if run["state"] != "done" or run.get("stage") != "confirm":
        raise HTTPException(409, "请先完成模型清洗复核")
    active = current_snapshot(conn)
    baseline = run["payload"]["base_snapshot_id"]
    if baseline != (active["id"] if active else None) or (
            active is None and run["payload"]["legacy_baseline"] != legacy_batches(conn)):
        raise HTTPException(409, "基础数据已变更，请按当前数据重新运行清洗")
    new_snapshot = require(conn, "snapshot", run["result"]["snapshot_id"])
    student_query.init(conn)
    student_query.index_snapshot(conn, new_snapshot)
    put(conn, "settings", "active_snapshot", {"id": new_snapshot["id"], "review_run_id": run_id})
    put(conn, "import", key, {**item, "confirmed": True, "snapshot_id": new_snapshot["id"]})
    result = dict(run["result"])
    if item.get("continuation"):
        try:
            result["recommendation_run_id"] = create_run(conn, Run(**item["continuation"], snapshot_id=new_snapshot["id"]))["id"]
        except HTTPException as error:
            result["continuation_error"] = error.detail
    put(conn, "job", run_id, {**run, "result": result, "stage": "published", "confirmed_at": time.time()})
    return result


@app.post("/api/imports/{key}/confirm")
def confirm(key: str, run_id: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return confirm_batch(conn, key, run_id)


def cleaning_report(conn):
    snapshot = current_snapshot(conn)
    if snapshot:
        run = require(conn, "job", snapshot["review_run_id"])
        return {**snapshot["report"], "snapshot_id": snapshot["id"],
                "last_agent_review": run["stages"]["clean_review"]}
    return {"raw_attempts": 0, "events": 0, "valid_scored": 0, "issues": [], "states": {},
            "legacy_pending": bool(legacy_batches(conn))}


@app.get("/api/imports/report")
def report():
    with db() as conn:
        return cleaning_report(conn)


class Correction(BaseModel):
    source: str
    sheet: str
    line: int
    file_id: str | None = None
    changes: dict[str, str]


@app.post("/api/imports/corrections")
def correct_record(edit: Correction):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return correct_record_in(conn, edit)


def correct_record_in(conn, edit):
    if not edit.changes or not edit.changes.keys() <= CORRECTION_FIELDS:
        raise HTTPException(400, "仅支持修正作答关联和评分字段")
    active = current_snapshot(conn)
    if not active:
        raise HTTPException(409, "请先确认数据快照")
    rows = [row for key in active["batch_ids"] for row in require(conn, "import", key)["rows"]]
    candidates = [r for r in rows if r["table"] == "作答记录"
                  and (r["source"], r["sheet"], r["line"]) == (edit.source, edit.sheet, edit.line)
                  and (edit.file_id is None or r.get("file_id") == edit.file_id)]
    if len(candidates) != 1 or not candidates[0]["values"].get("作答事件编号"):
        raise HTTPException(409, "无法唯一定位该作答事件")
    source = candidates[0]
    event = source["values"]["作答事件编号"]
    version = max((int(r["values"]["修订版本"]) for r in rows
                   if r["table"] == "作答记录" and r["values"]["作答事件编号"] == event
                   and r["values"]["修订版本"].isdigit()), default=0) + 1
    key = new_id()
    values = {**source["values"], **edit.changes, "修订版本": str(version), "记录编号": key}
    revision = {**source, "values": values, "source": "教师修订-" + key[:8]}
    original = {field: source[field] for field in ("source", "sheet", "line", "file_id") if field in source}
    batch = {"id": key, "confirmed": False, "rows": [revision], "sources": [],
             "correction": True, "revision_of": original, "label": revision["source"]}
    return cleaning_run(queue_cleaning(conn, batch))


@app.get("/api/imports/issues.csv")
def issues_csv(run_id: str = ""):
    with db() as conn:
        if run_id:
            run = require(conn, "job", run_id)
            snapshot_id = ((run.get("stages") or {}).get("python") or {}).get("snapshot_id")
            snapshot = require(conn, "snapshot", snapshot_id) if snapshot_id else None
            if not snapshot:
                raise HTTPException(404, "该运行尚无清洗结果")
            report = {**snapshot["report"], "snapshot_id": snapshot_id}
        else:
            report = cleaning_report(conn)
        issues = [{"snapshot_id": report["snapshot_id"], **issue} for issue in report["issues"]]
    return csv_response(issues, "cleaning-issues.csv")


def csv_response(rows, filename):
    output = io.StringIO()
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("'" + val if isinstance(val, str) and val.startswith(("=", "+", "-", "@", "\t", "\r")) else val)
                         for key, val in row.items()})
    return Response("\ufeff" + output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/curriculum")
def curriculum():
    with db() as conn:
        data = tables(conn)
        return {"indicators": list(data["课程要求"].values()), "nodes": list(data["测验节点"].values()),
                "questions": [{k: row.get(k) for k in ("材料编号", "主题编号", "指标编号", "题干或内容", "来源或资料文件")}
                              for row in data["题目与材料"].values()]}


def confirmed_snapshot_id(conn, requested):
    sid = requested or (get(conn, "settings", "active_snapshot") or {}).get("id", "")
    if sid and not student_query.is_indexed(conn, sid):
        raise HTTPException(404, "该快照不存在或尚未确认")
    return sid


@app.get("/api/profiles")
def profiles(student: str = "", group: str = "", node: str = "", snapshot_id: str = ""):
    with db() as conn:
        sid = confirmed_snapshot_id(conn, snapshot_id)
        data = student_query.metadata(conn, sid)
        students = list(data["模拟学生"].values())
        groups = sorted({r["模拟班级编号"] for r in students})
        scoped = student_query.profile(conn, sid, data, student, group, node, include_question_scope=True)
        grouped = [student_query.profile(conn, sid, data, group=g, node=node, include_question_scope=True) for g in groups]
    return {"profile": scoped, "students": students, "groups": grouped,
            "nodes": list(data["测验节点"].keys()), "snapshot_id": sid or None,
            "node_details": list(data["测验节点"].values()), "curriculum": list(data["课程要求"].values())}


@app.get("/api/profiles/evidence")
def evidence(indicator: str, student: str = "", group: str = "", node: str = "", snapshot_id: str = "", item: str = "",
             include_unscored: bool = False):
    with db() as conn:
        sid = confirmed_snapshot_id(conn, snapshot_id)
        data = student_query.metadata(conn, sid)
        curriculum = data["课程要求"]
        if indicator in curriculum:
            filters = {"skill": indicator}
        elif any(r["主题编号"] == indicator for r in curriculum.values()):
            filters = {"topics": [indicator]}
        else:
            raise HTTPException(404, "指标不存在")
        rows = student_query.answer_rows(conn, sid, student, group, node, item,
                                         scored_only=not include_unscored, **filters)
    if student and rows and sid:
        with db() as conn:
            settings_put(conn, "onboarding_viewed_answer", {
                "snapshot_id": sid, "student": student, "node": node,
                "indicator": indicator, "event": rows[0]["作答事件编号"],
                "group": data["模拟学生"].get(student, {}).get("模拟班级编号", ""),
            })
    return rows


@app.get("/api/profiles/distribution")
def distribution(indicator: str, group: str, node: str, snapshot_id: str):
    if not all((group, node, snapshot_id)):
        raise HTTPException(422, "请选择班级、测验和数据快照")
    rows = evidence(indicator, group=group, node=node, snapshot_id=snapshot_id)
    return {"snapshot_id": snapshot_id, "group": group, "node": node, "indicator": indicator,
            "students": [{"student": key, "score": round(100 * score / maximum, 1), "n": count}
                         for key, (score, maximum, count) in sorted(student_scores(rows).items())]}


@app.get("/api/materials")
def materials():
    with db() as conn:
        return [material_summary(doc) for doc in all_of(conn, "material")]


def material_summary(doc):
    return {**{k: doc[k] for k in ("id", "name", "state")},
            "source": doc.get("source", "upload" if doc.get("path") else "imported_question_bank"),
            "extraction": doc.get("extraction"), "error": doc.get("error")}


@app.post("/api/materials")
async def upload_material(file: UploadFile = File(...)):
    name = Path(file.filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "请上传 PDF、Word 或照片")
    path = save_file(await read_upload(file), suffix)
    with db() as conn:
        return queue_material(conn, name, path)


def queue_material(conn, name, path):
    key = new_id()
    put(conn, "material", key, {"id": key, "name": name, "state": "processing", "path": path,
                                 "source": "upload", "blocks": [], "previews": {}})
    return job(conn, "extract", {"material": key})


@app.post("/api/materials/seed-text")
def seed_text():
    with db() as conn:
        data = tables(conn)
        key = new_id()
        blocks = []
        for row in data["题目与材料"].values():
            text = "\n".join(f"{k}：{row[k]}" for k in ("题干或内容", "参考答案", "参考解析") if row.get(k))
            if text:
                blocks.append({"id": new_id(), "text": text, "locator": row["材料编号"], "material": key,
                               "title": "赛题题库与学习卡", "bbox": None})
        if not blocks:
            raise HTTPException(400, "请先导入题目与材料表")
        put(conn, "material", key, {"id": key, "name": "赛题题库与学习卡", "state": "review",
                                     "source": "imported_question_bank", "blocks": blocks, "previews": {}})
        return {"id": key}


@app.get("/api/materials/search")
def search_material(query: str):
    with db() as conn:
        return search(conn, query)


@app.get("/api/materials/{key}")
def material(key: str):
    with db() as conn:
        doc = require(conn, "material", key)
        return {**material_summary(doc),
                "locators": list(doc["previews"]),
                "blocks": [{k: v for k, v in block.items() if k != "vector"} for block in doc["blocks"]]}


@app.get("/api/materials/{key}/preview")
def preview(key: str, locator: str):
    with db() as conn:
        doc = require(conn, "material", key)
    if locator not in doc["previews"]:
        raise HTTPException(404, "该位置无页图")
    return FileResponse(doc["previews"][locator])


class Revision(BaseModel):
    changes: dict[str, str]


@app.put("/api/materials/{key}")
def edit_material(key: str, revision: Revision):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return edit_document(conn, key, revision)


def edit_document(conn, key, revision):
    doc = require(conn, "material", key)
    if doc["state"] not in {"review", "published"}:
        raise HTTPException(409, "该资料尚不可编辑")
    if doc["state"] == "published":
        key = new_id()
        doc = {**doc, "id": key, "replaces": doc["id"], "state": "review"}
        for block in doc["blocks"]:
            old = block["id"]
            block.update(id=new_id(), material=key, text=revision.changes.get(old, block["text"]))
            block.pop("vector", None)
    else:
        for block in doc["blocks"]:
            block["text"] = revision.changes.get(block["id"], block["text"])
    put(conn, "material", key, doc)
    return {"id": key}


@app.post("/api/materials/{key}/publish")
def publish(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return publish_document(conn, require(conn, "material", key))


def publish_document(conn, doc):
    if doc["state"] != "review" or not any(c["text"].strip() for c in doc["blocks"]):
        raise HTTPException(409, "资料尚不可发布")
    doc["state"] = "indexing"
    put(conn, "material", doc["id"], doc)
    return job(conn, "publish", {"material": doc["id"]})


class Run(BaseModel):
    student: str = ""
    group: str = ""
    node: str = ""
    variant: bool = False
    anchor_event: str = ""
    snapshot_id: str = ""


def create_run(conn, data):
    snapshot = current_snapshot(conn)
    if not snapshot:
        raise HTTPException(409, "请先确认数据快照")
    if data.snapshot_id and data.snapshot_id != snapshot["id"]:
        raise HTTPException(409, "数据快照已更新，请刷新画像后重新发起")
    source = table_data(snapshot)
    if not api_key(conn):
        raise HTTPException(400, "请先在模型接入中配置 API 密钥")
    if bool(data.student) == bool(data.group):
        raise HTTPException(400, "请选择单个学生或班级")
    if (data.student and data.student not in source["模拟学生"]) or (
        data.group and data.group not in {r["模拟班级编号"] for r in source["模拟学生"].values()}
    ) or (data.node and data.node not in source["测验节点"]):
        raise HTTPException(404, "学生、班级或测验不存在")
    if data.anchor_event:
        row = source["作答记录"].get(data.anchor_event)
        if (not row or not data.student or row["学生编号"] != data.student
                or row["评分状态"] != "已评分" or not row["指标"]
                or (data.node and row["节点编号"] != data.node)):
            raise HTTPException(400, "指定作答不属于当前学生和测验的有效证据")
    material_ids = [doc["id"] for doc in all_of(conn, "material") if doc["state"] == "published"]
    item = job(conn, "generate", {**data.model_dump(), "snapshot_id": snapshot["id"],
                                  "material_ids": material_ids})
    return {**item, "run_id": item["id"]}


@app.post("/api/agent/runs")
def start_run(data: Run):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return create_run(conn, data)


@app.get("/api/recommendations")
def recommendations():
    with db() as conn:
        return all_of(conn, "draft")


class Edit(BaseModel):
    revision: int
    exercises: list[str]
    message: str = Field(max_length=5000)
    observation: str | None = Field(default=None, max_length=5000)
    uncertainty: str | None = Field(default=None, max_length=5000)
    hints: dict[str, list[str]] = Field(default_factory=dict)
    teaching_texts: list[str] | None = None
    approved: bool


@app.put("/api/recommendations/{key}")
def edit(key: str, edit: Edit):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return edit_draft(conn, key, edit)


def edit_draft(conn, key, edit):
    draft = require(conn, "draft", key)
    if edit.revision != draft["revision"]:
        raise HTTPException(409, "建议已更新，请刷新")
    by_id = {q["question_id"]: q for q in draft["exercises"]}
    if set(edit.exercises) != by_id.keys() or len(edit.exercises) != len(by_id):
        raise HTTPException(400, "练习排序无效")
    if set(edit.hints) - by_id.keys() or any(
            not 1 <= len(hints) <= 4 or any(not hint.strip() or len(hint) > 1200 for hint in hints)
            for hints in edit.hints.values()):
        raise HTTPException(400, "每道练习需要 1 至 4 条有效提示")
    exercises = [{**by_id[key], "hints": edit.hints.get(key, by_id[key]["hints"])}
                 for key in edit.exercises]
    before = [q["question_id"] for q in draft["exercises"]]
    changed = edit.exercises != before or edit.message != draft.get("message", draft.get("follow_up", ""))
    changed = changed or any(hints != by_id[key]["hints"] for key, hints in edit.hints.items())
    copy = {field: val for field in ("observation", "uncertainty") if (val := getattr(edit, field)) is not None}
    if edit.teaching_texts is not None:
        points = draft.get("teaching_points", [])
        if len(edit.teaching_texts) != len(points) or any(
                not line.strip() or len(line) > 800 for line in edit.teaching_texts):
            raise HTTPException(400, "请保留各条教学说明及其引用")
        copy["teaching_points"] = [{**point, "text": line}
                                   for point, line in zip(points, edit.teaching_texts)]
    changed = changed or any(value != draft.get(field) for field, value in copy.items())
    if changed:
        if not draft.get("snapshot_id"):
            raise HTTPException(409, "历史建议未登记证据版本，请重新生成")
        draft.update(exercises=exercises, message=edit.message,
                     revision=draft["revision"] + 1, approved=False, updated=time.time(),
                     review_state="pending", **copy)
        draft.pop("final_review", None)
        item = job(conn, "recommendation_review", {"draft": key, "revision": draft["revision"],
                                                    "snapshot_id": draft.get("snapshot_id"),
                                                    "student": draft["student"], "group": draft["group"]})
        draft["review_run_id"] = item["id"]
    elif edit.approved:
        review_job = get(conn, "job", draft.get("review_run_id", ""))
        if (not draft.get("snapshot_id") or draft.get("review_state") != "pass"
                or not review_job or review_job["state"] != "done"):
            raise HTTPException(409, "当前建议版本需先通过模型终审")
        draft.update(approved=True, updated=time.time())
        put(conn, "job", review_job["id"], {**review_job, "stage": "published",
                                            "confirmed_at": time.time()})
    put(conn, "draft", key, draft)
    put(conn, "draft_revision", f'{key}:{draft["revision"]}', draft)
    return draft


@app.get("/api/recommendations/{key}/preview")
def student_preview(key: str):
    with db() as conn:
        draft = require(conn, "draft", key)
        binding = get(conn, "binding", draft["student"])
    return {"text": preview_text(draft), "group": binding["label"] if binding else None}


class Binding(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    webhook: str
    secret: str = ""


@app.get("/api/deliveries/binding/{student}")
def delivery_binding(student: str):
    with db() as conn:
        item = get(conn, "binding", student)
        return {"label": item["label"] if item else ""}


@app.put("/api/deliveries/binding/{student}")
def bind(student: str, data: Binding):
    import keyring

    url = urlparse(data.webhook)
    token = url.path.removeprefix("/open-apis/bot/v2/hook/")
    if url.scheme != "https" or url.netloc != "open.feishu.cn" or "?" in data.webhook or url.fragment or (
        not url.path.startswith("/open-apis/bot/v2/hook/") or not token or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in token)):
        raise HTTPException(400, "请输入飞书自定义机器人 HTTPS webhook")
    fingerprint = hashlib.sha256(data.webhook.encode()).hexdigest()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if student not in tables(conn)["模拟学生"]:
            raise HTTPException(404, "学生不存在")
        if any(b["fingerprint"] == fingerprint and b["student"] != student for b in all_of(conn, "binding")):
            raise HTTPException(409, "该群 webhook 已绑定其他学生")
        credential = f"{student}:{fingerprint}"
        keyring.set_password("aitolearn-feishu", credential, json.dumps(data.model_dump()))
        put(conn, "binding", student, {"student": student, "label": data.label, "fingerprint": fingerprint,
                                        "credential": credential})
    return {"student": student, "label": data.label}


def send_to(binding, text):
    import keyring

    raw = keyring.get_password("aitolearn-feishu", binding["credential"])
    if not raw:
        return "failed", "凭据缺失"
    data = json.loads(raw)
    payload = {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "智慧学伴 · 学习建议"}},
                 "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": text}}]}}
    if data["secret"]:
        stamp = str(int(time.time()))
        sign = hmac.digest(f'{stamp}\n{data["secret"]}'.encode(), b"", "sha256")
        payload.update(timestamp=stamp, sign=base64.b64encode(sign).decode())
    try:
        res = httpx.post(data["webhook"], json=payload, timeout=10)
        if res.status_code >= 500:
            return "unknown", "飞书服务错误，请核对收件群"
        if res.is_success:
            result = res.json()
            if not isinstance(result, dict):
                return "unknown", "飞书响应格式异常，请核对收件群"
            if result.get("code", result.get("StatusCode")) == 0:
                return "sent", ""
        return "failed", "飞书拒绝请求，请检查机器人配置"
    except (httpx.HTTPError, ValueError):
        return "unknown", "未收到可确认的响应，请核对收件群"


@app.post("/api/deliveries/test/{student}")
def test_binding(student: str):
    with db() as conn:
        binding = require(conn, "binding", student)
    state, message = send_to(binding, f"专属辅导群绑定测试 · {student}")
    return {"state": state, "message": message}


@app.post("/api/deliveries/send/{key}")
def send(key: str, revision: int):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = require(conn, "draft", key)
        if not draft["approved"] or draft["revision"] != revision:
            raise HTTPException(409, "请先确认当前版本")
        binding = require(conn, "binding", draft["student"])
        text = preview_text(draft)
        if not text:
            raise HTTPException(400, "卡片内容为空")
        uid = f"{key}:{revision}"
        existing = get(conn, "delivery", uid)
        if existing and existing["state"] != "failed":
            return existing
        item = {"id": uid, "draft": key, "student": draft["student"], "group": binding["label"],
                "state": "sending", "time": time.time()}
        put(conn, "delivery", uid, item)
    item["state"], item["message"] = send_to(binding, text)
    with db() as conn:
        put(conn, "delivery", uid, item)
    return item


@app.get("/api/deliveries")
def deliveries():
    with db() as conn:
        return all_of(conn, "delivery")


@app.get("/api/export")
def export_data(format: str = "json", formal: bool = False, expected_snapshot: str = ""):
    with db() as conn:
        snapshot = current_snapshot(conn)
        snapshot_id = snapshot["id"] if snapshot else None
        if expected_snapshot and expected_snapshot != snapshot_id:
            raise HTTPException(409, "数据快照已变化，请重新准备导出")
        data = table_data(snapshot)
        people = data["模拟学生"]
        groups = sorted({r["模拟班级编号"] for r in people.values()})
        drafts = all_of(conn, "draft")
        if formal:
            if format != "json" or not snapshot:
                raise HTTPException(409, "正式成果需要已确认的数据快照，使用 JSON 导出")
            drafts = [item for item in drafts if item.get("snapshot_id") == snapshot_id
                      and item["approved"] and item.get("final_review", {}).get("decision") == "pass"
                      and item["final_review"]["input_version"] == item["revision"]]
    learners = [profile(data, student=key) for key in people]
    classes = [profile(data, group=key) for key in groups]
    if format == "csv":
        return csv_response([{"snapshot_id": snapshot_id, "student": p["student"], "class": p["group"], "indicator": a["id"],
                              "score": a["score"], "earned": a["earned"], "possible": a["possible"],
                              "question_count": a["n"], "coverage": a["coverage"], "status": a["status"]}
                             for p in learners + classes for a in p["axes"]], "profiles.csv")
    payload = {"snapshot_id": snapshot_id, "curriculum": list(data["课程要求"].values()), "students": learners, "classes": classes,
               "recommendations": drafts, "fields": {"score": "有效作答得分率（百分比）",
                   "coverage": "具备有效作答的学生数", "attempt_ids": "支持该指标计算的作答事件编号"}}
    if formal:
        payload["manifest"] = {
            "snapshot_id": snapshot_id, "rules_version": snapshot["rules_version"],
            "input_batches": snapshot["manifest"], "clean_review_run_id": snapshot["review_run_id"],
            "recommendations": [{"id": item["id"], "revision": item["revision"],
                                 "review_run_id": item["review_run_id"], "material_ids": item["material_ids"]}
                                for item in drafts],
        }
    return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="aitolearn.json"'})


if Path("frontend/dist/index.html").exists():
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
