# Context Protocol

推荐使用[服务端工具网关](docs/tool-gateway.md)：在工作台“工具接入”中添加 MCP / 本地 / 上下文来源，模板或实例选择后，Agent 只调用 `/prepare` 和 `/tools/call`，无需自行注册工具或连接 MCP。现有宿主直连方式保留兼容。

状态和记忆工具现为[可选上下文工具提供方](docs/context-tools.md)：宿主显式选择并注册，支持本地调用、LangChain 普通提供方和独立 MCP Server。新实例不再自动注入工具，`GET /api/context-tools` 仅发现定义。

让开发 AI 创建 Agent：读取 `GET /api/agent-guide`（JSON）或 `GET /api/agent-guide?format=markdown`。接口集中提供当前服务地址、OpenAPI/协议入口、接入规则、请求示例、数据 Schema 和完整 MCP/JS 示例源码；只读且无需数据库即可获取。

工具推荐通过 MCP 接入，也支持本地 Python/JS/HTTP 注册。现已提供独立于 LangChain 的 MCP SDK 接入、按宿主连接隔离的工具目录、可编辑的工具描述和参数解释，以及工作台绑定预览。见 [MCP 优先工具接入](docs/tool-integration.md)。

框架无关宿主接入见 [宿主协议 text-tools / 0.1](docs/host-protocol.md)：提供版本发现、能力声明和统一的 `/prepare` 检查，以及不依赖 LangChain 的 [Python 工具循环](examples/plain_agent.py) 与 [JavaScript 宿主](examples/plain_host.mjs)。LangChain 是可选适配器，不是协议入口。

一个独立于 Agent、MCP 和模型提供商的上下文管理协议与本地可视化工作台。

后端按 **controller → service → mapper** 分层；业务模型、更新命令、数据库行与响应采用 Pydantic。入口与扩展方式见 [后端架构](docs/architecture.md)。

首页是**上下文模板库**：维护多个可复用模板，接入 Agent 时选择模板，再创建各自独立的运行实例。同一模板可供多个 Agent 使用，也可为不同用途创建不同模板。Agent 按“模板 ID + 实例简名”接入，首次可创建、之后复用同一实例，支持只连接已存在实例。侧栏只列模板，运行实例列在所属模板内部，对话记录只是实例里可选的数据来源。

模板可新建、编辑、复制、修改名称与用途、配置默认字段值、导出；已有实例的内部配置可另存为模板。初始模板包括通用助手与研究助手。模板级配置更新会原子同步到已有实例；字段定义与默认值同步，已有运行值保留。实例级编辑只影响当前实例。详见 [模板与多 Agent 使用](docs/templates.md)。

本项目管理的是**当前准备交给 AI 的一份有序上下文**。系统提示词、工具描述与 Schema、用户设置、状态、用户发言、Agent Loop、工具结果及最终输出都在运行上下文中，按组件顺序展示。模板只保存可复用的结构、默认内容与字段默认值。组件可以新增、排序、启停、重新绑定来源，并分别配置压缩策略。内部内容可编辑，外部工具的执行契约由提供方同步；工具描述和参数解释可以在工作台单独配置，Skill 定义仍由提供方同步。

现在直接保存当前上下文。已移除事件溯源、分支、历史版本选择、快照和构建存档；编辑用户发言会直接修改模型输入，已有工具记录保持原样，宿主工具的真实状态不受影响。

## 启动

Python 3.11+，原生 HTML/CSS/JavaScript，无需前端构建。依赖安装到项目的 `.venv`：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-postgres.txt
.\run.ps1
```

浏览器访问 [本地工作台](http://127.0.0.1:8010)，[API 文档](http://127.0.0.1:8010/docs)。首页打开模板，在模板内部创建带简名的实例；实例列表提供上下文查看和 Agent 接入代码。

存储只读取 `.env` 中的 `CONTEXT_DATABASE_URL`，不会回退到其他项目的 `DATABASE_URL`。当前本机使用独立的 **localhost:5432/context_protocol** 数据库。SQLite / MySQL 可通过 URL 与相应驱动切换，详见 [存储说明](docs/storage.md)。默认无配置时指向 `compose.context.yml` 中的独立库，端口 5433。

```powershell
# 若需启动 Compose 提供的独立库
 docker compose -f compose.context.yml up -d
# 按实际需要设置连接，已有本机配置无需改动
$env:CONTEXT_DATABASE_URL = 'postgresql://context:context_local@127.0.0.1:5433/context_protocol'
```

应用默认只绑定本机，是单用户工具，尚无身份认证。Web 服务在集中式模式中负责工具连接与执行，模型仍由 Agent 调用。Agent 标记仅便于区分实例，不提供认证或权限隔离。

## 上下文与 Agent 的关系

```text
Agent 回写用户发言和模型输出；工具由服务端接入与执行
                    ↓
            当前上下文（数据库）
                    ↓
       有序组件 → 来源筛选 → 每组压缩策略
                    ↓
        model_input.messages / model_input.tools
              ↙                  ↘
        工作台展示             Agent 适配器 → 模型
