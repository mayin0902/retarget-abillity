# 策略、插件、回放与发布

## 1. 不可变数据链

```text
Dataset
  └─ Run（输入、共享分析、七候选、Transform、性能、配置快照）
       └─ Evaluation（一个 Strategy 的指标、Rule 决策、Strategy 快照）
            └─ Agent Run（一个模型/Profile/Skill/Prompt 的建议）
                 └─ Review sidecar（人工当前结论 + 追加历史）
```

新规则创建新 Evaluation，不覆盖旧指标；新 Agent 创建新 Agent Run，不覆盖旧建议。正式 Rule
选择位于 `evaluations/<id>/rule-decisions/`。Strategy 快照包含 Bundle、Scoring、Selection、
Override、Prompt、Agent Skill 和独立 Knowledge，`snapshot.json` 保存逐文件 SHA-256。

## 2. 查看当前版本

```powershell
# 唯一 active Strategy、阈值和哈希
.\.venv\Scripts\retarget-engine.exe strategy show

# 查看历史版本
.\.venv\Scripts\retarget-engine.exe strategy show `
  strategies\movie60\v3_2_2\bundle.yaml

# 字段级比较
.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v3_2_2\bundle.yaml `
  strategies\movie60\v3_3\bundle.yaml

# 允许执行的插件
.\.venv\Scripts\retarget-engine.exe plugins list
```

历史 Strategy、旧 Run 和旧 Review 都保留。`strategies/registry.yaml` 只能有一个 `active`。

## 3. StrategyBundle 怎样拆分

一个版本目录通常包含：

```text
bundle.yaml          # 版本、父版本、各策略和插件 ID
scoring.yaml         # 权重、A/B/C/D 阈值、回归惩罚、通用门禁
selection.yaml       # Rule 排序顺序与 Agent 触发阈值
override.yaml        # Rule/Agent 覆盖约束与 advisory/challenge 模式
agent-skill.yaml     # Agent 行为原则和允许理由代码
agent-knowledge.yaml # 可选的通用案例知识
prompts/             # 严格输入字段的 Prompt Bundle
```

规则只允许通用场景、方法和数值条件，不能按 task ID、文件名或某张图片硬编码。

## 4. 只调参数怎样迭代

1. 复制当前版本到新目录，例如 `strategies/movie60/v3_4/`；
2. 更新 `bundle.yaml` 的 `version`、`parent_strategy` 和说明；
3. 修改 `scoring.yaml` 中权重、阈值、惩罚或门禁；
4. 保持 A≥B≥C，权重组总和为正；
5. 执行 `strategy show` 和 `strategy diff`；
6. 对冻结 Run 创建新 Evaluation ID；
7. 在 Calibration 看混淆与案例，冻结后只跑一次 Validation；
8. 通过后才把 registry 的 active 切到新 Bundle。

A/B/C/D 分数范围完全可配置，例如把 A 改为 80 分以上只需新版本修改
`proxy_a_threshold: 80.0`；旧版本和旧结果不变。

## 5. 新增或替换 Rule 实现

插件目录在 `plugin_catalog.py`，是受控白名单，不支持 YAML 任意 Python import。

### 新检测器套件

1. 实现输入 RGB、输出 `tuple[RegionRecord,...]` 的 Detector Suite Adapter；
2. 固定模型 ID/revision、阈值、运行时和本地权重审计；
3. 注册新的 `detector_suite_plugin` ID；
4. 添加原图/候选重复检测测试、缺模型失败测试和离线测试；
5. 新 Strategy 引用该 ID，不删除旧 Adapter。

### 新 Reference Scorer

1. 实现与当前 Scorer 相同的结构化输入输出；
2. 缺测保持 `None`，不能用 0 冒充；
3. 硬失败、软分和等级门禁分层；
4. 注册 `reference_scorer_plugin`；
5. 对同一冻结 Run Replay 比较，而不是重新生成候选。

### 新 Rule Selector

Selector 输入完整 CandidateEvidence，输出每个 Candidate 恰好一次的完整排列。注册
`rule_selector_plugin` 后，Evaluation 会在 `rule_selection.py` 统一校验并持久化；UI 和 Agent
不会再自行排序。

