# 扩展开发指南

这份文档面向要替换公司能力、增加实现或迭代策略的开发者。原则是：调用入口保持稳定，变化
放进 Adapter 或新的不可变 Strategy；禁止按图片文件名、Task ID 或单张答案硬编码。

## 1. 先判断扩展属于哪一类

| 需求 | 扩展位置 | 是否新建 Strategy |
|---|---|---:|
| 换 AIGC HTTP API | `providers/` Adapter + `generation_providers` | 否 |
| 换 OCR/目标/人脸检测组合 | Detector Suite Adapter | 是 |
| 新增自动指标实现 | Reference/Standalone Scorer Adapter | 是 |
| 改权重、阈值或门禁 | 新 Strategy 版本目录 | 是 |
| 改 Rule 排名实现 | Selector Adapter | 是 |
| 改 Agent 的视觉判断规则 | 新 Skill/Knowledge/Prompt + Strategy | 是 |
| 新增重定向算法 | `methods/` Adapter +方法 Profile | 视默认启用情况 |
| 只改 UI 文案 | `web/` | 否 |

实现代码通过 `plugin_catalog.py` 的固定 ID 注册。Strategy 只能引用白名单 ID，不能从 YAML
执行任意模块路径。

### 1.1 所有扩展先回答八个问题

开始写代码或 YAML 前，先在 PR 说明中回答：

1. 现有接口为什么不能满足需求，是换参数、换模型，还是新增一种能力；
2. 新实现遵守哪个 Protocol，输入输出是否保持兼容；
3. 插件 ID、版本、模型 revision 和配置由哪里冻结；
4. 失败、超时、缺测和降级分别怎样表示；
5. 会改变哪些 Artifact：Analysis、Candidate、Metric、Rule Decision 还是 Agent Run；
6. 哪些阶段必须重跑，哪些冻结像素可以复用；
7. 最小单元测试和真实图片 Smoke 分别验证什么；
8. 达到什么条件后才能进入默认 Profile 或切为 active Strategy。

统一判断原则是：先让新输出成为可审计 evidence，再通过独立验证决定是否进入总分、Gate 或
默认路由。能计算、能注册或单张 Smoke 成功，都不等于已经证明业务质量改善。

## 2. 替换或新增 AIGC API

### 2.1 稳定接口

新 Adapter 实现：

```python
class AIGCProvider(Protocol):
    provider_id: str
    provider_version: str

    def capabilities(self) -> ProviderCapability: ...
    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult: ...
```

通用请求只包含：

- `task_id/run_id/request_id`：审计身份；
- `source_path` 或 `source_url`：二选一；
- `source_sha256`：调用方预期的源像素；
- `target_width/target_height/target_format`；
- `prompt/prompt_version`。

`target_width/target_height` 表达调用方希望的构图尺寸。若厂商只支持 `1K/2K` 档位，Adapter
必须如实返回厂商图片的实际 `width/height`；需要精确像素时可以在 Adapter 内保留原件后再
生成归一化副本，或交给本地重定向流程处理，不能伪报尺寸。

不要把 Token、Cookie、私有 Endpoint 或公司素材审批结果放入 Request。密钥和 Endpoint 只从
环境变量或公司运行时配置读取。

### 2.2 Adapter 最小模板

新建 `src/retarget_agent/providers/company_api.py`：

