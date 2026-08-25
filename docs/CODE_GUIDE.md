# 代码架构与阅读指南

这份文档面向需要阅读、嵌入或继续开发 Retarget Engine 的 Python 开发者。它回答“从哪个
函数开始看、数据怎样流动、只迁一部分代码需要什么”。算法原理和公式见
[`ARCHITECTURE.md`](ARCHITECTURE.md)，安装与命令见 [`QUICKSTART.md`](QUICKSTART.md)。

## 1. 先看这 9 个入口

| 目标 | 首个文件 | 首个符号 |
|---|---|---|
| 直接嵌入图片处理 | `src/retarget_agent/api/retarget.py` | `retarget_image()` |
| 一次生成七候选 | `src/retarget_agent/api/retarget.py` | `generate_candidates()` |
| 只比较原图和候选 | `src/retarget_agent/api/scoring.py` | `score_pair()` |
| 理解保护分析 | `src/retarget_agent/analysis.py` | `ProtectionAnalyzerCore` |
| 理解七算法装配 | `src/retarget_agent/methods/__init__.py` | `built_in_methods()` |
| 理解自动评分 | `src/retarget_agent/evaluation.py` | `compute_proxy_metrics()` |
| 理解 Rule 排名 | `src/retarget_agent/rule_selection.py` | `materialize_rule_decisions()` |
| 理解/替换 AIGC API | `src/retarget_agent/providers/base.py` | `AIGCProvider.generate()` |
| 理解完整可审计流程 | `src/retarget_agent/runner.py` | `GenerationRunner.run()` |

只想复用能力时先读 `api/`；需要 Run、复现、UI 或 Agent 时再进入 Runner 和 CLI。

## 2. 三层接口

```text
纯计算接口（不写文件）
retarget_agent.api
        │
        v
可审计文件接口
GenerationRunner / score_image
        │
        v
用户入口
CLI / PowerShell / Review UI
```

纯计算接口只接收 RGB 数组和参数并返回结果。文件接口负责输入冻结、Strategy 快照、指标
JSON、Overlay、报告和恢复运行。CLI 只解析命令，不复制算法。

### 2.1 先建立数据对象的关系

```text
TaskSpec
  │  source + target
  v
AnalysisOutput（内存）
  │  Runner持久化
  v
AnalysisArtifact
  │
  v
CandidateRecord ──引用──> TransformRecord
  │
  v
MetricBundle
  │
  v
RuleDecisionRecord
```

| 对象 | 开发时把它理解成什么 | 重点字段 |
|---|---|---|
| `TaskSpec` | “哪张图要变成什么尺寸” | `source`、`target`、稳定 `task_id` |
| `AnalysisOutput` | Analyzer 的内存返回 | `regions`、两个 Map、Analyzer ID、warning |
| `AnalysisArtifact` | 同一分析结果的 Run 内不可变索引 | Map 的 `ArtifactRef`、配置 Hash |
| `CandidateRecord` | 某个方法的生成事实 | 输出、状态、失败类型、Transform 引用、耗时 |
| `TransformRecord` | 方法做过什么和风险多大 | `operations`、`risk_features`、warning |
| `MetricBundle` | 某个 Evaluator 对一个候选的结构化测量 | Evaluator ID/版本、`metrics` |
| `RuleDecisionRecord` | 一个 Task 的完整 Rule 排名 | `candidate_ranking`、Top1、失败候选、Strategy SHA |

`AnalysisOutput` 和 `MethodOutput` 是运行时数据类；`AnalysisArtifact` 和 `CandidateRecord` 是文件
流程中的 Pydantic 冻结记录。调试时先判断手里的是“内存返回”还是“落盘索引”，不要把 Map
数组和指向 `.npy` 的 `ArtifactRef` 混用。

### 2.2 常用函数的输入和输出

