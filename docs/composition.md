# 上下文组件与来源

工作台围绕一份有序上下文展开：系统提示词、工具定义、状态、对话和工具结果都是其中的组件。没有独立的运行记录侧栏；展示内容来自编译后的 `input_messages`，与适配器读取的 `model_input.messages` 一致。

## 可扩展组件

```json
{
  "id": "project_rules",
  "label": "项目约束",
  "kind": "custom/project-policy",
  "role": "context",
  "origin": "internal",
  "sources": ["blocks", "items"],
  "enabled": true,
  "policy": {"strategy": "keep", "unit": "auto", "limit": 12, "max_chars": 6000}
}
```

`kind` 是开放的类型名；编译行为由 `sources`、`role` 和 `policy` 决定，不硬编码组件 ID。默认组件均可编辑配置、排序、关闭或删除。`group.reorder` 接收全部组件 ID 的完整排列；`group.delete` 移除组件配置，原始内容仍保留，可重新绑定。

内置来源包括 blocks、items、fields、tools、messages、calls、traces、memories。旧版本的 requests 仅保留兼容读取，不再写入模型请求存档。自定义来源可通过 `register_source(name, provider)` 注册，提供方接收当前状态与组件配置，返回统一条目。推荐使用只依赖当前状态的纯函数。

内部文本使用 `block.upsert`，任意 JSON 使用 `item.upsert`，可附 Schema 与 source_uri。字段定义和默认值使用 `state.define`，运行值使用 `state.set`，删除使用 `state.delete`，可由用户或宿主映射的内置状态工具更新。

## 外部定义与工具记录

编辑端使用 `/updates`，宿主同步使用 `/ingest`。工具定义和标记为 external 的 Skill 内容由提供方更新，编辑端不能覆盖内容或解除外部归属。来源标签不赋予额外权限；这仍是本地单用户工作流约定，尚无网络身份认证。

```python
context.emit("group.upsert", {
    "id": "skills", "label": "外部 Skills", "kind": "skills",
    "origin": "external", "sources": ["items"]
})
context.emit("item.upsert", {
    "id": "skill:review", "group_id": "skills", "origin": "external",
    "source_uri": "skill://review/SKILL.md", "content": "提供方同步的内容"
})
```

用户发言、assistant、工具调用及结果按照标准消息顺序出现在对话组件中，并显示关联 Loop。宿主确实提供的 reasoning 会成为外部运行记录；不会生成或猜测隐藏思考。loop.start/end 是关联元数据，编译器不会把它们额外塞进模型消息。

默认 recent 按完整轮次保留；调用及其结果作为配对消息输出。跨组件重复绑定对话记录时，编译器按顺序去重。截断的对话记录作为引用文本提供，不发送损坏的原生工具调用消息。

## 修改当前输入

编辑用户发言直接修改该条消息在当前上下文中的内容。已经完成的工具记录保持原样，不创建分支、不重放工具，也不撤销宿主状态。配置、内容和策略保存后在下一次编译生效；已进行中的模型调用使用其开始时取得的输入。

压缩只改变编译结果；来源查看与编辑弹窗可以访问当前保留的原始内容，但模型输入区不会展示被排除或截断的隐藏尾部。

类型化扩展接口现位于 domain/sources/registry.py 与 domain/compression/strategies.py。应用通过 ContextCompiler 注入注册表；旧 register_source(name, callback) 保留在 JSON 兼容入口。详见 [后端架构](architecture.md)。
