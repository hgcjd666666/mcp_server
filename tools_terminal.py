
import asyncio
import os
import time
import fcntl
import select as select_module
from typing import Tuple

from .main import mcp
from .config import (
    ANSI_ESCAPE, IS_ROOT,
    TERM_IDLE_TIMEOUT, TERM_TOTAL_TIMEOUT, TERM_HIDE_INPUT_TIMEOUT,
    TRUNCATE_LIMIT, sessions, sessions_lock, _drop_privileges,
)
from .utils import ensure_folder, truncate_text, format_mixed_output, get_session_key, _to_bool, _to_int

# ======================== preexec 函数 ========================
def _preexec_termux():
	"""termux: 降权 + setsid"""
	_drop_privileges()
	os.setsid()

# ======================== 内部：关闭终端 ========================
async def _close_terminal_internal(key: Tuple[str, str]):
    """内部关闭终端，不加锁，调用者需持有 sessions_lock"""
    if key not in sessions:
        return
    session = sessions.pop(key)
    master_fd = session["master_fd"]
    process = session["process"]
    try:
        os.close(master_fd)
    except Exception:
        pass
    try:
        process.kill()
        await process.wait()
    except Exception:
        pass

# ======================== 内部：启动终端 ========================
async def _start_terminal_internal(
    session_id: str, folder_name: str,
) -> str:
    """启动 pty 终端（仅支持 termux 本机）"""
    key = get_session_key(session_id, folder_name)
    async with sessions_lock:
        if key in sessions:
            old_pid = sessions[key]["process"].pid
            await _close_terminal_internal(key)
            warning = f"已关闭现有 session {session_id}，PID {old_pid} 已终止"
        else:
            warning = None

        work_dir = await asyncio.to_thread(ensure_folder, folder_name)
        try:
            master_fd, slave_fd = os.openpty()
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"

            args = ["bash", "-i"]
            preexec_fn = _preexec_termux

            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=work_dir,
                env=env,
                preexec_fn=preexec_fn,
                close_fds=True,
            )
            os.close(slave_fd)

            sessions[key] = {
                "master_fd": master_fd,
                "process": process,
                "work_dir": work_dir,
            }

            content = f"终端会话已启动 (PID: {process.pid})"
            metadata = {
                "会话ID": session_id,
                "文件夹名": folder_name,
                "工作目录": work_dir,
                "进程ID": process.pid,
            }
            if warning:
                metadata["警告"] = warning
            return format_mixed_output(content, metadata)
        except Exception as e:
            return format_mixed_output("启动终端失败", {"错误": f"启动终端失败: {e}"})

