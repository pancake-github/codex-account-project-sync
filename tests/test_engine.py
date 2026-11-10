import copy  # 验证输入与未选账户保持原样。
import unittest  # 使用标准库执行单元测试。
from codex_project_sync import engine  # 测试公开版多账户选择与预览。
from codex_project_sync import legacy_core as core  # 检查真实字段白名单。
from codex_project_sync.files import json_bytes  # 预览绑定精确配置字节。
from fixtures import owner, project, state, thread  # 仅使用合成数据。

HOME = r"C:\SyntheticData\CodexHome"  # 不包含任何真实使用者的目录。


class EngineTests(unittest.TestCase):
    def test_many_accounts_and_multiple_targets(self):  # 支持不同人数和项目数，并保留未选账户。
        original = state(HOME, 7)  # 不固定为两账户。
        original_copy = copy.deepcopy(original)  # 验证纯函数没有修改输入。
        result = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1), owner(2), owner(5)])  # 一源多目标。
        self.assertEqual(result.summary["accounts"], 4)  # 仅所选四个账户参与。
        self.assertEqual(result.summary["projects"], 4)  # 补齐四个独有项目。
        self.assertEqual(original, original_copy)  # 输入不变。
        self.assertEqual(result.merged[core.ACCOUNT_KEY]["inactive"][owner(3)], original[core.ACCOUNT_KEY]["inactive"][owner(3)])  # 未选账户完整保留。
        self.assertEqual(result.merged["queued-follow-ups"], original["queued-follow-ups"])  # 待发送内容不传播。
        self.assertEqual(result.merged[core.ATOM_KEY]["private-preference"], original[core.ATOM_KEY]["private-preference"])  # 私有偏好不变。

    def test_inactive_source_with_unselected_current(self):  # 源账户不必是当前账户。
        original = state(HOME)  # 当前账户零不参与这次同步。
        result = engine.preview(original, json_bytes(original), HOME, owner(1), [owner(2)])  # 选择两个已保存账户。
        self.assertEqual(result.merged["local-projects"], original["local-projects"])  # 当前账户的项目不被影响。
        self.assertEqual(result.merged[core.ATOM_KEY], original[core.ATOM_KEY])  # 当前账户所有偏好原样保留。
        self.assertEqual(result.summary["projects"], 2)  # 仅所选项目合并。

    def test_source_default_and_target_conflict_choice(self):  # 源不存在对应记录时，多个目标之间必须明确选择。
        original = state(HOME)  # 源账户没有额外项目 x。
        for index in (1, 2):  # 两个目标保存同标识但不同名称的项目。
            globals_ = original[core.ACCOUNT_KEY]["inactive"][owner(index)]["globals"]  # 只改合成目标。
            globals_["local-projects"]["x"] = project("x", f"目标 {index} 的名称")  # 产生真实的语义冲突。
            globals_[core.ID_MAP_KEY][f"local:{HOME}"]["x"] = "server-x"  # 内部映射仍然一致。
        pending = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1), owner(2)])  # 没有源值可默认采用。
        self.assertEqual(len(pending.unresolved), 1)  # 必须处理这一项。
        resolved = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1), owner(2)], {"local-projects:x": owner(2)})  # 用户明确选中第二个目标值。
        self.assertFalse(resolved.unresolved)  # 冲突已完整解决。
        self.assertEqual(resolved.merged["local-projects"]["x"]["name"], "目标 2 的名称")  # 结果遵循用户选择。

    def test_source_value_is_visible_default(self):  # 源账户同标识记录默认优先，但冲突仍然可见。
        original = state(HOME)  # 在目标中加入源项目的不同名称。
        target = original[core.ACCOUNT_KEY]["inactive"][owner(1)]["globals"]  # 获取合成目标。
        target["local-projects"]["p0"] = project("p0", "目标改名")  # 制造项目名称冲突。
        target[core.ID_MAP_KEY][f"local:{HOME}"]["p0"] = "server-p0"  # 内部项目仍为同一条。
        result = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 默认采用源值。
        self.assertEqual(result.conflicts[0].chosen, owner(0))  # 预览明确标明源账户选择。
        self.assertEqual(result.merged["local-projects"]["p0"]["name"], "合成项目 p0")  # 不让目标自动覆盖源。

    def test_explicit_projectless_and_thread_conflict(self):  # 当前明确移出项目的会话状态也参与冲突确认。
        original = state(HOME)  # 源账户会话零原本有项目。
        target = original[core.ACCOUNT_KEY]["inactive"][owner(1)]["globals"]  # 目标把同一会话明确移出项目。
        target["projectless-thread-ids"] = [thread(0)]  # 不删除会话正文。
        result = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)], {f"membership:{thread(0)}": owner(1)})  # 用户选择保留无项目状态。
        self.assertNotIn(thread(0), result.merged["thread-project-assignments"])  # 不恢复旧归属。
        self.assertIn(thread(0), result.merged["projectless-thread-ids"])  # 会话标识仍保留。

    def test_repeated_preview_is_stable(self):  # 相同合并结果不会逐次制造新变化。
        original = state(HOME)  # 用全新合成配置开始。
        first = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1), owner(2)])  # 第一次合并。
        second = engine.preview(first.merged, json_bytes(first.merged), HOME, owner(0), [owner(1), owner(2)])  # 再次合并相同选择。
        self.assertEqual(first.merged, second.merged)  # 所有分类字段保持稳定。

    def test_cloud_and_other_account_settings_are_preserved(self):  # 云端条目不跨账户复制。
        original = state(HOME)  # 合成云端排序每个账户不同。
        result = engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 仅合并两个本地账户分区。
        atoms = result.merged[core.ACCOUNT_KEY]["inactive"][owner(1)]["atoms"]  # 检查目标自己的偏好。
        self.assertIn("chatgpt:project:synthetic-cloud-1", atoms[core.UNIFIED_ORDER_KEY])  # 原云端条目保留。
        self.assertNotIn("chatgpt:project:synthetic-cloud-0", atoms[core.UNIFIED_ORDER_KEY])  # 源云端条目不会传播。
        self.assertEqual(atoms["private-preference"], {"account": 1})  # 其他偏好保留。

    def test_logged_out_state_can_select_saved_source(self):  # 退出登录后仍可明确选择已经保存的源。
        original = state(HOME)  # 当前账户零变为没有所有者的顶层数据。
        original[core.ACCOUNT_KEY]["owner"] = None  # 只有已保存账户一、二、三可发现。
        result = engine.preview(original, json_bytes(original), HOME, owner(1), [owner(2)])  # 不推测哪个自然人是源。
        self.assertEqual(result.summary["accounts"], 2)  # 仍仅使用用户明确选择。
        self.assertEqual(result.merged["local-projects"], original["local-projects"])  # 无所有者的顶层配置保持原样。

    def test_missing_or_same_target_is_refused(self):  # 不允许无目标或自己同步给自己。
        original = state(HOME)  # 准备有效账户集合。
        for targets in ([], [owner(0)], ["not-a-known-account"]):  # 三类不完整选择。
            with self.subTest(targets=targets), self.assertRaises(core.SyncError):  # 都必须停止。
                engine.preview(original, json_bytes(original), HOME, owner(0), targets)  # 不自动纳入未知账户。

    def test_unknown_version_and_cloud_backing_stop(self):  # 内部结构改变时不能沿用旧写法。
        original = state(HOME)  # 模拟不支持的迁移规则。
        original[core.MIGRATION_KEY] = {f"local:{HOME}": {"version": 2}}  # 首版只理解版本一。
        with self.assertRaises(core.SyncError):  # 未知规则必须停止。
            engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 不假装已适配新版本。
        original = state(HOME)  # 换成含云端关联的项目。
        original["local-projects"]["p0"]["chatGptBacking"] = {"synthetic": True}  # 云端关联不能跨账户复制。
        with self.assertRaises(core.SyncError):  # 需单独核查。
            engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 保留原配置。

    def test_different_internal_mapping_is_refused(self):  # 内部标识冲突不能通过普通下拉框绕过。
        original = state(HOME)  # 模拟同一项目对应两个服务端标识。
        target = original[core.ACCOUNT_KEY]["inactive"][owner(1)]["globals"]  # 修改合成目标。
        target[core.ID_MAP_KEY][f"local:{HOME}"]["p0"] = "other-server"  # 引入需要专门检查的不一致。
        with self.assertRaises(core.SyncError):  # 必须停止。
            engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 不创建重复项目。

    def test_native_mapping_path_mismatch_stops(self):  # 路径别名不能让已有原生项目被误当成新项目。
        original = state(HOME)  # 模拟通过不同资料路径访问同一份配置。
        original[core.ID_MAP_KEY]["local:C:\\SyntheticData\\DifferentHome"] = original[core.ID_MAP_KEY].pop(f"local:{HOME}")  # 保留既有映射但路径不再明确对应。
        with self.assertRaisesRegex(core.SyncError, "路径不一致"):  # 首版必须停止而不是自动重复创建项目。
            engine.preview(original, json_bytes(original), HOME, owner(0), [owner(1)])  # 不更改任何原配置。


if __name__ == "__main__":  # 显式运行测试才进入验证。
    unittest.main()  # 不打开图形窗口或真实配置。
