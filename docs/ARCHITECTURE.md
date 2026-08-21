# 架构与算法原理

## 1. 项目解决什么问题

输入一张图片和目标像素尺寸，系统生成七种传统重定向候选，分别比较“内容保留、视觉自然、
构图和变换风险”，冻结 Rule 完整排名，并可选地让视觉 Agent 补充语义与非物理形变判断。
Rule/Agent 都不是人工金标；统一 UI 用于最终人工复核和后续策略迭代。

## 2. 项目级流程图

```text
CLI: run image / run batch
          │
          v
Simple Workflow ──冻结──> Dataset + run.yaml
          │
          v
Generation Runner
  ├─ 原图共享保护分析：OCR / 人脸 / 目标商品 / Logo候选 / 显著性
  ├─ 七方法并列生成，任何失败保留记录
  └─ Candidate + Transform + 性能 + 可视化
          │
          v
Evaluation
  ├─ 每张候选再次做同一检测
  ├─ 原图证据 vs 候选证据
  ├─ Scorer 插件 + Strategy 权重/门禁
  └─ Rule Selection 深模块：冻结完整排名与 Top1
          │
          ├─────────────> results/.../result.png + result.json
          │
          └─ 可选 Agent Replay
               ├─ 原图、候选图、Rule Top1、Rule完整排名
               ├─ Skill + 案例 Knowledge + Prompt
               └─ 中文建议、完整候选排序、置信度、理由代码
          │
          v
ReviewWorkspaceAdapter ──> FastAPI ──> 浏览器人工评审页
          │                                  │
          └<──── CSV 当前结果 + JSONL 追加历史 ┘
```

前端只认识统一的 Task/Candidate JSON 和受索引图片 URL，不解析 Run 或 Movie60 目录；后端
Adapter 负责把不同来源转换成同一界面模型。这样算法目录变化不需要重写页面。

## 3. 关键深模块

- `simple_workflow.py`：单图/批量的小接口，隐藏 Dataset、Run、Evaluation 的装配细节；
- `runner.py`：冻结输入，共享一次原图分析，并调用方法 Adapter；
- `evaluation.py`：候选重检与 Reference Scorer；
- `rule_selection.py`：Rule 排名唯一接口，Evaluation、Agent 和 UI 共用；
- `strategy.py`：加载、校验、哈希、快照 Strategy/Prompt/Skill/Knowledge；
- `agents.py`：消费冻结证据，不生成或改写传统候选；
- `review_workspace.py`：Movie60、Run、外部候选的统一后端 Adapter；
- `unified_review_app.py`：本机 HTTP 和页面资源，不承载评分规则。

## 4. 共享保护分析

### 4.1 为什么原图和候选都要检测

Generation 前只对原图检测一次，目的是让七方法共享完全相同的保护区域，避免每条路线的
先验不同。Evaluation 再对每张成功候选检测一次，才能回答“文字/人物/商品后来还剩多少”。
所以一个 Task 七候选的典型调用量是：原图检测 1 次，候选检测最多 7 次。

默认 Windows CPU 套件位于 `protection_detectors.py`：

- PP-OCRv6 small：文字多边形、识别文本和置信度；
- D-FINE nano COCO：人物、商品类和普通目标框；
- YuNet：人脸框；
- Logo Candidate CV：紧凑显著标记区域，只检测“疑似 Logo”，不识别品牌身份。

原图输出被转成 `RegionRecord`：位置、类别、置信度、`MUST_KEEP/PREFER_KEEP/RIGID`、
importance 与 tolerance；再栅格化为 importance/tolerance map 供算法使用。

### 4.2 OCR 比较

先对原图文本和候选文本做 Unicode NFKC 归一化、大小写折叠，并移除非字母数字字符。

```text
R_char = Σ_c min(count_source(c), count_candidate(c)) / |source_text|
R_seq  = SequenceMatcher(source_text, candidate_text)
Text   = weighted_mean(R_char×0.65, R_seq×0.35)
```

`R_char` 通俗上是“原图字符有多少还能在候选找到”，不要求同一位置；`R_seq` 再补充字符
顺序。OCR 对艺术字可能漏检，所以这两项是证据，不是“文字损坏”的单一真值。高清视觉仍
完整时，Agent/人工可以指出检测器冲突。

### 4.3 人物、商品、Logo 和目标数量

对每一语义类型分别计算：

```text
retained  = min(1, candidate_count / source_count)
additions = max(0, candidate_count - source_count) / source_count
P_count   = retained × exp(-0.25 × additions)
```

丢失实例会直接降低 `retained`；凭空多出实例也会受到指数惩罚。普通目标另计算标签集合
F1。数量一致仍不能证明“同一个人、同一动作或同一关系”，这是 Agent 的主要补充价值。

## 5. 七种重定向方法

七方法的统一输入是 RGB 原图、`TaskSpec`、共享 `AnalysisArtifact`、importance/tolerance
map 和版本化方法参数；统一输出是目标尺寸 RGB、`TransformRecord`、状态、警告和耗时。

### 5.1 Direct Warp

代码：`methods/direct_warp.py`。直接把整图非等比缩放到目标尺寸，内容不会因裁切消失。

```text
sx = target_width  / source_width
sy = target_height / source_height
anisotropy = max(sx/sy, sy/sx)
d_stretch  = |log((target_width/target_height) / (source_width/source_height))|
```

`d_stretch=0` 表示宽高比没改变，越大表示全局形变越强。优点是完整；失败模式是人物、圆形、
文字和刚性物品整体变胖/变瘦。比例改变本身不会自动判 C/D，最终仍看可见自然度和门禁。

### 5.2 Crop

代码：`methods/crop.py`。枚举符合目标比例的窗口，保留高 importance 区域，并惩罚切断
MUST_KEEP、偏离视觉中心和裁掉面积：

