# 当前上下文协议

工具接入首选 [MCP 与宿主绑定](tool-integration.md)。新增 host.open/renew/close、provider.sync、tool.prompt.set/delete 命令，分别管理宿主连接、提供方目录和描述配置；它们使用同一更新事务与幂等协议，不改变已有命令格式。

框架无关的执行接入契约见 [宿主协议](host-protocol.md)。新增 `POST /api/sessions/{id}/prepare` 用于版本、能力与输入可用性检查；`GET /api/protocol` 提供宿主版本和准备请求/响应 JSON Schema。现有编译、更新与预览格式保持兼容。

HTTP API 版本 0.6；更新载荷版本 0.1，ContextEnvelope 版本 0.2。类型定义位于 `models/`，纯更新与编译逻辑位于 `domain/`；`protocol.py` 是旧 JSON 函数的兼容入口。更新 Schema 使用按 type 判别的 Pydantic 联合，每种 payload 均有具体模型。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | /api/sessions | 列出 / 创建运行实例，可传 template_id 与 agent_id |
| GET | /api/sessions/{id} | 当前状态、修订号与更新时间 |
| POST | /api/sessions/{id}/updates | 编辑内部内容与组件 |
| POST | /api/sessions/{id}/ingest | 宿主同步外部定义、对话、工具结果 |
| POST | /api/sessions/{id}/compile | 只读编译当前上下文 |
| GET | /api/sessions/{id}/preview | 同一编译结果，支持 budget_chars |
| GET | /api/sessions/{id}/export | 导出当前状态与编译结果 |
| GET | /api/protocol | 更新 Schema、组件 Schema、默认组件与来源 |
| GET | /api/health | 实际存储位置（隐藏凭据） |

历史事件、分支、选择、快照、构建存档接口均已移除。旧的 seq / branch_id 查询或请求字段会被拒绝，避免误把当前状态当成历史状态。

兼容说明：现有 `/sessions` 路径及 `session_id` 字段沿用旧命名，标识的是一份运行上下文实例，不要求存在聊天或对话。界面区分“模板库”与“运行实例”；对话内容是其中一个可选组件。

## 模板选择

`GET /api/templates` 列出模板，`POST /api/templates` 新建，可传 source_template_id 复制模板或 source_context_id 提取实例配置。`GET /api/templates/{id}` 读取；`POST /updates` 编辑配置；`POST /metadata` 修改名称和用途；`GET /preview` 编译；`GET /export` 导出。参见 [模板协议](templates.md)。

创建运行实例时向 `/api/sessions` 传 name、template_id 和可选 agent_id。每次创建都会复制模板的当前配置；实例返回的 binding 标明来源模板、创建时修订号与 Agent 标记。实例之间不共享字段与对话，更新模板不覆盖既有实例。

## 更新载荷

```json
{
  "protocol_version": "0.1",
  "update_id": "unique-request-id",
  "type": "state.set",
  "source": "my-adapter",
  "turn_id": "turn-1",
  "expected_revision": 3,
  "payload": {"key": "phase", "value": "实现", "value_type": "string"}
}
```

`expected_revision` 可选，用于防止并发覆盖。`update_id` 用于重试去重：同内容返回 duplicate=true，异内容返回 409；去重记录仅保存 ID、内容哈希与修订号，不保存载荷或先前状态。

更新类型：group.upsert/reorder/delete、block.upsert/delete、item.upsert/delete、state.define/set/delete、tool.register/unregister、message.append/update、tool.call/result、memory.append、trace.append。完整字段和校验查看 `/api/protocol` 或 Swagger。

`message.update` 通过 message_seq 标识已有用户消息，更新 content；不会丢弃后续工具观察和回复。条目中的 seq 是稳定的顺序/关联标识，不是可访问的历史地址。工具 call_id 唯一，result 只能对应待完成的调用，按完整调用与结果保留。

## 编译结果

`groups` / `envelope.sections` 保存按用户顺序排列的组件，每项提供被选中的 items、text、messages、input_messages、策略、数量与截断原因。`model_input.messages` 是所有组件 input_messages 按顺序合并的通用模型消息；`model_input.tools` 是已选中的工具描述和 input_schema。

system 组件产生系统消息，context 组件用引用数据包装为用户消息，conversation 组件产生 user / assistant / tool 消息。默认工具组件既展示提供方 Schema，也产生上下文数据消息；适配器可将可执行工具转换为模型原生 tools。工具名的框架映射和实际可执行集合由宿主负责。

LangChain 适配器直接转换上述通用消息，不再从完整原始状态拼一份旁路输入。原生工具 Schema 不能截断。预算是字符估算，不是完整提供商请求大小或 token 数；over_budget 返回警告，适配器停止发送本次请求。

`prompt` 保留分组文本表示，供通用宿主参考；前端“完整模型输入 JSON”与复制功能展示 `model_input`。导出包含当前状态和编译结果，不包含历史恢复或工具重执行能力。


## 按模板与实例简名接入

`GET /api/templates/{id}/instances` 只列此模板实例；同路径 POST 创建，需 name、slug。`GET /instances/{slug}` 解析简名；`POST /instances/connect` 接收 slug、可选 name/agent_id、create=true/false，默认原子复用或创建。模板行锁和 (template_id, slug) 唯一约束保证并发连接不会重复创建。

实例信息返回 slug，binding 返回模板归属、Agent 标记与 origin（created/assigned）。已有模板绑定数据会补稳定简名。未归属历史实例通过 /api/instances/unassigned 与 /api/sessions/{id}/assign 整理，保留当前状态。

POST /api/sessions/{id}/metadata 修改名称、简名和 Agent 标记，expected_revision 防止覆盖。简名允许 `[a-z][a-z0-9_-]{0,63}`。同模板内重名 409；连接不存在实例且 create=false 返回 404；显式 Agent 标记与已有非空标记不同返回 409。这些检查不替代身份认证。
