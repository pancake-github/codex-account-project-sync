"""在独立 Windows 构建环境生成 EXE、公开源码包及校验文件。"""

import hashlib  # 生成最终分发文件的 SHA-256。
import importlib.metadata  # 查找构建依赖随安装包提供的许可证。
import os  # 使用当前 Windows 平台的参数路径分隔符。
from pathlib import Path  # 所有产物都放在仓库自己的 dist 目录。
import re  # 限定可用于官方版本标签的运行时版本格式。
import shutil  # 仅复制公开运行时的许可文本。
import subprocess  # 以明确参数运行 PyInstaller 和 Git。
import sys  # 使用构建环境自己的 Python，不调用开发者的固定环境。
import zipfile  # 打包无需安装的 Windows 下载包。
import urllib.request  # 构建时仅从运行时官方源码仓库取得缺失的同版本许可。

ROOT = Path(__file__).resolve().parents[1]  # 构建源限定为当前公开仓库。
VERSION = "v0.1.0-beta.1"  # 与初始 Release 标签保持一致。


def runtime_versions():  # 查询实际分发的 Tcl 与 Tk 补丁版本。
    import tkinter  # 只在构建时创建测试窗口，不扫描任何用户资料。
    window = tkinter.Tk()  # 与之后的 EXE 启动检查采用相同 GUI 运行时。
    window.withdraw()  # 构建查询不向使用者显示交互窗口。
    try:  # 不从文件夹名猜测具体补丁版本。
        return window.tk.call("info", "patchlevel"), window.tk.call("package", "present", "Tk")  # 分别返回 Tcl 与 Tk 的实际版本。
    finally:  # 版本查询后立即释放构建测试窗口。
        window.destroy()  # 不留后台界面进程。


def runtime_licenses(dist):  # 分发自带 Python/Tcl/Tk 的 EXE 时同时保留运行时许可。
    directory = dist / "licenses"  # 许可文本既放进 Windows 包，也嵌入独立 EXE。
    directory.mkdir(parents=True, exist_ok=True)  # 不修改 Python 安装目录。
    base = Path(sys.base_prefix)  # 构建环境的公开 CPython 安装位置。
    inputs = [("Python-LICENSE.txt", base / "LICENSE.txt")]  # CPython Windows 安装包附带完整 PSF 许可及第三方说明。
    versions = None  # 仅在安装目录缺少许可文件时查询运行时补丁版本。
    for index, name in enumerate(("tcl", "tk")):  # Tcl 与 Tk 各自的许可都保留。
        candidates = sorted((base / "tcl").glob(f"{name}*/license.terms"))  # 从实际安装的 Tk 运行时取许可，避免贴错版本。
        if candidates:  # 安装包提供原始许可时直接保留。
            inputs.append((f"{name}-license.terms", candidates[0]))  # 使用清楚且不冲突的文件名。
        else:  # CPython 的部分 Windows 安装包没有保留这两个文本文件。
            versions = versions or runtime_versions()  # 使用实际载入的 Tcl/Tk 版本。
            version = str(versions[index])  # 分别匹配所分发的运行时。
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):  # 不根据未知版本格式猜测源码标签。
                raise RuntimeError("Unsupported Tcl/Tk patch-level format")  # 未知格式停止打包。
            url = f"https://raw.githubusercontent.com/tcltk/{name}/core-{version.replace('.', '-')}/license.terms"  # 官方源码仓库中的精确版本标签。
            with urllib.request.urlopen(url, timeout=30) as response:  # 保留正常 TLS 校验，不使用不安全绕过。
                data = response.read()  # 只下载公开许可文本。
            data.decode("utf-8")  # 拒绝错误编码或意外二进制响应。
            if len(data) < 500:  # 防止空文件或明显错误的响应被当作许可。
                raise RuntimeError("Incomplete Tcl/Tk license response")  # 不继续构建缺少许可的二进制。
            (directory / f"{name}-license.terms").write_bytes(data)  # 将同版本原文放入构建资产。
    distribution = importlib.metadata.distribution("pyinstaller")  # 构建器的 bootloader 许可例外随其发行包提供。
    copying = next((entry for entry in distribution.files or [] if entry.name.lower() == "copying.txt"), None)  # 使用已安装版本的原始许可文本。
    if copying is not None:  # 新版 wheel 提供该文件时一并分发。
        inputs.append(("PyInstaller-COPYING.txt", Path(distribution.locate_file(copying))))  # 保留 GPL 和 bootloader 例外原文。
    for name, source in inputs:  # 只复制许可文本，不复制环境配置或其他本机文件。
        if not source.is_file():  # 缺少必要 CPython 许可时必须停止。
            raise RuntimeError(f"Missing runtime license: {name}")  # 不发布缺少许可的二进制包。
        shutil.copyfile(source, directory / name)  # 复制到构建产物自己的目录。
    return sorted(directory.iterdir())  # 返回本次实际纳入的许可文件。