```text
S_crop = Coverage_importance
         - 4 × N_cut_must_keep
         - 0.08 × D_center
         - 0.05 × R_cropped
```

选择最高分窗口后只做等比缩放。优点是几何最自然；风险是检测漏掉的关键主体或主标题被裁。
若没有窗口能装下全部 MUST_KEEP，候选仍保留，但状态为 `UNSAFE` 并写明原因。

### 5.3 Seam（受限）

代码：`methods/legacy.py`。按梯度与保护图寻找低代价 seam，单轴最多移除配置的 24 条，剩余
比例差用缩放完成。它速度较快、改动保守；大比例变化时更像“少量 seam + 全局缩放”。风险
记录包括 seam 平均重要度和最终对齐各向异性。

### 5.4 Seam Full（完整）

代码：`methods/seam.py`。在最长边受限的代理图上使用 forward energy，但雕刻的是原图坐标
map，最终只从原始分辨率采样一次，不把 512 代理图放大成结果。

```text
E = normalized_Scharr_gradient
    + protection_weight × importance
    - tolerance_weight × tolerance
```

`seam_fraction=1` 时允许完成所需的全部 seam 变化。记录移除数、路径平均/峰值 importance、
剩余缩放各向异性；路径穿过高重要区会标 `UNSAFE`。它能保内容，但密集海报、脸、肢体、
刚性物体和文字可能产生局部折叠、复制或带状扭曲。

### 5.5 Mesh（受限）

代码：`methods/legacy.py`。旧版保护网格以较保守的局部位移完成有限变形，并保留最低单元
比例门禁。它常比 Warp 自然，但表达力有限；风险是局部宽窄不一致。

### 5.6 Mesh Full（完整）

代码：`methods/mesh.py`。把图像划成二维网格，分别对 x/y 顶点解带权线性最小二乘：保护
区域强调局部刚性，边界锚定目标画布，二阶差分约束相邻单元平滑，弱均匀锚防止漂移。

对每个三角形局部变换 `J`：

```text
jacobian = det(J)
anisotropy = largest_singular_value(J) / smallest_singular_value(J)
```

`jacobian<=0` 表示折叠；anisotropy 越大，局部拉伸越不均。求解后会向均匀网格回退直到无
折叠，再用分片仿射坐标 map 从原图重采样。风险是人物身体、刚性物体和文字局部弯曲。

### 5.7 Seam + Scale

代码：`methods/seam.py`。`seam_fraction=0.45`，先用 seam 解决约 45% 的比例差，再用一次平滑
缩放完成剩余部分。它降低 Full Seam 的局部损伤概率，也减轻 Direct Warp 的全局拉伸，但
仍需同时检查 seam 路径和剩余各向异性。

## 6. Rule 指标与公式

候选清晰度、边缘密度、HSV 颜色直方图、Hough 结构线、ORB 局部特征和变换安全共同作为
软证据。裁剪/重排天然改变像素位置，因此颜色、线条和 ORB 不能单独做硬失败。

对缺测指标，`weighted_mean` 只在已观测项上重新归一化权重。3.3 的层级公式为：

```text
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

注意：`TransformSafety` 是 `VisualIntegrity` 内部的 0.20，不是第四个顶层权重。最终分数
限制在 0～100，不因方法或场景额外奖励。

当前 3.3 数值区间：A≥89，52≤B<89，42≤C<52，D<42。随后应用两类可解释规则：

1. 回归惩罚：例如关键文字、人脸或严重拉伸证据只扣声明的分值；
2. 等级门禁：例如主人物/人脸几乎消失封顶 D，场景+方法+多条件组合可封顶 C/D。

门禁只改变等级上限，不伪造连续分。最终文件中的 `base_quality_score`、`quality_score`、
`critical_regressions` 和 `human_gate:*` 可追查每一步。

## 7. 正式 Rule 排名

`rule_selection.py` 按 Strategy 声明顺序比较：技术有效、无硬失败、无关键回归、生成成功、
Quality 降序、方法 ID。Evaluation 把每个 Task 的完整排列和选中 Candidate 冻结到：

```text
evaluations/<evaluation-id>/rule-decisions/<task-id>.json
```

Agent 和 UI 读取该文件，不再各自按分数重排。旧 Run 没有此文件时，可由同一模块使用当时
Evaluation 的 Strategy 快照兼容重建，并明确标 `legacy_reconstructed`。

## 8. Agent 的输入输出和职责

Agent 输入包含：原图/候选总览、每个候选的结构化 Rule 证据、Rule Top1、完整 Rule 排名、
版本化 Skill、案例 Knowledge 和 Prompt。独立 Knowledge 与 Skill 都进入 Strategy SHA 和
每次 Agent Run 快照，不能只改外部文件而不留痕。

输出是严格 JSON：完整候选排列、建议 Top1/挑战候选、代理等级、是否保持核心内容、是否有
可见形变、置信度、中文理由代码和回退动作。Agent 主要补充：人物身份/数量/动作/关系、主
标题/Logo 的视觉完整、脸/肢体/刚性物品/文字的非物理现象、以及美观裁切与高相似拉伸的
取舍。

当前生产语义仍是 `advisory_only`：Agent 可更主动提出挑战，但没有独立人评证据时不会静默
覆盖 Rule。AIGC 也不会由普通工作流自动调用。

## 9. 可插拔接缝

Detector Suite、Reference/Standalone Scorer、Rule Selector、Agent Backend 与方法实现均通过
`plugin_catalog.py` 的白名单注册。Strategy 只能引用允许的插件 ID，不允许从 YAML 任意导入
Python。参数/阈值调整新建 Strategy 目录；实现替换新增 Adapter 并注册。具体步骤见
`ADVANCED.md`。
