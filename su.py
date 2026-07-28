"""
提权模块
优先 KernelSU 系统调用提权，失败则回退到 su -c
"""

import ctypes
import ctypes.util
import os
import subprocess

# ── KernelSU 常量 (来自 uapi/supercall.h) ─────────────────────
KSU_INSTALL_MAGIC1 = 0xDEADBEEF
KSU_INSTALL_MAGIC2 = 0xCAFEBABE

NR_REBOOT_TABLE = {
    'aarch64': 142,
    'armv7l': 169,
    'arm': 169,
    'x86_64': 169,
    'i686': 88,
}

KSU_IOCTL_GRANT_ROOT = (ord('K') << 8) | 1  # 0x4B01

# ── 全局 libc ──
_libc = ctypes.CDLL(ctypes.util.find_library('c'))
_libc.syscall.restype = ctypes.c_long
_libc.ioctl.restype = ctypes.c_int


def _get_reboot_nr() -> int:
    return NR_REBOOT_TABLE.get(os.uname().machine, 142)


def _scan_ksu_fd() -> int:
    """扫描 /proc/self/fd/ 寻找已安装的 [ksu_driver] fd"""
    try:
        fds = os.listdir('/proc/self/fd')
    except FileNotFoundError:
        return -1
    for fd_str in fds:
        try:
            fd = int(fd_str)
        except ValueError:
            continue
        try:
            target = os.readlink(f'/proc/self/fd/{fd}')
            if '[ksu_driver]' in target:
                return fd
        except OSError:
            continue
    return -1


def _install_ksu_fd_via_reboot() -> int:
    """通过 reboot 系统调用安装 ksu fd"""
    nr = _get_reboot_nr()
    out_fd = ctypes.c_int(-1)
    ret = _libc.syscall(
        nr,
        ctypes.c_uint32(KSU_INSTALL_MAGIC1),
        ctypes.c_uint32(KSU_INSTALL_MAGIC2),
        ctypes.c_int(0),
        ctypes.byref(out_fd),
    )
    if ret != 0:
        err = ctypes.get_errno()
        raise OSError(err, f'reboot syscall 失败: {os.strerror(err)}')
    fd = out_fd.value
    if fd < 0:
        raise RuntimeError(f'无效 fd: {fd}')
    return fd


def _grant_root(fd: int) -> None:
    """向内核发送 GRANT_ROOT 命令完成提权"""
    ret = _libc.ioctl(ctypes.c_int(fd), ctypes.c_uint(KSU_IOCTL_GRANT_ROOT), ctypes.c_void_p(0))
    if ret < 0:
        err = ctypes.get_errno()
        raise OSError(err, f'GRANT_ROOT 失败: {os.strerror(err)}')


def ensure_root() -> bool:
    """
    尝试获取 root 权限。
    优先 KernelSU 系统调用，失败则验证 su -c 是否可用。
    返回是否成功（KernelSU 成功时本进程 uid 变为 0）。
    """
    if os.getuid() == 0:
        return True

    # 1. KernelSU 提权
    try:
        fd = _scan_ksu_fd()
        if fd < 0:
            fd = _install_ksu_fd_via_reboot()
        _grant_root(fd)
        if os.getuid() == 0:
            return True
    except Exception:
        pass

    # 2. su -c 可用性验证
    try:
        result = subprocess.run(
            ["su", "-c", "id -u"],
            capture_output=True, timeout=5, text=True
        )
        return result.returncode == 0 and result.stdout.strip() == "0"
    except Exception:
        return False