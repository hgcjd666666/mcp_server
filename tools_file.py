
import asyncio
import os
import shutil
import re
import difflib
import codecs
from typing import Optional, List

from .main import mcp
from .config import BASE_DIR, TRUNCATE_LIMIT, TRUNCATE_PREFIX
from .utils import (
    expand_path, truncate_text, format_mixed_output, _read_file_lines,
    _write_file_lines, _read_file_content, _write_file_content, _count_lines,
    ensure_own_file, _sudo_ensure_file, _to_bool, _to_int,
)
# ======================== 工具函数 2: read_lines ========================
@mcp.tool()
async def read_lines(
	path: str,
	start_line: int | str = 0,
	end_line: int | str | None = None,
	max_lines: int | str | None = None,
	max_chars: int | str | None = None,
	tail_lines: int | str | None = None,
	truncate_limit: int | str = TRUNCATE_LIMIT
) -> str:
	"""
	读取文件的指定行范围（0-based，与 Python 列表索引一致）。

	- start_line: 起始行号（包含），默认 0。
	- end_line: 结束行号（包含），None 或 -1 表示到文件末尾。
	- max_lines: 限制返回的最大行数。
	- max_chars: 限制返回的最大字符数（保留尾部）。
	- tail_lines: 若指定，读取文件末尾最后 N 行，忽略 start_line/end_line。
	公共参数（path, truncate_limit）详见 readme。
	"""
	# 统一类型
	start_line = _to_int(start_line, 0)
	end_line = _to_int(end_line) if end_line is not None else None
	max_lines = _to_int(max_lines) if max_lines is not None else None
	max_chars = _to_int(max_chars) if max_chars is not None else None
	tail_lines = _to_int(tail_lines) if tail_lines is not None else None
	truncate_limit = _to_int(truncate_limit, TRUNCATE_LIMIT)

	try:
		lines = await asyncio.to_thread(_read_file_lines, path)
	except FileNotFoundError:
		return format_mixed_output("文件未找到", {"错误": f"文件未找到 {path}"})
	except IsADirectoryError:
		return format_mixed_output("路径是目录", {"错误": f"路径是一个目录 {path}"})
	except PermissionError:
		return format_mixed_output("权限不足", {"错误": f"权限不足 {path}"})
	except Exception as e:
		return format_mixed_output("读取失败", {"错误": f"读取失败: {e}"})

	total = len(lines)
	if total == 0:
		return format_mixed_output("文件为空", {"路径": path, "总行数": 0})

	if tail_lines is not None and tail_lines > 0:
		start_line = max(0, total - tail_lines)
		end_line = total - 1

	if start_line < 0:
		return format_mixed_output("起始行号错误", {"错误": "起始行号必须 >= 0"})
	if end_line is not None and end_line < -1:
		return format_mixed_output("结束行号错误", {"错误": "结束行号必须 >= -1"})
	if end_line is None or end_line == -1 or end_line >= total:
		end_line = total - 1
	if start_line > end_line:
		return format_mixed_output("范围错误", {"错误": f"起始行 {start_line} 大于结束行 {end_line}"})
	if start_line >= total:
		return format_mixed_output("超出范围", {"错误": f"起始行 {start_line} 超出文件范围（共 {total} 行）"})

	selected = lines[start_line:end_line + 1]
	if max_lines is not None and len(selected) > max_lines:
		selected = selected[:max_lines]
		end_line = start_line + max_lines - 1

	content_lines = [f"{i:4d} | {line.rstrip()}" for i, line in enumerate(selected, start=start_line)]
	full_content = "\n".join(content_lines)

	if max_chars is not None and len(full_content) > max_chars:
		full_content = TRUNCATE_PREFIX + full_content[-(max_chars - len(TRUNCATE_PREFIX)):]

	content_tr, content_cut, _ = truncate_text(full_content, truncate_limit)

	metadata = {
		"路径": path,
		"总行数": total,
		"起始行": start_line,
		"结束行": end_line,
		"实际返回行数": len(selected),
		"截断": content_cut,
		"截断限制": truncate_limit if content_cut else None,
	}
	return format_mixed_output(content_tr, metadata)

