# 工具接入：MCP 优先，按宿主绑定

上下文自带的状态、记忆能力也使用相同的提供方契约，需宿主显式接入；本地、LangChain 及独立 MCP 用法见 [上下文工具提供方](context-tools.md)。

推荐把 Python / JavaScript 方法或 HTTP 接口包装成 MCP 工具。工具可在调用模型前发现；本项目保存来源契约和提示词配置，宿主持有执行函数、MCP 连接及凭据。核心服务不依赖 MCP SDK 或 LangChain，也不会启动工作台用户输入的命令。

## 三类持久数据

| 数据 | 作用 | 是否证明工具能执行 |
| --- | --- | --- |
| `state.tool_catalog` | 最近发现的原始工具定义，供离线编辑 | 否 |
| `state.tool_prompts` | 描述、参数解释、启停和必需标记，可在模板中复用 | 否 |
| `state.host_bindings` | 当前宿主连接、租约、提供方状态及实际工具集合 | 仅在绑定有效且提供方 ready 时 |

一个实例可以保存多个 host binding。每个连接使用新的不透明 `binding_id`；稳定的 `host_id` 仅供显示。`provider_id/tool_name` 是工具身份，应由注册方稳定维护，例如 `documents/search`；它不是模型工具名，也不是认证信息。

准备输入必须指定 binding_id，不会取所有宿主的工具并集。首次使用绑定后，未带绑定的 prepare 返回 `binding_required`，避免旧客户端误用缓存目录。旧 `tool.register` / `tool.unregister` 仍可在尚未使用绑定的实例中兼容运行；多环境接入应升级到 provider.sync。

## 直接使用 MCP，无需 LangChain

编写工具时可直接提供函数说明和参数解释，MCP SDK 会生成工具描述与 JSON Schema：

```python
from typing import Annotated
from pydantic import Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")

@mcp.tool()
def search(query: Annotated[str, Field(description="包含模块名或错误关键词的检索词")]) -> str:
    """检索项目文档，返回相关实现的位置。"""
    return lookup(query)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

`lookup` 是用户自己的业务实现。原始说明来自这里；针对不同任务的措辞调整放在工作台覆盖，不需要反复修改或重新部署方法。

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-mcp.txt
.venv/Scripts/python.exe -m examples.mcp_host
```

示例启动项目自带的本地 stdio MCP 服务，先发现目录，再用确定性模型替身演示调用、记录结果及执行本地状态保存函数，不调用付费模型。用 `--session ID --host-id laptop` 连接已有实例。

接入已有 MCP 服务：

```python
from context_protocol.tools import HostTools
from context_protocol.adapters.mcp import connect_stdio, connect_http

# context 是已连接某个实例的 ContextClient。
with HostTools(context, "laptop") as host:
    async with connect_stdio(host, "documents", command="python",
                             args=["my_documents_server.py"]) as provider:
        # 进入连接后，目录已发现，未执行业务工具或调用模型。
        prepared = await provider.prepare()
        if not prepared.ready:
            raise ValueError(prepared.issues)
        # 将 prepared.compiled.model_input 交给模型。
        # 得到工具调用后，由宿主记录 tool.call，并按返回名称执行：
        result = await provider.call(prepared, model_tool_name, arguments)
        # 再将 result 写为 tool.result；完整记录示例见 examples/mcp_host.py。
```

远程 MCP 使用 `connect_http(host, "documents", url="https://.../mcp", headers=...)`。连接配置、环境变量、地址和令牌留在宿主，不写入共享上下文。

工具目录会取完 `tools/list` 的全部分页。接收到 `tools/list_changed` 时，SDK 将该提供方标记 pending，旧准备结果不能继续执行；下一次 `provider.prepare()` 自动重新发现。没有通知支持的服务应由宿主调用 `await provider.refresh()` 定期或按需更新。发现失败标记 error，不能使用缓存假装在线。上下文退出时标记提供方不可用；绑定租约过期也会阻止执行。

MCP 工具的模型名称由提供方 ID 和原名称生成稳定、安全的别名，避免不同 Server 的同名工具冲突；`ToolSpec.tool_ref` 保持原始身份。MCP 结果中的 `isError` 与协议传输异常分别处理。

## 注册本地方法

```python
from context_protocol.tools import HostTools

def search(query: str):
    return {"matches": [query]}

with HostTools(context, "desktop") as host:
    host.register("documents", search, name="search", description="检索文档",
                  input_schema={
                      "type": "object",
                      "properties": {"query": {"type": "string", "description": "检索词"}},
                      "required": ["query"]
                  })
    prepared = host.prepare()
    result = await host.call(prepared, "search", {"query": "compiler"})
```

注册时函数只存于宿主内存，服务只接收描述和 Schema。支持同步和异步函数，执行前校验参数。`unregister(provider_id, name)` 同步该提供方的新清单；`sync` 用于其他语言或 HTTP 执行适配器注册实际目录。`HostTools` 只路由工具，不替宿主调用模型或自动写入调用历史。

