"""Read-only bootstrap guide; independent of persistence and agent frameworks."""
import json
from pathlib import Path
from ..models.agent_guide import AgentGuide, GuideLinks, GuideStep, GuideResource
from ..models.context import JsonObject
from ..models.commands import ContextUpdate
from ..models.requests import ConnectInput
from ..models.host import PrepareInput, PreparedContext
from ..models.persistence import ConnectionResult, UpdateResult

ROOT = Path(__file__).resolve().parents[2]


def update_example(kind: str, payload: dict, update_id: str) -> JsonObject:
    command = ContextUpdate.model_validate({"protocol_version": "0.1", "type": kind,
        "update_id": update_id, "source": "example-host", "turn_id": "example-turn", "payload": payload})
    return JsonObject(command.model_dump(mode="json", by_alias=True, exclude_unset=True))


def build_agent_guide(service_base_url: str, links: GuideLinks) -> AgentGuide:
    base = service_base_url.rstrip('/')
    ingest = '/api/sessions/{sid}/ingest'
    binding = 'example-connection-id'
    schema = {"type": "object", "properties": {
        "a": {"type": "integer", "description": "第一个整数"},
        "b": {"type": "integer", "description": "第二个整数"}}, "required": ["a", "b"]}
    workflow = [
        GuideStep(id='discover', description='读取协议版本和 Schema', method='GET', path='/api/protocol'),
        GuideStep(id='templates', description='列出模板并选择模板 ID', method='GET', path='/api/templates',
                  notes=['将选中的模板 ID 替换路径中的 {tid}。']),
        GuideStep(id='connect', description='创建或复用模板内的运行实例', method='POST',
            path='/api/templates/{tid}/instances/connect',
            body=JsonObject(ConnectInput(slug='example-agent', name='Example Agent', create=True).model_dump(exclude_unset=True)),
            notes=['使用响应 instance.id 替换后续路径中的 {sid}。已有实例可跳过此步直接使用其 ID。']),
        GuideStep(id='open_host', description='为当前环境建立独立宿主绑定', method='POST', path=ingest,
            body=update_example('host.open', {'binding_id': binding, 'host_id': 'example-environment'}, 'example-open'),
            notes=['每次连接生成新的 binding_id；示例中的固定 ID 只用于说明。']),
        GuideStep(id='sync_tools', description='MCP 发现或本地注册后，同步该提供方的实际完整工具清单', method='POST', path=ingest,
            body=update_example('provider.sync', {'binding_id': binding, 'provider_id': 'math', 'kind': 'mcp', 'status': 'ready',
                'tools': [{'name': 'add', 'model_name': 'math_add', 'description': '计算两个整数的和', 'input_schema': schema}]}, 'example-sync'),
            notes=['此清单必须来自真实 tools/list 或宿主注册，不能根据示例虚构执行能力。',
                   '原生 MCP SDK 会自动生成安全的模型工具别名；原始工具身份为 math/add。']),
        GuideStep(id='user_message', description='记录用户输入', method='POST', path=ingest,
            body=update_example('message.append', {'role': 'user', 'content': '计算 6+7'}, 'example-user')),
        GuideStep(id='prepare', description='取得当前绑定的模型输入', method='POST', path='/api/sessions/{sid}/prepare',
            body=JsonObject(PrepareInput(binding_id=binding).model_dump(mode='json')),
            notes=['ready=false 时处理 issues，暂停本次模型调用。',
                   '将 compiled.model_input.messages/tools 映射到模型 SDK，保留顺序和编译后的描述。',
                   '保存 binding_revision，执行前再次 prepare，传 expected_binding_revision 检查目录未变化。']),
        GuideStep(id='assistant_call', description='记录模型实际返回的助手消息及工具调用 ID', method='POST', path=ingest,
            body=update_example('message.append', {'role': 'assistant', 'content': '', 'tool_call_ids': ['example-call']}, 'example-assistant'),
            notes=['保存响应 update.seq，作为下一步的 assistant_seq；示例值 1 需要替换。']),
        GuideStep(id='tool_call', description='保存工具调用，再由宿主执行实际工具', method='POST', path=ingest,
            body=update_example('tool.call', {'call_id': 'example-call', 'name': 'math_add',
                'arguments': {'a': 6, 'b': 7}, 'assistant_seq': 1}, 'example-tool-call'),
            notes=['name、arguments、call_id 必须取自模型实际响应，工具必须在此次准备结果中。',
                   '实际执行发生在宿主，通过 MCP tools/call 或本地方法；上下文服务不执行工具。']),
        GuideStep(id='tool_result', description='记录真实工具结果', method='POST', path=ingest,
            body=update_example('tool.result', {'call_id': 'example-call', 'output': 13, 'is_error': False}, 'example-tool-result'),
            notes=['13 是示例值，必须替换为实际返回结果；工具失败时使用 is_error=true。',
                   '回到 prepare 继续模型循环，直到模型返回无工具调用的最终输出。']),
        GuideStep(id='final_message', description='保存模型实际生成的最终回答', method='POST', path=ingest,
            body=update_example('message.append', {'role': 'assistant', 'content': '6+7=13'}, 'example-final')),
        GuideStep(id='close_host', description='退出时关闭绑定和宿主 MCP 连接', method='POST', path=ingest,
            body=update_example('host.close', {'binding_id': binding}, 'example-close'),
            notes=['长时间运行时在租约到期前发送 host.renew；到期后新建绑定并重新发现工具。'])
    ]
    rules = [
        '模型调用、Agent Loop、业务工具执行和凭据由宿主负责；上下文服务保存并编译输入。',
        '优先 MCP 接入；Python、JavaScript、HTTP 或 LangChain 宿主遵循同一 HTTP 契约。',
        '上下文状态和记忆工具同样需要显式接入提供方；可从 links.context_tools 发现定义，通过 ContextTools 或独立 MCP Server 注册，不会自动注入。',
        '先发现并同步工具，再调用模型；工具目录缓存和提示词配置不代表当前环境可执行。',
        '同一实例可以有多个宿主，每次 prepare 明确指定本连接的 binding_id，不合并其他环境的工具。',
        '使用 compiled.model_input 的工具描述与参数解释，不能用原始 SDK 描述覆盖工作台配置。',
        '每个独立更新生成唯一 update_id；网络重试使用同一 ID 和完全相同的内容。',
        '409 表示修订或去重冲突，读取状态后重新决策；404 资源不存在，422 输入或业务约束失败，503 存储不可用。',
        'provider.sync 原子替换该绑定下一个提供方的完整清单；ready+空列表与 pending/error 有不同含义。',
        '维护 call_id 与 assistant_seq 关联；外部工具副作用的幂等由执行方负责，不能盲目重放历史调用。',
        '多个宿主同时推进同一段对话时由宿主协调轮次；工具绑定不提供共享对话的执行锁。',
        'LangChain 多环境接入传 host_id；未启用绑定的旧版接入仅保留兼容。',
        'quickstart_argv 和 Python SDK 示例在本项目根目录或已配置本项目导入路径的环境中运行；独立项目可按 workflow 直接实现 HTTP 客户端。'
    ]
    instructions = (
        f'请基于 {base}/api 实现一个 Agent。先读取本说明及 links.openapi、links.protocol。'
        '使用用户提供的任务、模型环境变量配置和真实 MCP/本地工具，不在代码中硬编码凭据。'
        '按 workflow 完成实例连接、宿主绑定、目录发现、准备模型输入、工具执行和结果回写。'
        '示例请求用于说明契约，替换其中的 ID、别名、消息、参数与结果。'
        '参考 resources 中完整的无 LangChain MCP 示例，交付可运行代码、配置说明与启动命令。'
        '先用确定性模型替身验证工具循环，再按用户配置接入真实模型。'
    )
    # Fixed allowlist: never serve .env, runtime configuration or arbitrary paths.
    resources = [GuideResource(path=path, media_type=media, description=description,
                               content=(ROOT/path).read_text(encoding='utf-8'))
        for path, media, description in (
            ('docs/tool-integration.md', 'text/markdown', '工具发现、注册、描述覆盖、绑定及生命周期规范'),
            ('docs/host-protocol.md', 'text/markdown', '框架无关的宿主协议、错误及并发语义'),
            ('docs/context-tools.md', 'text/markdown', '可选上下文工具提供方：本地、LangChain 和独立 MCP 接入'),
            ('context_protocol/providers/context.py', 'text/x-python', '框架无关的上下文工具执行实现'),
            ('examples/mcp_host.py', 'text/x-python', '不依赖 LangChain 的完整 MCP 与本地方法宿主示例'),
            ('examples/mcp_server.py', 'text/x-python', '函数描述和参数解释直接生成 Schema 的 MCP Server 示例'),
            ('context_protocol/adapters/context_mcp.py', 'text/x-python', '将选中的上下文能力暴露为普通 MCP Server'),
            ('examples/plain_host.mjs', 'text/javascript', '无 npm 依赖的 JavaScript HTTP 宿主示例'))]
    return AgentGuide(service_base_url=base, api_base_url=base+'/api', links=links,
        required_inputs=['Agent 业务目标', '模型提供方、模型名及凭据环境变量名',
                         '真实 MCP 连接配置或本地工具实现', '上下文实例 ID 或模板选择与实例简名'],
        instructions=instructions, rules=rules, workflow=workflow, resources=resources,
        placeholders={'{tid}': 'GET /api/templates 中选择的模板 ID', '{sid}': 'connect 响应 instance.id 或已有实例 ID',
                      'example-connection-id': '每个宿主连接新生成的 ID', 'example-*': '替换为真实且唯一的更新、轮次或调用标识'},
        schemas={name: JsonObject(model.model_json_schema()) for name, model in (
            ('ContextUpdate', ContextUpdate), ('ConnectInput', ConnectInput), ('ConnectionResult', ConnectionResult),
            ('PrepareInput', PrepareInput), ('PreparedContext', PreparedContext), ('UpdateResult', UpdateResult))},
        quickstart_argv=[['python', '-m', 'pip', 'install', '-r', 'requirements-mcp.txt'],
                        ['python', '-m', 'examples.mcp_host', '--url', base+'/api']],
        limitations=['本接口只读，不创建实例、不连接 MCP、不调用模型，也不返回当前实例数据或环境凭据。',
                     '提供说明不代表服务存储已就绪，运行前检查 links.health。',
                     '远程 AI 无法直接访问你电脑的 localhost；可下载本说明作为文件提供，或让开发宿主处于可访问服务的网络。',
                     '默认服务只监听本机且没有身份认证；绑定标识不是访问控制。',
                     '当前支持文本与工具调用；完整 Web 托管 Agent、多模态和原子响应批次尚未实现。'])