# ======================== 工具函数 3: replace_lines (增强：自动创建文件，auto_fix_newline) ========================
@mcp.tool()
async def replace_lines(
	path: str,
	start_line: int | str,
	end_line: int | str,
	new_content: str,
	unescape: bool | str | int = False,
	auto_fix: bool | str | int = True,
	truncate_limit: int | str = TRUNCATE_LIMIT
) -> str:
	"""
	替换文件中指定行范围（0-based，包含两端）。

	- start_line: 起始行号（包含）。
	- end_line: 结束行号（包含），-1 表示到文件末尾。
	- new_content: 替换后的新内容。
	- auto_fix: 自动修复换行 + 相邻行重复跳过（默认 True）。
	公共参数（path, truncate_limit, unescape）详见 readme。
	"""
	# 统一类型
	start_line = _to_int(start_line, 0)
	end_line = _to_int(end_line, -1)
	unescape = _to_bool(unescape)
	auto_fix = _to_bool(auto_fix)
	truncate_limit = _to_int(truncate_limit, TRUNCATE_LIMIT)

	backup_path = path + ".bak"
	created_file = False
	warn_messages = []

	# 检查文件是否存在，若不存在则创建（及父目录）
	if not os.path.exists(path):
		try:
			os.makedirs(os.path.dirname(path), exist_ok=True)
			with open(path, 'w', encoding='utf-8') as f:
				pass  # 创建空文件
			created_file = True
			warn_messages.append("目标文件不存在，已自动创建空文件。")
		except Exception as e:
			# 直接创建失败，尝试提权创建（修复权限 + SELinux context）
			if _sudo_ensure_file(path):
				created_file = True
				warn_messages.append("目标文件不存在，已通过提权创建空文件。")
			else:
				return format_mixed_output("无法创建文件", {"错误": f"创建文件失败: {e}"})

	try:
		if unescape:
			new_content = codecs.decode(new_content.encode('ascii', errors='backslashreplace'), 'unicode_escape')

		lines = await asyncio.to_thread(_read_file_lines, path)
		total = len(lines)

		# 空文件处理
		if total == 0:
			if not created_file:
				warn_messages.append("文件为空，将继续替换（将插入新内容）。")
			if start_line != 0:
				return format_mixed_output("范围错误", {"错误": "文件为空，起始行必须为 0"})
			if end_line != -1 and end_line != 0:
				# return format_mixed_output("范围错误", {"错误": "文件为空，结束行必须为 -1 或 0"})
				warn_messages.append("范围不符合预期：文件为空，结束行必须为 -1 或 0，已知道修复")
			end_line = -1
			lines = []

		# 参数校验
		if start_line < 0:
			return format_mixed_output("起始行号错误", {"错误": "起始行号必须 >= 0"})
		if end_line < -1:
			return format_mixed_output("结束行号错误", {"错误": "结束行号必须 >= -1"})
		if end_line == -1 or end_line >= total:
			end_line = total - 1 if total > 0 else 0
		if total > 0 and start_line > end_line:
			return format_mixed_output("范围错误", {"错误": f"起始行 {start_line} 大于结束行 {end_line}"})
		if total > 0 and start_line >= total:
			return format_mixed_output("起始行超出范围", {"错误": f"起始行 {start_line} 超出文件范围（共 {total} 行）"})

		# 获取旧行内容
		if total == 0:
			old_lines = []
			old_content = ""
		else:
			old_lines = lines[start_line:end_line + 1]
			old_content = ''.join(old_lines)

		# ---------- auto_fix 逻辑 ----------
		#这里判断是否空文件，因为写空文件会给第一行自动加换行并警告，这不是预期行为
		if total >= 0:
			newline_warnings = []
			if auto_fix and old_lines:
				# 如果被替换的第一行有行首换行？通常不处理行首，只处理行尾衔接。
				# 这里遵循 replace_text 的策略：检查 old_content 开头/结尾的换行情况
				if old_content.endswith('\n') and not new_content.endswith('\n'):
					new_content += '\n'
					newline_warnings.append("已自动在末尾补换行（原内容以换行结尾）")
				if old_content.startswith('\n') and not new_content.startswith('\n'):
					new_content = '\n' + new_content
					newline_warnings.append("已自动在开头补换行（原内容以换行开头）")
				# 也可以检查相邻行的换行，但逻辑较复杂，按需求可简化
			#神秘ai，没有旧内容还检查什么
			# else:
				# if old_lines and old_content.endswith('\n') and not new_content.endswith('\n'):
					# newline_warnings.append("风险：原内容以换行结尾，但新内容末尾无换行，可能造成粘连")
				# if old_lines and old_content.startswith('\n') and not new_content.startswith('\n'):
					# newline_warnings.append("风险：原内容以换行开头，但新内容开头无换行，可能造成粘连")
			# --------------------------------------------

		# 备份原文件
		await asyncio.to_thread(ensure_own_file, backup_path)
		await asyncio.to_thread(shutil.copy2, path, backup_path)

		# 构建新行列表
		if new_content == "":
			new_lines = []
		else:
			raw_lines = new_content.splitlines(keepends=True)
			if new_content.endswith('\n'):
				new_lines = raw_lines
			else:
				new_lines = raw_lines[:-1] + [raw_lines[-1].rstrip('\n')] if raw_lines else []

		# ---------- 相邻行重复检查 ----------
		adjacent_warnings = []
		if auto_fix and start_line > 0 and total > 0 and new_lines:
			prev_stripped = lines[start_line - 1].strip()
			# 检查第一行
			if new_lines[0].strip() == prev_stripped:
				skipped = new_lines.pop(0)
				adjacent_warnings.append(f"新内容第一行与上一行重复，已跳过: {skipped.strip()!r}")
			# 检查最后一行
			if new_lines and new_lines[-1].strip() == prev_stripped:
				skipped = new_lines.pop(-1)
				adjacent_warnings.append(f"新内容最后一行与上一行重复，已跳过: {skipped.strip()!r}")
		# ------------------------------------

		new_file_lines = lines[:start_line] + new_lines + lines[end_line + 1:]
		await asyncio.to_thread(_write_file_lines, path, new_file_lines)

		# 生成 unified diff
		diff_lines = difflib.unified_diff(
			old_content.splitlines(keepends=True),
			new_content.splitlines(keepends=True),
			# fromfile=f"{path} (旧)",
			# tofile=f"{path} (新)"
		)
		diff_text = ''.join(diff_lines)
		content_main = f"替换成功（行 {start_line} ~ {end_line}）"
		if warn_messages:
			content_main += " [警告] " + "; ".join(warn_messages)
		if newline_warnings:
			content_main += " [换行提示] " + "; ".join(newline_warnings)
		if adjacent_warnings:
			content_main += " [相邻行重复] " + "; ".join(adjacent_warnings)
		content_full = content_main + "\n" + (diff_text if diff_text else "（无差异）")

		metadata = {
			# "路径": path,
			# "备份路径": backup_path,
			"范围": {"起始": start_line, "结束": end_line, "数量": (end_line - start_line + 1) if total > 0 else 0},
			"启用转义还原": unescape,
			"自动修复": auto_fix,
			"旧内容长度": len(old_content),
			"新内容长度": len(new_content),
			"删除旧行数": len(old_lines),
			"插入新行数": len(new_lines),
			"自动创建文件": created_file,
			"换行修补警告": newline_warnings if newline_warnings else None,
			"相邻行重复跳过": adjacent_warnings if adjacent_warnings else None,
		}
		return format_mixed_output(content_full, metadata)

	except FileNotFoundError:
		return format_mixed_output("文件未找到", {"错误": f"文件未找到 {path}"})
	except IsADirectoryError:
		return format_mixed_output("路径是目录", {"错误": f"路径是一个目录 {path}"})
	except PermissionError:
		return format_mixed_output("权限不足", {"错误": f"权限不足 {path}"})
	except Exception as e:
		if os.path.exists(backup_path):
			await asyncio.to_thread(shutil.copy2, backup_path, path)
		return format_mixed_output("替换失败，已恢复备份", {"错误": f"替换失败: {e}"})

