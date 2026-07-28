import os
import json
import subprocess
import shlex
from typing import Tuple, Dict, Any, List

from .config import BASE_DIR, TRUNCATE_LIMIT, TRUNCATE_PREFIX, IS_ROOT, ROOT_FILE_DIR, SU_CMD

# ======================== 类型兼容辅助 ========================
def _to_bool(v: bool | str | int | None) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return False

def _to_int(v: int | str | None, default: int = 0) -> int:
    if v is None:
        return default
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (ValueError, TypeError):
        return default

# ======================== 辅助函数 ========================
def expand_path(path: str) -> str:
    expanded = os.path.expanduser(path)
    if IS_ROOT:
        expanded = os.path.normpath(os.path.join(ROOT_FILE_DIR, expanded.lstrip('/')))
    return expanded

def get_session_key(session_id: str, folder_name: str) -> Tuple[str, str]:
    return (session_id, folder_name)

def ensure_folder(folder_name: str | None) -> str:
    name = folder_name or ""
    work_dir = os.path.join(BASE_DIR, name) if name else BASE_DIR
    os.makedirs(work_dir, exist_ok=True)
    return work_dir

def truncate_text(text: str, limit: int | str = TRUNCATE_LIMIT) -> Tuple[str, bool, int]:
    limit = _to_int(limit, TRUNCATE_LIMIT)
    original_len = len(text)
    if limit <= 0 or original_len <= limit:
        return text, False, original_len
    keep = max(limit - len(TRUNCATE_PREFIX), 0)
    truncated = TRUNCATE_PREFIX + (text[-keep:] if keep else "")
    return truncated, True, original_len

def format_mixed_output(content: str, metadata: Dict[str, Any]) -> str:
    meta_text = json.dumps(metadata, indent='\t', ensure_ascii=False)
    if content.endswith('\n'):
        return content + "--- METADATA ---\n" + meta_text
    return content + "\n--- METADATA ---\n" + meta_text

# ======================== 提权原语（chown + chcon） ========================
# ======================== 提权原语（chown + chcon + mkdir） ========================
def _su_chown(path: str) -> bool:
    """将 path 所有者改为当前用户。SU_CMD 为空时用 os.chown，否则用 su -c chown"""
    try:
        uid = os.getuid()
        gid = os.getgid()
        if SU_CMD:
            result = subprocess.run(
                [*SU_CMD, f"chown {uid}:{gid} {shlex.quote(path)}"],
                capture_output=True, timeout=10
            )
            return result.returncode == 0
        os.chown(path, uid, gid)
        return True
    except Exception:
        return False


def _su_chcon(path: str) -> bool:
    """SELinux 强制模式下将 path 的 context 修复为 app_data_file"""
    if not _selinux_is_enforcing():
        return True
    try:
        if SU_CMD:
            result = subprocess.run(
                [*SU_CMD, f"chcon u:object_r:app_data_file:s0 {shlex.quote(path)}"],
                capture_output=True, timeout=10
            )
        else:
            result = subprocess.run(
                ["chcon", "u:object_r:app_data_file:s0", path],
                capture_output=True, timeout=10
            )
        return result.returncode == 0
    except Exception:
        return False


def _su_mkdir(path: str) -> bool:
    """创建目录。SU_CMD 为空时用 os.makedirs，否则用 su -c mkdir -p"""
    try:
        if SU_CMD:
            result = subprocess.run(
                [*SU_CMD, f"mkdir -p {shlex.quote(path)}"],
                capture_output=True, timeout=10
            )
            return result.returncode == 0
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def _fix_permission_sync(path: str) -> bool:
    """修复文件权限：chown + SELinux context"""
    return _su_chown(path) and _su_chcon(path)


def ensure_own_file(path: str) -> bool:
    """若文件存在且不属于当前用户，则 chown + chcon 为自己"""
    try:
        st = os.stat(path)
        if st.st_uid == os.getuid():
            return True
        return _fix_permission_sync(path)
    except FileNotFoundError:
        return True
    except Exception:
        return False
# ======================== 文件操作（带权限自动修复） ========================
def _with_retry_on_eperm(path: str, mode: str, action, *args, **kwargs):
    """尝试对 path 执行 action，PermissionError 时自动修复权限后重试一次"""
    try:
        with open(path, mode, encoding='utf-8') as f:
            return action(f, *args, **kwargs)
    except PermissionError:
        if _fix_permission_sync(path):
            with open(path, mode, encoding='utf-8') as f:
                return action(f, *args, **kwargs)
        raise


def _read_file_lines(path: str) -> List[str]:
    return _with_retry_on_eperm(path, 'r', lambda f: f.readlines())

def _write_file_lines(path: str, lines: List[str]):
    _with_retry_on_eperm(path, 'w', lambda f, l: f.writelines(l), lines)

def _read_file_content(path: str) -> str:
    return _with_retry_on_eperm(path, 'r', lambda f: f.read())

def _write_file_content(path: str, content: str):
    _with_retry_on_eperm(path, 'w', lambda f, c: f.write(c), content)

def _count_lines(path: str) -> int:
    return _with_retry_on_eperm(path, 'r', lambda f: sum(1 for _ in f))


# ======================== SELinux 检测 + 提权创建文件 ========================
_SELINUX_ENFORCING = None

def _selinux_is_enforcing() -> bool:
    """检查 SELinux 是否为强制模式（Enforcing），结果缓存"""
    global _SELINUX_ENFORCING
    if _SELINUX_ENFORCING is not None:
        return _SELINUX_ENFORCING
    try:
        if SU_CMD:
            result = subprocess.run(
                [*SU_CMD, "getenforce"],
                capture_output=True, timeout=5, text=True
            )
            _SELINUX_ENFORCING = result.returncode == 0 and "Enforcing" in result.stdout
        else:
            with open('/sys/fs/selinux/enforce', 'r') as f:
                _SELINUX_ENFORCING = f.read().strip() == '1'
        return _SELINUX_ENFORCING
    except Exception:
        _SELINUX_ENFORCING = False
        return False



def _sudo_ensure_file(path: str) -> bool:
    """
    提权创建目录和文件，修复路径上所有目录的 owner 和 SELinux context。
    在 os.makedirs / open 因权限不足失败时作为 fallback 使用。
    """
    try:
        dirpath = os.path.dirname(path)

        # 1. 创建父目录
        if not _su_mkdir(dirpath):
            return False

        # 2. 从 dirpath 向上递归修复父目录（su 模式下）
        if SU_CMD:
            current = dirpath
            home_prefix = '/data/data/com.termux/files/home'
            while True:
                parent = os.path.dirname(current)
                if parent == current or parent == home_prefix:
                    break
                _su_chown(parent)
                _su_chcon(parent)
                current = parent

        # 3. 修复 dirpath 本身
        _su_chown(dirpath)
        _su_chcon(dirpath)

        # 4. 创建文件 + 修复 context
        with open(path, 'w'):
            pass
        _su_chcon(path)

        return True
    except Exception:
        return False