| 函数 | 主要输入 | 主要输出 | 典型调用方 |
|---|---|---|---|
| `ProtectionAnalyzerCore.analyze()` | RGB、`TaskSpec`、可选 Guidance/Provided Regions | `AnalysisOutput` | Public API、Runner、Reference Scoring |
| `CandidateMethod.generate()` | RGB、Task、Analysis Artifact、两个 Map、方法配置 | `MethodOutput` | `GenerationRunner`、Public Retarget API |
| `generate_candidates()` | RGB、Target、场景、方法列表 | 多个 `RetargetResult` | 嵌入式调用、Smoke |
| `score_pair()` | 原图 RGB、候选 RGB、可选 Transform/Strategy | `PairScoreResult` | 服务内评分、reference 文件评分 |
| `compute_proxy_metrics()` | 两张图、两组 Region、Task、Transform | 原始指标字典 | Reference Scorer Adapter |
| `apply_human_aligned_policy()` | 原始指标、场景、方法、Scoring Policy | 分数、惩罚和门禁后的新指标字典 | `human_aligned_proxy_v3` |
| `materialize_rule_decisions()` | Run、Evaluation、Strategy | 每个 Task 的 `RuleDecisionRecord` | Evaluation、Agent、UI |
| `score_image()` | 文件路径、模式、Strategy、输出目录 | `report.json/md`、Overlay、输入和快照 | CLI `score` |

读调用链时先看接口的返回类型，再追具体实现。Runner 负责把这些返回转换成文件，不应在 CLI、
UI 或报告代码里重新实现检测、评分或排序。

## 3. Public Retarget API

输入契约：

- `image` 必须是 `numpy.ndarray`；
- 形状必须为 `(height, width, 3)`；
- 数据类型必须为 `uint8`，颜色顺序为 RGB；
- `target=(width, height)`，不是 `(height, width)`；
- 默认检测器是当前公司 CPU Profile，首次构造会加载模型。

```python
import numpy as np
from PIL import Image

from retarget_agent.api import retarget_image

source = np.asarray(Image.open("poster.jpg").convert("RGB"))
result = retarget_image(
    source,
    target=(1080, 1920),
    method="crop",
    scene="movie_poster",
)
if result.image is None:
    raise RuntimeError(result.error_summary)
Image.fromarray(result.image).save("crop.png")
```

`RetargetResult` 的主要字段：

| 字段 | 含义 |
|---|---|
| `image` | 成功候选 RGB 数组；失败为 `None` |
| `method` | 实际方法 ID |
| `status` | `SUCCESS`、`UNSAFE` 或 `FAILED` |
| `transform` | 可审计的裁剪、缩放、Seam/Mesh 风险记录 |
| `regions` | 原图 OCR/人脸/人物/商品/Logo 候选区域 |
| `warnings` | 检测器降级、算法风险等非致命信息 |

`UNSAFE` 表示技术上生成了图片但风险超阈值，不等于图片一定不可用；最终仍应由 Rule、Agent
和人工视觉判断结合。

## 4. 一次生成多个候选

```python
from retarget_agent.api import generate_candidates

results = generate_candidates(
    source,
    target=(1536, 1536),
    scene="movie_poster",
    methods=("direct_warp", "crop", "seam", "mesh"),
)
```

一次调用只对原图做一次保护分析，所有方法共享同一组区域和 Map。单个方法失败会返回一条
`FAILED` 结果，不会抹掉其他方法。返回顺序与 `methods` 输入顺序一致。

需要批量处理时应复用 Analyzer，避免每张图重新加载模型：

```python
from retarget_agent.analysis import ProtectionAnalyzerCore
from retarget_agent.api import generate_candidates
from retarget_agent.config import AnalysisConfig

config = AnalysisConfig(
    detector_mode="required",
    detector_suite_plugin="company_cpu_v2",
)
analyzer = ProtectionAnalyzerCore(config)

for image in images:
    results = generate_candidates(
        image,
        (1536, 1536),
        scene="movie_poster",
        analyzer=analyzer,
    )
```

## 5. Public Scoring API

```python
from retarget_agent.api import score_pair

score = score_pair(
    source,
    candidate,
    scene="movie_poster",
    transform=result.transform,
)
print(score.quality_score, score.grade, score.business_success)
print(score.content_fidelity, score.visual_integrity, score.composition)
print(score.gates)
```

