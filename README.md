# LitDiscovery

LitDiscovery 是一个由文献检索与文本信息提取驱动的科研发现 Agent。项目将文献检索、全文获取、文档预处理、结构化信息提取、研究空白检测、证据追踪、数据库验证和报告生成整合为可断点续跑的工作流。

当前提取提示词与数据结构针对相变材料进行了专门优化，同时保留可扩展的领域注册机制，可用于其他材料研究方向。

## 快速开始

> 下面所有命令都要在**命令行终端**里输入。如果你是第一次接触，先花一分钟看「0. 这些指令在哪里输入」，照着做即可。

### 0. 这些指令在哪里输入？

本项目通过命令行运行。「终端」（也叫 Terminal / 命令行 / Shell）就是一个输入文字命令的窗口。打开方式：

- **Windows**：点「开始」菜单 → 输入 `PowerShell` 或 `cmd` → 回车。
- **macOS**：按 `Cmd + 空格` 唤出聚焦搜索 → 输入 `Terminal`（终端）→ 回车。
- **Linux**：应用菜单里一般叫「终端 / Terminal」。
- **VS Code（推荐）**：打开项目文件夹后，点顶部菜单「终端 Terminal」→「新建终端 New Terminal」。

打开终端后，先进入项目所在的文件夹（把下面的路径换成你实际存放项目的位置）：

```bash
# Windows（PowerShell / cmd）
cd C:\你的路径\LLM_material

# macOS / Linux
cd ~/你的路径/LLM_material
```

> 小技巧：在 Windows 资源管理器里**打开项目文件夹**，在顶部地址栏输入 `cmd` 回车，终端会直接定位到这个文件夹，无需手动 `cd`。

### 准备工作：拿到代码与密钥

1. **下载代码**。在 GitHub 仓库页面点绿色「Code」→「Download ZIP」下载并解压；或已安装 Git 的话：

   ```bash
   git clone https://github.com/shenzoutian/Agent_material.git
   cd Agent_material
   ```

2. **准备密钥**。本项目依赖 LLM 服务，至少需要一个 DeepSeek API Key（去 DeepSeek 开放平台注册后获取）。先把模板复制成你自己的配置：

   ```bash
   # Windows PowerShell：
   Copy-Item .env.example .env

   # macOS / Linux（以及 Windows 的 Git Bash）：
   cp .env.example .env
   ```

   （Windows 的 cmd.exe 请用 `copy .env.example .env`）

   然后用任意文本编辑器（记事本、VS Code 等）打开 `.env` 文件，在 `DEEPSEEK_API_KEY=` 后面填入你的密钥并保存。其余密钥按需填写，缺哪项对应功能会自动跳过，不影响主流程。

### 方式一：Docker（推荐，无需装 Python 和依赖）

