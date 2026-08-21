# 公司 pip 镜像配置留白

本页必须在安装环境前由项目负责人填写或向开发同学提供。

```text
公司 PyPI index-url：

公司 trusted-host（如需要）：

公司 Paddle/模型 wheel 制品库（如需要）：

离线 wheelhouse 路径（如使用）：

```

仓库和 Release 不包含公司的真实地址，也不会自动回退公网 PyPI。负责人给出地址后，
将其作为 `--index-url`/`--trusted-host` 参数使用；Movie60 v3 数据包则填写根目录的
`PIP_MIRROR.ini`。
