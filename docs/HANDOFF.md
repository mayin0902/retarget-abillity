# Retarget Engine 开发交接

## 1. 项目一句话

系统不是固定选一种缩放算法，而是先对原图做一次共享保护分析，生成七种传统候选，再由 Rule
排序、视觉 Agent 做高清语义复核；传统候选都不够好且素材/预算允许时，才进入 AIGC 或人工队列。

## 2. 当前交接基线

| 项目 | 当前事实 |
| --- | --- |
| 目标画布 | 1:1，1536×1536 |
| 当前数据 | Movie Visual 60：人物、海报、影片画面、视频封面各15张 |
| 候选 | 60 Task × 7 方法 = 420 Candidate，Generation 0失败 |
| 规则评分 | 冻结 Evaluation + Rule 完整排名 |
| 已完成 Agent 实验 | Rule 锚定 v6：Rule Top1强制高清；challenger证据不足即回退 |
| 后续 Agent 默认配置 | `agent_skills/qwen4-selector/v7/skill.yaml`，自由文本输出简体中文 |
| 当前策略入口 | `strategies/movie60/v2/bundle.yaml`；v1/v2 均不可变且可直接重跑 |
| 人工状态 | 420候选尚未由项目人员完整评分；机器结果不是金标准 |
| AIGC | 困难20张执行一次：8张回图、12张失败；不得只报成功子集 |
| 当前本地入口 | `deliverables/movie60-review/` |

v7 是后续新运行的配置，并不意味着已经重跑并替代 v6 的冻结实验数字。完整实验事实见
[`reports/MOVIE60_STRICT_END_TO_END_REPORT.md`](reports/MOVIE60_STRICT_END_TO_END_REPORT.md)。

## 3. 最短阅读路径

1. [`START_HERE.md`](START_HERE.md)：Clone 到可运行单图；
2. [`STRATEGY_BUNDLES.md`](STRATEGY_BUNDLES.md)：规则版本与增量迭代；
3. [`ARCHITECTURE.md`](ARCHITECTURE.md)：模块和 Artifact；
4. [`ALGORITHMS.md`](ALGORITHMS.md)：七种方法；
5. [`SCORING.md`](SCORING.md)：Rule、Agent、AIGC、人评；
6. [`DATA_AND_RESULTS.md`](DATA_AND_RESULTS.md)：数据与结果在哪里；
7. [`runbooks/SINGLE_IMAGE_END_TO_END.md`](runbooks/SINGLE_IMAGE_END_TO_END.md)：真正跑一张图。

## 4. 代码导航

| 要改什么 | 入口 | 必须保持的边界 |
| --- | --- | --- |
| 数据集/权利 | `datasets.py`、`movie_visual60.py` | 像素不进 Git；下载/再分发/API 外发分开 |
| OCR/保护 | `analysis.py`、`protection_detectors.py` | 检测失败显式记录，不伪造空结果 |
| 重定向算法 | `methods/` | 方法不调用 UI、Agent 或 Provider |
| Run/恢复 | `runner.py`、`storage.py` | 已冻结 Candidate 不覆盖 |
| 可插拔策略 | `strategy.py`、`strategies/` | v1/v2不改写；每个派生Run保存策略快照 |
| 自动评分 | `evaluation.py`、`selector.py` | Proxy 不称为人工等级；范围来自 scoring.yaml |
| Agent | `agents.py`、`rule_anchored_review.py`、`agent_skills/` | Schema失败或证据矛盾回退Rule |
| AIGC | `providers/seedream.py`、`aigc_experiment.py`、`costing.py` | 预算/权限/幂等任一失败即拒绝 |
| 人工 UI | `movie60_review_app.py`、`web_movie60/` | 人评追加保存，不覆盖机器证据 |
| 人机校准 | `calibration.py` | 同级不进入排序正确率 |

Generation、Evaluation、Agent、AIGC、Review 是追加 Artifact 层；后层不能修改前层像素和结论。

## 5. 环境与验证

新机器执行：

```powershell
gh repo clone mayin0902/retarget-abillity G:\Projects\retarget-abillity
Set-Location G:\Projects\retarget-abillity
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
```

模型权重和业务图片不在 Git 中。依赖、模型和单图运行的完整步骤见 `docs/runbooks/`。

## 6. 当前图片和机器证据

- 完整60张/420候选：物化后 `local_data/movie60-review-v1/all60/`；
- 重点20张 Rule/Agent/AIGC：`local_data/movie60-review-v1/focus20/`；
- 可执行评分规则：Git 中 `strategies/movie60/`；历史评审规则快照仍在 Release；
- 每张图的候选、Rule排名、Agent理由、高清局部和AIGC状态都放在同一个任务目录；
- 旧 ZIP、失败构建和旧版本已移到 `local_data/deliverables_archive/`，不是交接入口。

详细路径约定见 [`DATA_AND_RESULTS.md`](DATA_AND_RESULTS.md)。

## 7. 修改算法的规则

1. 在 `MethodProtocol` 下实现，注册唯一 `method_id`；
2. 输出精确目标像素、`TransformRecord`、状态和资源记录；
3. 对同一冻结数据重新生成新 Run ID，不覆盖旧 Run；
4. 增加单元测试、Run 合同测试和真实图 Smoke；
5. 在 Calibration 20 看真实图片，再决定是否晋级；
6. 更新 `ALGORITHMS.md`，明确实现边界和已知风险。

## 8. 修改评分或 Agent 的规则

1. 项目人员先完成 Calibration 20 人工等级、细分项和理由；
2. 只读 Calibration 错误案例，复制当前 Bundle 创建新版本，绝不修改 v1/v2；
3. Rule完整排名和Rule Top1必须送入Agent；
4. Rule Top1与challenger都做高清整图和关键局部复核；
5. 核心内容、关键文字或主体计数退化时，不允许覆盖可用Rule A/B；
6. 在 registry 记录新版本哈希，冻结后 Validation 40只运行一次；
7. 人工和机器同级时不要求连续分相同，只检查同级分差是否异常。

## 9. 当前风险

- Full Seam/Full Mesh 技术实现完整，但尚无人工证据证明总体优于 Crop；
- OCR、商品和 Logo 是工程检测代理，可能漏检或误检；完整高清图与人工判断优先；
- 当前 Agent 实验没有证明可稳定覆盖 Rule；0次覆盖是保守门禁的真实结果；
- AIGC 回图率和可用率都偏低，且存在超时后的不确定计费；
- 商业海报没有公开再分发授权，不能上传 GitHub Release 或公开数据集；
- UI 没有生产鉴权；Java、队列、对象存储仍未实现。

## 10. 交接完成条件

- 接手人能按新机器手册安装并通过测试；
- 能按单图手册跑出共享分析、七候选、Evaluation、Rule和Agent证据；
- 能打开 `movie60-review`，理解 all60 与 focus20 的区别；
- 项目人员完成 Calibration20 和 Validation40 人工评审；
- 基于 Calibration 创建下一 Strategy 版本，冻结后只验证一次；
- Git 只提交代码、测试、配置、当前文档和无像素数据合同；不提交图片、Run、模型或旧包。
