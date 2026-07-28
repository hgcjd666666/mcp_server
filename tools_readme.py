from .main import mcp


@mcp.tool()
async def readme() -> str:
    """
    所有工具的详细参数说明。
    你不应该调用这个工具，这个工具唯一的用途就是提供说明，没有实际作用

    公共参数（多处复用，不再逐个工具重复）：
    - path: 文件路径，支持 ~ 展开。
    - truncate_limit: 输出截断长度上限。
    - folder_name: 工作子目录名。
    - time_out: 超时，"auto" 或数字秒。
    - truncate_output: 是否截断输出内容。
    - unescape: 保底机制，默认 False。当 AI 无法嵌入真实换行时设为 True，可将 \n 等转义序列还原。

    小技巧：
    replace_line 会尝试自动创建文件以及对应的文件夹，所以可以直接使用而不用 touch
    exec 不支持管道等 shell 语法，需要时用 bash -c 包裹，如 ["bash", "-c", "cmd1 | cmd2"]
    也可以省略引号直接写 bash -c cmd1 | cmd2
    """
    return ""