`score_pair()` 不复制输入、不创建目录、不写报告。原图和候选分别执行同一检测，然后比较：

- OCR 文字召回与顺序相似度；
- 人脸、人物、商品、Logo 候选和普通目标数量；
- ORB 内容、清晰度、边缘、颜色和结构线；
- 构图与保护区域边界；
- `TransformRecord` 中的裁剪、拉伸、Seam/Mesh 风险；
- 当前 Strategy 的权重、A/B/C/D 阈值与门禁。

仓库模式从 `strategies/registry.yaml` 解析唯一 active Strategy；Wheel 独立安装且找不到仓库时，
Public Scoring API 回退到包内同哈希的 `retarget@1.0.0` 快照。显式传入 Strategy 时永远优先，
而 registry 存在但非法时会失败，不会用包内快照掩盖配置错误。

若不传 `transform`，仍可比较两张图片，但方法特有的裁剪/形变风险和方法门禁可能不可用。
如果要评价 Public Retarget API 刚生成的候选，应把 `RetargetResult.transform` 原样传入。

## 6. 纯计算评分与审计评分的区别

| 接口 | 写文件 | 适合场景 |
|---|---:|---|
| `score_pair()` | 否 | 服务内调用、批量处理、单元测试 |
| `score_image()` | 是 | 人工复核、问题复现、交接证据 |
| CLI `score reference` | 是 | 命令行用户 |

`score_image()` 有两条路径：

```text
有 source（reference 模式）
→ 调用 score_pair()
→ 保存原图/候选比较指标

没有 source（standalone 模式）
→ 调用 Strategy 指定的 no-reference scorer
→ 只保存单图技术指标
```

两条路径共享文件审计和报告输出层，都会保存输入副本、检测框 Overlay、Strategy 快照、
`report.json` 和 `report.md`。standalone 不是 reference 的简化参数形式：它没有原图，不能计算
OCR 召回、人物数量保持或内容语义损失。

## 7. Protection Analyzer 调用链

```text
RGB image
  + AnalysisConfig
  + optional provided RegionRecord
  + optional HumanGuidance
          │
          v
ProtectionAnalyzerCore
  ├─ gradient / contrast / center saliency
  ├─ Detector Suite
  │    ├─ PP-OCRv6
  │    ├─ D-FINE
  │    ├─ YuNet
  │    └─ Logo Candidate CV
  ├─ provided regions
  └─ human guidance
          │
          v
AnalysisOutput
  ├─ importance_map
  ├─ tolerance_map
  ├─ regions
  ├─ analyzer_ids
  └─ warnings
```

`ProtectionAnalyzerCore` 不读取 Dataset。`SharedProtectionAnalyzer` 是 Runner 使用的 Dataset
Adapter：它从 `regions.csv` 读取区域，转换为 `RegionRecord` 后交给 Core。两者产生相同类型的
`AnalysisOutput`。

## 8. 为什么原图和候选都要检测

Generation 阶段原图只检测一次，目的是让七种算法共享保护先验。评分阶段必须再检测候选，
否则无法知道文字、人脸或商品在变换后是否消失。典型七候选流程是：

```text
原图检测 1 次
+ 每个成功候选检测 1 次
= 最多 8 次检测
```

同一 `ProtectionAnalyzerCore` 会按图像 SHA 缓存检测结果；重复分析同一数组不会再次推理。

## 9. 一张图片的完整文件流程

```text
cli.py: run_image_command()
        ↓
simple_workflow.py: run_image() / execute_images()
        ↓
materialize_image_dataset()
        ↓
service.py: generate_from_config()
        ↓
runner.py: GenerationRunner.run()
        ↓
analysis.py: SharedProtectionAnalyzer → ProtectionAnalyzerCore
        ↓
methods/*: CandidateMethod.generate()
        ↓
service.py: evaluate()
        ↓
evaluation.py: compute_proxy_metrics()
        ↓
human_aligned_scoring.py: apply_human_aligned_policy()
        ↓
rule_selection.py: 冻结完整 Rule 排名和 Top1
```

