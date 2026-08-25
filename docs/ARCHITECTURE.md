# 架构与算法原理

本文解释 Retarget Engine 为什么采用“多候选生成 + Rule 排名 + Agent 建议 + 人工复核”，以及
一张图片在系统内部怎样流动。具体命令见 [`QUICKSTART.md`](QUICKSTART.md)，代码入口见
[`CODE_GUIDE.md`](CODE_GUIDE.md)，扩展步骤见 [`EXTENSION_GUIDE.md`](EXTENSION_GUIDE.md)。

## 1. 项目解决什么问题

图片重定向不是简单地把宽高改成目标值。一个可交付结果需要同时满足四个目标：

1. **尺寸满足**：输出像素必须严格等于目标 `width × height`；
2. **内容保持**：主人物、商品、主标题、Logo 和关键数字不能错误丢失；
3. **视觉自然**：内容即使都在，也不能出现脸变形、肢体折断、文字拉伸或刚性物体弯曲；
4. **构图可用**：主体不能严重贴边，画面不能明显失衡，也不能只满足指标却无法直接发布。

不同算法只能覆盖其中一部分目标。Crop 几何自然但可能丢内容；Direct Warp 保留整图但可能
整体变胖或变瘦；Seam 能删除低重要区域，但可能造成局部条带或折叠；Mesh 能给保护区域更高
刚性，却仍可能让人体、文字或商品局部弯曲。因此系统不假设存在一种永远最好的算法，而是用
同一份保护分析生成多个候选，再对冻结结果择优。

### 1.1 统一领域术语

- **Source**：由 ID、SHA-256、像素尺寸和来源信息确定的不可变输入图；
- **Target**：调用方要求的输出画布，可为任意受支持宽高，不限定 1:1；
- **Task**：一个 Source 到一个 Target 的稳定请求；
- **Shared Protection Analysis**：同一 Task 中所有方法共享的 OCR、人脸、人物、商品、
  Logo 候选、结构和显著性分析；
- **Protection Region**：包含位置、语义、重要度与形变容忍度的证据区域，不等同于人工真值；
- **Candidate**：一种方法对一个 Task 的不可变输出；技术成功不代表业务质量通过；
- **Transform**：算法做过的裁剪、缩放、Seam 或 Mesh 操作及其风险记录；
- **Evaluation**：在候选像素不变的前提下，用某个 Strategy 产生的一套指标和 Rule 决策；
- **Rule**：对冻结 Candidate 执行的确定性指标、门禁和完整排序；
- **Agent**：读取原图、候选、Rule 排名和冻结 Skill/Knowledge 后给出的视觉语义建议；
- **AIGC Provider**：把统一生成请求映射到某个外部图片生成接口的 Adapter；是否调用由上层
  显式决定；
- **Review**：人工对冻结 Candidate 给出的 A/B/C/D 和理由；大模型预审不属于人工金标；
- **Release**：从代码、Strategy、Run、Evaluation、Agent Run 和 Review 中选择部分资产形成的
  冻结交付快照。

## 2. 完整处理流程

```text
CLI: run image / run batch
          │
          v
Simple Workflow ──冻结──> Dataset + run.yaml
          │
          v
Generation Runner
  ├─ 原图共享保护分析：OCR / 人脸 / 人物 / 商品 / Logo候选 / 显著性
  ├─ 七方法并列生成，任何失败保留记录
  └─ Candidate + Transform + 性能 + 可视化
          │
          v
Evaluation
  ├─ 每张成功候选再次执行同一检测
  ├─ 原图证据 vs 候选证据
  ├─ Scorer 插件 + Strategy 权重/惩罚/门禁
  └─ Rule Selection：冻结完整排名与 Top1
          │
          ├─────────────> results/.../result.png + result.json
          │
          ├─ 可选 Agent Replay
          │    ├─ 原图、候选图、Rule Top1、Rule完整排名
          │    ├─ Skill + Knowledge + Prompt
          │    └─ 中文建议、完整候选排序、置信度、理由代码
          │
          └─ 可选显式 AIGC
               ├─ AIGCGenerationRequest
               ├─ generation_providers 选择 Adapter
               └─ 图片 + SHA-256 + 耗时 + 可选成本
          │
          v
ReviewWorkspaceAdapter ──> FastAPI ──> 浏览器人工评审页
          │                                  │
          └<──── CSV 当前结果 + JSONL 追加历史 ┘
```

### 2.1 每个阶段的输入和输出