```

默认六个组件都是初始配置，可以修改或删除；`kind` 可自定义，`sources` 可扩展，`role` 指定系统指令、上下文数据或对话记录。对话记录本身也是可排序的组件，用户发言、模型调用与工具结果在其内部按输入消息顺序排列。思考仅记录宿主实际提供的内容。

前端和 LangChain 适配器读取同一份 `model_input`。集中式工具的名称、可用性与执行路由由服务端维护；Agent 只映射模型提供商的原生请求格式。宿主直连模式继续可选。工作台展示通用模型输入，并提供 JSON 复制与当前上下文导出。

[组件与来源](docs/composition.md) · [协议与接口](docs/protocol.md) · [LangChain 接入](docs/langchain-integration.md)

## 接入

```python
import httpx
from context_protocol.client import ContextClient

with httpx.Client(base_url="http://127.0.0.1:8010/api") as http:
    templates = ContextClient.templates(http)
    context = ContextClient.connect(http, templates[0]["id"], "researcher",
                                    name="研究任务", agent_id="researcher")
    context.emit("message.append", {"role": "user", "content": "分析项目"}, "turn-1")
    compiled = context.build()
    model_input = compiled["model_input"]
```

通用客户端在 `context_protocol/client.py`，框架适配在 `context_protocol/adapters/langchain.py`，业务测试工具在 `examples/langchain_agent.py`。核心协议、编译器和存储服务均不依赖 LangChain。

### MCP 工具

MCP Server 只提供工具，上下文仍由本项目保存和编译。`examples/mcp_agent.py` 用 `langchain-mcp-adapters` 加载 MCP 工具，交给同一套 `ContextAgent`，并提供异步 `arun()`（MCP 工具只实现协程）：

```powershell
.venv/Scripts/python.exe -X utf8 -m examples.mcp_agent --mock --smoke-test
.venv/Scripts/python.exe -X utf8 -m examples.mcp_agent --mcp-config mcp.json --template TEMPLATE_ID --instance researcher --interactive
```

示例使用真实 stdio MCP server，验证 MCP `add`、状态持久化，以及换一个新 Agent 后从同一上下文读取结果。详见 [MCP 接入](docs/mcp-integration.md)。

```powershell
.venv/Scripts/python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-agent.txt
# 模拟模型，真实 LangChain loop、工具、HTTP 与数据库
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --mock --smoke-test
# 使用 .env 配置的模型 API，发送当前上下文并产生调用费用
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --smoke-test
```

两轮测试：计算 6×7，保存 `last_result=42`，更换 Agent 实例后从同一上下文读取 42。2026-10-21 已在当前版本完成模拟模型端到端测试；早先版本的真实模型测试记录属于旧版本验证，不代表本次重新调用了模型。

## 策略与保存

| 策略 | 行为 |
| --- | --- |
| keep | 完整保留 |
| recent | 保留最近 N 项、轮次或循环；对话记录默认按完整轮次 |
| truncate | 截断组件文本；原始内容仍保留在当前上下文 |
| exclude | 从模型输入排除此组件，仍可重新启用 |

预算单位为字符，尚未计算真实 token。超预算返回警告，LangChain 适配器停止本次模型请求，等待调整策略。原生工具 Schema 不允许截断。启停、排序、压缩只影响模型输入，不触发工具执行。

数据库通过 `context_templates` 保存模板，通过 `context_bindings` 记录实例的来源模板与可选 Agent 标记，`context_handles` 保证模板内简名唯一；`sessions` 保存实例名称及修订号，`contexts` 保存当前 JSON 状态，`context_receipts` 用于网络重试去重。修订号不提供历史读取，编译是只读操作，模板与实例都不提供历史版本恢复。

升级时，旧库原先选中的上下文一次性转为当前状态；原事件、分支和快照表保留为旧数据归档，不再由工作台读取或写入。没有导入新内容时，后续启动不会重新覆盖已经编辑的上下文。

## 测试

```powershell
.venv/Scripts/python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q
$env:CONTEXT_TEST_POSTGRES = '1'
.venv/Scripts/python.exe -m pytest -q
node --check web/app.js
```

SQLite 和 PostgreSQL 使用同一套行为测试；设置 `CONTEXT_TEST_MYSQL_URL` 可追加 MySQL 联调。本次完成 85 项测试，包括输入一致性、组件扩展、压缩、编辑、并发写入、回滚、旧数据导入和模拟模型 loop。MySQL 仅验证了方言建表语句，尚未做实际服务器联调。

当前未实现：语义摘要、真实 token 预算、自动预算分配、RAG、记忆自动晋升、其他框架适配、工作台内模型执行、导出包导入和身份认证。
