import importlib.util  # 导入公开构建脚本而不执行其构建入口。
import io  # 模拟公开许可下载，不在测试中联网。
from pathlib import Path  # 所有测试资产都放在临时目录。
import tempfile  # 不修改真实 Python 安装目录。
from types import SimpleNamespace  # 为构建器元数据提供明确测试记录。
import unittest  # 使用标准测试库。
from unittest.mock import patch  # 隔离运行时版本查询与联网动作。

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_windows.py"  # 从当前仓库取得构建实现。
SPEC = importlib.util.spec_from_file_location("public_build_script", SCRIPT)  # 不加载安装环境中的同名模块。
BUILD = importlib.util.module_from_spec(SPEC)  # 建立独立模块对象。
SPEC.loader.exec_module(BUILD)  # 这里只定义函数，不启动 PyInstaller。


class BuildTests(unittest.TestCase):
    def test_existing_runtime_licenses_are_copied(self):  # 安装目录已有许可时不联网。
        with tempfile.TemporaryDirectory(prefix="synthetic-runtime-") as temporary:  # 使用全新合成环境。
            base = Path(temporary) / "python"  # 假 Python 安装目录。
            base.mkdir()  # 创建许可来源目录。
            (base / "LICENSE.txt").write_text("Synthetic Python license", encoding="utf-8")  # 明确为合成测试文本。
            for name in ("tcl", "tk"):  # 两种运行时都有本地许可。
                folder = base / "tcl" / f"{name}8.6"  # 模拟 Windows 安装层级。
                folder.mkdir(parents=True)  # 不访问真实 Python 目录。
                (folder / "license.terms").write_text(f"Synthetic {name} license", encoding="utf-8")  # 测试只核对复制行为。
            with patch.object(BUILD.sys, "base_prefix", str(base)), patch.object(BUILD.importlib.metadata, "distribution", return_value=SimpleNamespace(files=[])), patch.object(BUILD.urllib.request, "urlopen") as download:  # 隔离环境和网络。
                paths = BUILD.runtime_licenses(Path(temporary) / "dist")  # 收集实际测试文件。
            self.assertEqual(len(paths), 3)  # Python、Tcl、Tk 三份许可齐全。
            download.assert_not_called()  # 有本地许可时不得重复联网。

    def test_missing_runtime_licenses_use_exact_official_tags(self):  # 缺失文件时使用实际补丁版本的官方源码。
        with tempfile.TemporaryDirectory(prefix="synthetic-runtime-") as temporary:  # 使用独立资料目录。
            base = Path(temporary) / "python"  # 假运行时不包含 Tcl/Tk 许可。
            base.mkdir()  # 创建必要来源目录。
            (base / "LICENSE.txt").write_text("Synthetic Python license", encoding="utf-8")  # Python 许可仍来自安装目录。
            urls = []  # 记录请求目标供明确断言。
            def download(url, **kwargs):  # 提供可作为上下文管理器的合成响应。
                urls.append(url)  # 验证没有按最新版或其他版本猜测。
                return io.BytesIO(b"Synthetic public license text. " * 30)  # 足够长度的合成许可文本。
            with patch.object(BUILD.sys, "base_prefix", str(base)), patch.object(BUILD, "runtime_versions", return_value=("8.6.14", "8.6.13")), patch.object(BUILD.importlib.metadata, "distribution", return_value=SimpleNamespace(files=[])), patch.object(BUILD.urllib.request, "urlopen", side_effect=download):  # 不真实联网或创建图形窗口。
                paths = BUILD.runtime_licenses(Path(temporary) / "dist")  # 走完整的缺失许可补齐逻辑。
            self.assertEqual(len(paths), 3)  # 三份许可均进入分发文件列表。
            self.assertEqual(urls, ["https://raw.githubusercontent.com/tcltk/tcl/core-8-6-14/license.terms", "https://raw.githubusercontent.com/tcltk/tk/core-8-6-13/license.terms"])  # 精确匹配各自运行时版本和官方仓库。


if __name__ == "__main__":  # 只有显式测试才执行合成用例。
    unittest.main()  # 不构建 EXE 或修改真实安装环境。
