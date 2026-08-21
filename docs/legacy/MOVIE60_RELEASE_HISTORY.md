# Movie60 版本与遗留资产索引

## 当前唯一推荐版本

`movie60-review-v3`：Dataset `movie-visual-60-v1@1.0.0`，Evaluation
`movie60-human-aligned-v3-2-2-20260821`，Strategy `movie60@3.2.2`。

## 旧版本

| 版本 | 做过什么 | 主要问题 | 查找方式 |
|---|---|---|---|
| `movie60-review-v1` | 第一版内部人工评审材料 | 旧 Rule/Agent，人工记录少 | 私有 GitHub Release 标签 `movie60-review-v1` |
| `movie60-review-v2` | 60×7、Focus20 AIGC、早期 v3 证据 | `all60` 主表与最新 v3.2.2 证据分离；启动器依赖外部仓库相对路径 | 私有 GitHub Release 标签 `movie60-review-v2`；原本机目录未删除 |
| v3/v3.1/v3.2 研究线 | 三轮 Rule/Agent 代理校准与留出比较 | 标签是人工粗审过的模型代理建议，不是独立人工真值 | `docs/reviews/movie60-v3/` 和 Git 历史 |

## 本地中间资产

以下本机目录属于构建、解压或 API 上传验证，不是产品入口，也不提交 Git：

- `local_data/movie60-review-v2-staging/`；
- `local_data/movie60-review-v2-verify/`；
- `local_data/movie60-review-v2-det-verify/`；
- `local_data/release-build/`；
- `local_data/api-upload-*`。

旧目录不删除、不覆盖。需要复核旧结论时按 Release 标签、Commit、Run ID 或上述路径查找；
日常开发和人工评审只使用 `movie60-review-v3`。
