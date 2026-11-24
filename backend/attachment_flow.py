"""Plan only over server-validated groups. The model sees counts, never paths or file contents."""
import json

from .models import complete_with_tools

TOOLS = {
    "queue_student_import": "为本次已验证的学情表组启动清洗，不启用快照",
    "queue_material_extract": "为本次已验证的资料组启动解析，不发布教材",
}


def plan_attachment_tools(provider, batch_id, counts):
    allowed = {name: TOOLS[name] for name, count in counts.items() if count}
    chosen = []
    if provider and allowed:
        definitions = [{"type": "function", "function": {"name": name, "description": description,
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
                       for name, description in allowed.items()]
        try:
            reply = complete_with_tools(provider, [
                {"role": "system", "content": "老师已授权处理本批附件。为每个非空组调用其对应工具一次。只准备待校对或待确认结果，不发布、不修改现有数据。"},
                {"role": "user", "content": json.dumps({name: counts[name] for name in allowed})},
            ], batch_id, tools=definitions, max_tokens=1000)
            for call in (reply.get("tool_calls") or [])[:4]:
                try:
                    function = call.get("function") or {}
                    name = function.get("name")
                    if name in allowed and name not in chosen and json.loads(function.get("arguments") or "{}") == {}:
                        chosen.append(name)
                except (ValueError, TypeError, AttributeError):
                    continue
        except ValueError:
            pass  # Upload is already authorized; backend uses the same typed tools as fallback.
    return [(name, "agent" if name in chosen else "validated_fallback")
            for name in chosen + [name for name in allowed if name not in chosen]]