```python
from pathlib import Path

from retarget_agent.models import ProviderCapability
from retarget_agent.providers.base import (
    AIGCGenerationRequest,
    AIGCProviderError,
    AIGCProviderResult,
    AIGCProviderRuntime,
)


class CompanyImageAdapter:
    provider_id = "company_image_api"
    provider_version = "1.0.0"

    def __init__(self, runtime: AIGCProviderRuntime) -> None:
        self._runtime = runtime
        self._endpoint = runtime.environ.get("COMPANY_AIGC_ENDPOINT", "")
        self._token = runtime.environ.get("COMPANY_AIGC_TOKEN", "")
        if not self._endpoint or not self._token:
            raise ValueError("company AIGC endpoint and token are required")

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            supports_async=False,
            supports_cancel=False,
            supports_seed=False,
            supports_mask=False,
            max_outputs=1,
        )

    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult:
        # 1. 读取/编码 source；验证公司接口自己的尺寸和格式限制
        # 2. 使用 self._runtime.timeout_seconds 调用接口
        # 3. 厂商异步任务、轮询、状态码与 JSON 都封装在这里
        # 4. 把一张最终图片写到 self._runtime.output_root 内
        # 5. 返回实际路径、SHA-256、尺寸和可选成本
        # 失败时抛 AIGCProviderError(code, sanitized_message)
        raise NotImplementedError


def create_company_image_adapter(runtime: AIGCProviderRuntime) -> CompanyImageAdapter:
    return CompanyImageAdapter(runtime)
```

不要直接复制 SeedDream 的请求 JSON。不同厂商的图片字段、鉴权、异步轮询、费用和尺寸规则
应全部留在各自 Adapter。

### 2.3 注册

在 `plugin_catalog.py::built_in_plugin_catalog()` 增加：

```python
from .providers.company_api import create_company_image_adapter

catalog.generation_providers.register(
    "company_image_api",
    create_company_image_adapter,
)
```

注册 ID、Adapter 的 `provider_id` 和运行命令的 `--provider` 必须完全一致。重复 ID 会被
`Registry` 拒绝。

### 2.4 通用执行层会做什么

`generation_execution.execute_generation()` 负责：

1. 根据 Provider ID 查注册表；
2. Dry-run 时只返回 `planned`，不读取密钥、不写文件；
3. Execute 时校验本地源图 SHA-256；
4. 构造 `AIGCProviderRuntime`；
5. 调用 Adapter；
6. 验证输出位于受控目录、哈希一致、图片可解码、尺寸元数据一致；
7. 保存无密钥的 `executions/<request-id>.json`。

通用层不判断素材是否允许发送给外部接口。`--execute` 表示调用方已经按公司流程完成授权。
如果公司需要审批，应在调用此模块之前完成，或由上层业务系统实现自己的 Policy。

预算、调用者幂等标签和成本字段都是可选的：

- 不传 `--budget-cny`：通用层不按金额阻塞；Provider 仍可执行自身限制；
- 不传 `--idempotency-key`：通用层不要求厂商支持幂等；
- API 不返回实际费用：`actual_cost_cny=null`；
- Provider 自带内容哈希缓存时，可以返回自己的 `idempotency_key/cache_hit`。

### 2.5 测试顺序

第一层必须完全离线：

```powershell
python -m pytest -q tests\test_generation_providers.py
python -m pytest -q tests\test_seedream_provider.py
```

Fake Adapter 至少覆盖：成功、厂商错误、超时、无效图片、Hash 不符、未配置密钥、Dry-run
零调用。断言只跨 `AIGCProvider.generate()` 和 `execute_generation()` 接口，不读取 Adapter
内部状态。

第二层才是一次受控真实 Smoke：

```powershell
# 先执行不付费的 preflight
retarget-engine generation run source.jpg `
  --provider company_image_api `
  --output-root local_data\company-api-smoke `
  --request-id company-smoke-v1 --task-id poster-001 `
  --prompt-file prompt.txt

# 经负责人确认后才增加 --execute
retarget-engine generation run source.jpg `
  --provider company_image_api `
  --output-root local_data\company-api-smoke `
  --request-id company-smoke-v1-paid --task-id poster-001 `
  --prompt-file prompt.txt --execute --timeout-seconds 300
```

真实 Smoke 只证明接口、下载和审计链路可用；一张图不能证明质量提升。

## 3. 新增 Detector Suite

Detector Suite 输入 RGB，输出 `tuple[RegionRecord, ...]`。步骤：

1. 固定模型名称、revision、权重 Hash、运行后端和阈值；
2. 为 OCR、人脸、人物、商品、Logo 分配稳定 `semantic_type`；
3. 缺模型时按 Profile 约定明确失败或降级，不返回伪检测；
4. 在 `plugin_catalog.detector_suites` 注册新 ID；
5. 新建 Strategy 引用新 ID；
6. 对原图和候选分别测试，不把原图检测结果复制给候选；
7. 用真实图片核对召回、误检与耗时。