| 阶段 | 主要输入 | 主要输出 | 是否修改图片 |
|---|---|---|---:|
| Dataset materialization | 图片路径、Source 元数据、Target | `TaskSpec`、Dataset 文件、`run.yaml` | 否 |
| Protection Analysis | 原图、Task、可选人工区域 | `RegionRecord[]`、importance/tolerance map | 否 |
| Generation | 原图、Analysis、Target、方法参数 | Candidate 图片、`TransformRecord`、耗时与状态 | 是 |
| Evaluation | 原图、Candidate、Transform、Strategy | `MetricBundle`、回归和门禁证据 | 否 |
| Rule Selection | 同一 Task 的所有 Candidate 与 Metrics | 完整排名、Top1、失败候选列表 | 否 |
| Agent Replay | 原图、候选、Rule 证据、Skill/Knowledge/Prompt | 建议排名、Challenger、理由、置信度 | 否 |
| AIGC execution | 显式请求、Provider 配置、源图 | 新生成图片、执行审计记录 | 是 |
| Review | 冻结图片和机器证据 | 人工 A/B/C/D、理由和追加历史 | 否 |

这张表也是第一层排障索引：图片像素已经坏了先查 Generation；图片正常但分数不合理先查
Evaluation；每个分数都合理但最终 Top1 不合理再查 Rule Selection；页面与冻结文件不一致才查
Review Adapter 或前端。

### 2.2 前后端边界

前端只认识统一的 Task/Candidate JSON 和受索引图片 URL，不解析 Run、Movie60 或外部候选目录。
后端 `ReviewWorkspaceAdapter` 把不同来源转换成同一页面模型，`unified_review_app.py` 只负责本地
HTTP 和静态页面。评分公式、Rule 排名和 Agent 决策都不在前端执行，所以算法目录变化不需要
重写页面，前端也不能自行按 Quality 重排候选。

## 3. 核心模块与职责边界

| 模块 | 输入 | 输出 | 不负责什么 |
|---|---|---|---|
| `api/retarget.py` | RGB、Target、方法 ID、可选 Analyzer | 内存中的一个或多个 `RetargetResult` | 不建立 Run，不写文件，不做最终 Rule 排名 |
| `api/scoring.py` | 原图 RGB、候选 RGB、可选 Transform | `PairScoreResult` | 不生成候选，不保存报告 |
| `analysis.py` | RGB、`TaskSpec`、可选区域/人工指导 | 内存 `AnalysisOutput` | 不裁图、不评分、不选择方法 |
| `runner.py` | Dataset、RunConfig、方法 Registry | 落盘的 Analysis、Candidate、Transform、Run manifest | 不解释人工等级，不调用 Agent |
| `evaluation.py` | 原图、候选、原图/候选检测、Transform | 可复现原始指标和 `MetricBundle` | 不改变候选像素，不决定人工等级 |
| `human_aligned_scoring.py` | 原始指标、场景、方法、Scoring Policy | 重组分数、软惩罚、等级门禁证据 | 不重新检测、不按 Task ID 特判 |
| `rule_selection.py` | 全部 Candidate 与 Metrics、Selection Policy | 唯一完整 Rule 排名和 Top1 | 不重新算指标，不改候选 |
| `strategy.py` | Bundle 路径或 Registry | 校验后的 Strategy、SHA、快照 | 不执行任意 YAML Python 路径 |
| `agents.py` | 冻结图片、Rule 排名、Skill/Knowledge/Prompt | Agent Run 和建议决策 | 不生成传统候选，不写人工金标 |
| `generation_execution.py` | 统一 AIGC Request、Provider ID | 图片与无密钥执行记录 | 不决定素材能否外发，不自动写回 Run |
| `review_workspace.py` | Run/Movie60/外部候选目录 | 统一页面模型、Review sidecar | 不重新评分，不修改冻结证据 |

### 3.1 核心数据对象怎样串起来

```text
TaskSpec
  ├─ source：处理哪张图
  └─ target：变成什么尺寸
        │
        v
AnalysisOutput（内存）
        │ Runner 落盘
        v
AnalysisArtifact
  ├─ regions
  ├─ importance_map
  └─ tolerance_map
        │
        v
CandidateRecord ───────> candidate.png
  └─ transform ref ────> TransformRecord
        │
        v
MetricBundle
        │
        v
RuleDecisionRecord
  ├─ candidate_ranking
  └─ selected_candidate_id
```

- `TaskSpec` 回答“哪张图要变成什么尺寸”；
- `AnalysisOutput` 是 Analyzer 的内存返回，`AnalysisArtifact` 是 Runner 将同一证据持久化后的
  可审计记录；
- `CandidateRecord` 回答“某个算法生成了什么、是否成功、输出在哪里”；
- `TransformRecord` 回答“算法具体做了什么、有哪些可量化风险”；
- `MetricBundle` 回答“原图与候选比较后测到了什么”；
- `RuleDecisionRecord` 回答“所有候选按哪套证据排序，最后为什么选它”。