# ======================== 工具函数 4: replace_text ========================
@mcp.tool()
async def replace_text(
	path: str,
	old_text: str,
	new_text: str,
	use_regex: bool | str | int = False,
	count: int | str = -1,
	unescape: bool | str | int = False,
	auto_fix_newline: bool | str | int = True,
	truncate_limit: int | str = TRUNCATE_LIMIT
) -> str:
	"""
	在文件中查找并替换文本（支持纯文本或正则表达式）。

	- old_text: 要查找的文本（若 use_regex=True 则为正则模式）。
	- new_text: 替换后的文本（支持 \1、\2 等反向引用）。
	- use_regex: 是否启用正则表达式模式（默认 False）。
	- count: 替换次数，-1 表示全部替换。
	- auto_fix_newline: 自动修补行首/行尾换行以避免粘连（默认 True）。
	公共参数（path, truncate_limit, unescape）详见 readme。
	返回 unified diff + 元数据。未匹配不修改。失败从 .bak 恢复。
	"""
	# 统一类型
	use_regex = _to_bool(use_regex)
	count = _to_int(count, -1)
	unescape = _to_bool(unescape)
	auto_fix_newline = _to_bool(auto_fix_newline)
	truncate_limit = _to_int(truncate_limit, TRUNCATE_LIMIT)

	path = expand_path(path)
	backup_path = path + ".bak"
	newline_warnings = []

	try:
		if unescape:
			old_text = codecs.decode(old_text.encode('ascii', errors='backslashreplace'), 'unicode_escape')
			new_text = codecs.decode(new_text.encode('ascii', errors='backslashreplace'), 'unicode_escape')

		if auto_fix_newline:
			if old_text.endswith('\n') and not new_text.endswith('\n'):
				new_text += '\n'
				newline_warnings.append("已自动在末尾补换行（原匹配以换行结尾）")
			if old_text.startswith('\n') and not new_text.startswith('\n'):
				new_text = '\n' + new_text
				newline_warnings.append("已自动在开头补换行（原匹配以换行开头）")
		else:
			if old_text.endswith('\n') and not new_text.endswith('\n'):
				newline_warnings.append("风险：原匹配以换行结尾，但替换内容末尾无换行，可能造成粘连")
			if old_text.startswith('\n') and not new_text.startswith('\n'):
				newline_warnings.append("风险：原匹配以换行开头，但替换内容开头无换行，可能造成粘连")

		original_content = await asyncio.to_thread(_read_file_content, path)

		if use_regex:
			try:
				pattern = re.compile(old_text, re.MULTILINE | re.DOTALL)
				new_content = pattern.sub(new_text, original_content, count=count if count >= 0 else 0)
			except re.error as e:
				return format_mixed_output("正则表达式错误", {"错误": f"正则表达式错误: {e}"})
		else:
			if count == -1:
				new_content = original_content.replace(old_text, new_text)
			else:
				parts = original_content.split(old_text, count + 1)
				if len(parts) <= count + 1:
					new_content = new_text.join(parts)
				else:
					new_content = new_text.join(parts[:count + 1]) + parts[count + 1]

		if new_content == original_content:
			return format_mixed_output("未找到匹配，未做任何更改", {"路径": path})

		await asyncio.to_thread(ensure_own_file, backup_path)
		await asyncio.to_thread(shutil.copy2, path, backup_path)
		await asyncio.to_thread(_write_file_content, path, new_content)

		if use_regex:
			replaced_count = len(re.findall(pattern, original_content))
			if count >= 0:
				replaced_count = min(replaced_count, count)
		else:
			replaced_count = original_content.count(old_text)
			if count >= 0:
				replaced_count = min(replaced_count, count)

		diff_lines = difflib.unified_diff(
			original_content.splitlines(keepends=True),
			new_content.splitlines(keepends=True),
			fromfile=f"{path} (旧)",
			tofile=f"{path} (新)"
		)
		diff_text = ''.join(diff_lines)
		content_main = f"替换完成，共替换 {replaced_count} 处"
		if newline_warnings:
			content_main += " [注意] " + "; ".join(newline_warnings)
		content_full = content_main + "\n" + (diff_text if diff_text else "（无差异）")

		metadata = {
			"路径": path,
			"备份路径": backup_path,
			"使用正则": use_regex,
			"启用转义还原": unescape,
			"替换次数": replaced_count,
			"旧内容长度": len(original_content),
			"新内容长度": len(new_content),
			"换行修补警告": newline_warnings if newline_warnings else None,
		}
		return format_mixed_output(content_full, metadata)

	except FileNotFoundError:
		return format_mixed_output("文件未找到", {"错误": f"文件未找到 {path}"})
	except IsADirectoryError:
		return format_mixed_output("路径是目录", {"错误": f"路径是一个目录 {path}"})
	except PermissionError:
		return format_mixed_output("权限不足", {"错误": f"权限不足 {path}"})
	except Exception as e:
		if os.path.exists(backup_path):
			await asyncio.to_thread(shutil.copy2, backup_path, path)
		return format_mixed_output("替换失败，已恢复备份", {"错误": f"替换失败: {e}"})