检测器变化会改变内容保留证据，通常需要新 Evaluation；如果生成算法也使用新保护区域，还需
重跑 Generation。

### 3.1 什么时候应该新增 Detector Suite

如果只是调整同一模型的阈值，应在新 Strategy/Profile 中配置；如果模型家族、预处理、标签
映射或运行后端发生变化，应新增 Suite ID，让旧 Run 能继续找到旧实现。不要在
`ProtectionAnalyzerCore` 中加入 `if company_xxx`，也不要让某个 Candidate Method 私下重新
检测原图。

新 Suite 必须明确：

- `semantic_type` 怎样映射为 text、face、person、product、logo_candidate 或 object；
- 一个 Detector 失败时是整个 Suite 失败，还是带 warning 返回其余可靠 Region；
- 坐标是否基于 EXIF 校正后的 RGB，是否保证不越界；
- `confidence`、`importance`、`tolerance` 和 `kind` 各自表达什么；
- 模型缺失时 `required` 与可降级模式的行为。

最小测试应覆盖固定 Fake 输出、空检测、越界框、单个子模型失败、缓存复用和 Region 序列化；
真实 Smoke 至少包含简体中文文字、多人、人脸、商品/Logo 候选和无目标背景，并记录首次加载与
热运行耗时。只有召回、误检、时延和下游回归均可接受，才考虑进入默认 Profile。

## 4. 新增 Scorer 或指标

Reference Scorer 同时读取原图和候选证据；Standalone Scorer 只能看单张候选。新增指标时：

1. 先定义字段含义、范围、缺测值和方向；
2. 缺测使用 `None`，不能用 0 冒充失败；
3. 指标计算与等级门禁分开；
4. 在 Scorer Adapter 输出结构化值；
5. 新 Strategy 再决定权重、阈值、惩罚和门禁；
6. 用同一冻结 Run Replay，避免候选像素变化干扰比较。

### 4.1 先定义指标契约，再决定是否进总分

每个新指标至少写清：字段名、数据类型、理论范围、方向、绝对/相对原图、缺测条件、业务含义
和已知误差。例如“文字美观度”不能用 0 同时表示“严重损坏”和“OCR 没检测到文字”。缺测必须
是 `None`，由 `weighted_mean` 在已观测项上重新归一化。

推荐分三步上线：

```text
Scorer 先输出新字段
→ 在报告/UI中作为 evidence 观察
→ Calibration 与独立 Validation 证明有效
→ 新 Strategy 再赋权或加入 Gate
```

禁止在 `compute_proxy_metrics()` 中直接写人工等级，禁止按文件名/Task ID 调分，也不要用一个
新增指标同时承担测量、惩罚和门禁。Reference 指标要测试原图与候选互换、合理 Crop、全局
Warp、空纹理和 Detector 缺测；Standalone 指标不得暗示自己能判断内容丢失。

## 5. 迭代 Rule

只改权重、A/B/C/D 阈值或门禁时，不改 Python：

1. 复制当前不可变 Strategy 到新版本目录；
2. 设置 `parent_strategy`；
3. 修改 `scoring.yaml/selection.yaml/override.yaml`；
4. 在 Registry 登记新版本 Hash，但先保持非 active；
5. Calibration 多轮迭代；
6. 冻结后在 Validation 只运行一次；
7. 确认人工指标、严重 C/D 召回和回归案例；
8. 最后才切唯一 active。

旧 Strategy、旧 Evaluation、旧人工事件和 Run 内快照都不能覆盖。

如果只是改变数值政策，不应修改 Python；如果需要新的排序语义或新的测量实现，才分别新增
Selector/Scorer Adapter。Rule 修改的验收至少包含：Strategy schema/hash、旧 Strategy Replay、
完整候选排列、失败候选仍在分母、Calibration 差异、Validation 一次性结果，以及代表性错误
案例的人工解释。总体一致率上升但严重 C/D 召回下降，不能直接晋升。

