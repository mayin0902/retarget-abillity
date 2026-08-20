# 当前状态

更新时间：2026-08-20（Asia/Shanghai）

## 已完成

- 建立干净的私有交接仓库 `retarget-abillity`，稳定 Python 包名为 `retarget-engine`；
- 实现共享 OCR、人脸、人物、商品/Logo、结构和显著性保护分析；
- 实现七种传统候选：Warp、Crop、限额/完整 Seam、限额/完整 Mesh、Seam+Scale；
- 实现冻结 Generation、Evaluation、Rule、Agent、AIGC、成本资源和人工 Review Artifact；
- 完成 Movie Visual 60 的 60×7=420 候选生成，0生成失败；
- 完成严格自动 Evaluation、Rule完整排名和 Rule 锚定 Agent v6；
- 完成困难20张的一次性AIGC实验：8回图、12失败；
- 建立 `deliverables/movie60-review/`：完整60张/420候选与重点20张分开评审；
- 人工 UI 支持逐候选 A/B/C/D、六项细分、问题原因、自由理由、断点草稿和追加保存；
- 后续 Agent v7 自由文本统一输出简体中文，稳定字段和 reason code 保持英文。
- 新增不可变 StrategyBundle v1/v2：评分、A/B/C/D 范围、Rule 排名、覆盖门禁和 Skill 可插拔；
- Evaluation、Agent、严格复核均保存实际策略文件与 SHA-256 快照；
- 新机器 bootstrap、模型哈希下载、Movie60 Release 安全物化和单图七方法流程已实测。

## 最新冻结实验

| 项目 | 结果 |
| --- | --- |
| Run | `runs/movie60-square-v1-20260818` |
| Generation | 60 Task、420 Candidate、七方法齐全、0 FAILED |
| 自动 Evaluation | 420/420 |
| Rule锚定Agent | 60/60 Rule Top1高清；45个不同challenger配对 |
| Agent最终A/B/C | 10/15/35，A+B 25/60 |
| Agent覆盖Rule | 0/60；证据不足时保守回退 |
| AIGC计划 | 20个困难Task，8成功回图、12失败 |
| AIGC人工已确认 | `still_003` 为A；其余仍需项目人员复评 |

上述 A/B/C 是机器高清预审，不是业务人工金标准。最新完整报告为
[`reports/MOVIE60_STRICT_END_TO_END_REPORT.md`](reports/MOVIE60_STRICT_END_TO_END_REPORT.md)。

## 尚未完成

- 项目人员对 Calibration 20 的全部候选完成金标准评分；
- 项目人员对 Validation 40 的全部候选完成冻结验证评分；
- 生成正式的人机校准报告和同级稳定性报告；
- 根据 Calibration 人工反馈创建新的 v3，而不是原地修改当前技术基线 v2；
- 证明 Full Seam/Full Mesh 在人工可用率上优于现有折中路线；
- 收紧 AIGC 的文字、Logo、人脸/肢体和结构放行门禁；
- 生产鉴权、队列、对象存储和 Java 接口。

## 当前入口

- 文档：`docs/START_HERE.md`；
- 安装：`docs/runbooks/CODE_AGENT_NEW_MACHINE_SETUP.md`；
- 单图运行：`docs/runbooks/SINGLE_IMAGE_END_TO_END.md`；
- 数据与证据：`docs/DATA_AND_RESULTS.md`；
- 人工评审：先运行 `scripts/materialize_movie60_release.py`，再执行 Release 中的
  `start-review.bat`。

## 版本边界

- Strategy v1 与 v2 均在 `strategies/registry.yaml` 固定哈希，不得原地修改；
- v6 是当前已完成实验的冻结 Agent 版本；v2 Bundle 内的 Skill 2.3.0 是后续默认配置，尚未
  产生可替代 v6 冻结实验的完整人工校准结果；
- 旧 CN60 五方法实验和一次性审计保存在 `local_data/docs_archive/`，不作为当前主线；
- 图片和评审证据通过私有、校验和固定的 Release 共享；Run、模型权重仍不进入 Git。