# ======================== 工具函数 5: revert_to_backup ========================
@mcp.tool()
async def revert_to_backup(path: str, truncate_limit: int | str = TRUNCATE_LIMIT) -> str:
	"""从 .bak 备份文件恢复原文件。公共参数（path, truncate_limit）详见 readme。"""
	truncate_limit = _to_int(truncate_limit, TRUNCATE_LIMIT)
	path = expand_path(path)
	backup_path = path + ".bak"
	if not os.path.exists(backup_path):
		return format_mixed_output("没有备份文件", {"错误": f"没有找到备份文件 {backup_path}"})
	try:
		backup_content = await asyncio.to_thread(_read_file_content, backup_path)
		await asyncio.to_thread(ensure_own_file, path)
		await asyncio.to_thread(shutil.copy2, backup_path, path)
		preview, _, orig = truncate_text(backup_content, truncate_limit)
		metadata = {
			"路径": path,
			"备份路径": backup_path,
			"内容长度": orig,
			"保留备份": True,
			"消息": "备份文件已保留，可再次恢复同一版本"
		}
		# return format_mixed_output(f"已恢复备份，内容预览：{preview}", metadata)
		return format_mixed_output(f"已恢复备份", metadata)
	except Exception as e:
		return format_mixed_output("恢复失败", {"错误": f"恢复失败: {e}"})

# ======================== 工具函数 6: file_info ========================
@mcp.tool()
async def file_info(path: str) -> str:
	"""获取文件基本信息：大小、行数、是否有替换备份。公共参数（path）详见 readme。"""
	path = expand_path(path)
	try:
		if not os.path.exists(path):
			return format_mixed_output("文件不存在", {"错误": f"文件不存在: {path}"})
		stat = os.stat(path)
		line_count = await asyncio.to_thread(_count_lines, path)
		has_backup = os.path.exists(path + ".bak")
		content = f"文件：{path}\n大小：{stat.st_size} 字节\n行数：{line_count}\n有替换备份：{has_backup}"
		metadata = {
			"路径": path,
			"大小字节": stat.st_size,
			"行数": line_count,
			"有替换备份": has_backup
		}
		return format_mixed_output(content, metadata)
	except Exception as e:
		return format_mixed_output("获取信息失败", {"错误": f"获取信息失败: {e}"})