把这些对象拆开，是为了允许只换评分而不重生成图片、只换 Agent 而不重算 Rule，也允许任何
结论沿着 ID、SHA 和快照回到当时的像素与参数。

## 4. 共享保护分析

### 4.1 原图和候选为什么都要检测

Generation 前只对原图检测一次，目的是让七种算法共享同一组保护先验，避免某种方法因自己
重新检测而获得不同输入。Evaluation 再对每张成功候选检测一次，才能比较文字、人脸、人物、
商品和 Logo 候选后来是否仍能被检测到。

因此，一个 Task 有七个成功候选时，典型调用量是：

```text
原图检测 1 次 + 候选检测 7 次 = 最多 8 次检测
```

`ProtectionAnalyzerCore` 按图像 SHA 缓存结果，同一实例重复分析同一像素不会再次推理。Core
返回 `AnalysisOutput`；Runner 将 Map、Region 和 Analyzer ID 保存为 `AnalysisArtifact`。

### 4.2 RegionRecord 表达什么

| 字段 | 含义 | 对算法的影响 |
|---|---|---|
| `rect` | 区域在原图中的像素坐标 | 决定保护 Map 的覆盖位置 |
| `kind` | `MUST_KEEP`、`PREFER_KEEP`、`RIGID`、`REMOVABLE` 等 | 表达保留或可删除倾向 |
| `label` / `semantic_type` | text、face、person、product、logo_candidate 等 | 用于内容计数和场景解释 |
| `confidence` | 检测器对该区域的可信度 | 是证据强弱，不是是否存在的真值 |
| `importance` | 应多努力保留，范围 0～1 | 越高，Crop/Seam/Mesh 越应避开破坏 |
| `tolerance` | 可承受删除或形变的程度，范围 0～1 | 越高，越适合承担变化 |
| `source` | Detector、Dataset 标注或 Human Guidance | 用于追查证据来源 |
| `attributes` | OCR 文本、识别置信度等扩展字段 | 用于后续 Reference 比较 |

人脸、主文字和显著商品通常需要高 importance、低 tolerance；明确可删除背景则相反。这里
记录的是当前分析器的判断，不能把它描述为人工框或 Ground Truth。

### 4.3 importance map 与 tolerance map

没有检测区域时，Core 先组合梯度、局部对比度和中心先验：

```text
importance = weighted_mean(gradient, local_contrast, center_prior)
tolerance  = 1 - importance
```

随后检测区域和人工指导对局部 Map 做上下限修正：保护区域提高 importance、降低 tolerance；
`REMOVABLE` 区域降低 importance、提高 tolerance。

通俗地说：

```text
importance map = 哪些地方最好不要删、不要大幅变形
tolerance map  = 哪些地方更适合承担比例变化
```

Crop 用 importance 评价裁剪窗口；Seam 提高穿越高 importance 区域的能量，并降低高 tolerance
区域的能量；Mesh 用保护权重强调局部刚性。七种方法共享 Map，含义才可比较。

### 4.4 当前 Detector Suite

默认 Windows CPU Profile 位于 `protection_detectors.py`：

- PP-OCRv6 small：文字多边形、识别文本和置信度；
- D-FINE nano COCO：人物、商品类和普通目标框；
- YuNet：人脸框；
- Logo Candidate CV：紧凑显著标记区域，只判断“疑似 Logo”，不识别品牌身份。

这些输出既供 Generation 的保护 Map 使用，也供 Evaluation 比较原图和候选。Detector Suite
改变后，如果只影响候选重检，应新建 Evaluation；如果生成算法也使用了新的原图保护区域，
还必须新建 Generation Run。

### 4.5 原图与候选怎样比较

OCR 先执行 Unicode NFKC 归一化、大小写折叠，并去掉非字母数字字符：

```text
R_char = Σ_c min(count_source(c), count_candidate(c)) / |source_text|
R_seq  = SequenceMatcher(source_text, candidate_text)
Text   = weighted_mean(R_char×0.65, R_seq×0.35)
```

`R_char` 回答“原图字符有多少还能在候选中找到”，不要求位置相同；`R_seq` 补充字符顺序。

人物、人脸、商品和 Logo 候选按语义类型比较数量：

```text
retained  = min(1, candidate_count / source_count)
additions = max(0, candidate_count - source_count) / source_count
P_count   = retained × exp(-0.25 × additions)
```

丢失实例降低 retained，凭空增加实例也会受惩罚。普通目标另外比较标签集合 F1。

### 4.6 Detection 不等于 Ground Truth