def render_agent_guide(guide: AgentGuide) -> str:
    lines = [f'# {guide.title}', '', f'服务：{guide.service_base_url}', f'API：{guide.api_base_url}', '',
             '## 给开发 AI 的任务说明', '', guide.instructions, '', '## 开始前需要的配置', '']
    lines.extend('- '+item for item in guide.required_inputs)
    lines.extend(['', '## 规范入口', ''])
    lines.extend(f'- {name}: {url}' for name, url in guide.links.model_dump().items())
    lines.extend(['', '## 接入规则', ''])
    lines.extend('- '+item for item in guide.rules)
    lines.extend(['', '## 请求示例与流程', '', '所有路径相对于服务地址；ID 和结果均为示例，按实际响应替换。'])
    for step in guide.workflow:
        lines.extend(['', f'### {step.id}：{step.description}', '', f'`{step.method} {step.path}`'])
        if step.body is not None:
            lines.extend(['', '```json', json.dumps(step.body.root, ensure_ascii=False, indent=2), '```'])
        lines.extend('- '+note for note in step.notes)
    lines.extend(['', '## 数据 Schema', '', '```json',
                  json.dumps({key: value.root for key, value in guide.schemas.items()}, ensure_ascii=False, indent=2), '```'])
    for resource in guide.resources:
        lines.extend(['', f'## {resource.path}', '', resource.description, ''])
        if resource.media_type == 'text/markdown':
            lines.append(resource.content)
        else:
            language = 'python' if resource.media_type == 'text/x-python' else 'javascript'
            lines.extend(['```'+language, resource.content, '```'])
    lines.extend(['', '## 边界与运行条件', ''])
    lines.extend('- '+item for item in guide.limitations)
    return '\n'.join(lines)+'\n'
