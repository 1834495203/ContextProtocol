# 后端架构

工具接入使用 `HostTools`（本地注册）与 `adapters/mcp.py`（官方 MCP SDK，无 LangChain 依赖）。宿主租约、提供方目录及工具描述配置在 ContextState 的独立字段内持久化，使用同一事务和幂等命令机制；不需要重建已有数据库表。`domain/tools.py` 负责按 binding_id 解析目录、校验契约指纹并合并描述覆盖，`CompilationService.prepare` 返回当前绑定专属的输入。详见 [工具接入](tool-integration.md)。

业务对象、更新载荷、数据库行、编译结果、HTTP 请求与响应都由 Pydantic 类定义。数据在各层之间传递模型实例，通过属性访问；服务层不接收 SQL 行字典，不拼接业务返回字典。

```text
HTTP 请求
  ↓ Pydantic 校验、按 type 选择具体命令
controllers/              路由、请求/响应、依赖注入
  ↓
services/                 模板、实例、事务、幂等、用例编排
  ├── domain/updates/     每种命令一个处理器
  ├── domain/compiler.py 来源 → 压缩 → 标准消息
  └── storage/base.py    强类型 mapper 事务契约
         ↓
mappers/                  SQL 行/JSON ↔ Pydantic 模型
         ↓
storage/                  数据库连接、方言、事务生命周期
```

## 从哪里读代码

| 位置 | 职责 |
| --- | --- |
| app.py | 应用装配：配置、lifespan、挂载 controller |
| models/context.py | 组件、字段、内容、消息、工具、ContextState |
| models/commands.py | 18 种更新命令，各自带具体 Pydantic payload；ContextUpdate 为 type 判别联合 |
| models/persistence.py | 数据库行模型、实例/模板信息、返回 DTO |
| models/compilation.py | SourceItem、ModelMessage、CompiledContext 与 ContextEnvelope |
| models/requests.py | CRUD 与实例连接请求 DTO |
| controllers/templates.py | 模板 HTTP 接口 |
| controllers/instances.py | 实例、更新、编译、简名接入 HTTP 接口 |
| controllers/system.py | 健康检查、协议 Schema 和静态入口 |
| services/templates.py | 模板创建/复制、配置提取、默认字段、更新 |
| services/instances.py | 实例创建、归属、简名唯一性和连接复用 |
| services/updates.py | 更新事务、幂等、修订号检查，调用命令处理器 |
| services/compilation.py | 编译及导出用例 |
| services/bootstrap.py | 初始模板和已有实例简名补齐 |
| services/application.py | 显式装配各 service，提供兼容 Python 调用入口 |
| domain/updates/ | 组件、内容、字段、外部观察的独立处理器与注册器 |
| domain/sources/registry.py | 类型化来源接口及内置提供方 |
| domain/compression/strategies.py | keep/recent/truncate/exclude 独立策略及注册器 |
| domain/messages.py | 工具调用配对与标准消息组装 |
| domain/compiler.py | 组合来源、策略、格式转换，不访问数据库 |
| mappers/sql_mapper.py | SQL 查询及行模型转换 |
| mappers/schema.py / types.py | 物理表结构及跨数据库字段类型 |
| storage/base.py | Pydantic 输入/输出的 mapper 事务端口 |
| storage/postgres.py / sqlite.py / mysql.py | 所选数据库连接和锁语义 |

`store.py` 仅保留 Store 的兼容导入。`protocol.py` 仅保留旧 JSON 函数入口，立即转换为类型模型后调用相同 domain；线上 service 不通过这个兼容入口执行业务。旧数据归档读取在 storage/legacy_* 和 migrate_legacy.py，不参与当前上下文的日常读写。

## 命令与校验

例：StateSetCommand.payload 是 SetField，ToolCallCommand.payload 是 CallTool，GroupUpsertCommand.payload 是 Group。HTTP 用 type 字段选择联合中的具体类；缺字段、类型错误在请求边界返回 422。字段值按声明类型校验，工具调用和结果在事务内检查关联。

UpdateRegistry 按命令类派发处理器。权限和状态约束由对应处理器维护，模板只允许 template_safe 处理器。新增处理器不需要在一个大 apply_update 函数里追加 elif。

## 压缩和检索扩展

压缩实现 CompressionStrategy：提供 name 与 compress(items, group)，返回 CompressionResult。将策略注册到 CompressionRegistry，Policy.strategy 使用注册名称。自动摘要实现可从这个接口接入；当前没有实现模型摘要调用。

检索实现 SourceProvider：提供 name 与 extract(state, group)，返回 list[SourceItem]。在 SourceRegistry 注册后，组件 sources 可以绑定这个名称；向量库、检索参数和外部依赖由具体提供方持有。当前没有实现向量数据库或自动 RAG 调用。

```python
sources = default_sources()
sources.register(your_retrieval_provider)
strategies = default_compression()
strategies.register(your_summary_strategy)
compiler = ContextCompiler(sources=sources, compression=strategies)
application = ContextApplication(repository, compiler=compiler)
app = create_app(application)
```

检索来源与新压缩策略已通过独立假实现验证：注册后可编译，未修改主编译器或 controller。

## JSON 边界与开放数据

SQL mapper 将驱动行转换成 Pydantic 行模型，写入时才序列化为 JSON。FastAPI 序列化响应模型。LangChain 适配器也使用类型化命令和编译结果，仅在第三方 SDK 的接口处转换数据。

用户自定义字段是 dict[str, StateField]，条目是 dict[str, ContentItem]：这些是开放名称的类型化集合。任意用户 JSON 值使用 JsonValue，外部工具参数/JSON Schema 使用 JsonObject；明确业务结构均有专用类。

## 兼容与验证

HTTP 路径、模板归属、实例简名、工具记录与持久化表继续兼容。已有实例的状态在 mapper 读取时校验；不会清空数据库或重建运行实例。原始数据仍保存在现有 JSON 列中。旧幂等摘要格式有专门回归测试。

Python service 返回值现在是 Pydantic 模型，直接调用时使用 instance.id、view.state、result.update.seq。HTTP SDK 新增 submit(Command)、view()、compile() 类型化方法；既有 emit/state/build JSON 方法作为边界兼容保留。

测试覆盖 PostgreSQL、SQLite、LangChain 模拟模型、controller 与数据库隔离、业务 service 返回 DTO、命令联合 Schema、来源/策略扩展，以及旧迁移、事务回滚、并发与幂等。MySQL 仍只验证方言建表语句。

状态定义使用 StateDefinition，与 StateField 当前值分开持久化。services/template_sync.py 负责模板差异同步；TemplateService 先锁模板，再按实例 ID 排序加锁，在同一事务中写入模板、实例配置与关联修订号。编译器仍只读取 fields，不读取 field_definitions。
