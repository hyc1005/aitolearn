"""Verify the configured model with a synthetic tool call."""
import os
from uuid import uuid4

from smolagents import OpenAIServerModel, ToolCallingAgent, tool

from backend.agent import api_key

calls = []


@tool
def add_example(x: int) -> int:
    """Add three to a synthetic number.
    Args:
        x: The input number.
    """
    calls.append(x)
    return x + 3


if __name__ == "__main__":
    key = api_key()
    if not key:
        raise SystemExit("未配置 OpenCode Go 密钥")
    model = OpenAIServerModel(
        model_id=os.getenv("OPENCODE_GO_MODEL", "glm-5.3-flash"),
        api_base=os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/inference/go/openai/v1"),
        api_key=key, client_kwargs={"default_headers": {"x-opencode-session": uuid4().hex,
                                                        "User-Agent": "aitolearn/1.0"},
                                    "timeout": 30, "max_retries": 0})
    result = ToolCallingAgent(tools=[add_example], model=model, max_steps=3, verbosity_level=0).run(
        "Call add_example with 11 and report the returned integer.")
    if calls != [11] or "14" not in str(result):
        raise SystemExit("工具调用未通过")
    print("GLM 工具往返已验证")
