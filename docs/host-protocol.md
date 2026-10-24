# 宿主接入规范：text-tools / 0.1

集中式模式见 [工具网关](tool-gateway.md)：实例配置 tool.sources.set 后，prepare 无需 binding_id，工具通过服务端 tools/call 执行。下文描述的 host.open / provider.sync 为宿主直连兼容流程。

本文件定义框架无关的 HTTP 接入契约。Python 服务是参考实现；客户端不需要导入本项目、Pydantic、LangChain 或继承 Agent 类。本文中的“必须”是实现此宿主配置的符合性要求。

开发 AI 可以从 `GET /api/agent-guide` 获取自包含接入说明；`?format=markdown` 返回可直接阅读或保存的 Markdown，包含本规范、工具接入说明、请求/响应 Schema 与示例源码。接口只读、不查询存储，返回的服务链接按实际请求地址和部署前缀生成。

多环境接入应使用 [工具绑定与 MCP 接入](tool-integration.md)：先提交 `host.open` 与 `provider.sync`，再以 binding_id 准备输入。`tool.register` 是旧的实例级兼容目录；一旦实例使用过宿主绑定，prepare 必须明确指定绑定。

## 职责与发现

服务保存和编译上下文。宿主负责调用模型、执行工具、权限、取消及步数限制。上下文中的工具定义不代表宿主有权或有能力执行工具。服务不运行 Agent Loop。

`GET /api/protocol` 返回 `host_protocol_versions`、`host_profile`、`prepare_schema`、`prepared_schema`、`update_schema`。Schema 使用 JSON Schema，语言无关；完整路径和请求响应 Schema 同时通过 `/openapi.json` 提供。客户端必须选择支持的宿主版本。宿主协议 0.1、更新协议 0.1、envelope 0.2 是三个独立版本标识，不能互相替代。

## 上下文实例与更新

实例标识是服务返回的不透明 ID，不是聊天框架的 thread/checkpoint ID。

1. `POST /api/sessions`，例如 `{"name":"My host"}`，创建实例；已有实例可直接按 ID 使用。模板和简名连接是可选便利接口，见 [模板文档](templates.md)。
2. `POST /api/sessions/{id}/ingest` 提交 `update_schema` 定义的命令。每条命令必须有稳定的 `update_id`，网络重试必须发送原始 ID 和相同内容，成功返回 `update.seq`。重复命令返回 `duplicate=true`，不会重复应用；同 ID 异内容返回 409。
3. 可传 `expected_revision` 防止覆盖。409 后必须读取当前状态并重新决策，不能盲目更换修订号重试。

用户输入使用 `message.append`。工具目录使用 `tool.register` / `tool.unregister`。工具名称为协议名称，框架别名只存在宿主映射中。`source`、`agent_id` 是标记，不是身份认证。

## 推理前准备

`POST /api/sessions/{id}/prepare`：

```json
{
  "supported_versions": ["0.1"],
  "capabilities": {
    "roles": ["system", "user", "assistant", "tool"],
    "tool_calls": true
  },
  "budget_chars": 24000
}
```

字段可省略，默认完整支持 text-tools / 0.1。未来扩展必须通过显式版本或新配置声明；当前能力对象拒绝未知字段，不会假装支持。

HTTP 200 返回 `protocol_version`、`ready`、`issues`、`compiled`。不兼容版本返回 `unsupported_version`，`compiled=null`；其他不可用情况仍返回编译结果供检查。`issues[].code` 包括 `over_budget`、`truncated_tool_schema`、`unsupported_role`、`unsupported_tool_calls`。宿主必须在 `ready=false` 时停止此次模型调用，调整输入或能力后重新准备，不允许静默丢失结构。

准备操作只读，不建立锁或模型执行租约。返回的 `compiled.revision` 标识读取版本；它不保证模型执行期间无人修改上下文。多个宿主并发操作同一实例时必须由宿主协调写入，或使用修订检查。

`compiled.envelope.sections` 保存有序组件及选择信息；`compiled.model_input` 是当前 text-tools 配置的通用输入。宿主必须保持消息顺序、工具调用 ID 和结果关联。执行目录应取 `model_input.tools` 与宿主可执行工具的交集。历史调用可以在新宿主中保留，不能据此重新执行。

`POST /compile` 与 `GET /preview` 继续用于检查，即使超预算也返回结果；执行宿主应使用 `/prepare`。预算目前是字符估算，不是 token 限制。

## 模型响应和工具回写

1. 提交 assistant `message.append`，带 `tool_call_ids`，保存返回 `update.seq`。
2. 为每个调用提交 `tool.call`：`call_id`、协议工具名、`arguments`、`assistant_seq`。
3. 宿主检查实际可执行集合、参数与权限，然后执行工具。
4. 提交 `tool.result`：`call_id`、`output`、`is_error`；结果必须关联待完成调用。
5. 重新准备上下文并推理；无工具调用的助手输出可结束此轮。

当前每条更新独立提交，不支持原子批量回写。宿主应先保存整批调用，再执行工具；进程中断时通过相同 update_id 恢复未确认的写入。幂等回写不等于工具执行恰好一次；外部副作用工具需要自己的幂等机制，不能自动重放。

模型失败时不伪造助手响应。工具失败应写入错误结果；取消后无法取得结果的调用可保持 pending，宿主决定人工恢复或记录明确错误。没有结果的调用在当前编译器中呈现为观察数据，不被伪装成已完成的原生调用。

## 错误与限制

HTTP 404 表示资源不存在；409 表示修订或去重冲突；422 表示输入或业务约束不满足；503 表示存储不可用。错误正文为 `{"detail": "..."}`；准备被拒绝使用上述结构化 issues。传输失败可使用同一 update_id 重试写入；HTTP 业务错误必须处理后再决定。

此配置支持文本和结构化工具调用；不宣称支持多模态、隐藏推理内容、原生框架状态或任意模型格式的无损转换。工具执行、认证、分布式锁、实时通知、原子响应批次和新的内容块配置不在当前实现范围。新配置应与现有文本配置并存演进。

## 独立实现与验证

Python 示例：[plain_agent.py](../examples/plain_agent.py)，仅使用 httpx，完成工具循环和状态保存，无付费模型调用：

```powershell
.venv/Scripts/python.exe -m examples.plain_agent
```

随后把输出的实例 ID 交给独立 JavaScript 宿主（Node 18+，无 npm 依赖）：

```powershell
node examples/plain_host.mjs http://127.0.0.1:8010/api INSTANCE_ID
```

JavaScript 示例读取同一编译输入并回写助手消息，不执行 Python 宿主的工具。两者的模型部分是确定性替身；接入真实模型只需在宿主边界映射输入输出。

`tests/test_host_protocol.py` 通过 HTTP 验证发现、版本拒绝、能力和预算拒绝、重试、并发冲突、完整工具循环及 Python → JavaScript 接续。Node 缺失时跨语言用例明确跳过。LangChain 测试继续验证框架适配器；它使用相同准备接口。
