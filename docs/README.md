# 文档入口

当前目录只保留接手、安装、运行、评审、数据和一份结果基线所需文档。

当前 Release 的唯一入口是
[Movie60 Review v3 构建、下载与 Windows 使用](MOVIE60_REVIEW_V3_RELEASE.md)。

## 开发交接必读

1. [DEVELOPER_OPERATION_MANUAL.md](DEVELOPER_OPERATION_MANUAL.md)：日常操作简版，从 Clone、公司镜像、`.venv`、模型安装，到单图、批量、Rule-only 评分和策略迭代；
2. [DEVELOPER_OPERATION_MANUAL_DETAILED.md](DEVELOPER_OPERATION_MANUAL_DETAILED.md)：逐步工程详解版，每一步均说明原因、成功检查和故障处理；
3. [DEVELOPER_ALGORITHM_PRINCIPLES.md](DEVELOPER_ALGORITHM_PRINCIPLES.md)：算法与评分原理简版；
4. [DEVELOPER_ALGORITHM_REFERENCE.md](DEVELOPER_ALGORITHM_REFERENCE.md)：变量、公式、七算法、检测器输入输出、Rule/Agent 和误判排查的工程参考；
5. [reviews/movie60-v3/CURRENT_DATA_AND_ROUTE_STATUS.md](reviews/movie60-v3/CURRENT_DATA_AND_ROUTE_STATUS.md)：当前 Dataset/Release/Strategy 版本关系和 Agent 不如 Rule 的证据分析。

## 按需深入

1. [MOVIE60_V3_RULE_AGENT_GUIDE.md](MOVIE60_V3_RULE_AGENT_GUIDE.md)：当前 v3.3 Rule 主决策、双 Challenger Agent 建议、代理验证和版本迭代；
2. [PLUGIN_STRATEGY_GUIDE.md](PLUGIN_STRATEGY_GUIDE.md)：如何换完整实现或只改参数，并保留历史；
3. [ALGORITHMS.md](ALGORITHMS.md)：七种重定向算法的代码级说明；
4. [REVIEW_GUIDE.md](REVIEW_GUIDE.md)：逐候选人工评分；
5. [DATA_AND_RESULTS.md](DATA_AND_RESULTS.md)：图片、Run、Release 和 Git 边界；
6. [reports/MOVIE60_STRICT_END_TO_END_REPORT.md](reports/MOVIE60_STRICT_END_TO_END_REPORT.md)：现有 Movie60 结果证据；
7. [runbooks/WINDOWS_INSTALL.md](runbooks/WINDOWS_INSTALL.md) 与 [runbooks/RUN_ONE_OR_BATCH.md](runbooks/RUN_ONE_OR_BATCH.md)：精简命令速查。

旧 Release 和本机中间资产只通过
[Movie60 版本与遗留资产索引](legacy/MOVIE60_RELEASE_HISTORY.md) 查找，不作为当前入口。

Movie60 v3 的开发/代理留出明细和最终部署冻结见
[reviews/movie60-v3/](reviews/movie60-v3/README.md)。

`adr/` 保存仍有效的架构决策。历史过程笔记不在当前 Git 树中；需要时从 Git 历史或本机 `local_data/` 追溯。