## 10. 七算法统一接口

所有算法满足 `protocols.py::CandidateMethod`：

```python
generate(
    image,
    task,
    analysis,
    importance_map,
    tolerance_map,
    guidance,
    config,
    context,
) -> MethodOutput
```

`methods/__init__.py::built_in_methods()` 是唯一内置算法 Registry。方法概览：

| ID | 实现文件 | 作用 |
|---|---|---|
| `direct_warp` | `methods/direct_warp.py` | 直接缩放基线 |
| `crop` | `methods/crop.py` | 保护区域感知裁剪 |
| `seam` | `methods/seam_limited.py` | 限定 Seam 数的稳定基线 |
| `seam_full` | `methods/seam.py` | 完整前向能量 Seam Carving |
| `mesh` | `methods/mesh_legacy.py` | 受限轴向 Mesh |
| `mesh_full` | `methods/mesh.py` | 二维保护 Mesh 优化 |
| `seam_scale` | `methods/seam.py` | Seam 与缩放的混合路线 |

## 11. Crop 阅读路径

从 `ProtectionCropMethod.generate()` 开始：先根据目标比例计算裁剪窗尺寸，再遍历尺度和位置，
用 importance map、MUST_KEEP 覆盖率和中心偏差评分。最佳窗裁剪后才缩放到目标尺寸。
`TransformRecord.risk_features` 保存必须保护区域损失和裁剪比例，供评分器复核。

## 12. Seam 阅读路径

`LimitedSeamMethod` 是固定最大删除数量的稳定基线。`FullSeamMethod` 在代理分辨率计算前向
能量 Seam，将坐标映射回原分辨率并继续变换；importance 提高穿越重要区域的代价，tolerance
降低可删除区域代价。风险记录包含删除 Seam 的平均/峰值重要度和局部各向异性。

## 13. Mesh 阅读路径

`AxisAlignedMeshMethod` 分别压缩行列，是历史稳定基线。`FullMeshMethod` 联合优化二维网格，
目标函数同时包含目标画布、保护权重、均匀锚点和平滑项，并检查网格翻折和最大轴向比例。

## 14. Rule 评分和排名

`compute_proxy_metrics()` 只计算可复现指标；`apply_human_aligned_policy()` 根据不可变 Strategy
重新组合权重、应用软惩罚和门禁。`rule_selection.py` 读取所有候选指标，按 Strategy 指定的完整
排序键生成唯一 Rule 决策。Agent 和 UI 消费这份冻结决策，不自行按 Quality 重排。

## 15. Agent 调用链

Agent Replay 的输入包括原图、候选图、完整 Rule 排名、Rule Top1、指标、冻结 Skill、Knowledge
和 Prompt。Agent 只能返回候选的完整排序、建议 Top1、置信度与理由；Schema 失败或证据矛盾时
回退 Rule。Agent Run 是附加证据，不覆盖 Generation、Evaluation 或人工记录。

## 16. AIGC Provider 调用链

```text
CLI / RetargetApplicationService
        │ AIGCGenerationRequest + provider_id
        v
generation_execution.execute_generation()
        │
        ├─ execute=False：只验证 Provider 已注册，返回 planned，不读取密钥、不写文件
        │
        └─ execute=True
             ├─ 校验本地源图 SHA-256
             ├─ plugin_catalog.generation_providers.get(provider_id)
             ├─ Factory(AIGCProviderRuntime)
             ├─ Adapter.generate(request)
             ├─ 校验输出路径、SHA-256、图片解码和实际尺寸
             └─ executions/<request-id>.json
```

通用接口只有 `capabilities()` 和 `generate()`。`AIGCGenerationRequest` 包含源图、目标尺寸、
Prompt 和稳定 ID；不包含 API Key，也不包含公司的素材外发规则。Endpoint、鉴权、模型名、
请求 JSON、异步轮询和厂商错误由 Adapter 实现。

该调用链结束于生成图片和 `execution.json`，不会把图片自动写回已有 Run/Evaluation，也不会
自动挑战 Rule Top1。评价图片使用 Public Scoring API；是否把它加入最终路由由上层业务编排
决定。