影响范围要按层判断：

```text
只改阈值/权重/Gate
→ 新 Strategy + 新 Evaluation

改 Reference Scorer 实现
→ 新插件 ID + 新 Strategy + 新 Evaluation

改 Rule Selector
→ 新 Selector ID + 新 Strategy + 新 Evaluation
→ 如需比较 Agent，再建新 Agent Run
```

## 6. 迭代 Agent Skill、Knowledge 与 Prompt

- Skill：行为原则、视觉优先级、允许覆盖 Rule 的条件、理由代码；
- Knowledge：可泛化正反例，不包含 Task ID 和逐图答案；
- Prompt：严格声明输入字段与 JSON 输出 Schema；
- Backend Adapter：模型服务协议、图片编码、结构化输出和有限重试。

修改任一项都创建新版本和新 Agent Run ID。Agent 必须接收完整 Rule 排名和 Rule Top1；模型
输出 Schema 失败、视觉证据矛盾或覆盖条件不成立时回退 Rule。机器建议不是人工金标。

### 6.1 Agent 四层不要混改

- 视觉判断原则变化改 Skill；
- 新增可泛化正反例改 Knowledge；
- 输入组织、Schema 或字段说明变化改 Prompt；
- 模型服务、图片编码、超时和重试变化改 Backend Adapter/Profile。

Knowledge 禁止出现 Task ID、文件名或“这张图必须选 Crop”一类逐图答案。Agent 的最小离线
测试覆盖 Prompt 渲染、候选别名、完整排列修复、非法 JSON、缺项/重复项、Rule 回退和无密钥
路径；真实 Replay 需要检查 Schema 有效率、超时、平均/P95 时延、Top1 变化、人工最佳命中和
严重 C/D。Agent 自主权扩大必须通过独立人工 Validation，不能只看模型理由更长或置信度更高。

## 7. 新增重定向算法

算法实现 `CandidateMethod.generate(...) -> MethodOutput`：

1. 只使用共享 Analysis/importance/tolerance；
2. 成功输出必须是准确目标尺寸 RGB；
3. 失败返回结构化状态，不从分母删除；
4. 保存 `TransformRecord`，包含裁剪、缩放或形变风险；
5. 在 `built_in_methods()` 注册稳定 ID；
6. 为多个目标比例、确定性和失败隔离写测试；
7. 默认启用前使用真实图片评测。

### 7.1 开发边界和验收

不要在 Runner 中增加 `if method == "new_method"`，也不要复用旧方法 ID 悄悄替换像素实现。
正确路径是：实现 `CandidateMethod`，在 `built_in_methods()` 注册稳定 ID，为默认 Profile 增加
版本化 MethodConfig，并让 `TransformRecord` 记录该算法特有的操作和风险。

新方法至少保证：

- 输入数组不被原地修改，输出为目标尺寸 RGB `uint8`；
- 同一输入、配置和 seed 结果确定；
- 使用共享 Analysis，不私自重跑 Detector；
- 超时、不可行和异常返回结构化失败，不能从七方法分母消失；
- `UNSAFE` 仍保留图片和风险，`FAILED` 不伪造输出；
- 多目标比例、极小图、无保护区、密集保护区和中途失败都有测试。

真实 Smoke 应覆盖人物、多人关系、文字海报、商品/Logo、结构线和复杂背景，并同时查看像素、
Transform、Rule 指标、耗时和峰值内存。新增方法改变候选像素，必须创建新 Generation Run；
只有质量、失败率、时延和资源均达到约定标准后，才能加入默认七方法 Profile。

## 8. 提交前检查

```powershell
ruff check src tests scripts
python -m pytest -q
retarget-engine plugins list
retarget-engine strategy show
```

还应完成：干净 Clone、公司镜像下 Bootstrap、`doctor`、一张真实图片 Generation/Rule、UI
载入，以及本次新增 Adapter 的 Fake E2E。真实外部 API Smoke 必须由负责人明确授权，次数、
预算、结果和失败都要记录。
