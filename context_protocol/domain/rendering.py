"""Serialization to model-facing text is an explicit boundary."""
import json
from pydantic import BaseModel, JsonValue
from ..models.compilation import SourceItem, StructuredContent

def as_text(value: BaseModel | JsonValue) -> str:
    if isinstance(value, str):
        return value
    serialized = value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value
    return json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True)

def render_items(items: list[SourceItem]) -> str:
    parts: list[str] = []
    for item in items:
        if item.content_schema:
            value = item.content.model_dump(mode="json") if isinstance(item.content, BaseModel) else item.content
            parts.append(as_text(StructuredContent(content=value, content_schema=item.content_schema)))
            continue
        parts.append(as_text(item.content))
    return "\n\n".join(parts)
