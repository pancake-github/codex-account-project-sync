"""在独立 Windows 构建环境生成 EXE、公开源码包及校验文件。"""

import hashlib  # 生成最终分发文件的 SHA-256。
from pathlib import Path  # 所有产物都放在仓库自己的 dist 目录。
import subprocess  # 以明确参数运行 PyInstaller 和 Git。
import sys  # 使用构建环境自己的 Python，不调用开发者的固定环境。
import zipfile  # 打包无需安装的 Windows 下载包。

ROOT = Path(__file__).resolve().parents[1]  # 构建源限定为当前公开仓库。
VERSION = "v0.1.0-beta.1"  # 与初始 Release 标签保持一致。


def main():  # 只有显式构建才运行打包依赖。
    subprocess.run([sys.executable, "scripts/audit_public_tree.py"], cwd=ROOT, check=True)  # 未通过公开内容审计不打包。
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "CodexAccountProjectSync", "--paths", "src", "launcher.py"], cwd=ROOT, check=True)  # 独立 EXE 包含 Python 与 Tkinter 运行时。
    dist = ROOT / "dist"  # 发布文件只位于此目录。
    executable = dist / "CodexAccountProjectSync.exe"  # 便于普通用户辨认的双击入口。
    windows_zip = dist / f"CodexAccountProjectSync-{VERSION}-windows-x64.zip"  # 下载包明确标示系统与版本。
    with zipfile.ZipFile(windows_zip, "w", zipfile.ZIP_DEFLATED) as package:  # 压缩文件内容使用明确白名单。
        for path in (executable, ROOT / "README.md", ROOT / "README.en.md", ROOT / "LICENSE", ROOT / "docs" / "validation.md"):  # 不包含本地配置、日志或备份。
            package.write(path, path.name if path == executable else str(path.relative_to(ROOT)))  # 解压后 EXE 位于包根目录。
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
