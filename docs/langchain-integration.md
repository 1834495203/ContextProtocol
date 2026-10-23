# LangChain 接入当前上下文

通用 HTTP 客户端：`context_protocol/client.py`；可复用适配器：`context_protocol/adapters/langchain.py`；业务工具与测试入口：`examples/langchain_agent.py`。核心协议与数据库服务不依赖 LangChain。

```powershell
.\run.ps1
# 另一个终端
.venv/Scripts/python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-agent.txt
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --mock --smoke-test
```

模拟模型仅替换聊天模型，真实运行 create_agent、middleware、工具 handler、HTTP API 和数据库。2026-10-21 当前版本在 PostgreSQL 验证：计算 6×7、保存 last_result=42、更换 Agent 实例后读取 42，第二轮没有新增工具调用。

实际模型模式去掉 `--mock`，读取 `.env` 的 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL，会向配置的模型提供商发送当前上下文、Schema 与工具结果并产生费用。示例关闭 LangSmith tracing。旧 `langchain-test-result.json` 属于之前版本的真实 DeepSeek 测试记录，本次改动没有重新调用外部模型。

可用参数：`--api-url` 指定本地服务、`--session ID` 复用现有上下文、`--prompt 文本` 指定输入、`--interactive` 继续交互、`--smoke-test` 运行两轮验收。

## 适配时机

| 时机 | 处理 |
| --- | --- |
| 用户发言 | message.append，然后执行 Agent |
| 每次模型调用前 | 标记 loop.start，读取 /compile，转换 model_input.messages 与已选工具 Schema |
| 模型响应 | 保存 assistant、工具调用 ID、tool.call；实际提供 reasoning 才记录 |
| 工具执行 | 宿主 handler 执行；状态 handler 额外更新 state.set |
| 工具完成/失败 | 保存 tool.result，完成循环关联 |

没有分支选择、历史恢复、模型请求存档或构建存档。消息元数据关联当前内容，不代表能重新执行或撤销宿主工具。

编译器负责来源筛选、排序、压缩和消息配对。工作台与适配器使用相同 model_input，适配器转换为 SystemMessage / HumanMessage / AIMessage / ToolMessage。工具 catalog 来自宿主同步的 Schema；只有宿主配置了执行 handler 的工具才进入模型原生工具集合。context.state.set 在 LangChain 中映射为 context_state_set，数据库仍保存协议名称。

`ContextAgent(context, model, tools=[...], instructions=...)` 接收业务工具，乘法只在示例中定义。初始化仅在没有 agent_instructions 时写入初始指令，不覆盖用户已编辑的提示词。示例支持同步、单个 Agent 实例串行执行；不同实例同时驱动同一份上下文的完整轮次尚未提供任务级互斥。

修改后的上下文在下一次编译生效；进行中的模型调用保留开始时的输入。按完整轮次压缩可避免工具调用和结果被拆散。超预算或截断原生 Schema 会阻止模型请求，调整策略后再运行。


## 选择模板供多个 Agent 使用

`--list-templates` 查看可选模板；`--template TEMPLATE_ID --agent-id NAME` 为当前宿主创建独立实例。SDK 对应 `ContextClient.templates(http)` 和 `ContextClient.from_template(http, tid, name=..., agent_id=...)`。选择同一模板的不同实例可各自运行 LangChain 或其他框架，不共享运行字段、对话或工具结果。`--session` 用于显式续用同一个实例，不能同时指定模板。

已用两个独立 LangChain 模拟模型 Agent 验证选择同一模板，各自完成 6×7、字段写入与工具调用；模板保持无运行数据。详见 [模板管理](templates.md)。


## 稳定实例接入

```powershell
.venv/Scripts/python.exe -X utf8 -m examples.langchain_agent --template TEMPLATE_ID --instance researcher --agent-id researcher --mock --smoke-test
```

同一命令再次启动会通过模板和简名连接同一实例，不重新初始化状态。加 --existing-only 可禁止创建。模板实例表内“Agent 接入”提供对应 SDK 与 HTTP 示例，SDK 为 ContextClient.connect(http, template_id, slug, create=False)。简名变更需同步更新宿主配置；内部 UUID 接入仍兼容。