- OCR 可能漏掉艺术字、竖排字或低对比文字；
- Logo Candidate 只检测显著标记，不知道品牌身份，也可能把装饰误判为 Logo；
- COCO 目标检测知道“有人”，不知道是不是同一个人，也不知道人物关系和动作是否改变；
- YuNet 能发现脸框，但不能判断五官是否被拉坏、脸是否自然；
- 数量保持为 1 不代表身份、姿态和语义保持。

因此检测结果既不能单独决定 A/B/C/D，也不能用一个异常数字覆盖高清视觉证据。Rule 提供可
重复的粗粒度证据，Agent 补充语义和非物理现象，人工负责最终发布判断。

## 5. 七种重定向方法

七种方法都实现 `CandidateMethod.generate(...) -> MethodOutput`。统一输入为 RGB 原图、
`TaskSpec`、共享 `AnalysisArtifact`、importance/tolerance map、可选 Guidance、版本化方法参数
和 Execution Context；统一输出为目标尺寸 RGB、`TransformRecord`、状态、警告和错误。

方法不能自行重跑 Detector，也不能决定自己是否为 Top1。无论成功、`UNSAFE` 还是失败，
Runner 都保留结构化记录，避免只统计成功方法造成分母偏差。

### 5.1 Direct Warp

代码：`methods/direct_warp.py`。直接把整图非等比缩放到目标尺寸：

```text
sx = target_width / source_width
sy = target_height / source_height
anisotropy = max(sx/sy, sy/sx)
d_stretch = |log(target_aspect / source_aspect)|
```

它不裁掉内容，适合建立完整性基线；风险是人物、圆形、文字和刚性物体整体变胖或变瘦。
Transform 记录缩放比例和 `d_stretch`。比例变化本身不自动等于 C/D，最终仍看可见自然度。

### 5.2 Crop

代码：`methods/crop.py`。先计算符合目标比例的窗口，再遍历尺度和位置：

```text
S_crop = importance_coverage
         - 4 × cut_must_keep_count
         - 0.08 × center_distance
         - 0.05 × cropped_fraction
```

选出最高分窗口后只做等比缩放，因此局部几何通常最自然。Transform 记录裁掉比例、importance
覆盖率和切断 MUST_KEEP 的数量。典型失败是 Detector 漏掉真正的主角、主标题或关系人物，
导致算法以为那部分可以裁。

### 5.3 Seam（受限）

代码：`methods/seam_limited.py`。按梯度和保护图寻找低代价 Seam，单轴最多移除 Profile 限定
的数量，剩余比例差再统一缩放。它改动保守、速度较快，适合作为稳定基线；大比例变化时更像
“少量 Seam + Warp”。Transform 记录 Seam 数量、平均重要度和最终对齐各向异性。

### 5.4 Seam Full（完整）

代码：`methods/seam.py`。在受限最长边的代理图上计算 forward energy，但维护原图坐标 Map，
最后只从原始分辨率采样一次：

```text
E = normalized_Scharr_gradient
    + protection_weight × importance
    - tolerance_weight × tolerance
```

`seam_fraction=1` 时允许 Seam 承担所需的全部比例变化。Transform 记录移除数量、路径平均/峰值
importance 和剩余缩放各向异性。它可能保住画面边界，却在密集海报、脸、肢体、刚性物体和
文字中产生局部折叠、复制或条带扭曲。

### 5.5 Mesh（受限）

代码：`methods/mesh_legacy.py`。历史稳定实现以较保守的轴向局部位移完成有限变形，并保留最小
网格单元比例门禁。它通常比 Direct Warp 更灵活，但表达能力有限，风险是局部宽窄不一致。

### 5.6 Mesh Full（完整）

代码：`methods/mesh.py`。把图像划成二维网格，对 x/y 顶点解带权最小二乘：边界锚定目标
画布，保护区域提高局部刚性，二阶差分约束相邻单元平滑，弱均匀锚避免整体漂移。

对每个三角形局部变换 `J`：

```text
jacobian = det(J)
anisotropy = largest_singular_value(J) / smallest_singular_value(J)
```

`jacobian <= 0` 表示网格翻折；anisotropy 越大表示局部拉伸越不均。求解器会向均匀网格回退，
直到没有翻折，再用分片仿射 Map 从原图重采样。典型失败是人物身体、文字和刚性商品局部弯曲。

### 5.7 Seam + Scale

代码：`methods/seam.py`。当前 Profile 使用 `seam_fraction=0.45`，先让 Seam 承担约 45% 的比例
变化，再用一次平滑缩放完成剩余部分。它降低 Full Seam 的局部损伤概率，也减轻 Direct Warp
的全局拉伸，但两类风险都需要检查。

### 5.8 方法对比

