
import asyncio
import shlex
from typing import Dict, Any, List

from .main import mcp
from .config import (
    BASE_DIR, DROIDSPACES_CONTAINER, IS_ROOT,
    DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT, TRUNCATE_LIMIT,
    _drop_privileges, SU_CMD,
)
from .utils import ensure_folder, truncate_text, format_mixed_output, _to_bool, _to_int

# ======================== 核心执行逻辑 ========================
async def _run_shell(
    command: List[str],
    work_dir: str,
    preexec_fn,
    time_out: str | int,
    truncate_output: bool | str | int,
    truncate_limit: int | str,
    metadata_extra: Dict[str, Any] | None = None,
) -> str:
    # 统一类型
    truncate_output_bool = _to_bool(truncate_output)
    truncate_limit_int = _to_int(truncate_limit, TRUNCATE_LIMIT)

    # 超时解析
    timeout_seconds = DEFAULT_TIMEOUT
    timeout_warning = None
    if isinstance(time_out, int) or time_out != "auto":
        try:
            input_seconds = int(time_out) if isinstance(time_out, str) else time_out
        except ValueError:
            input_seconds = DEFAULT_TIMEOUT
        if input_seconds <= 0:
            timeout_seconds = MIN_TIMEOUT
        elif input_seconds > MAX_TIMEOUT:
            timeout_seconds = MAX_TIMEOUT
            timeout_warning = f"超时时间上限为 {MAX_TIMEOUT} 秒，已自动调整为 {MAX_TIMEOUT} 秒"
        else:
            timeout_seconds = input_seconds

    stdout_raw = ""
    stderr_raw = ""
    returncode = -1
    timed_out = False

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            preexec_fn=preexec_fn,
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
            stdout_raw = stdout_raw.decode('utf-8', errors='ignore')
            stderr_raw = stderr_raw.decode('utf-8', errors='ignore')
            returncode = proc.returncode
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
                stdout_raw, stderr_raw = await proc.communicate()
                stdout_raw = stdout_raw.decode('utf-8', errors='ignore') if stdout_raw else ""
                stderr_raw = stderr_raw.decode('utf-8', errors='ignore') if stderr_raw else ""
            except Exception:
                pass
    except Exception as e:
        return format_mixed_output("执行命令失败", {"错误": str(e)})

    metadata: Dict[str, Any] = {
        "标准错误": stderr_raw,
        "返回码": returncode,
        "工作目录": work_dir,
    }
    if timeout_warning:
        metadata["超时警告"] = timeout_warning
    if timed_out:
        metadata["超时"] = True
        metadata["超时时间"] = timeout_seconds
    if metadata_extra:
        metadata.update(metadata_extra)

    if truncate_output_bool:
        stdout_tr, stdout_cut, stdout_orig = truncate_text(stdout_raw, truncate_limit_int)
        stderr_tr, stderr_cut, stderr_orig = truncate_text(stderr_raw, truncate_limit_int)
        content = stdout_tr
        metadata["标准错误"] = stderr_tr
        metadata["截断信息"] = {
            "标准输出已截断": stdout_cut,
            "标准错误已截断": stderr_cut,
            "原始输出长度": stdout_orig,
            "原始错误长度": stderr_orig,
            "截断限制": truncate_limit_int
        }
    else:
        content = stdout_raw
        metadata["标准错误"] = stderr_raw
        metadata["截断信息"] = None

    return format_mixed_output(content, metadata)

# ======================== 工具: exec ========================
@mcp.tool()
async def exec(
    command: List[str] | str,
    target: str,
    folder_name: str | None = "",
    time_out: str | int = "30",
    truncate_output: bool | str | int = True,
    truncate_limit: int | str = TRUNCATE_LIMIT,
) -> str:
    """
    执行 shell 命令。

    - command: 要执行的命令。支持列表形式如 ["ls", "-la"]，或字符串形式如 "ls -la"。
               当传入单元素列表时自动视为字符串并用 shlex 分割。
    - target: "d"/"droidspaces"（容器）或 "t"/"termux"（本机，尽量避免）。必填。
    公共参数（folder_name, time_out, truncate_output, truncate_limit）详见 readme。
    """
    # 统一转为 List[str]
    if isinstance(command, str):
        command = shlex.split(command)
    elif isinstance(command, list) and len(command) == 1:
        command = shlex.split(command[0])

    # 空命令检查
    if not command:
        return format_mixed_output("参数错误", {"错误": "command 不能为空"})

    # 兼容 folder_name None
    folder_name = folder_name or ""

    # target 大小写不敏感
    target_lower = target.lower()
    if target_lower in ("d", "droidspaces"):
        droidspaces_args = ["/data/local/Droidspaces/bin/droidspaces", f"--name={DROIDSPACES_CONTAINER}", "run"] + command
        cmd_str = " ".join(shlex.quote(arg) for arg in droidspaces_args)
        full_cmd = [*SU_CMD, cmd_str]
        work_dir = BASE_DIR
        preexec_fn = None  # Droidspaces 不降权
        meta = {"目标": "droidspaces", "容器": DROIDSPACES_CONTAINER}
    elif target_lower in ("t", "termux"):
        if IS_ROOT:
            return format_mixed_output("不支持的操作", {"错误": "root 模式下不支持 termux 本机 shell，请使用 target='d'"})
        full_cmd = command
        work_dir = await asyncio.to_thread(ensure_folder, folder_name)
        preexec_fn = _drop_privileges
        meta = {"目标": "termux"}
    else:
        return format_mixed_output("参数错误", {"错误": f"target 必须为 'd'/'droidspaces' 或 't'/'termux'，收到 '{target}'"})

    return await _run_shell(full_cmd, work_dir, preexec_fn, time_out, truncate_output, truncate_limit, meta)


# ======================== 兼容旧接口: exec_shell ========================
# @mcp.tool()
# async def exec_shell(command: str, folder_name: str = "", time_out: str = "30",
                     # truncate_output: bool = True,
                     # truncate_limit: int = TRUNCATE_LIMIT) -> str:
    # """exec 的 termux 别名，向后兼容。"""
    # return await exec(command=command, target="termux", folder_name=folder_name,
                      # time_out=time_out, truncate_output=truncate_output,
                      # truncate_limit=truncate_limit)
