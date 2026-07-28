
import os
import re
import asyncio
from typing import Dict, Tuple

# ======================== preexec 辅助 ========================
def _drop_privileges():
	"""
	保留用于兼容；实际不再降权，
	因为会有奇奇怪怪的环境变量问题，如home变成了.suroot
	"""
	pass

# ======================== 运行模式 ========================
IS_ROOT = os.getuid() == 0
ROOT_FILE_DIR = "/data/data/com.termux/files/home/droidspaces/"

# ======================== 常量定义 ========================
BASE_DIR = "/data/data/com.termux/files/home/mcp"
TRUNCATE_LIMIT = 10240
TRUNCATE_PREFIX = "..."

# Droidspaces
DROIDSPACES_CONTAINER = "u24"  # 硬编码容器名

# 超时相关
DEFAULT_TIMEOUT = 30          # "auto" 或非法输入时的默认秒数
MIN_TIMEOUT = 20              # 非正数时的兜底值
MAX_TIMEOUT = 100             # 允许的最大秒数

# 终端交互 auto 模式
TERM_IDLE_TIMEOUT = 0.5       # 无新数据等待秒数
TERM_TOTAL_TIMEOUT = 30.0     # 最长等待总秒数
TERM_HIDE_INPUT_TIMEOUT = 2.0 # 隐藏输入回显的最长等待秒数

# ANSI 转义正则
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[PX^_].*?\x1b\\')

# 会话存储及锁
sessions: Dict[Tuple[str, str], dict] = {}
sessions_lock = asyncio.Lock()

# ======================== 启动时提权检测 ========================
# 优先 KernelSU，失败则回退到 su -c
from . import su

_HAS_ROOT = su.ensure_root()
if _HAS_ROOT and os.getuid() == 0:
    SU_CMD = []  # KernelSU 成功，本进程已是 root
else:
    SU_CMD = ["su", "-c"]  # su 或保底