| 方法 | 最主要优势 | 最典型失败 | 优先检查的 Transform 证据 |
|---|---|---|---|
| `direct_warp` | 内容不会因裁切消失 | 全局变胖/变瘦 | `d_stretch`、anisotropy |
| `crop` | 局部几何自然、速度快 | 主体、关系人物或主文字被裁 | coverage、cut count、cropped fraction |
| `seam` | 改动保守 | 少量局部扭曲 + 剩余拉伸 | Seam importance、alignment anisotropy |
| `seam_full` | 可承担大比例变化 | 条带、折叠、重复纹理 | Seam 数量、路径峰值 importance |
| `mesh` | 历史稳定局部变形 | 局部宽窄不一致 | axis ratio、最小单元比例 |
| `mesh_full` | 二维保护与平滑约束 | 身体、文字、商品局部弯曲 | foldover、max anisotropy |
| `seam_scale` | 折中局部删除和全局缩放 | 两类中等风险叠加 | Seam importance、residual anisotropy |

## 6. Rule 自动评分

Rule 评分按下面顺序工作：

```text
技术检查
  ↓
原图/候选检测比较 + 像素统计 + Transform 风险
  ↓
Content / VisualIntegrity / Composition
  ↓
基础 Quality
  ↓
Strategy 软惩罚
  ↓
A/B/C/D 分数阈值
  ↓
Hard Failure / Scene Gate 等级封顶
```

### 6.1 原始指标怎么看

| 指标 | 通俗含义 | 方向 | 没有值时表示什么 |
|---|---|---:|---|
| `ocr_character_recall` | 原图识别字符有多少还能在候选找到 | 越高越好 | 原图没有可靠文字或未执行候选检测 |
| `ocr_sequence_similarity` | 归一化文字顺序有多相似 | 越高越好 | 同上 |
| `face/person/product/logo_count_preservation` | 对应检测实例数量保持程度 | 越高越好 | 原图没有对应实例，不能把 `None` 当 0 |
| `object_label_f1` | 普通目标标签集合是否保持 | 越高越好 | 原图没有可比较目标 |
| `orb_content_similarity` | 局部关键点是否还能形成一致几何匹配 | 越高越好 | 图像纹理不足、无法提取描述子 |
| `sharpness_preservation` | 候选与原图拉普拉斯清晰度比例是否接近 | 越高越好 | 一侧近乎没有可测清晰度 |
| `edge_density_preservation` | 边缘密度比例是否接近 | 越高越好 | 一侧几乎没有边缘 |
| `color_histogram_similarity` | HSV 颜色分布是否接近 | 越高越好 | 当前实现始终可计算 |
| `structure_line_similarity` | Hough 线方向分布是否接近 | 越高越好 | 原图或候选没有足够结构线 |
| `protected_border_safety` | 重要检测框是否过度贴边 | 越高越好 | 候选没有对应重要区域 |
| `composition_center_score` | 梯度重心是否没有极端偏离中心 | 越高越好 | 当前实现始终可计算 |
| `transform_safety_score` | 当前方法声明的变换风险是否可控 | 越高越好 | 未提供 Transform 或未知方法 |

这些指标大多是“候选相对原图”的证据，不是美学真值。合理 Crop 会改变颜色比例、ORB 匹配和
结构线位置；Warp 可能获得较高像素相似度却肉眼变形。因此颜色、ORB、结构线和清晰度只进入
软分，不能单独作为内容损坏的硬结论。

### 6.2 清晰度、边缘、颜色、ORB 和结构线

清晰度和边缘使用对称比例分：

```text
ratio_score(value, reference) = exp(-0.35 × |log(value/reference)|)
```

候选比原图更锐或更糊都会偏离 1。颜色使用 HSV 二维直方图的 Bhattacharyya 距离；ORB 结合
宽松匹配比例和 RANSAC 内点比例；结构线把 Hough 线段按方向累计为 18 个桶，再计算方向直方图
点积。它们适合发现大面积异常，不适合单独评价合理重构后的每个像素位置。

### 6.3 TransformSafety 按方法计算

TransformSafety 不是统一的图像相似度，而是读取各方法自己的 `TransformRecord`：

- Direct Warp：`exp(-k × d_stretch)`，比例差越大风险越高；
- Crop：`importance_coverage × exp(-k × cut_must_keep_count)`；
- Seam：同时惩罚 Seam 平均 importance 和剩余缩放各向异性；
- Mesh：惩罚最大局部各向异性，出现 foldover 时同时写入硬失败。

这项分数只说明“算法记录的变换有多冒险”，不保证肉眼一定自然。Detector 漏检、局部脸部
形变或文字条带仍需候选检测、Agent 和人工补充。

### 6.4 三个组件和 Quality

当前 `retarget@1.0.0` 继承 `movie60@3.3.0` 的配置：

