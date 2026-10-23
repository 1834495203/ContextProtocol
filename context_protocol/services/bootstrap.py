"""Non-destructive defaults and instance-name backfill."""
from ..models.context import Model, ContentItem, new_state
from ..models.persistence import TemplateRow, HandleRow
from ..storage.base import ContextRepository
from .access import now, require_template

class SeedTemplate(Model):
    id: str
    name: str
    description: str
    instructions: str

SEEDS = (
    SeedTemplate(id="03e2017e-53db-4668-a832-626de5acf001", name="通用助手",
        description="通用提示词、工具来源、字段和对话的基础布局。",
        instructions="你是一名严谨的中文助手。根据任务使用工具，准确区分事实与推测。"),
    SeedTemplate(id="03e2017e-53db-4668-a832-626de5acf002", name="研究助手",
        description="适合资料检索与分析，保留来源并按完整轮次组织对话。",
        instructions="你是一名研究助手。先明确问题，再整理证据；引用资料时保留来源，标明不确定性。"),
)

class BootstrapService:
    def __init__(self, repository: ContextRepository):
        self.repository = repository
    def initialize(self) -> None:
        self.repository.initialize()
        with self.repository.transaction(write=True) as tx:
            if not tx.templates():
                for seed in SEEDS:
                    state = new_state()
                    item = ContentItem(id="template_instructions", group_id="instructions", content=seed.instructions)
                    state.blocks[item.id] = item
                    tx.insert_template(TemplateRow(id=seed.id, name=seed.name, description=seed.description,
                        revision=0, state=state, created_at=now(), updated_at=now()))
        with self.repository.transaction(write=True) as tx:
            for row in tx.sessions():
                if row.template_id and not row.slug:
                    require_template(tx, row.template_id, lock=True)
                    if tx.handle(row.id) is None:
                        tx.insert_handle(HandleRow(session_id=row.id, template_id=row.template_id,
                            slug="ctx-"+row.id.replace("-", ""), origin="created"))
