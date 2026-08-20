# retarget-engine

> 私有交接发行仓库名为 `retarget-abillity`；Python 包、命令和 Artifact 合同继续使用稳定名称
> `retarget-engine` / `retarget_agent`，避免破坏既有 Run。

面向简体中文海报和中国业务图片的可回放图片重定向引擎。当前目标画布固定为
`1536×1536`，主流程是：共享保护分析 → 七种传统候选 → 确定性评分 → 视觉 Agent 复核 →
受控 AIGC 回退 → 人工校准。

## 当前能力

- OCR、人脸、人物、商品/Logo 候选、结构与显著性共享保护分析；
- `direct_warp`、`crop`、限额 `seam`、`seam_full`、限额 `mesh`、`mesh_full`、
  `seam_scale` 七种候选；
- 冻结 Generation Run、Evaluation Replay、Rule/Agent Decision、成本和资源记录；
- Rule Top1 强制高清复核，Agent challenger 只有在视觉证据明确且保护指标不退化时才能覆盖；
- 受预算、素材出域和幂等门禁保护的生成式回退；
- FastAPI 本地人工评审网页，支持 60 个任务、420 个候选逐张评分和人工理由。

## 边界

- 自动 Quality、视觉 Agent 和辅助模型建议都不是人工金标准；
- 近期商业海报只用于本地内部评测，不随 Git 分发；
- 当前是 Python 原型，没有生产鉴权、分布式队列或 Java 服务接口；
- `seam_full` 与 `mesh_full` 已实现，但是否更好必须由实际图片和人评决定。

## 安装

新机器建议直接执行 [从零开始](docs/START_HERE.md)，它包含冻结依赖、分析模型、内部 Release
和单图 Smoke。规则迭代见 [StrategyBundle 版本指南](docs/STRATEGY_BUNDLES.md)。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e ".[dev]"
.\.venv\Scripts\retarget-engine.exe --help
```

只安装依赖见 [Python 依赖安装](docs/runbooks/PYTHON_DEPENDENCIES.md)；新机器从 Clone 开始见
[新机器安装手册](docs/runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md)。

## 运行与评审

- 单张图从 OCR/YOLO 到七候选、评分、Agent：
  [单图全流程](docs/runbooks/SINGLE_IMAGE_END_TO_END.md)
- 当前 Movie60 人工评审：双击本机
  `deliverables/movie60-review/start-review.bat`，打开 `http://127.0.0.1:8766`
- 全仓验证：

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
```

## 文档入口

不要逐个猜文档版本。统一从 [docs/README.md](docs/README.md) 开始；开发交接看
[docs/HANDOFF.md](docs/HANDOFF.md)，数据、图片、Run 和 Git 边界看
[docs/DATA_AND_RESULTS.md](docs/DATA_AND_RESULTS.md)，当前实验结果看
[Movie60 技术报告](docs/reports/MOVIE60_STRICT_END_TO_END_REPORT.md)。

工程来源见 [ORIGIN.md](ORIGIN.md)，领域语言见 [CONTEXT.md](CONTEXT.md)。