```text
Text = weighted_mean(CharacterRecall×0.65, SequenceSimilarity×0.35)

Content = weighted_mean(
  ORB×0.25, Text×0.35, Face×0.20, Person×0.20,
  Product×0.20, Logo×0.10, ObjectF1×0.20)

VisualIntegrity = weighted_mean(
  Sharpness×0.25, EdgeDensity×0.20, Color×0.15,
  StructureLines×0.20, TransformSafety×0.20)

Composition = weighted_mean(ProtectedBorder×0.60, VisualCenter×0.40)

Quality = 100 × weighted_mean(
  Content×0.58, VisualIntegrity×0.32, Composition×0.10)
```

`weighted_mean` 只对实际存在的指标重新归一化权重。例如原图没有人脸，Face 为 `None`，它不会
被当成 0 拉低 Content，而是由剩余已观测项分担权重。这就是“动态赋权”的当前含义：缺测时
自动归一化；要改变业务权重，则必须创建新 Strategy，不能运行时随意改旧结果。

`TransformSafety` 是 VisualIntegrity 内部的 0.20，不是第四个顶层分量。Quality 最终限制在
0～100，不按方法名称额外奖励。

### 6.5 分数、软回归、硬失败和门禁

当前分数区间为：A≥89，52≤B<89，42≤C<52，D<42。分数之后还有三层证据：

- **软回归惩罚**：例如关键文字、严重全局拉伸或显著人脸未重检，只扣 Strategy 声明的分值；
- **Hard Failure**：目标尺寸错误、近空白、Mesh 翻折等技术失败，当前 Strategy 将等级封顶 D；
- **Gate**：场景、方法和一个或多个指标组合触发的业务门禁，只限制等级上限。

门禁不会伪造连续分。例如 Quality 仍可为 92，但人物只保留 30% 时，等级可被
`main_person_major_loss` 封顶 C。报告同时保留 `base_quality_score`、`quality_score`、
`hard_failures`、`human_alignment_soft_regressions` 和 `human_alignment_matched_gates`，开发可以
区分“分数低”和“分数高但触发风险门禁”。

### 6.6 两个计算例子

下面数字只用于解释公式，不是 Movie60 冻结样本。

例一：某候选 `Content=0.88`、`VisualIntegrity=0.80`、`Composition=0.72`：

```text
Quality = 100 × (0.88×0.58 + 0.80×0.32 + 0.72×0.10)
        = 83.84
```

没有回归和门禁时，按当前阈值为 B。它不是因为“差 5.16 分所以不能用”，而是当前 Strategy
把 A 设为更严格的直接发布代理区间。

例二：某候选基础 Quality 为 92，按分数本应为 A；但
`person_count_preservation=0.30`，触发人物重大损失 Gate：

```text
base_quality_score = 92
quality_score      = 92
proxy_grade        = C
matched_gate       = main_person_major_loss
```

这表示连续指标整体较高，但确定性证据发现主人物数量风险，要求人工复核；不是把 92 改写成
一个虚假的低分。

### 6.7 指标边界

Rule 最适合做稳定粗排、明显技术失败和已知内容回归门禁，不擅长人物身份、关系、动作、脸部
自然度、文字视觉字形和整体美感。检测器误差还会沿着 OCR/计数指标传播。因此新增一个指标时，
应先作为 evidence 验证；“代码能算出来”不等于“应该立刻加入 Quality 或 Gate”。

### 6.8 非 1:1 的验证边界

1:1、16:9、9:16、4:3、3:4 Smoke 证明七方法、Rule、落盘和 UI 在工程上支持这些尺寸；当前
人工阈值主要依据 Movie60 1:1 校准。非 1:1 可以运行和人工查看，但不能只凭工程 Smoke 宣称
其 A/B/C/D 已完成人工泛化验证。

## 7. Rule 排名

`rule_selection.py` 使用 Strategy 中声明的词典序排序键：

```text
technical_valid_desc
→ hard_failures_absent_desc
→ critical_regressions_absent_desc
→ generation_success_desc
→ quality_score_desc
→ method_id_asc
```

这不是简单按 Quality 从高到低。Quality 92 但有 Hard Failure 的候选，会排在 Quality 84 且
无 Hard Failure 的候选之后。失败候选仍在完整记录和分母中，但不会因缺失分数被选为 Top1。

### 7.1 为什么保存完整排名

Agent 需要同时看到 Rule Top1、Top2 和其余候选，才能提出明确 Challenger，而不是脱离 Rule
证据重新猜一个答案。Benchmark、UI 和 Replay 也需要同一顺序，所以 Evaluation 将每个 Task
的完整排列冻结到：

```text
evaluations/<evaluation-id>/rule-decisions/<task-id>.json
```

