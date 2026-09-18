# Harbor AI Commerce

跨境电商应用项目，包含 Python 应用代码、Web 页面资源和 PowerShell 启动脚本。

## 项目结构

```text
.
├── app/                  # Python 应用代码
│   ├── __init__.py
│   ├── agents.py
│   ├── db.py
│   ├── main.py
│   ├── schemas.py
│   └── services.py
├── scripts/
│   └── start.ps1         # 启动脚本
├── web/                  # Web 页面、脚本及样式
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── .env.example          # 环境变量配置示例
├── .gitignore
├── pytest.ini            # pytest 配置
├── requirements.txt      # 应用依赖
└── requirements-dev.txt  # 开发依赖
```

## 本地开发

以下命令适用于 Windows PowerShell，请在项目根目录执行。

### 1. 创建虚拟环境

确保已安装 Python，且 `python` 命令可用：

```powershell
python --version
python -m venv .venv
```

### 2. 安装依赖

直接使用虚拟环境中的 Python，无需激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

需要运行测试或进行开发时，安装开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Python 版本及第三方服务要求，请结合依赖文件和实际配置确认。

### 3. 配置环境变量

首次运行时，将配置示例复制为本地配置；已有 `.env` 时保留原文件：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

根据 `.env.example` 中的变量填写本地配置。真实密钥、访问令牌和密码仅保存在本地，示例文件中应使用空值或明确的占位符。

### 4. 启动项目

项目提供 PowerShell 启动脚本：

```powershell
.\scripts\start.ps1
```

具体启动参数、服务依赖和访问地址，以脚本内容及运行输出为准。

## 测试

项目包含 pytest 配置。安装开发依赖后，可执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

如果提示未发现测试，请检查当前项目是否包含测试用例。

## 提交约定

提交前检查暂存内容：

```powershell
git status --short
git diff --cached
```

以下本地配置及生成文件不应提交：

- `.env` 及其他包含真实凭据的环境配置。
- `data/` 中的本地数据。
- `.venv/` 等虚拟环境目录。
- Python 缓存、测试缓存和日志。
- `artifacts/` 中的生成产物。

`.env.example` 可纳入版本控制，但不能包含真实凭据。已经被 Git 跟踪的文件，需要从索引中移除后，忽略规则才会生效。

## 项目仓库

https://github.com/Wissky-cmd/harbor-ai-commerce