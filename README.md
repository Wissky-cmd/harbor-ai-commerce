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

## 打开与操作测试流程

以下流程适用于 Windows PowerShell。需要保持启动服务的 PowerShell 窗口一直运行；关闭窗口后，`127.0.0.1` 会停止监听，浏览器会提示“拒绝建立连接”。

### 1. 启动服务

在项目根目录执行：

```powershell
cd "E:\my_project\跨境电商"
.\scripts\start.ps1
```

看到下面的输出后，说明服务已启动：

```text
Uvicorn running on http://127.0.0.1:8000
```

然后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。如果页面提示无法访问，回到 PowerShell 重新执行启动命令，并确认窗口没有关闭。

### 2. 登录演示环境

当前默认是本地演示模式，登录页会预填管理员账号：

| 角色 | 邮箱 | 密码 |
| --- | --- | --- |
| 管理员 | `admin@harbor.local` | `HarborDemo2026!` |
| 业务员 | `sales@harbor.local` | `HarborDemo2026!` |
| 跟单员 | `ops@harbor.local` | `HarborDemo2026!` |
| 只读访客 | `viewer@harbor.local` | `HarborDemo2026!` |

演示数据库会自动创建在 `data/harbor.db`。该目录已被 `.gitignore` 忽略，不要提交到 GitHub。

### 3. 验证工作台与 AI 助手

1. 进入“工作台概览”，应看到 6 笔演示订单、5 笔履约中订单和交期风险提示。
2. 进入“AI 助手中心”，选择“AI 跟单员”。
3. 保留默认问题“检查当前订单的交期风险，并给出跟进建议”，点击“运行助手”。
4. 页面应展示风险订单、建议下一步、知识来源和执行轨迹。
5. 在“最近执行”中点击“查看记录”，确认记录刷新后仍然存在。

未配置 `OLLAMA_URL` 时，执行方式显示 `rules` 是正常的：系统使用固定业务规则和权限过滤后的企业知识检索，不会伪装成大模型回答。

### 4. 验证报价到订单闭环

进入“选品与商品”，搜索 `HB-LN-001`，点击“创建报价”。在“智能报价”中填写：

| 字段 | 测试值 |
| --- | --- |
| 商品 | 法式亚麻混纺衬衫 |
| 客户 | Nord & Co. |
| 数量 | `500` |
| 贸易术语 | `FOB` |
| 人民币 / 美元汇率 | `7.20` |
| 目标毛利率 | `30%` |
| 包装成本 | `3` CNY / 件 |
| 国内物流 | `2` CNY / 件 |

依次点击“计算预览” → “保存并提交审批” → “人工审批” → “确认审批通过” → “确认转订单”。最后在“订单与履约”中确认出现一笔“打样确认”订单。

异常校验：把数量改成 `50`，应提示数量不得低于 MOQ `100`；目标毛利低于 `25%` 时，业务员不能自行审批，必须使用管理员账号。

### 5. 验证订单流转规则

订单阶段必须按以下顺序流转：

```text
打样确认 → 大货生产 → 质量检验 → 物流出运 → 已交付 → 已结清
```

以演示订单 `HB-2609-1048`（当前“大货生产”、进度 `68%`）为例：

1. 打开“订单与履约”，点击“查看 / 跟进”。把下一节点改为“质量检验”，保持进度 `68`，保存时应被拒绝；把进度改为 `100` 后保存，才会进入“质量检验”。
2. 重新打开订单，选择“物流出运”，保持进度 `100`，不勾选“已确认验货通过”并保存，应被拒绝；勾选后再次保存，才会进入“物流出运”。
3. 进入“物流出运”后，选择“已交付”、保持进度 `100`，填写跟进记录并保存。
4. 在“已交付”阶段直接选择“已结清”，由于该订单只收款一半，应提示“货款未结清，不能关闭订单”。
5. 管理员在订单弹窗下方登记剩余货款 `9960`，重新打开订单并选择“已结清”，保存后应成功关闭订单。

系统还会拒绝跳过阶段、让生产进度倒退，以及在旧版本号上保存订单（并发更新时返回 409）。登记收款只写入业务记录，不会触发真实支付。

### 6. 验证知识库、客户与审计

- 在“企业知识库”搜索“生产进度不足时，如何处理交期风险？”，应返回带文档标题、版本和片段编号的引用。
- 管理员可以添加一篇 SOP；保存后重新检索其中的关键词。
- 在“客户管理”新增客户或点击“记录已联系”，确认只记录业务状态，不会自动发送邮件或企业微信消息。
- 管理员进入“集成与审计”，确认报价、审批、订单更新、收款和 `agent.run` 都有审计记录。
- 使用 `sales@harbor.local`、`ops@harbor.local` 和 `viewer@harbor.local` 分别验证报价、订单和客户资料权限。

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

首次运行时，可复制配置示例作为参考；当前应用直接读取操作系统环境变量，不会自动加载 `.env` 文件。演示模式无需额外配置：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

根据 `.env.example` 中的变量设置操作系统环境变量。真实密钥、访问令牌和密码仅保存在本地，示例文件中应使用空值或明确的占位符。生产环境必须关闭演示模式、设置至少 16 位管理员密码，并启用安全 Cookie。

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
