# 文档入口

本目录只保留当前开发、运行、评审和交接需要的文档。一次性诊断、旧 CN60 协议、失败实验和
重复交付包不作为当前入口；它们保留在本机 `local_data/`，不会随 Git 分发。

## 第一次接手

按下面顺序阅读即可，不需要遍历整个 `docs/`：

1. [`START_HERE.md`](START_HERE.md)：从 Clone、冻结依赖、Release 到单图执行的最短路径；
2. [`HANDOFF.md`](HANDOFF.md)：项目做什么、当前完成到哪里、代码从哪里看；
3. [`STRATEGY_BUNDLES.md`](STRATEGY_BUNDLES.md)：规则 v1/v2、A/B/C/D 范围和增量迭代；
4. [`runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md`](runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md)：
   新机器从 Clone 到跑通；
5. [`ARCHITECTURE.md`](ARCHITECTURE.md)：模块和 Artifact 数据流；
6. [`ALGORITHMS.md`](ALGORITHMS.md)：七条传统重定向路线；
7. [`SCORING.md`](SCORING.md)：Rule、视觉 Agent、AIGC 和人工等级关系；
8. [`REVIEW_GUIDE.md`](REVIEW_GUIDE.md)：如何完成 420 个候选人工评分；
9. [`DATA_AND_RESULTS.md`](DATA_AND_RESULTS.md)：数据、Run、结果图片和中间证据在哪里；
10. [`reports/MOVIE60_STRICT_END_TO_END_REPORT.md`](reports/MOVIE60_STRICT_END_TO_END_REPORT.md)：
   当前完成的 Movie60 技术结果。

## 按任务查文档

| 任务 | 文档 |
| --- | --- |
| 只安装 Python 依赖 | [`runbooks/PYTHON_DEPENDENCIES.md`](runbooks/PYTHON_DEPENDENCIES.md) |
| 新机器完整安装 | [`runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md`](runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md) |
| 跑一张图片全流程 | [`runbooks/SINGLE_IMAGE_END_TO_END.md`](runbooks/SINGLE_IMAGE_END_TO_END.md) |
| 看当前完成/未完成 | [`CURRENT_STATE.md`](CURRENT_STATE.md) |
| 看下一阶段 | [`ROADMAP.md`](ROADMAP.md) |
| 复现实验合同 | [`experiments/MOVIE_VISUAL60_STRICT_PROTOCOL.md`](experiments/MOVIE_VISUAL60_STRICT_PROTOCOL.md) |
| 看架构决策 | [`adr/`](adr/) |

## 文档状态规则

- `CURRENT_STATE.md` 只写当前事实；每个重大版本结束后更新。
- `HANDOFF.md` 只写接手所需信息，不堆放逐次实验日志。
- `reports/` 只保留当前基线的一份完整技术报告；已冻结数字不能静默改写。
- `experiments/` 保存可复现实验合同，不保存大量过程笔记。
- 一次性预审、调试记录和旧报告放 `local_data/docs_archive/`，不提交 Git。
- 图片、模型、Run 和商业素材均不进入 Git，边界见 [`DATA_AND_RESULTS.md`](DATA_AND_RESULTS.md)。
