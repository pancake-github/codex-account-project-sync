import contextlib  # 正确关闭测试数据库连接。
import json  # 准备明确的合成配置字节。
from pathlib import Path  # 使用独立临时资料目录。
import sqlite3  # 检查备份内的 WAL 提交。
import tempfile  # 测试不会修改任何真实使用者资料。
import unittest  # 标准库测试入口。
from types import SimpleNamespace  # 为未解决冲突构造最小明确的测试记录。
from unittest.mock import patch  # 仅在合成资料测试中替换进程检查。
from codex_project_sync import engine, storage  # 测试真实提交、备份及恢复入口。
from codex_project_sync.files import digest_bytes, file_digest, json_bytes, read_json  # 使用正式校验逻辑。
from codex_project_sync.legacy_core import SyncError  # 检查明确的停止条件。
from fixtures import add_databases, owner, state  # 全部输入均为合成样本。


class StorageTests(unittest.TestCase):
    def setUp(self):  # 每个用例重新建立独立测试环境。
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-codex-sync-")  # 不位于真实 Codex 主目录。
        self.addCleanup(self.temporary.cleanup)  # 测试完成后由标准库清理自己的临时目录。
        self.root = Path(self.temporary.name)  # 记录隔离根目录。
        self.home = self.root / "中文资料 空格"  # 同时验证 Unicode 与空格路径。
        self.store = self.root / "tool-data"  # 工具资料与来源分离。
        self.original = state(self.home)  # 任意使用者路径都来自运行时输入。
        add_databases(self.home, self.original)  # 创建元数据、分页正文和侧栏合成数据库。
        self.path = self.home / ".codex-global-state.json"  # 唯一允许提交的主配置。
        self.raw = json.dumps(self.original, ensure_ascii=False, indent=2).encode("utf-8")  # 保留不同于输出格式的原始字节。
        self.path.write_bytes(self.raw)  # 只准备临时配置。
        self.old_bak = b'{"synthetic_previous_backup":true}\n'  # 原备用文件可能与主配置不同。
        Path(str(self.path) + ".bak").write_bytes(self.old_bak)  # 准备精确恢复验证。
        self.plan = engine.preview(self.original, self.raw, self.home, owner(0), [owner(1), owner(2)])  # 源与多个目标都明确选定。

    def test_structure_detection_ignores_version_numbers(self):  # 不把开发者数据库版本写死。
        found = storage.databases(self.home)  # 按必要表与字段发现结构。
        self.assertEqual(found["会话元数据"].name, "state_99.sqlite")  # 文件名数字不必与开发电脑相同。
        self.assertEqual(found["分页历史"].name, "thread_history_77.sqlite")  # 同样支持结构一致的分页数据库。

    def test_full_sync_repeat_restore_preserves_databases(self):  # 完整流程必须保留所有正文及未选账户。
        found = storage.databases(self.home)  # 为全部来源数据库留下字节基线。
        before = {role: file_digest(path) for role, path in found.items()}  # 不使用实现逻辑自证数据未变。
        with patch.object(storage, "require_closed", return_value=None):  # 仅合成目录允许测试忽略本机真实 Codex 进程。
            first = storage.synchronize(self.plan, self.home, self.store, confirmed=True)  # 执行正式同步路径。
            self.assertTrue(first["changed"])  # 首次确实提交了分类修改。
            merged = read_json(self.path)  # 从真实写入的临时文件重新读取。
            again = engine.preview(merged, self.path.read_bytes(), self.home, owner(0), [owner(1), owner(2)])  # 再次生成同一选择的预览。
            self.assertFalse(storage.synchronize(again, self.home, self.store, confirmed=True)["changed"])  # 重复运行不反复写入。
            storage.restore(self.home, self.store, confirmed=True)  # 使用正式恢复入口。
        self.assertEqual(self.path.read_bytes(), self.raw)  # 主配置逐字节恢复。
        self.assertEqual(Path(str(self.path) + ".bak").read_bytes(), self.old_bak)  # 原备用文件也逐字节恢复。
        self.assertEqual(before, {role: file_digest(path) for role, path in found.items()})  # 来源聊天数据库完全没有被改写。

    def test_confirmation_required_before_any_store_write(self):  # 没有确认时连备份资料目录都不创建。
        with self.assertRaises(SyncError):  # 必须拒绝未确认调用。
            storage.synchronize(self.plan, self.home, self.store)  # 默认 confirmed=False。
        self.assertFalse(self.store.exists())  # 没有任何工具数据副作用。
        self.assertEqual(self.path.read_bytes(), self.raw)  # 来源配置保持原样。

    def test_running_application_blocks_before_backup(self):  # 进程保护先于任何备份或提交。
        with patch.object(storage, "require_closed", side_effect=SyncError("应用仍在运行")):  # 模拟未退出应用。
            with self.assertRaises(SyncError):  # 必须停止。
                storage.synchronize(self.plan, self.home, self.store, confirmed=True)  # 即使点击确认也不能绕过。
        self.assertFalse(self.store.exists())  # 不创建备份或成功回执。

    def test_unresolved_conflict_blocks_before_any_write(self):  # 点击确认不能绕过尚未选择的目标冲突。
        self.plan.conflicts.append(SimpleNamespace(chosen=None))  # 模拟预览中仍有未解决条目。
        with self.assertRaises(SyncError):  # 提交接口自身也必须拒绝。
            storage.synchronize(self.plan, self.home, self.store, confirmed=True)  # 不依赖界面按钮禁用作为唯一保护。
        self.assertFalse(self.store.exists())  # 甚至不创建工具资料目录。

    def test_changed_after_confirmation_is_refused(self):  # 用户确认后其他写入者更新了配置。
        newer = self.raw + b"\n"  # JSON 含义相同但文件已经被重新保存。
        self.path.write_bytes(newer)  # 模拟更新。
        with patch.object(storage, "require_closed", return_value=None):  # 只跳过与此测试无关的真实进程。
            with self.assertRaisesRegex(SyncError, "已经变化"):  # 原字节摘要必须使旧计划失效。
                storage.synchronize(self.plan, self.home, self.store, confirmed=True)  # 不能覆盖新的配置。
        self.assertEqual(self.path.read_bytes(), newer)  # 保留新写入内容。
        self.assertFalse((self.store / "backups").exists())  # 在备份前就拒绝过时计划。

    def test_unknown_and_ambiguous_database_structure_stops(self):  # 不能靠最高版本号或空数据库猜测。
        duplicate = self.home / "state_100.sqlite"  # 制造第二个同结构候选。
        with contextlib.closing(storage.connect_readonly(self.home / "state_99.sqlite")) as source:  # 只读来源合成数据库。
            with contextlib.closing(sqlite3.connect(duplicate)) as target:  # Windows 上显式关闭句柄后才能删除测试文件。
                source.backup(target)  # 复制出明确歧义。
        with self.assertRaises(SyncError):  # 多个匹配必须要求核查。
            storage.databases(self.home)  # 不自动选择数字更大的文件。
        duplicate.unlink()  # 删除本测试明确创建的单个候选文件。
        (self.home / "thread_history_77.sqlite").unlink()  # 模拟无法识别的历史结构。
        with self.assertRaises(SyncError):  # 缺少受支持的正文结构也必须停止。
            storage.databases(self.home)  # 不生成空正文替代。

    def test_wal_content_is_included_in_database_backup(self):  # 初次备份包含 WAL 已提交数据。
        other_home = self.root / "wal-synthetic"  # 使用另一完整合成目录。
        other = state(other_home)  # 与当前测试目录没有共享来源。
        connections = add_databases(other_home, other, wal=True)  # 保持 WAL 来源连接以维持测试场景。
        for connection in connections:  # 即使断言失败也释放测试来源连接。
            self.addCleanup(connection.close)  # 由 unittest 统一清理。
        (other_home / ".codex-global-state.json").write_bytes(json_bytes(other))  # 备份还需要该目录主配置。
        with patch.object(storage, "require_closed", return_value=None):  # 此处来源全部是合成数据库。
            folder = storage.make_backup(other_home, self.store, storage.databases(other_home), True, lambda message: None)  # 使用正式数据库备份逻辑。
        manifest = read_json(folder / "manifest.json")  # 核对实际备份清单。
        history = next(item for item in manifest["databases"] if "thread_history_" in item["source"])  # 定位分页正文备份。
        with contextlib.closing(sqlite3.connect(folder / history["file"])) as backup:  # 显式释放 Windows 上的备份文件句柄。
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM thread_items").fetchone()[0], 4)  # 不能漏掉只在 WAL 的已提交内容。
        self.assertEqual(file_digest(folder / history["file"]), history["sha256"])  # 摘要匹配清单。

    def test_failed_backup_write_rolls_back_both_configuration_files(self):  # 模拟第二个文件提交失败。
        with patch.object(storage, "require_closed", return_value=None):  # 副本测试不查询真实进程。
            folder = storage.make_backup(self.home, self.store, {}, False, lambda message: None)  # 创建正式回退点。
            real_write = storage.atomic_write  # 保留实际原子写入用于回退。
            failed = []  # 只注入一次磁盘失败。
            def write(path, data):  # 在备用文件上触发故障。
                if str(path).endswith(".bak") and not failed:  # 主文件已经提交但备用文件尚未成功。
                    failed.append(True)  # 后续回退允许正常写入。
                    raise OSError("synthetic disk failure")  # 不操作真实磁盘错误场景。
                return real_write(path, data)  # 其余操作使用正式实现。
            with patch.object(storage, "atomic_write", write):  # 注入只限本用例。
                with self.assertRaisesRegex(SyncError, "已经恢复"):  # 必须报告已自动回退。
                    storage.commit(self.home, b'{}', b'{}', digest_bytes(self.raw), folder)  # 尝试小型双文件提交。
        self.assertEqual(self.path.read_bytes(), self.raw)  # 主文件已回退。
        self.assertEqual(Path(str(self.path) + ".bak").read_bytes(), self.old_bak)  # 备用文件原样保留。

    def test_tampered_backup_is_refused(self):  # 备份损坏时禁止恢复。
        folder = storage.make_backup(self.home, self.store, {}, False, lambda message: None)  # 创建合成配置备份。
        (folder / "main.json").write_bytes(b"{}")  # 模拟文件被截断。
        with self.assertRaisesRegex(SyncError, "SHA-256"):  # 必须发现摘要不匹配。
            storage._backup_configuration(folder)  # 不把损坏内容写入来源。


if __name__ == "__main__":  # 只有显式执行才运行测试。
    unittest.main()  # 不打开真实账户或图形界面。