## 纯 HTTP / JavaScript 注册契约

以下命令均提交到 `POST /api/sessions/{id}/ingest`，外层包含 `protocol_version: "0.1"`、稳定的 `update_id`、`type` 和 `payload`。完整 JSON Schema 由 `/api/protocol` 的 `update_schema` 返回。

```json
{"type":"host.open","payload":{"binding_id":"connection-uuid","host_id":"environment-a","lease_seconds":3600}}
```

```json
{
  "type": "provider.sync",
  "payload": {
    "binding_id": "connection-uuid",
    "provider_id": "documents",
    "kind": "http",
    "status": "ready",
    "tools": [{
      "name": "search",
      "description": "检索文档",
      "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "检索词"}},
        "required": ["query"]
      }
    }]
  }
}
```

`provider.sync` 原子替换当前绑定下该提供方的全部清单；ready + 空列表表示已确认无工具，pending / error + 空列表表示发现未完成或失败，后两者阻止准备。其他提供方、其他 host binding 和历史调用不受影响。目录刷新不会删除已保存的提示词配置及离线目录。

`model_name` 可指定与身份分离的模型工具名。跨提供方同名且未区分 model_name 时准备返回 `ambiguous_tool`，不会猜测路由。可传 `expected_binding_revision` 防止同一绑定上的并发目录覆盖。

然后调用：

```json
{"binding_id":"connection-uuid","supported_versions":["0.1"],"budget_chars":24000}
```

提交到 `/prepare`，返回 `binding_id`、`binding_revision`、`ready`、`issues` 和 `compiled`。执行前再次 prepare，带相同 binding_id 与 expected_binding_revision 检查工具目录未变；只执行模型输入实际提供的工具。Python SDK 已执行这些检查。此检查不是分布式执行锁，最终授权及防止执行期间撤销的机制仍由宿主负责。

绑定默认租约一小时，可用 `host.renew` 在到期前续租，`host.close` 主动关闭。到期后必须建立新绑定、重新发现，不能靠续租恢复旧工具。一个绑定上的目录修订不受其他绑定影响；同一实例的对话仍然共享，并发完整轮次仍需宿主协调。

## 工具提示词配置

工作台新增“工具绑定与提示词”面板，可切换宿主预览、查看发现状态，并编辑描述及参数解释。也可在工具尚未连接时按身份预先配置。模板只同步这些配置，不复制宿主连接、凭据或工具实现。

通过实例或模板的 `/updates` 提交：

```json
{
  "type": "tool.prompt.set",
  "update_id": "unique-id",
  "payload": {
    "tool_ref": "documents/search",
    "description": "检索项目文档。回答实现问题前优先使用。",
    "parameter_descriptions": {"/properties/query": "包含模块名或错误关键词的检索词"},
    "enabled": true,
    "required": true
  }
}
```

`parameter_descriptions` 使用 JSON Pointer 指向 Schema 节点，只写入 description，不能修改类型、必填项、枚举、默认值或 const 数据。嵌套字段和 `$defs` 可使用对应节点路径。`tool.prompt.delete` 的 payload 为 `{"id":"documents/search"}`，删除覆盖配置，恢复来源描述。

工作台编辑已知工具时还会提交 `base_schema`，服务计算并保存 `contract_sha256`。后续类型等执行契约变化时返回 `incompatible_tool`，需要检查并重新保存描述；纯描述变化不造成冲突。没有执行定义时可以预配置，首次接入时校验参数路径；希望严格锁定契约的外部客户端应提供 base_schema 或 contract_sha256。

可选工具缺失时不进入模型可调用列表；必需工具缺失或被组件策略排除时返回 missing_required_tool。禁用工具保留配置和历史。来源定义、工作台覆盖和最终模型 Schema 分开保存，覆盖会同时反映在组件文本与原生 tools 中。

## LangChain

```python
agent = ContextAgent(context, model, tools=tools,
                     host_id="environment-a", provider_id="documents")
try:
    agent.run("分析项目")
finally:
    agent.close()
```

显式 host_id 开启独立绑定；不传则保留旧版行为，仅适用于未启用绑定的实例。工具目录在构造时同步，不需要运行一轮。工具集合改变时重建 Agent（新的绑定），或在宿主更新执行集合的同时同步新目录；仅改变描述覆盖无需重建。

## 当前边界

MCP 采用官方 Python SDK，支持本地 stdio 和 Streamable HTTP。自动测试覆盖真实 stdio、本机真实 Streamable HTTP，以及发现失败、分页、失效处理；未连接外部远程 MCP 服务。SDK 不代理任意 Agent 的内部工具注册表；宿主仍需要使用本接入接口。

没有身份认证，host_id / binding_id 不提供访问权限隔离。租约内突然失联的宿主可能直到租约过期才被服务识别；客户端退出和已观察到的传输错误会主动使目录失效。没有原子模型响应批次、自动部署工具代码或跨环境分发凭据。
