# mcp_server

在 Termux 上运行的 MCP服务器，为 AI 助手提供shell和文件编辑能力（其实不止于此，有root可以管理硬件）

## 所需工具

- **Termux**（必需）— Android 上的终端模拟器
- **Python 3.10+**（必需）— 运行环境
- **pip 包：`fastmcp`**（必需）— MCP 框架
- **DroidSpaces、KernelSU**（推荐）— 容器环境支持、并且让ai可以访问你的设备

## 安装

```bash
# 1. 安装 Python 依赖
pip install fastmcp

# 2. 克隆本项目到 Termux （推荐~目录）
git clone https://github.com/hgcjd666666/mcp_server

# 3. 运行服务器
python run.py
```

服务器默认监听 `http://127.0.0.1:2749/mcp`，可通过环境变量 `PORT` 修改端口。

## 特点

- **Shell 命令执行** — 在 Termux 本地或 DroidSpaces 容器中执行命令，支持超时控制与输出截断
- **文件操作** — 读写替换文件，自动处理 SELinux 上下文和文件权限（chown + chcon）
- **持久化终端会话** — 交互式终端管理，支持长时间运行命令（有点bug，莫名其妙会让服务器的日志乱掉，但问题不大）
- **输出安全** — 内置截断机制，防止过多输出浪费token
- **更好用的文件替换工具** — 让ai指定行，最后输出diff便于检查