# ======================== 内部：终端交互 ========================
async def _terminal_interact_internal(
    session_id: str,
    folder_name: str,
    command: str,
    time_out: str | int,
    truncate_output: bool | str | int,
    strip_ansi: bool | str | int,
    hide_input: bool | str | int,
    truncate_limit: int | str,
) -> str:
    """向 pty 发送命令并读取输出"""
    # 统一类型
    truncate_output_bool = _to_bool(truncate_output)
    strip_ansi_bool = _to_bool(strip_ansi)
    hide_input_bool = _to_bool(hide_input)
    truncate_limit_int = _to_int(truncate_limit, TRUNCATE_LIMIT)

    key = get_session_key(session_id, folder_name)
    async with sessions_lock:
        if key not in sessions:
            return format_mixed_output("会话不存在", {"错误": "会话不存在，请先调用 terminal(action='start') 或 start_terminal"})
        session = sessions[key]
        master_fd = session["master_fd"]

    if command:
        try:
            os.write(master_fd, (command + "\n").encode())
        except Exception as e:
            return format_mixed_output("写入命令失败", {"错误": f"写入命令失败: {e}"})

    hide_success = False
    remaining = b""
    if command and hide_input_bool:
        hide_success, remaining = await _discard_input_echo(master_fd, command, TERM_HIDE_INPUT_TIMEOUT)

    loop = asyncio.get_running_loop()
    output_bytes = bytearray(remaining)
    read_event = asyncio.Event()
    reader_removed = False

    def _reader():
        nonlocal reader_removed
        try:
            data = os.read(master_fd, 4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            output_bytes.extend(data)
            read_event.set()
        else:
            if not reader_removed:
                loop.remove_reader(master_fd)
                reader_removed = True
            read_event.set()

    loop.add_reader(master_fd, _reader)

    try:
        if time_out == "auto":
            start = time.time()
            last_data = start
            while True:
                try:
                    timeout = min(TERM_IDLE_TIMEOUT, TERM_TOTAL_TIMEOUT - (time.time() - start))
                    if timeout <= 0:
                        break
                    await asyncio.wait_for(read_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    if time.time() - last_data >= TERM_IDLE_TIMEOUT:
                        break
                    if time.time() - start >= TERM_TOTAL_TIMEOUT:
                        break
                    continue
                read_event.clear()
                last_data = time.time()
                if time.time() - start >= TERM_TOTAL_TIMEOUT:
                    break
        else:
            try:
                seconds = float(time_out)
            except ValueError:
                if not reader_removed:
                    loop.remove_reader(master_fd)
                    reader_removed = True
                return format_mixed_output("time_out参数错误", {"错误": f"time_out 参数必须为 'auto' 或数字，收到 '{time_out}'"})
            try:
                await asyncio.wait_for(read_event.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
            while True:
                try:
                    data = os.read(master_fd, 4096)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not data:
                    break
                output_bytes.extend(data)
    finally:
        if not reader_removed:
            try:
                loop.remove_reader(master_fd)
            except Exception:
                pass

    output_str = bytes(output_bytes).decode('utf-8', errors='ignore')
    if strip_ansi_bool:
        output_str = ANSI_ESCAPE.sub('', output_str)

    if truncate_output_bool:
        out_tr, cut, orig = truncate_text(output_str, truncate_limit_int)
        content = out_tr
        metadata = {
            "截断": cut,
            "原始长度": orig,
            "截断限制": truncate_limit_int if cut else None,
        }
    else:
        content = output_str
        metadata = {"截断": False}

    metadata["隐藏输入"] = hide_success
    if not output_str:
        metadata["消息"] = "无输出"
    return format_mixed_output(content, metadata)


# ======================== 隐藏输入回显 ========================
def _discard_input_echo_sync(master_fd: int, command: str, timeout: float) -> Tuple[bool, bytes]:
    end_time = time.time() + timeout
    buf = b""
    command_bytes = command.encode()

    while True:
        remaining = max(end_time - time.time(), 0)
        if remaining <= 0:
            return False, buf

        r, _, _ = select_module.select([master_fd], [], [], remaining)
        if not r:
            return False, buf

        try:
            data = os.read(master_fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            break

        if not data:
            break

        buf += data
        idx = buf.find(command_bytes)
        if idx != -1:
            discard_end = idx + len(command_bytes)
            if discard_end < len(buf) and buf[discard_end:discard_end+1] == b'\n':
                discard_end += 1
            remaining_data = buf[discard_end:]
            return True, remaining_data

        if b'\n' in buf:
            return False, buf

    return False, buf

async def _discard_input_echo(master_fd: int, command: str, timeout: float) -> Tuple[bool, bytes]:
    return await asyncio.to_thread(_discard_input_echo_sync, master_fd, command, timeout)


# ======================== 工具: terminal (统一入口) ========================
@mcp.tool()
async def terminal(
    session_id: str,
    folder_name: str | None,
    action: str = "interact",
    command: str = "",
    time_out: str | int = "auto",
    truncate_output: bool | str | int = True,
    strip_ansi: bool | str | int = True,
    hide_input: bool | str | int = True,
    truncate_limit: int | str = TRUNCATE_LIMIT,
) -> str:
    """
    终端管理统一入口。

    - session_id: 会话标识符。
    - action: "start" / "interact" / "close"。先 start，用完必须 close。
    - command: 发送的命令（interact 时）。
    - strip_ansi: 是否去除 ANSI 转义。
    - hide_input: 是否隐藏输入回显。
    公共参数（folder_name, time_out, truncate_output, truncate_limit）详见 readme。
    """
    if IS_ROOT:
        return format_mixed_output("不支持的操作", {"错误": "root 模式下不支持终端操作"})

    # folder_name 兼容 None
    folder_name = folder_name or ""

    # action 大小写不敏感
    action_lower = action.lower()
    if action_lower == "start":
        return await _start_terminal_internal(session_id, folder_name)
    elif action_lower == "close":
        key = get_session_key(session_id, folder_name)
        async with sessions_lock:
            if key not in sessions:
                return format_mixed_output("会话不存在", {"错误": f"会话不存在: session_id='{session_id}', folder_name='{folder_name}'"})
            process = sessions[key]["process"]
            pid = process.pid
            await _close_terminal_internal(key)
        return format_mixed_output(f"终端已关闭 (PID: {pid})", {"会话ID": session_id, "文件夹名": folder_name, "进程ID": pid})
    else:  # interact
        return await _terminal_interact_internal(
            session_id, folder_name, command, time_out,
            truncate_output, strip_ansi, hide_input, truncate_limit,
        )


# ======================== 兼容旧接口 ========================
# @mcp.tool()
# async def start_terminal(session_id: str, folder_name: str) -> str:
#     """terminal(action='start', target='termux') 的别名。"""
#     return await terminal(session_id=session_id, folder_name=folder_name, action="start", target="termux")

# @mcp.tool()
# async def close_terminal(session_id: str, folder_name: str) -> str:
#     """terminal(action='close') 的别名。"""
#     return await terminal(session_id=session_id, folder_name=folder_name, action="close", target="termux")

# @mcp.tool()
# async def terminal_interact(
#     session_id: str,
#     folder_name: str,
#     command: str = "",
#     time_out: str = "auto",
#     truncate_output: bool = True,
#     strip_ansi: bool = True,
#     hide_input: bool = True,
#     truncate_limit: int = TRUNCATE_LIMIT,
# ) -> str:
#     """terminal(action='interact', target='termux') 的别名。"""
#     return await terminal(
#         session_id=session_id, folder_name=folder_name, action="interact", target="termux",
#         command=command, time_out=time_out, truncate_output=truncate_output,
#         strip_ansi=strip_ansi, hide_input=hide_input, truncate_limit=truncate_limit,
#     )