`AIGCProviderRuntime` 提供输出目录、缓存目录和超时；预算、调用者幂等标签均可为 `None`。
`AIGCProviderResult` 的成本字段也允许 `None`。通用执行模块一定记录耗时、输出 SHA-256 和
明确成功/失败，因为后续评分必须能够定位同一张像素结果。

当前 SeedDream 阅读顺序：

1. `providers/base.py`：先理解稳定接口；
2. `plugin_catalog.py::built_in_plugin_catalog()`：查看 `seedream_api` 注册；
3. `providers/seedream.py::SeedDreamAIGCAdapter`：通用请求到厂商请求的映射；
4. `providers/seedream.py::SeedDreamProvider`：HTTP、下载、内容校验和其自带的耐久幂等；
5. `generation_execution.py`：调用方共有的审计记录；
6. `service.py::execute_external_generation()` 和 CLI `generation run`：外部入口。

历史 `aigc_experiment.py` 只保留 Movie60 冻结实验与报表兼容；其中的实际调用也已经通过
`generation_providers` 接缝。新业务不能复制该实验文件来接新 API，应按
`EXTENSION_GUIDE.md` 新增 Adapter。

## 17. UI 为什么与算法解耦

`review_workspace.py` 把 Run、Movie60 或外部候选转换为统一 Task/Candidate JSON；
`unified_review_app.py` 只提供 HTTP；前端只读取统一模型和索引图片。更换算法、Strategy 或
Agent Run 不需要改页面结构。

## 18. 如何新增算法

本指南只负责定位阅读入口：先看 `methods/__init__.py::built_in_methods()`，再看一个现有
`CandidateMethod.generate()` 和 `TransformRecord` 的落盘位置。新增、注册、Profile、测试与
真实图 Smoke 的标准步骤只维护在 [`EXTENSION_GUIDE.md` §7](EXTENSION_GUIDE.md#7-新增重定向算法)。

## 19. 如何新增评分指标或 Detector

阅读评分先从 `evaluation.py::compute_proxy_metrics()` 开始；阅读检测器从
`protection_detectors.py` 和 `plugin_catalog.py::built_in_plugin_catalog()` 开始。新增实现、
注册、缺测语义、Strategy 版本和 Replay 步骤只维护在
[`EXTENSION_GUIDE.md` §3～§5](EXTENSION_GUIDE.md#3-新增-detector-suite)。

## 20. 最小迁移清单

只迁图片处理：

```text
retarget_agent/api/retarget.py
retarget_agent/analysis.py
retarget_agent/methods/
retarget_agent/models.py
retarget_agent/protocols.py
retarget_agent/config.py
retarget_agent/protection_detectors.py（需要完整保护检测时）
```

只迁评分：

```text
retarget_agent/api/scoring.py
retarget_agent/evaluation.py
retarget_agent/human_aligned_scoring.py
retarget_agent/analysis.py
retarget_agent/strategy.py
retarget_agent/protection_detectors.py
一个不可变 StrategyBundle
```

Movie60、Release 打包、Review UI、Agent、AIGC、Benchmark 和 GenerationRunner 都不是纯计算
接口的必需依赖。

只迁 AIGC Provider 执行：

```text
retarget_agent/providers/base.py
retarget_agent/generation_execution.py
retarget_agent/plugin_catalog.py
一个具体 Provider Adapter
retarget_agent/registry.py
```

## 21. 测试入口

```powershell
# Public API 与 Analyzer Core
python -m pytest -q tests\test_public_api.py

# 七算法
python -m pytest -q tests\test_methods.py

# 评分与插件
python -m pytest -q tests\test_evaluation.py tests\test_plugins_and_image_scoring.py

# 可替换 AIGC Provider（全部使用 Fake，不产生费用）
python -m pytest -q tests\test_generation_providers.py tests\test_seedream_provider.py

# 完整回归
python -m pytest -q
ruff check src tests scripts
```

单元测试可用程序化小图验证形状、确定性和失败处理；算法效果结论必须来自真实图片或冻结
评测集。
