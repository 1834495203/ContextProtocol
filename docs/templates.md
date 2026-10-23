# 上下文模板与多 Agent 使用

模板库是主要入口。一个模板定义有序组件、内部默认提示词、字段默认值和每组的压缩策略；运行实例是从模板复制出来的当前上下文，存放某次使用中的字段、对话和工具结果。

## 工作台操作

1. 打开模板库，选择“编辑模板”，配置组件、内容、字段默认值及策略。模板只保存配置，不能写入用户发言、模型响应或工具结果。
2. 打开模板，在模板内点击“新建实例”，填写实例简名、名称和可选 Agent 标记。实例列表只展示本模板的实例，左侧只列模板。
3. 点击实例的“接入代码”，将模板 ID 和实例简名配置到宿主。宿主通过 ContextClient.connect 获取同一实例，再同步自己的工具目录、Skill、消息与调用结果。
4. 其他 Agent 可以选择同一模板下的另一个简名，或选择另一模板。简名仅在所属模板内唯一；不同模板可以各有 main 实例。

默认提供通用助手与研究助手模板。可创建、复制、修改名称与用途、导出模板；不强制绑定某个 Agent 框架或工具提供方。

修改状态定义、提示词和组件时，可选择“仅当前实例”或“基于模板”。模板级增删改在同一事务内同步所有已有实例，并更新其 template_revision 和实例 revision。实例级修改不会更改模板或其他实例；新实例采用模板最新配置。模板同步是配置修改，不提供模板历史、分支或回退功能。

## Python SDK

```python
import httpx
from context_protocol.client import ContextClient

with httpx.Client(base_url="http://127.0.0.1:8010/api") as http:
    available = ContextClient.templates(http)
    selected = next(t for t in available if t["name"] == "研究助手")
    researcher = ContextClient.connect(http, selected["id"], "researcher",
        name="研究任务 A", agent_id="researcher-A")
    reviewer = ContextClient.connect(http, selected["id"], "reviewer",
        name="复核任务 B", agent_id="reviewer-B")
    researcher.emit("state.set", {"key":"phase", "value":"检索中"})
    # reviewer 的状态和 selected 模板都不会因此改变
```

`connect` 默认 create=True：实例不存在时原子创建，存在时复用，保留已有字段、提示词与工具记录。create=False 仅允许连接既有实例，拼错简名返回 404，不会新建。显式提供的 Agent 标记与实例现有非空标记不同会返回 409，避免误接。Agent 标记仍是可选说明字段，不是权限或执行入口。Agent、模型和工具仍由宿主运行。共享配置通过模板完成；如确实需要共享运行数据，可以显式让多个宿主连接同一个实例，但完整 Agent 轮次的并发互斥仍应由宿主协调。

## LangChain 示例

```powershell
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --list-templates
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --template TEMPLATE_ID --instance researcher --agent-id researcher --mock --smoke-test
```

`--list-templates` 不创建 Agent 或调用模型；`--template --instance 简名` 首次创建、之后复用；加 `--existing-only` 只连接既有实例。仅 --template 不传简名仍可创建独立实例并生成简名；--session 按旧 UUID 方式延续实例，与模板参数互斥。去掉 `--mock` 才使用配置的模型提供商。

## 从已有实例另存模板

保留组件顺序、来源绑定、压缩策略、内部文本/JSON 与 Schema、字段类型和说明。字段当前值重置为空默认值：string → 空字符串，number → 0，boolean → false，object → {}，array → []，json → null。之后可在模板编辑器设置明确的默认值。

不复制用户/assistant 消息、工具调用及结果、运行记忆、Loop/思考记录、模型请求记录、外部工具或 Skill 内容。外部来源的组件结构保留，真正内容由新实例的宿主同步；内置状态工具声明继续提供。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | /api/templates | 列出 / 新建模板 |
| GET | /api/templates/{id} | 模板配置与修订号 |
| POST | /api/templates/{id}/updates | 修改组件、内部内容和字段默认值 |
| POST | /api/templates/{id}/metadata | 修改名称和用途，需 expected_revision |
| GET | /api/templates/{id}/preview | 编译模板默认输入 |
| GET | /api/templates/{id}/export | 导出模板 |
| POST | /api/sessions | 传 template_id、name 和可选 agent_id 创建实例 |

