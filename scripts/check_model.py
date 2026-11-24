"""Verify the configured model with a synthetic tool call."""
from uuid import uuid4

from smolagents import ToolCallingAgent, tool

from backend.agent import api_key, configured_model

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
    if not api_key():
        raise SystemExit("请先在模型接入中配置 API 密钥")
    model = configured_model(uuid4().hex)
    result = ToolCallingAgent(tools=[add_example], model=model, max_steps=3, verbosity_level=0).run(
        "Call add_example with 11 and report the returned integer.")
    if calls != [11] or "14" not in str(result):
        raise SystemExit("工具调用未通过")
    print("模型工具往返已验证")
