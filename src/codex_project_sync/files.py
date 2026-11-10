"""本地文件与 Windows 进程检查基础函数；不处理账户凭据。"""

import ctypes  # 调用 Windows 只读进程枚举接口。
from ctypes import wintypes  # 匹配 Windows 系统结构字段。
import hashlib  # 对配置及备份生成校验值。
import json  # 读取与保存原生 JSON 配置。
import os  # 提供原子替换和文件刷新。
from pathlib import Path  # 使用明确路径。
import tempfile  # 在目标目录准备完整临时文件。
from .legacy_core import SyncError  # 统一使用可向用户展示的停止原因。

BLOCKING_NAMES = {"chatgpt.exe", "codex.exe", "codex-code-mode-host.exe"}  # 不强制结束这些可能写入配置的进程。

def digest_bytes(data):  # 对完整字节内容生成校验值。
    return hashlib.sha256(data).hexdigest()  # 既不输出文件内容，也不读取任何登录凭据。


def file_digest(path):  # 流式校验大数据库备份，避免一次读入内存。
    digest = hashlib.sha256()  # 初始化校验器。
    with Path(path).open("rb") as stream:  # 以只读方式打开需要校验的文件。
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):  # 每次读取 8 MiB。
            digest.update(chunk)  # 将这一块加入校验。
    return digest.hexdigest()  # 返回可写入本地备份清单的摘要。


def read_json(path):  # 从指定文件读取 JSON。
    try:  # 将底层解析错误转换为可理解的说明。
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # 兼容普通 UTF-8 和带 BOM 的 UTF-8。
    except (OSError, ValueError) as error:  # 文件损坏时绝不改用空配置继续执行。
        raise SyncError(f"无法读取配置 {Path(path).name}：{error}") from error  # 保留具体诊断信息。


def json_bytes(value):  # 使用原生 JSON 格式保存中文项目名称。
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")  # 不使用系统默认代码页。


def atomic_write(path, data):  # 只在最后一步替换已准备好的完整文件。
    path = Path(path)  # 将输入规范化为路径对象。
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.sync-", dir=path.parent)  # 临时文件与目标文件位于同一磁盘。
    try:  # 任何错误都清理未提交的临时文件。
        with os.fdopen(descriptor, "wb") as stream:  # 接管安全创建的文件句柄。
            stream.write(data)  # 写入完整内容。
            stream.flush()  # 刷新 Python 缓冲区。
            os.fsync(stream.fileno())  # 请求操作系统刷新到磁盘。
        os.replace(temporary, path)  # 用原子替换避免半个 JSON 文件。
    finally:  # 无论是否提交都不留下临时配置。
        Path(temporary).unlink(missing_ok=True)  # 提交后原临时路径已不存在。


def running_codex_processes():  # 不依赖 PowerShell 命令行，也不读取进程中的凭据。
    if os.name != "nt":  # 本工具只在已确认的 Windows 环境中写入实际配置。
        raise SyncError("实际同步入口仅支持当前 Windows 电脑。")  # 其他系统可以运行纯函数测试。
    class ProcessEntry(ctypes.Structure):  # Windows Toolhelp 的进程记录结构。
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]  # 字段布局与 PROCESSENTRY32W 一致。
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # 获取 Windows 标准进程查询接口。
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]  # 声明参数，防止 64 位句柄被截断。
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE  # 快照返回完整宽度的句柄。
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]  # 声明首条记录接口。
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]  # 声明后续记录接口。
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]  # 声明关闭句柄接口。
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS=2，只查询进程清单。
    if handle == ctypes.c_void_p(-1).value:  # 查询失败时不能声称应用已退出。
        raise SyncError("无法确认 Codex 是否退出，未修改任何配置。")  # 选择停止而非绕过保护。
    found = []  # 仅保存进程名称和进程编号。
    try:  # 确保所有分支都关闭快照句柄。
        entry = ProcessEntry()  # 分配记录结构。
        entry.dwSize = ctypes.sizeof(ProcessEntry)  # Windows 要求填入结构字节数。
        success = kernel.Process32FirstW(handle, ctypes.byref(entry))  # 读取第一条进程记录。
        while success:  # 遍历当前系统快照。
            if entry.szExeFile.lower() in BLOCKING_NAMES:  # 检测可能使用配置或会话数据的 Codex 进程。
                found.append({"name": entry.szExeFile, "pid": int(entry.th32ProcessID)})  # 不记录进程命令行。
            success = kernel.Process32NextW(handle, ctypes.byref(entry))  # 读取下一条记录。
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES=18 表示正常枚举完毕。
            raise SyncError("进程清单读取不完整，未修改任何配置。")  # 查询不完整时拒绝实际写入。
    finally:  # 查询结束后释放系统资源。
        kernel.CloseHandle(handle)  # 关闭进程快照句柄。
    return found  # 调用者决定是否显示或拒绝写入。


def require_closed():  # 所有实际同步和恢复操作都必须通过这项检查。
    found = running_codex_processes()  # 查询本机当前运行情况。
    if found:  # 关闭窗口不一定等于完全退出，因此检查相关进程。
        names = "、".join(sorted({item["name"] for item in found}))  # 只显示必要的进程名称。
        raise SyncError(f"Codex 尚未完全退出（{names}）。本次没有同步。请先结束当前对话并正常退出 Codex，再运行这个入口。")  # 不自动终止应用或计算。