## 6. 新增重定向算法

方法 Adapter 需实现统一 `generate(...) -> MethodOutput`：

1. 新建 `src/retarget_agent/methods/<name>.py`；
2. 声明稳定 `method_id/method_version`；
3. 只消费共享 Analysis/importance/tolerance，不自行修改原图证据；
4. 输出精确目标尺寸 RGB 和可审计 `TransformRecord`；
5. 风险特征必须能解释算法失败，例如 seam importance、crop cut count、mesh Jacobian；
6. 在方法注册表加入 ID；
7. 新建 method profile 或 Strategy，不改历史 profile；
8. 给正方形、横屏、竖屏、4:3、3:4 添加永久 Smoke。

默认 `retarget_default_v1` 的七方法与参数只由 `config.py` 的 profile 注册表提供，单图、批量
和 RunConfig 共用，避免入口之间漂移。

## 7. Agent Skill、Knowledge 与 Prompt

Skill 规定行为、排序优先级、AIGC 门禁和理由代码；Knowledge 只放可泛化案例，禁止样本 ID
和逐图答案。Skill 可用 `knowledge_file` 引用同目录 YAML。Loader 会：

1. 拒绝绝对路径和 `..`；
2. 校验 Knowledge schema；
3. 合并案例后渲染 Prompt；
4. 把 Skill 和 Knowledge 两个文件都加入 Strategy SHA；
5. 在 Evaluation/Agent Run 中一起快照。

需要迭代时：新建 Skill/Knowledge 版本，再由新 Strategy 引用；不要修改已发布 3.3。当前
仓库还保留独立中文 v8 Skill/Knowledge 作为下一版素材，但 current 3.3 的既有 Agent 结果
没有因此被伪装成“已重跑”。

Agent Backend 输入还必须包含 Rule Top1 和完整 Rule 排名。返回 JSON 必须是候选的精确全
排列，原图不能进入排名；Schema 错误按有限次数重试，仍失败则回退 Rule并保留错误证据。

## 8. Agent Profile 与安全边界

```powershell
Copy-Item configs\agent-profile.private.example.yaml `
  configs\agent-profile.private.yaml
$env:RETARGET_AGENT_API_KEY = "<临时Token>"
```

私有 Profile 只写模型地址、模型标识和环境变量名；Token 不落盘。只允许 HTTPS 或本机
loopback HTTP。没有显式 Profile 时 Agent 绝不运行。普通 `run image/batch` 固定
`allow_external_aigc=False`，所以 Agent 建议外部生成也不会自动产生付费调用。

## 9. 手工 Dataset、Generation 与 Evaluation

标准 Dataset 是：

```text
dataset.yaml + sources.csv + targets.csv + tasks.csv + images/
```

Run 配置是 `run.yaml`。完整手工流程：

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate <dataset-dir>
.\.venv\Scripts\retarget-engine.exe run generate <dataset-dir>\run.yaml
.\.venv\Scripts\retarget-engine.exe evaluate runs\<run-id> `
  --evaluation-id rule-v-next `
  --strategy strategies\movie60\v3_3\bundle.yaml
```

Evaluation 会冻结 Strategy 和 Rule 决策，但不会重新生成图片。若同一 Evaluation ID 已存在，
命令拒绝覆盖。

## 10. 哪些修改需要重跑

| 修改 | Generation | Evaluation | Agent Run | 人工复核 |
|---|---:|---:|---:|---:|
| A/B/C/D 阈值、权重、Rule 门禁 | 否 | 是 | 如比较 Agent 则是 | 建议抽检 |
| Rule Selector 顺序 | 否 | 是 | 是 | 建议抽检 |
| Agent Skill/Knowledge/Prompt | 否 | 否 | 是 | 是 |
| 检测器模型或候选重检逻辑 | 否* | 是 | 是 | 是 |
| 原图保护分析或重定向算法 | 是 | 是 | 是 | 是 |
| 仅 UI 文案/样式 | 否 | 否 | 否 | Smoke |

`*` 若算法本身也依赖该检测器的新原图保护区域，则 Generation 也必须重跑。

