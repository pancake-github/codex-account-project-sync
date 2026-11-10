"""发布前审核文件白名单、敏感内容及 Git 可达历史。"""

from pathlib import Path  # 审计范围始终限定到仓库根目录。
import re  # 检查常见凭据和真实用户目录模式。
import subprocess  # 使用 Git 列出实际提交文件与可达历史。
import sys  # 发现问题时返回失败状态。

ROOT = Path(__file__).resolve().parents[1]  # 不向仓库外搜索私人资料。
TOP_FILES = {".gitignore", "LICENSE", "README.md", "README.en.md", "pyproject.toml", "launcher.py"}  # 明确允许公开的根文件。
TOP_DIRECTORIES = {"src", "tests", "scripts", "docs", ".github"}  # 只公开代码、合成测试、文档与构建配置。
BAD_NAMES = {"settings.json", "aliases.json", "auth.json", "real-state-before.json", "last-sync.json", "last-restore.json", "first-database-backup.json"}  # 这些真实本机资料不应进入仓库。
BAD_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".db-wal", ".db-shm", ".lnk", ".exe", ".log")  # 二进制发布资产通过 Release 分发，数据文件不入库。
PATTERNS = [("GitHub token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{24,}")), ("API secret", re.compile(rb"sk-[A-Za-z0-9_-]{24,}")), ("JWT", re.compile(rb"eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")), ("Windows personal directory", re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Example\b|Synthetic\b)[A-Za-z0-9_.-]+", re.I))]  # 不把真实账户凭据或个人目录发布出去。


def git(*args):  # 所有 Git 参数都是列表，不经过 shell 拼接。
    return subprocess.check_output(["git", *args], cwd=ROOT)  # 不访问凭据或远程仓库。


def check_blob(label, data, errors):  # 对每份待发布内容应用相同检查。
    for name, pattern in PATTERNS:  # 匹配文本和编译前源码中的常见敏感模式。
        if pattern.search(data):  # 不打印匹配到的真实内容。
            errors.append(f"{label}: detected {name}")  # 只报告文件或对象位置。


def main():  # CI 与本地提交前使用相同入口。
    errors = []  # 收集全部问题，一次报告必要位置。
    files = set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}  # 包括待暂存源码，忽略明确排除的本机产物。
    for name in sorted(files):  # 依次检查实际将被提交或打包的文件。
        path = Path(name)  # 按仓库相对路径判断白名单。
        if name not in TOP_FILES and path.parts[0] not in TOP_DIRECTORIES:  # 禁止把个人版目录整体复制进来。
            errors.append(f"Unexpected public path: {name}")  # 新文件需明确加入白名单才能公开。
        if path.name in BAD_NAMES or path.name.startswith(".codex-global-state") or name.lower().endswith(BAD_SUFFIXES):  # 检查敏感数据文件名。
            errors.append(f"Private or binary file in public tree: {name}")  # 不读取并输出真实数据。
        absolute = ROOT / path  # 内容检查限定在仓库内。
        if absolute.is_file():  # 已暂存但已删除的文件留给 Git 状态处理。
            check_blob(name, absolute.read_bytes(), errors)  # 审查源码及文档内容。
    objects = git("rev-list", "--objects", "--all").decode().splitlines()  # 连历史对象也不能藏有后来删除的私密内容。
    for entry in objects:  # 每个可达对象只按 Git 给出的标识读取。
        parts = entry.split(" ", 1)  # 提交与树对象可能没有路径。
        if len(parts) != 2:  # 无路径对象不含公开文件内容。
            continue  # 后续只审查 blob。
        oid, name = parts  # 保存对象标识与历史路径。
        if git("cat-file", "-t", oid).strip() == b"blob":  # 仅内容对象需要模式检查。
            path = Path(name)  # 历史路径也必须符合当前公开范围。
            if name not in TOP_FILES and path.parts[0] not in TOP_DIRECTORIES or path.name in BAD_NAMES or name.lower().endswith(BAD_SUFFIXES):  # 历史里出现私密文件同样拒绝。
                errors.append(f"Disallowed historical path: {name}")  # 不只依赖当前 .gitignore。
            check_blob(f"history:{name}:{oid[:10]}", git("cat-file", "blob", oid), errors)  # 不输出 blob 的实际内容。
    if errors:  # 任何问题都应阻止发布。
        print("\n".join(errors))  # 给出可操作的路径和问题类型。
        return 1  # CI 与本地都会失败。
    print(f"Public audit passed: {len(files)} working/index files; {len(objects)} reachable Git objects.")  # 只汇报统计。
    return 0  # 表示文件与历史审计都完成。


if __name__ == "__main__":  # 显式运行脚本才启动审核。
    raise SystemExit(main())  # 让构建流程准确获取审核状态。
