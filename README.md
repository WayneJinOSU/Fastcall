# Fast-Call / Collection Agents

语音外呼系统，包含 Fast-Call 控制面、独立 FreeSWITCH Adapter、共享外呼内核和 Collection Agent。

## 唯一维护的文档

本项目只维护以下六份架构文档，不并行维护其他模块说明：

| 文档 | 内容 |
|---|---|
| [系统架构总览](docs/SYSTEM_ARCHITECTURE.md) | 设计目标、运行单元、接口协作、事务、安全和验证 |
| [Fast-Call 控制面](docs/modules/FASTCALL.md) | Open/Admin API、Robot Config、Console、Scheduler、Webhook 与本地聚合 |
| [Fast-Call Adapter](docs/modules/FASTCALL_ADAPTER.md) | 拨号栅栏、ESL、Agent Session、媒体、核查与挂断恢复 |
| [Fast-Call Core](docs/modules/FASTCALL_CORE.md) | 共享领域、状态机、事务用例、ORM/DAO/UOW 和数据契约 |
| [Collection Agent](docs/modules/COLLECTION_AGENT.md) | Bot Pipeline、Provider、配置模式、Agent 协议和独立落库 |

推荐阅读顺序：先读系统总览，再根据修改范围阅读对应模块分册。

## 修改约定

- 修改接口、状态机、事务或部署边界时，同步更新总览、相关分册和对应测试。
- 当前行为与未来规划分开描述，不把计划写成已实现能力。
- 不记录密钥、完整号码、Provider 凭据或业务敏感内容。
- 验证结论注明覆盖范围；跳过、网络失败和既有无关错误不能写成通过。
