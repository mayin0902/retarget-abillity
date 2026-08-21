# retarget-abillity

面向简体中文海报和业务图片的本地可回放重定向引擎。Python 包和 CLI 保持兼容名称 `retarget_agent` / `retarget-engine`。

主流程：共享保护分析 → 七种传统候选 → 候选逐张重检与 Rule 排名 → 视觉 Agent 挑战 → 高清 Rule-vs-challenger 门禁 → 可选 AIGC → 人工反馈。

## 当前能力

- direct warp、crop、受限/完整 seam、受限/完整 mesh、seam+scale；
- 当前 Windows CPU 检测栈：PP-OCRv6 small、D-FINE-HGNetV2-N、YuNet、Logo 候选；
- Detector、参考/无参考 Scorer、Rule、Selector、Agent backend、Prompt 和 Skill 均为白名单插件或不可变策略文件；
- StrategyBundle v1/v2/v2.1/v3/v3.1/v3.2/v3.2.1/v3.2.2 与旧 Run 兼容，每次 Evaluation 保存完整策略快照；
- 单图有参考评分、无参考技术检查、批量 Run、Agent Replay 和本地人工评审 UI。

## 从零开始

```powershell
git clone <私有仓库URL> retarget-abillity
cd retarget-abillity
PowerShell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 -PythonVersion 3.12
```

公司 pip 镜像留空位置、Python 未安装处理和手工等价命令见 [Windows 安装](docs/runbooks/WINDOWS_INSTALL.md)。

## 单图

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "D:\images\poster.jpg" -CaseId "poster-001"
```

单独评分已有候选：

```powershell
.\.venv\Scripts\retarget-engine.exe score reference source.jpg candidate.jpg `
  --output-dir local_data\scores\poster-001 `
  --strategy strategies\movie60\v3_2_2\bundle.yaml
```

完整命令见 [单张与批量运行](docs/runbooks/RUN_ONE_OR_BATCH.md)。

## 文档

- [开发操作手册：Clone、安装、单图、批量与 Rule-only](docs/DEVELOPER_OPERATION_MANUAL.md)
- [开发操作手册工程详解版](docs/DEVELOPER_OPERATION_MANUAL_DETAILED.md)
- [算法原理：重定向、检测比较、评分与 Agent](docs/DEVELOPER_ALGORITHM_PRINCIPLES.md)
- [算法变量与公式工程参考](docs/DEVELOPER_ALGORITHM_REFERENCE.md)
- [Movie60 当前数据、评分和 Agent 路线状态](docs/reviews/movie60-v3/CURRENT_DATA_AND_ROUTE_STATUS.md)
- [插件与策略迭代](docs/PLUGIN_STRATEGY_GUIDE.md)
- [七种算法](docs/ALGORITHMS.md)
- [人工评审](docs/REVIEW_GUIDE.md)
- [数据与结果边界](docs/DATA_AND_RESULTS.md)
- [当前 Movie60 技术结果](docs/reports/MOVIE60_STRICT_END_TO_END_REPORT.md)
- [Movie60 v3 Rule + Agent 代理验证](docs/reviews/movie60-v3/AGENT_COMBINED_REPORT.md)

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m pytest -q
```

自动分数和 Agent 建议不是人工金标准；真实商业素材、Run、模型权重和密钥不进入 Git。
