# 架构与算法流程

## 项目级流程

```text
输入图片 + WIDTHxHEIGHT
        │
        v
共享保护分析（OCR / 人脸 / 目标 / Logo / 显著性）
        │  同一份原图区域、置信度、重要度与容忍度
        v
七种候选：Warp / Crop / Seam / Seam Full / Mesh / Mesh Full / Seam+Scale
        │
        v
候选逐张重检 + 原图/候选证据比较
        │
        v
current Strategy + Rule 门禁/加权评分/排序
        │
        ├── 默认：Rule Top1
        └── 显式启用：视觉 Agent 读取 Rule 完整排名并给出挑战建议
                         │
                         v
                Run Adapter / Movie60 Adapter
                         │
                         v
                    同一人工评审 UI
```

## 核心深模块与边界

- `simple_workflow.py`：普通开发者入口，把图片目录折叠成 Dataset、Run、Evaluation。
- `runner.py`：冻结 Task、共享分析与候选；算法只接收统一 `TaskSpec/AnalysisArtifact`。
- `evaluation.py` 与 Scorer 插件：逐候选重检、指标和 Rule 分数。
- `strategy.py`：加载不可变 StrategyBundle，并校验所有组成文件哈希。
- `agents.py`：只消费冻结候选、Rule 排名和显式模型配置，不修改候选。
- `review_workspace.py`：统一适配 Movie60、标准 Run 与外部候选，前端不理解来源目录。
- `unified_review_app.py`：很薄的 HTTP 边界，只负责读取、保存和受索引媒体访问。

## OCR、目标、人脸与 Logo 怎样比较

保护分析不是只对原图做一次就结束：

1. Generation 前，原图检测一次，形成共享保护区域，供七种算法共同使用；
2. Evaluation 时，每张成功候选再次运行同一检测栈；
3. 通过文本字符、多实例数量/位置、区域覆盖与结构风险比较原图和候选；
4. 检测结果和变换记录一起进入门禁与软分数。

OCR 输入是 RGB 图片，输出文字多边形、识别文本与置信度。常用的字符召回可通俗写成：

```text
OCR字符召回 = 候选中仍能匹配的原图字符数 / 原图关键字符数
```

它回答“原图关键文字还剩多少”，但不等于排版美观；艺术字识别失败也不等于图片真的坏了。

目标检测输入同样是图片，输出类别、置信度和矩形框；人物/商品保留率近似为：

```text
实例保留率 = 候选匹配到的主要实例数 / 原图主要实例数
```

人脸检测补充人物头脸完整性；Logo 使用候选区域和结构/显著性证据。框的重叠、数量与重要度
用于发现主体被切掉，但“数量相同”无法证明身份、动作和关系相同，所以 Agent/人工仍重要。

## 为什么不能只用像素相似度

裁剪和比例变化天然会改变颜色分布、结构线位置和像素对应。SSIM、颜色、ORB、边缘线等
只作为软证据：它们能发现大面积破坏，却不能把“与原图不同”直接判成失败。Rule v3.3
优先使用核心内容门禁，随后组合内容保真、视觉完整、构图与变换风险。

抽象形式为：

```text
Quality = clamp(0, 100,
  w_content * 内容保真
  + w_visual * 视觉完整
  + w_composition * 构图
  + w_transform * 变换安全)
```

`w_*` 是 Strategy 中可版本化的权重；A/B/C/D 阈值也在 Strategy 中配置。`clamp` 只把
结果限制在 0～100，不额外奖励方法或场景。若命中主人物/关系、主标题/Logo 或非物理形变
等硬门禁，等级可被封顶为 C/D；门禁和软权重是两层，不应混成一个不可解释数字。

## 七种方法

- Direct Warp：整图缩放，内容最完整但可能全局拉伸。
- Crop：按重要区域搜索目标比例裁剪，自然但可能丢边缘内容。
- Seam：限制 seam 数量的内容感知缩放，速度和稳定性优先。
- Seam Full：允许完成更大比例变化，保护强但可能产生局部缝合形变。
- Mesh：受限网格形变，局部保护、幅度保守。
- Mesh Full：完整网格优化，表达力更强，也更需检查折叠和刚性结构弯曲。
- Seam + Scale：部分 seam 后再平滑缩放，折中内容保持与形变风险。

## Rule 与 Agent 的职责

Rule 擅长统一、快速、可复现地筛查 OCR/数量/变换风险并进行完整排名。Agent 读取原图、
候选视觉、Rule Top1 和完整 Rule 排名，补充身份、动作、人物关系、非物理形变和视觉偏好。
Agent 只有看到明确高清视觉证据时才建议覆盖；证据冲突回退 Rule。当前证据不足以让 Agent
成为生产主判，因此默认命令不运行 Agent。

## 可插拔位置

Detector、Reference Scorer、Standalone Scorer、Rule Selector、Agent Backend 和候选方法均
通过受控白名单注册。只改权重/阈值时新建 Strategy YAML；换已有实现时更换插件 ID；新增
算法或指标时实现 Python Adapter、注册、测试，再由新 Strategy 引用。禁止从任意路径动态
导入 Python，以保持可审计性。
