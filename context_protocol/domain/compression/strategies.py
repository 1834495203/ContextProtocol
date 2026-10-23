"""Pluggable compression strategies; no persistence or framework dependency."""
from typing import Protocol
from pydantic import Field
from ...models.context import Model, Group
from ...models.compilation import SourceItem, MessageSourceItem
from ..rendering import render_items

class CompressionResult(Model):
    items: list[SourceItem]
    text: str
    included_count: int
    included_ids: list[str]
    source_seqs: list[int]
    truncated: bool = False
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def selected(cls, items: list[SourceItem], reason: str = ""):
        return cls(items=items, text=render_items(items), included_count=len(items),
                   included_ids=[item.id for item in items], source_seqs=[item.seq for item in items if item.seq], reason=reason)

class CompressionStrategy(Protocol):
    name: str
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult: ...

class KeepStrategy:
    name = "keep"
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult:
        return CompressionResult.selected(items)

class ExcludeStrategy:
    name = "exclude"
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult:
        return CompressionResult.selected([], "组件已排除")

class RecentStrategy:
    name = "recent"
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult:
        unit = group.policy.unit
        if unit == "auto":
            unit = "turn" if group.role == "conversation" else "item"
        selected = items[-group.policy.limit:]
        if unit != "item":
            buckets: dict[str, list[SourceItem]] = {}
            last_user: str | None = None
            for item in items:
                if isinstance(item, MessageSourceItem) and item.content.role == "user":
                    last_user = item.turn_id or f"user:{item.seq}"
                loop_key = item.loop_id if unit == "loop" else None
                key = loop_key or item.turn_id or last_user or item.id
                buckets.setdefault(key, []).append(item)
            selected = [item for key in list(buckets)[-group.policy.limit:] for item in buckets[key]]
        reason = ""
        if len(selected) != len(items):
            suffix = "项" if unit == "item" else "轮" if unit == "turn" else "个循环"
            reason = f"保留最近 {group.policy.limit} {suffix}"
        return CompressionResult.selected(selected, reason)

class TruncateStrategy:
    name = "truncate"
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult:
        result = CompressionResult.selected(items)
        if len(result.text) <= group.policy.max_chars:
            return result
        marker = "\n[…组件视图已截断，原文保留在当前上下文中]"
        result.text = result.text[:max(0, group.policy.max_chars-len(marker))]+marker
        result.truncated = True
        result.items = []
        result.reason = f"截断至 {group.policy.max_chars} 字符"
        result.warnings.append(f"{group.label} 已截断；结构化数据按引用文本提供。")
        return result

class CompressionRegistry:
    def __init__(self):
        self.strategies: dict[str, CompressionStrategy] = {}
    def register(self, strategy: CompressionStrategy) -> None:
        if strategy.name in self.strategies:
            raise ValueError("压缩策略已注册")
        self.strategies[strategy.name] = strategy
    def compress(self, items: list[SourceItem], group: Group) -> CompressionResult:
        name = group.policy.strategy if group.enabled else "exclude"
        strategy = self.strategies.get(name)
        if strategy is None:
            raise ValueError(f"未注册压缩策略：{name}")
        return strategy.compress(items, group)

def default_compression() -> CompressionRegistry:
    registry = CompressionRegistry()
    for strategy in (KeepStrategy(), RecentStrategy(), TruncateStrategy(), ExcludeStrategy()):
        registry.register(strategy)
    return registry
