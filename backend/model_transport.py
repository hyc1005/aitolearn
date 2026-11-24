import json
import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass

import httpx


logger = logging.getLogger("uvicorn.error")
model_job = ContextVar("model_job", default=None)


MESSAGES = {
    "connection": "模型平台连接中断，可以重试当前阶段",
    "timeout": "等待模型平台响应超时，可以重试当前阶段",
    "rate_limit": "模型平台请求过多，请稍后重试",
    "upstream": "模型平台暂时不可用，可以重试当前阶段",
    "credentials": "模型密钥无效或缺少访问权限，请检查模型设置",
    "configuration": "模型平台拒绝了请求，请检查模型设置",
    "quota": "模型平台余额或额度不足",
    "context": "本次模型输入超过可用上下文，请缩小查询范围",
    "response": "模型平台没有返回可用内容",
}


class ModelRequestError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


def request_budget():
    # Local serialized UTF-8 budget, independent of the provider's tokenizer.
    return int(os.getenv("APP_MODEL_REQUEST_BYTES", "32768"))


def response_code(response):
    status = response.status_code
    if status < 400:
        return ""
    if status in (400, 413, 422):
        text = response.text[:2000].lower()
        if status == 413 or any(marker in text for marker in (
            "context_length", "context length", "context window", "too many tokens", "maximum context",
        )):
            return "context"
    return {401: "credentials", 403: "credentials", 402: "quota", 429: "rate_limit",
            408: "timeout"}.get(status, "upstream" if status >= 500 else "configuration")


def retry_delay(response):
    value = response.headers.get("retry-after") if response is not None else None
    if not value:
        return 0.75
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.75


class ModelTransport(httpx.BaseTransport):
    def __init__(self, provider, model, session, transport=None, stage="chat", base_url=""):
        self.provider = provider
        self.model = model
        self.session = session
        self.stage = stage
        origin = urlparse(base_url)
        proxy = None if not origin.hostname or proxy_bypass(origin.hostname) else getproxies().get(origin.scheme)
        self.transport = transport if transport is not None else httpx.HTTPTransport(proxy=proxy)

    def close(self):
        self.transport.close()

    def handle_request(self, request):
        job = model_job.get()
        size = len(request.content)
        for retry in range(2):
            started = time.monotonic()
            response = None
            if size > request_budget():
                code = "context"
            else:
                try:
                    response = self.transport.handle_request(request)
                    response.read()
                    code = response_code(response)
                except httpx.RequestError as error:
                    if response is not None:
                        response.close()
                        response = None
                    code = "timeout" if isinstance(error, httpx.TimeoutException) else "connection"
            logger.info("model_request %s", json.dumps({
                "run_id": job["id"] if job else self.session,
                "stage": job.get("stage") if job else self.stage,
                "provider": self.provider, "model": self.model, "bytes": size,
                "elapsed_ms": round((time.monotonic() - started) * 1000), "retry": retry,
                "status": response.status_code if response is not None else None, "result": code or "ok",
                "request_id": response.headers.get("x-request-id", "")[:96] if response is not None else "",
            }, ensure_ascii=False))
            delay = retry_delay(response)
            if not retry and code in {"connection", "timeout", "rate_limit", "upstream"} and delay <= 5:
                if response is not None:
                    response.close()
                self.set_retry(job, True)
                try:
                    time.sleep(delay)
                except BaseException:
                    self.set_retry(job, False)
                    raise
                continue
            if retry:
                self.set_retry(job, False)
            if response is None or code == "context":
                if response is not None:
                    response.close()
                raise ModelRequestError(code)
            return response

    @staticmethod
    def set_retry(job, value):
        if job is not None:
            from .store import db, put
            job["model_retrying"] = value
            with db() as conn:
                put(conn, "job", job["id"], job)


def model_failure(error):
    while error is not None:
        if isinstance(error, ModelRequestError):
            return error
        response = getattr(error, "response", None)
        if isinstance(response, httpx.Response) and response.status_code >= 400:
            return ModelRequestError(response_code(response))
        error = error.__cause__
    return None
