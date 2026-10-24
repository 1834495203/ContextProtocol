# 可替换的当前上下文存储

集中式工具来源保存在新增 `tool_sources` 表，使用 JSON 文档保存类型化配置、目录缓存和配置修订。实例与模板只保存来源 ID；凭据建议使用环境变量引用。启动时自动创建新表，不重建现有上下文表；进程重启后旧网关连接标记失效，实际执行前重新连接。

运行时只读取 `CONTEXT_DATABASE_URL`，不使用其他项目的 `DATABASE_URL`。本机现有服务使用独立的 `localhost:5432/context_protocol`，不会写入 novel。

| 后端 | URL 示例 | 依赖 |
| --- | --- | --- |
| PostgreSQL | postgresql+psycopg://user:password@localhost:5432/context_protocol | requirements-postgres.txt |
| SQLite | sqlite:///./data/context_protocol.db | requirements.txt，Python 内置驱动 |
| MySQL | mysql+pymysql://user:password@localhost:3306/context_protocol?charset=utf8mb4 | requirements-mysql.txt |

安装使用 `.venv/Scripts/python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r <依赖文件>`。数据库服务器上的库需预先创建；服务启动创建表。SQLite 创建文件和父目录，路径相对项目目录。修改连接不会自动跨数据库搬迁数据。

## 分层

`models/` 定义 Pydantic 实体、行模型和返回 DTO；`services/` 分别负责模板、实例、更新事务和编译用例；`storage/base.py` 声明强类型 mapper 端口。`mappers/sql_mapper.py` 执行 SQL 并转换 Pydantic 模型，`mappers/schema.py` 定义表，`storage/sql.py` 只管理连接和事务，具体数据库模块处理方言与锁。`app.py` 负责装配 controller，详见 [架构说明](architecture.md)。

当前业务表：

- context_handles：稳定实例简名和归属类型，唯一约束 (template_id, slug)；与既有表分开，升级无需跨后端 ALTER。

- context_templates：模板元数据、当前配置 JSON、修订号及更新时间。
- context_bindings：实例与源模板、创建时模板修订号、可选 Agent 标记的关联。

- sessions：运行实例 ID、名称、创建时间、并发修订号；底层 version 列为兼容既有表保留，API 名为 revision。
- contexts：每个会话一份当前 JSON 状态及更新时间，每次变更直接更新。
- context_receipts：请求 ID、摘要、修订号；仅用于重复请求去重，没有原始更新载荷和历史上下文。

一次更新在同一事务中保存当前状态、递增修订号、保存去重凭据。PostgreSQL / MySQL 锁定会话行；SQLite 使用 BEGIN IMMEDIATE。任何一步失败会整体回滚。编译与导出都是只读操作。

## 升级旧版本

启动时 `storage/legacy_import.py` 将旧库原先选中的分支/状态一次性转为根会话的普通当前上下文，之后只使用新表。旧表保留归档，不再暴露历史接口或继续写入；已导入的会话不会在后续启动被旧数据覆盖。全新库只建立上述六张表，不建立事件、分支或快照表。

`storage/legacy_sql.py` 是读取归档数据的兼容映射，不是当前存储端口。早期 novel.context_protocol 的显式跨库迁移工具仍在 `migrate_legacy.py`，只有这个主动执行的旧库迁移命令读取通用 DATABASE_URL；它验证旧表复制后再物化当前状态。现有独立库无需重复执行。

历史迁移报告 `storage-migration-report.json` 留作当时的记录。不会删除旧数据，也不支持通过旧数据恢复宿主工具状态。

## 验证

SQLite 文件库和本机 PostgreSQL 已完成当前上下文保存、并发、幂等、故障回滚和旧数据导入测试。MySQL 验证了方言建表语句，尚无实际 MySQL 服务联调。API 错误隐藏底层驱动信息与凭据，health 只显示后端、数据库、主机和端口。

模板复制与实例创建在同一事务中读取模板并保存独立 JSON 与关联。模板编辑锁定模板行并校验 expected_revision；实例和模板分别保存，不通过继承链读取运行数据。首次没有模板时创建两个基础模板，不改变现有实例。

新建实例、修改简名和按简名连接均锁定所属模板行；连接再锁定实例行，避免重复创建。初始化只给已有模板绑定实例补简名，不改变上下文、字段或版本；无归属旧实例等待用户指定归属。