def main():  # 只有显式构建才运行打包依赖。
    subprocess.run([sys.executable, "scripts/audit_public_tree.py"], cwd=ROOT, check=True)  # 未通过公开内容审计不打包。
    dist = ROOT / "dist"  # 发布文件只位于此目录。
    licenses = runtime_licenses(dist)  # 从干净构建环境收集实际运行时许可。
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "CodexAccountProjectSync", "--paths", "src"]  # 独立 EXE 包含 Python 与 Tkinter 运行时。
    for path in licenses:  # 把必要许可同时嵌入独立 EXE。
        command.extend(["--add-data", f"{path}{os.pathsep}licenses"])  # 使用当前构建平台支持的源与目标分隔符。
    command.append("launcher.py")  # 唯一运行入口始终来自公开源码。
    subprocess.run(command, cwd=ROOT, check=True)  # 只有许可与源码审计完成才实际打包。
    executable = dist / "CodexAccountProjectSync.exe"  # 便于普通用户辨认的双击入口。
    windows_zip = dist / f"CodexAccountProjectSync-{VERSION}-windows-x64.zip"  # 下载包明确标示系统与版本。
    with zipfile.ZipFile(windows_zip, "w", zipfile.ZIP_DEFLATED) as package:  # 压缩文件内容使用明确白名单。
        for path in (executable, ROOT / "README.md", ROOT / "README.en.md", ROOT / "LICENSE", ROOT / "docs" / "validation.md", ROOT / "docs" / "third-party-notices.md"):  # 不包含本地配置、日志或备份。
            package.write(path, path.name if path == executable else str(path.relative_to(ROOT)))  # 解压后 EXE 位于包根目录。
        for path in licenses:  # Windows 下载包中也可直接阅读完整运行时许可。
            package.write(path, f"licenses/{path.name}")  # 不要求启动 EXE 才能查看许可。
    source_zip = dist / f"CodexAccountProjectSync-{VERSION}-source.zip"  # 源码包只包含 Git 已跟踪内容。
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=codex-account-project-sync-{VERSION}/", "-o", str(source_zip), "HEAD"], cwd=ROOT, check=True)  # 排除 .git 和任何未跟踪资料。
    hashes = []  # 校验清单涵盖 EXE 和两个下载包。
    for path in (executable, windows_zip, source_zip):  # 所有公开资产均有 SHA-256。
        with path.open("rb") as stream:  # 校验后显式释放 Windows 文件句柄。
            digest = hashlib.file_digest(stream, "sha256").hexdigest()  # 构建使用 Python 3.12 的流式文件摘要接口。
        hashes.append(f"{digest}  {path.name}")  # 文件名使用下载资产的原名。
    (dist / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")  # 校验文件可直接用于下载验证。
    print("Built Windows EXE, portable ZIP, tracked-source ZIP and SHA256SUMS.txt.")  # 只声明构建产物，不宣称真实账户验收完成。


if __name__ == "__main__":  # 导入脚本不会触发构建。
    main()  # CI 明确运行时才执行打包。
