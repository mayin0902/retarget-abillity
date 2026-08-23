# Movie60 历史实验

这里保留 Movie60 历史校准、Agent/AIGC 实验、中文 v8 Replay、报告和 Release 构建证据，
便于从 Git 历史与已发布资产复查结果。它们不是新开发者的日常入口，正式 `src/` 不得依赖
本目录；新业务也不应复制这里的实验编排作为生产入口。

正式开发入口按顺序是：

1. 根目录 `README.md`；
2. `docs/QUICKSTART.md`；
3. `docs/CODE_GUIDE.md`；
4. `docs/EXTENSION_GUIDE.md`。

需要重跑某个历史实验时，从仓库根目录执行相应脚本，并先核对脚本引用的冻结 Run、授权
素材、模型和费用边界。