## 11. 外部候选导入

```text
D:\review-case\
├── source.jpg
└── candidates\
    ├── crop.png
    └── generated.png
```

```powershell
.\.venv\Scripts\retarget-engine.exe review import D:\review-case
.\.venv\Scripts\retarget-engine.exe review open `
  local_data\reviews\review-case
```

导入只复制并哈希图片，不制造 Rule/Agent 证据。要获得 Reference 分数，使用 `score reference`；
要获得完整七方法与 Rule 排名，使用 `run image`。

## 12. Release 和数据边界

- Git：代码、配置、不可变策略、测试、小型 manifest 和四份主文档；
- 私有 Movie60 Release：经授权像素、处理结果、机器/人工评审和必要说明；
- 本地忽略：模型权重、Run、缓存、Token、临时实验和不可再分发素材。

发布软件版本前：全量 pytest、Ruff、五比例 Smoke、干净 Clone 安装和单图 result 验证必须
通过。Movie60 数据 Release 不因纯代码升级自动覆盖；需要重打包时创建新标签并校验资产，
保留旧标签。

### 12.1 当前与旧打包入口

- 当前唯一推荐入口：`scripts/package_movie60_review_v3.py`；
- 公共确定性 ZIP/SHA 工具：`scripts/release_packaging.py`；
- `scripts/package_movie60_release.py` 只用于复现历史 v1/v2，命令帮助会明确标为 legacy。

普通交接和使用不需要执行任何打包脚本。下载/物化使用
`scripts/materialize_review.ps1`，它会优先检查
`local_data/release_assets/<CURRENT_RELEASE tag>/` 的三个原始资产。

## 13. 公司网络模型下载决策

**DEC-20260821-01｜公司网络下固定模型下载 SSL 降级策略**

1. 正常请求显式使用 `verify=True`；
2. 只有捕获 `requests.exceptions.SSLError` 才允许重试；
3. 重试只允许 `materialize_analyzer_models.py` 的 `ALLOWED_HOSTS`，每次重定向前重新检查
   HTTPS 与 Host；
4. manifest 必须同时有固定 `sha256`、`expected_bytes` 和普通文件名；
5. 降级请求使用 `verify=False`，打印明确 warning，只抑制该次请求的
   `InsecureRequestWarning`；
6. 下载超过 pin、字节数不等或 SHA-256 不等都会删除 `.part` 并使 Bootstrap 失败；
7. 已有且 pin 正确的模型直接复用，不访问网络。

准确的安全口径是：TLS 服务端身份校验在降级请求中关闭，但下载产物仍通过固定 SHA-256
和字节数校验完整性与预期内容。这个例外不能用于没有内容 pin 的 pip、普通 API、Agent、
AIGC 或任意用户 URL。

普通 Bootstrap 使用
`datasets/analyzer_models_company_cpu_v2/download_manifest.csv`，其中只保留当前正式路线需要
预下载的 YuNet。`datasets/analyzer_models_v1/model_manifest.csv` 中 PPOCRv3、CRNN、YOLOX
仅供显式历史回放，不再由正常 Bootstrap 下载。

## 14. 常见故障

- `.venv` 从其他电脑复制：删除或移走后重新 Bootstrap；
- 新 Run UI 缺候选：检查 `candidate.json`；失败候选应显示 N/A，不能被隐藏；
- UI 排名与 Rule 不符：检查 `rule-decisions/<task-id>.json`，不要手工按 Quality 重排；
- Strategy 快照缺 Knowledge：说明旧 Loader 或旧 Run，只能标为历史证据；
- Agent 显示未运行：确认显式 Profile、模型服务、环境变量和 Agent Run ID；
- 端口占用：`review open <path> --port 8766`；
- 固定 YuNet 下载出现证书错误：脚本会按 DEC-20260821-01 自动降级并验证内容 pin；非
  `SSLError`、非 allowlist Host 或 Hash/字节数不符仍会直接失败；
- PP-OCRv6/D-FINE 第三方模型缓存受限：联系公司模型缓存负责人；不要把固定资源的 SSL
  例外扩展到无 pin 的第三方请求。
