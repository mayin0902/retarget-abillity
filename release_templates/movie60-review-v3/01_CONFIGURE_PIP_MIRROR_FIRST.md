# 第一步：由负责人填写公司 pip 镜像

在安装任何 Python 依赖前，请由项目负责人填写本目录的 `PIP_MIRROR.ini`：

```text
INDEX_URL=
TRUSTED_HOST=
```

- `INDEX_URL`：必填，例如公司 PyPI simple 地址；
- `TRUSTED_HOST`：仅在公司镜像要求时填写；
- 本交付包不会猜测、保存或上传公司的镜像地址；
- 未填写 `INDEX_URL` 时，`INSTALL_WINDOWS.bat` 会主动停止，不会访问公网 PyPI。

填写后再双击 `INSTALL_WINDOWS.bat`。安装只写入本数据包的 `.review-venv`，不会修改
系统 Python，也不能把该虚拟环境复制到另一台电脑。
