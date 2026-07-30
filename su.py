"""
提权模块
验证 su -c 是否可用，KernelSU 路径已移除。
"""
import os
import subprocess


def ensure_root() -> bool:
    """
    验证 su -c 是否可用。
    KernelSU syscall 方式在 Termux 中被 seccomp 拦截，不再使用。
    """
    if os.getuid() == 0:
        return True

    try:
        result = subprocess.run(
            ["su", "-c", "id -u"],
            capture_output=True, timeout=5, text=True
        )
        return result.returncode == 0 and result.stdout.strip() == "0"
    except Exception:
        return False