前置：先到 [docker.com](https://www.docker.com/products/docker-desktop/) 安装 **Docker Desktop** 并启动它（任务栏出现 Docker 图标即可）。

在**项目文件夹**下打开终端，依次执行：

```bash
# 第 1 步：构建镜像（首次会下载较多内容，耐心等待几分钟）
docker compose build

# 第 2 步：环境自检——检查 Python / 依赖 / 密钥是否就绪
docker compose run --rm --entrypoint python litdiscovery tools/doctor.py

# 第 3 步：运行完整科研工作流（把引号里的需求换成你自己的）
docker compose run --rm litdiscovery run --requirement "识别相变材料中的结构-性能研究空白" --auto
```

说明：`docker compose run` 会自动挂载 `./artifacts`（运行产物）、注入 `.env`（密钥）。运行结果会出现在项目下的 `artifacts/` 文件夹里。

### 方式二：本地安装（需要 Python 3.10+）

前置：先到 [python.org](https://www.python.org/downloads/) 安装 Python 3.10 或更高版本，安装时务必勾选 **Add Python to PATH**。

在**项目文件夹**下打开终端，依次执行：

```bash
# 第 1 步：创建虚拟环境（隔离本项目依赖，避免污染系统）
python -m venv .venv

# 第 2 步：激活虚拟环境（Windows 用这一行）
.venv\Scripts\activate
# macOS / Linux 用这一行（二选一）
source .venv/bin/activate

# 第 3 步：安装依赖（首次会下载，耐心等待）
pip install -r requirements.txt

# 第 4 步：安装 litdiscovery 命令
pip install -e .

# 第 5 步：环境自检
python tools/doctor.py

# 第 6 步：运行完整科研工作流（把引号里的需求换成你自己的）
litdiscovery run --requirement "识别相变材料中的结构-性能研究空白" --auto
```

> 第 2 步激活成功后，命令行最前面会出现 `(.venv)` 字样。以后每次**重新打开终端**，都要先重新执行第 2 步「激活」，再运行后续命令。

## 系统架构

```text
用户科研需求
      |
      v
Planner -> plan.v3.json（执行计划）
      |
      v
确定性 Executor <-> run_state.json（运行状态）
      |
      +-- researcher_agent  文献检索与引用扩展
      +-- filter_agent      文献筛选、全文获取与预处理
      +-- extractor_agent   材料、工艺、结构与性能提取
      +-- gap_chain         证据物化与研究空白检测
      +-- validate          材料数据库交叉验证
      +-- review_agent      运行异常诊断与恢复建议
      +-- report_writer     科研报告生成
```

运行产物统一存放在 `artifacts/` 下，并已从版本控制中排除。原始文献按照文件格式进入对应目录，再统一转换与预处理：

```text
pdfs/ xmls/ txts/ texs/ -> markdowns/ -> end_mds/
```

## 环境要求

- Python 3.10 或更高版本（Docker 方式无需本地安装）
- OpenAI API 兼容的对话模型服务（如 DeepSeek）
- 可选：使用 Docling 完成 PDF 到 Markdown 的转换

## 安装

### 依赖锁定

`requirements.txt` 锁定了与已知可用环境（Python 3.12）一致的精确版本，避免版本漂移：

```bash
pip install -r requirements.txt
```

其中已包含 PDF 转换（`docling`）与测试（`pytest`）；如不需要 PDF 转换，可删除该文件中 `docling` 一行。

### 源码安装（提供 `litdiscovery` 命令）

```bash
pip install -e .
```

如需按 pyproject.toml 的可选依赖安装：`pip install -e ".[pdf,test]"`。

## 配置

通过环境变量或本地 `.env` 文件配置服务凭据。`.env` 已被 Git 忽略，不会上传至仓库；模板见 `.env.example`。

常用环境变量：

```text
DEEPSEEK_API_KEY          # 核心 LLM（planner / 提取全链路，必需）
OPENAI_API_KEY            # OpenAI Deep Research（前沿综述）
APIFY_API_KEY             # 文献检索（Apify Academic Paper Scraper）
TAVILY_API_KEY            # 联网前沿检索（Tavily）
UNPAYWALL_EMAIL           # 全文下载
ELSEVIER_API_KEY          # 全文下载
MATERIALS_PROJECT_API_KEY # 材料数据库验证（Materials Project）
```

只需配置当前工作流实际使用的服务。完整变量列表见 `.env.example`。

## 环境自检

运行前可用独立自检脚本一次性检查 Python 版本、依赖安装与密钥配置，避免跑到一半才因缺密钥 / 缺依赖而报错：

```bash
python tools/doctor.py
# 或
python -m tools.doctor
```

它会列出：缺失的密钥、影响哪个阶段、以及去哪申请。Docker 用户可在容器内执行：

```bash
docker compose run --rm --entrypoint python litdiscovery tools/doctor.py
```

## 使用方法

运行由 Planner 编排的完整科研工作流：

```bash
litdiscovery run --requirement "识别相变材料中的结构-性能研究空白" --auto
```

使用已有的 `plan.v3.json` 和 `run_state.json` 恢复异常中断的批次：

```bash
litdiscovery run --requirement run --batch artifacts/batches/<batch-name> --auto
```

常用阶段命令：

```bash
litdiscovery retrieve --help
litdiscovery download --help
litdiscovery preprocess --help
litdiscovery extract --help
litdiscovery gap --help
litdiscovery report --help
```

查看已注册的 Agent 和工具：

```bash
litdiscovery run --list-agents
litdiscovery tools
```

## 测试

```bash
python -m pytest -q
```

Executor 会把步骤级状态记录在 `run_state.json` 中。恢复运行时，已经成功的步骤会被自动跳过，只执行失败或尚未完成的步骤。结构化执行日志和 `review_agent` 生成的审查报告用于提供异常上下文，所有实际科研产物均不会写入代码仓库。
