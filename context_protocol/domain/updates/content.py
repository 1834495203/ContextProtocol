from ...models.commands import BlockUpsertCommand, ItemUpsertCommand, BlockDeleteCommand, ItemDeleteCommand
from ...models.context import ContentItem
from .base import UpdateContext, UpdateHandler


class ContentHandler:
    template_safe = True

    def collection(self, context: UpdateContext) -> dict[str, ContentItem]:
        raise NotImplementedError

    def upsert(self, context: UpdateContext, command: BlockUpsertCommand | ItemUpsertCommand) -> None:
        payload = command.payload
        collection = self.collection(context)
        old = collection.get(payload.id)
        if old:
            context.writable(old.group_id, old)
        context.writable(payload.group_id)
        origin = payload.origin or context.component(payload.group_id).origin
        if context.actor == "editor" and origin == "external":
            raise ValueError("外部数据必须由提供方同步")
        collection[payload.id] = ContentItem(id=payload.id, group_id=payload.group_id, content=payload.content,
            origin=origin, seq=context.seq, content_schema=payload.content_schema, source_uri=payload.source_uri)

    def delete(self, context: UpdateContext, item_id: str) -> None:
        collection = self.collection(context)
        old = collection.get(item_id)
        if old:
            context.writable(old.group_id, old)
            del collection[item_id]


class BlocksHandler(ContentHandler):
    def collection(self, context: UpdateContext) -> dict[str, ContentItem]:
        return context.state.blocks


class ItemsHandler(ContentHandler):
    def collection(self, context: UpdateContext) -> dict[str, ContentItem]:
        return context.state.items


class UpsertBlockHandler(BlocksHandler, UpdateHandler[BlockUpsertCommand]):
    command_type = BlockUpsertCommand

    def handle(self, context: UpdateContext, command: BlockUpsertCommand) -> None:
        self.upsert(context, command)


class UpsertItemHandler(ItemsHandler, UpdateHandler[ItemUpsertCommand]):
    command_type = ItemUpsertCommand

    def handle(self, context: UpdateContext, command: ItemUpsertCommand) -> None:
        self.upsert(context, command)


class DeleteBlockHandler(BlocksHandler, UpdateHandler[BlockDeleteCommand]):
    command_type = BlockDeleteCommand

    def handle(self, context: UpdateContext, command: BlockDeleteCommand) -> None:
        self.delete(context, command.payload.id)


class DeleteItemHandler(ItemsHandler, UpdateHandler[ItemDeleteCommand]):
    command_type = ItemDeleteCommand

    def handle(self, context: UpdateContext, command: ItemDeleteCommand) -> None:
        self.delete(context, command.payload.id)