所有消费者读取该文件，不自行按 Quality 重排。最后的 `method_id_asc` 只是证据完全相同时的
稳定 tie-break，确保不同电脑 Replay 一致，不表示某个方法天然优先。

旧 Run 没有 Rule Decision 文件时，可由同一模块使用当时 Evaluation 的 Strategy 快照重建，
并明确标记 `legacy_reconstructed`。

## 8. Agent

### 8.1 Rule 后面为什么还需要 Agent

Rule 擅长 OCR 召回、实例数量、像素统计、Transform 风险和确定性门禁；它不擅长判断人物
是不是同一个人、动作和关系是否改变、脸和肢体是否自然、艺术字是否肉眼损坏，以及某个合理
Crop 是否比完整但僵硬的 Warp 更适合发布。Agent 的职责是阅读高清视觉并补充这些语义证据，
不是把同一批数值换一种语言复述。

### 8.2 Skill、Knowledge、Prompt、Backend 和 Agent Run

| 组件 | 作用 | 改动后的版本影响 |
|---|---|---|
| Skill | 判断原则、视觉优先级、允许挑战 Rule 的条件、理由代码 | 新 Strategy + 新 Agent Run |
| Knowledge | 可泛化正反例，不含 Task ID 和逐图答案 | 新 Strategy + 新 Agent Run |
| Prompt | 把本次图片、Rule 证据和输出 Schema 组织成模型请求 | 新 Strategy + 新 Agent Run |
| Backend | 具体模型/API 的图片编码、请求、重试和结构化响应 | 新 Agent Run，必要时新插件 ID |
| Agent Run | 某一模型/Profile/Strategy 对冻结 Evaluation 的一次执行结果 | 不覆盖旧 Agent Run |

Skill、Knowledge 和 Prompt 都进入 Strategy SHA 和 Agent Run 快照。只改外部文件却沿用旧
Strategy ID，会破坏回放，Loader 会阻止非法引用或哈希漂移。

### 8.3 当前 Knowledge 覆盖什么

当前中文 v8 Knowledge 包含 14 类通用案例：直接发布优先、干净次要裁剪、人物关系丢失、自然
全局缩放、局部非物理形变、文字视觉完整、主 Logo、脸和身体完整、贴边但未丢内容、Detector
冲突、多个中等缺陷叠加、审美 Crop 优先、方法名不是证据、视觉证据不确定。

这些案例告诉模型怎样迁移判断原则，不提供 Movie60 某张图的标准答案。

### 8.4 Agent 实际决策过程

```text
读取 Rule Top1 和完整排名
        ↓
比较原图与全部候选高清视觉
        ↓
检查身份、数量、关系、文字/Logo和非物理形变
        ↓
Rule Top1 没有明确问题 ──> 保留 Rule
        │
        └─ 有明确问题 ──> 提出最多两个 Challenger
                              ↓
                    确认 Challenger 没引入更严重内容损失
                              ↓
                    输出建议排名、置信度和理由代码
```

Agent 必须返回候选的完整排列，原图不能进入排名。方法名不是视觉证据，不能因为候选叫 Crop、
Seam 或 AIGC 就预设等级。

### 8.5 Challenger 与安全回退

当前 `override.yaml` 要求 Rule 可用等级、受保护指标下降容忍度、最小配对置信度、明确视觉证据
和一致配对证据。生产模式仍为 `advisory_only`：Agent 可以主动提出 Challenger，但不会静默
覆盖 Rule。

以下情况 Fail-closed 回到 Rule 并保留错误：

- 模型返回非法 JSON，有限重试后仍不符合 Schema；
- 排名重复、漏项且无法按冻结 Rule 顺序安全修复；
- 证据不足或模型理由与确定性保护证据强冲突；
- Challenger 造成更严重的人物、商品、文字或 Logo 损失；
- Agent Backend、Profile 或密钥未显式配置。

当前 Movie60 v4 中文 v8 Replay 为 60/60 Schema 有效，已有 18 个人工 Task 上 Agent 与 Rule
都命中人工最佳 13/18，且 `top1_change_count=0`。正确结论是“Agent 全量执行稳定且没有使已有
基准退化”，不是“Agent 已经比 Rule 更准确”。

### 8.6 AIGC 和场景边界

Agent 建议外部生成不等于执行 AIGC。普通工作流固定不自动调用付费 API；只有显式
`AIGCGenerationRequest` 和 `execute=True` 才进入 Provider。AIGC 图片也不会自动写回已有 Run
或覆盖 Rule Top1。

普通 `run image/batch` 通过 `--scene` 冻结 `movie_poster`、`film_still`、`video_cover`、
`person` 等场景。省略时为 `unspecified`，通用分数和门禁仍工作，但场景化 Gate 不触发。当前
不引入自动场景分类，避免未经验证的分类错误被静默放大。