新建模板可传 source_template_id 复制模板，或传 source_context_id 从实例提取配置，两者互斥。模板编辑支持 expected_revision 并发校验；更新 ID 不提供事件溯源或模板历史。模板编辑端没有自动网络重试。已有实例 API 保持兼容。


## 实例简名与归属

简名以小写字母开头，允许小写字母、数字、下划线和连字符，最长 64 字符。同模板内唯一，例如 main、researcher、task_1。实例信息页可修改简名、显示名称和 Agent 标记；更改简名后接入方需更新配置，已取得内部 ID 的运行客户端仍连接原实例。

旧的模板绑定实例会获得稳定自动简名，可在实例信息中改成人类可读名称。没有模板归属的历史实例放在模板库底部“待归属旧实例”，指定模板和简名后会出现在该模板下。这只整理归属，不把目标模板覆盖到旧内容；关联 origin=assigned 与从模板新建的 origin=created 有所区别。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | /api/templates/{id}/instances | 本模板内列出 / 创建实例；创建需 name、slug，可传 agent_id |
| GET | /api/templates/{id}/instances/{slug} | 通过模板与简名读取实例 |
| POST | /api/templates/{id}/instances/connect | 原子查找或创建；传 slug，可选 name、agent_id、create |
| POST | /api/sessions/{id}/metadata | 修改 name、slug、agent_id，需 expected_revision |
| GET | /api/instances/unassigned | 读取尚无归属的历史实例 |
| POST | /api/sessions/{id}/assign | 将旧实例归属到 template_id / slug；保留全部原始数据 |

旧 `/api/sessions` 创建接口保留兼容，支持 slug。未传模板时使用第一个可用模板；未传 slug 时生成稳定唯一简名。新接入推荐显式配置模板 ID 与实例简名，避免每次启动创建新实例。通过 template_id + slug 定位不是身份认证机制，服务仍用于本机单用户环境。

## 状态管理与作用范围

工作台在模型输入之前单独显示“状态定义与当前状态”。`ContextState.field_definitions` 保存 Pydantic `StateDefinition`（类型、说明、默认值）；`fields` 保存当前值。管理信息不会额外进入模型输入，原有状态组件位置、来源、编译格式均保持不变，组件内容可折叠。

- `state.define` 创建或修改定义，payload 为 key、value、value_type、description，其中 value 是默认值。实例上编辑默认值不会改写当前值；新增字段使用默认值初始化。
- `state.set` 修改实例运行值。内置 Agent 工具仍使用此命令，未定义字段会自动建立实例定义；已有字段按定义类型校验。模板端兼容的 state.set 同时设置定义和默认输入值。
- `state.delete` 删除定义和当前值。模板级删除同步移除所有实例中的同名字段。
- 模板默认值修改会更新全部实例的定义，但保留各实例当前值。新类型不兼容任一实例当前值时返回 409，模板与所有实例整体回滚，需要先调整实例值。
- 模板提示词修改会更新所有实例中的同名条目，包括之前在实例内覆盖过的同名条目。不同 ID 的实例专属提示词保留；模板级删除同样作用于同名条目。外部只读内容冲突会阻止整次同步。
- 模板组件排序同步共享组件的相对顺序，实例专属组件保留。对话、调用结果、运行记忆和外部工具目录不随模板配置复制。

实例编辑器默认选择“仅当前实例”。选择“基于模板”会调用关联模板的 updates 接口，使用打开编辑器时读到的模板修订号防止并发覆盖。实例专属组件中添加模板提示词前，需先在模板中创建同 ID 组件。管理区用“模板共享 / 仅此实例 / 实例定义覆盖”等标签展示归属。

旧数据无需清空或迁移表结构。读取旧状态时，缺失的定义从已有字段类型、说明和当前值补齐；补齐的默认值可在面板中单独调整。不会在启动时批量重放旧模板变更。下次模板修改只同步本次配置差异。