## 9. 可插拔架构

Strategy YAML 只能引用稳定插件 ID，不能写 `python_module: some.path.Class`。唯一映射入口是
`plugin_catalog.py` 的白名单：

| 类型 | 接口/Registry | 当前例子 | 更换后主要影响 |
|---|---|---|---|
| Detector Suite | `detector_suites` | `company_cpu_v2` | 原图保护分析、候选重检、评分 |
| Reference Scorer | `reference_scorers` | `human_aligned_proxy_v3` | Reference 指标和 Quality |
| Standalone Scorer | `standalone_scorers` | `technical_no_reference_v1` | 无原图技术评分 |
| Rule Selector | `selectors` | `deterministic_rule_ranking_v1` | 完整排名和 Top1 |
| Agent Backend | `agent_backends` | OpenAI-compatible adapters | Agent 请求和结构化响应 |
| Generation Provider | `generation_providers` | `seedream_api` | 外部生图执行 |
| Candidate Method | `built_in_methods()` | crop、seam、mesh 等 | 候选像素和 Transform |

白名单防止 Strategy 执行任意 Python；稳定 ID 让旧实现继续可回放；新旧 Adapter 可以并存；
替换模型或 API 不需要把条件分支塞进 Runner。参数和门禁变化新建 Strategy；实现变化新增插件
或方法 ID，并按影响范围创建新 Run/Evaluation。

## 10. 单张图片怎样定位问题

| 现象 | 先看文件 | 再看代码 |
|---|---|---|
| 保护框或重要区域不对 | `analysis/<task>/analysis.json`、`importance.png`、`tolerance.png` | `analysis.py`、`protection_detectors.py` |
| 某方法生成的图片坏了 | `candidates/<task>/<method>/candidate.png`、`transform.json`、`candidate.json` | 对应 `methods/*.py`、`runner.py::_candidate_for_method()` |
| 图片正常但自动分数不合理 | `evaluations/<id>/metrics/<candidate>.json` | `evaluation.py::compute_proxy_metrics()`、`human_aligned_scoring.py` |
| 分数合理但 Rule 选错 | `evaluations/<id>/rule-decisions/<task>.json` | `rule_selection.py`、`selection.yaml` |
| Agent 建议不合理 | `agent-runs/<id>/decisions/<task>.json`、`calls/`、Strategy 快照 | `agents.py`、Skill/Knowledge/Prompt、Backend Adapter |
| UI 与冻结证据不一致 | 页面 API JSON、`reviews/` sidecar | `review_workspace.py`、`unified_review_app.py` |

排查顺序应沿着上游到下游进行。不要先改 Rule 去掩盖坏候选，也不要先改 Agent 去弥补 Detector
完全错误；先确认像素、Transform 和原始指标是否正确，再判断问题属于权重、门禁还是视觉语义。

## 11. Artifact 和版本生命周期

```text
Dataset
  └─ Generation Run
       └─ Evaluation
            └─ Agent Run
                 └─ Review sidecar

Release = 从上述资产中选择一组冻结快照进行交付
```

| 类型 | 代表什么 | 什么变化需要新版本 |
|---|---|---|
| Software | Python 代码与 CLI/API 行为 | 代码实现变化 |
| Strategy | Scoring、Selection、Override、Skill/Knowledge/Prompt | 权重、阈值、Gate 或 Agent 政策变化 |
| Dataset | Source、Target 和 Task 定义 | 输入图片或任务定义变化 |
| Generation Run | 一次候选像素与 Transform | 算法、保护分析或输入变化 |
| Evaluation | 某 Strategy 对冻结 Run 的指标和 Rule 排名 | Scorer、权重、阈值、Gate、Selector 变化 |
| Agent Run | 某模型/Profile/Agent Strategy 的一次建议 | Skill、Knowledge、Prompt、Backend 或模型变化 |
| Review | 人工当前结论和追加事件 | 人工重新评价 |
| Movie60 Release | 面向交接的冻结数据资产版本 | 交付数据或证据集合变化 |

三个典型例子：

```text
只把 A 阈值从 89 改为 85
→ 新 Strategy + 新 Evaluation
→ 不重新生成候选图片

替换 Seam 算法实现
→ 新 Generation Run + 新 Evaluation
→ 如需比较 Agent，再建新 Agent Run

只更换 Agent API 或 Prompt
→ 新 Agent Run
→ 不重新生成候选，也不重算 Rule
```

并非每次修改都要发新 Release。Release 只在确实需要交付新的代码或数据快照时创建；旧 Tag、
旧 Run、旧 Evaluation、旧 Agent Run 和旧 Review 继续保留，不能用同一个 ID 覆盖。
