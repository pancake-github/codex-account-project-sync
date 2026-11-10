"""发现任意数量的本地账户，在确认范围内预演多目标合并。"""

import copy  # 合并预览始终使用独立副本。
from dataclasses import dataclass  # 明确账户和冲突记录的数据形状。
from . import legacy_core as core  # 沿用已经验证的本地项目合并及字段白名单。
from .files import digest_bytes, json_bytes  # 用摘要绑定用户确认过的配置版本。

MANAGED = ("local-projects", "project-order", "project-appearances", "thread-project-assignments", "thread-project-membership-host-ids", "projectless-thread-ids", "sidebar-project-thread-orders", core.ID_MAP_KEY, core.MIGRATION_KEY, *core.THREAD_MAP_KEYS)  # 仅这些本地分类字段允许写回。


@dataclass  # 账户只包含本地配置引用，不包含登录令牌。
class Account:
    key: str  # Codex 保存的不可逆账户摘要。
    globals: dict  # 本账户的本地项目配置。
    atoms: dict  # 本账户的界面偏好。
    current: bool = False  # 标识是否为当前登录账户。

    @property  # 界面使用短标识区分同机账户。
    def label(self):  # 不尝试从账户摘要猜测邮箱或自然人身份。
        return ("当前账户 · " if self.current else "账户 · ") + self.key[:12]  # 别名由用户在自己的电脑上补充。


@dataclass  # 每个冲突都明确列出所有候选账户。
class Conflict:
    key: str  # 绑定到字段和记录标识的稳定选择键。
    category: str  # 项目、归属或附属目录等字段名称。
    record: str  # 发生冲突的项目或会话标识。
    candidates: dict  # 各账户对应的完整候选记录。
    chosen: str | None  # 源账户默认优先，源缺失时必须手动选择。


@dataclass  # 预览与确认后提交共用同一份计划对象。
class Preview:
    original_hash: str  # 用户预览时配置的 SHA-256。
    merged: dict  # 已解决全部冲突时的完整候选配置。
    summary: dict  # 不含登录信息的合并统计。
    rows: list  # 每个所选账户的前后数量和变化记录。
    conflicts: list  # 包括默认解决和仍需用户选择的冲突。
    selected: list  # 源账户在前，目标账户按固定顺序排列。

    @property  # 界面与提交层使用相同的未解决判断。
    def unresolved(self):  # 没有选择候选账户的记录不得写入。
        return [item for item in self.conflicts if item.chosen is None]  # 返回需要用户处理的具体冲突。


def discover(state):  # 只发现选定 Codex 数据目录内已有的账户。
    core.require_dict(state, "主配置")  # 未知配置不能默认为空账户。
    registry = core.require_dict(state.get(core.ACCOUNT_KEY), "账户分区")  # 目前支持已识别的 renderer-state-v1 结构。
    inactive = core.require_dict(registry.get("inactive"), "已保存账户")  # 获取已经在本机登录过的账户。
    owner = registry.get("owner")  # 当前账户可能因退出登录而为空。
    if owner is not None and (not isinstance(owner, str) or not owner or owner in inactive):  # 拒绝互相矛盾的账户分区。
        raise core.SyncError("账户分区不完整或重复，请保留原配置并先核查。")  # 不替使用者猜测真实账户。
    result = {}  # 保留所有发现的账户供用户选择。
    if owner is not None:  # 当前账户存在时优先显示。
        result[owner] = Account(owner, state, core.require_dict(state.get(core.ATOM_KEY, {}), "当前界面偏好"), True)  # 只引用当前账户已有配置。
    for key in sorted(inactive):  # 固定显示顺序，避免每次启动变化。
        entry = core.require_dict(inactive[key], "账户快照")  # 验证保存的账户结构。
        result[key] = Account(key, core.require_dict(entry.get("globals"), "账户项目配置"), core.require_dict(entry.get("atoms"), "账户界面偏好"))  # 不提取任何凭据。
    return result  # 单个或零个账户仍可检查，不能进行跨账户同步。


def local_records(account):  # 将可发生冲突的本地记录整理为同一种形式。
    records = {}  # 键由字段名和真实记录标识共同组成。
    globals_ = account.globals  # 所有查询只作用于该账户的项目配置。
    for category in ("local-projects", "project-appearances", "sidebar-project-thread-orders", *core.THREAD_MAP_KEYS):  # 不比较或复制账户专属设置。
        for key, value in core.require_dict(globals_.get(category, {}), category).items():  # 读取已存在的本地分类附属记录。
            if category in core.THREAD_MAP_KEYS and not core.local_thread_id(key):  # 非本地目录提示不在共享范围。
                continue  # 保持原账户中的其他记录。
            if category in ("project-appearances", "sidebar-project-thread-orders") and key not in globals_.get("local-projects", {}):  # 这些表还可能包含云端和远程项目。
                continue  # 不把其他项目类型当成本地冲突候选。
            records[(category, key)] = copy.deepcopy(value)  # 完整候选值在界面中可供比较。
    for key, value in core.require_dict(globals_.get("thread-project-assignments", {}), "会话归属").items():  # 仅比较本地项目归属。
        if core.require_dict(value, "会话归属记录").get("projectKind") == "local":  # 云端与远程归属保持账户独立。
            records[("membership", key)] = copy.deepcopy(value)  # 本地归属与无项目状态使用同一个冲突键。
    for key in core.string_list(globals_.get("projectless-thread-ids", []), "无项目会话"):  # 记录明确移出项目的操作。
        if core.local_thread_id(key):  # 仅本地 UUID 可以参与这一类合并。
            records[("membership", key)] = {"projectKind": "projectless"}  # 这只是内存候选记录，不会写入归属表。
    return records  # 交给预览逻辑进行逐项冲突比较。


def _apply_choice(globals_, category, key, value):  # 在虚拟合并副本中统一已经确认的候选记录。
    if category == "membership":  # 会话归属与无项目状态需要互相排斥。
        memberships = globals_.setdefault("thread-project-assignments", {})  # 不在真实配置上直接操作。
        projectless = globals_.setdefault("projectless-thread-ids", [])  # 获取虚拟副本中的无项目列表。
        globals_["projectless-thread-ids"] = [item for item in projectless if item != key]  # 先移除旧状态。
        if value.get("projectKind") == "projectless":  # 用户选择保持无项目状态。
            memberships.pop(key, None)  # 移除较旧的本地归属。
            globals_["projectless-thread-ids"].append(key)  # 会话正文和标识仍然保留。
        else:  # 用户选择某个本地项目归属。
            memberships[key] = copy.deepcopy(value)  # 使用确认过的完整记录。
    else:  # 项目定义与附属映射可以直接覆盖对应记录。
        globals_.setdefault(category, {})[key] = copy.deepcopy(value)  # 只改变虚拟副本的指定字段。


def preview(state, original_bytes, home, source, targets, resolutions=None):  # 不写文件的多账户预览接口。
    accounts = discover(state)  # 每次预览重新验证账户范围。
    targets = sorted(set(targets))  # 多目标使用稳定顺序。
    selected = [source] + targets  # 源账户是同标识冲突的默认优先来源。
    if not targets or source in targets or any(key not in accounts for key in selected):  # 必须明确选择不同且已存在的账户。
        raise core.SyncError("请选择一个源账户和至少一个不同的目标账户；新账户需先在本机登录一次。")  # 不自动选中全部账户。
    resolutions = resolutions or {}  # 没有用户选择时只应用明确的源账户优先规则。
    candidates = {}  # 汇总每个本地字段在各账户中的候选值。
    for owner in selected:  # 未选账户完全不参与合并。
        for field, value in local_records(accounts[owner]).items():  # 获取所选账户已有的本地记录。
            candidates.setdefault(field, {})[owner] = value  # 保存候选账户与记录的对应关系。
    conflicts = []  # 同时记录自动默认和用户必须处理的冲突。
    for (category, key), values in sorted(candidates.items()):  # 固定预览顺序。
        if len({json_bytes(value) for value in values.values()}) < 2:  # 记录相同就不制造无意义的冲突提示。
            continue  # 合并核心会直接补齐这一记录。
        conflict_key = f"{category}:{key}"  # 在重新生成预览时仍能识别相同冲突。
        chosen = resolutions.get(conflict_key, source if source in values else None)  # 源不存在时不在多个目标之间偷偷取舍。
        if chosen is not None and chosen not in values:  # 旧选择失效时必须让用户重新选择。
            chosen = None  # 不沿用不再存在的候选账户。
        conflicts.append(Conflict(conflict_key, category, key, values, chosen))  # 保存供界面展示的具体候选记录。
    virtual = copy.deepcopy(accounts[source].globals)  # 将用户选择的源账户作为虚拟当前账户。
    virtual.pop(core.ACCOUNT_KEY, None)  # 不把真实账户分区嵌入虚拟分区。
    virtual[core.ATOM_KEY] = copy.deepcopy(accounts[source].atoms)  # 源账户的其他偏好只在副本中存在。
    virtual[core.ACCOUNT_KEY] = {"owner": source, "inactive": {key: {"globals": copy.deepcopy(accounts[key].globals), "atoms": copy.deepcopy(accounts[key].atoms)} for key in targets}}  # 虚拟分区只包含本次选择。
    for entry in virtual[core.ACCOUNT_KEY]["inactive"].values():  # 目标可能原本是实际当前账户，其 globals 引用了整个主配置。
        entry["globals"].pop(core.ACCOUNT_KEY, None)  # 删除仅用于真实顶层的账户分区副本。
        entry["globals"].pop(core.ATOM_KEY, None)  # 界面偏好已被单独放在 atoms 中。
    for conflict in conflicts:  # 将已选择的冲突值统一到虚拟副本。
        if conflict.chosen is None:  # 尚未选择的冲突只允许生成待确认预览。
            continue  # 提交层会拒绝这种预览。
        for _, globals_, _ in core.account_snapshots(virtual, selected):  # 对虚拟源和虚拟目标应用相同的确定结果。
            _apply_choice(globals_, conflict.category, conflict.record, conflict.candidates[conflict.chosen])  # 避免合并顺序再次改变用户的选择。
    local_map = {}  # 内部项目标识必须在所选账户之间一致。
    for owner in selected:  # 内部映射不能由普通冲突下拉框擅自改写。
        host_maps = core.require_dict(accounts[owner].globals.get(core.ID_MAP_KEY, {}), "内部项目主机映射")  # 数据目录别名不一致时不能默认为没有映射。
        if f"local:{home}" not in host_maps and any(key.startswith("local:") and value for key, value in host_maps.items()):  # 避免符号链接或路径别名让既有项目被当成全新项目。
            raise core.SyncError("所选数据目录与原生本机映射路径不一致，需先核查原路径，未创建重复项目。")  # 首版不猜测不同路径是否指向同一资料目录。
        mappings = host_maps.get(f"local:{home}", {})  # 只检查明确对应当前数据目录的映射。
        for project, internal in core.require_dict(mappings, "内部项目映射").items():  # 未知结构会停止。
            if project in local_map and local_map[project] != internal:  # 同一项目指向两个内部标识需要专门核查。
                raise core.SyncError("同一本地项目存在不同内部标识，需先核查，未自动创建重复项目。")  # 不让用户随意选择一个失效映射。
            local_map[project] = internal  # 保留已有原生标识。
    merged_virtual, summary = core.merge_state(virtual, selected, str(home))  # 沿用受限制的本地分类合并实现。
    merged = copy.deepcopy(state)  # 写回目标仍然是完整原配置的独立副本。
    destinations = discover(merged)  # 定位真实顶层或 inactive 中的账户。
    virtual_accounts = {key: (globals_, atoms) for key, globals_, atoms in core.account_snapshots(merged_virtual, selected)}  # 取出各个虚拟账户的最终分类。
    rows = []  # 提供逐账户前后数量和变化明细。
    for owner in selected:  # 未选账户不会被这一循环触及。
        globals_, atoms = virtual_accounts[owner]  # 获取该账户的虚拟结果。
        destination = destinations[owner]  # 定位完整副本中的对应账户。
        before_projects = accounts[owner].globals.get("local-projects", {})  # 统计原来的本地项目。
        after_projects = globals_.get("local-projects", {})  # 统计合并后项目。
        changes = [{"id": key, "name": value["name"], "kind": "新增" if key not in before_projects else "更新"} for key, value in after_projects.items() if before_projects.get(key) != value]  # 明确展示哪些项目会改变。
        before_assignments = {key: value for key, value in accounts[owner].globals.get("thread-project-assignments", {}).items() if value.get("projectKind") == "local"}  # 归属记录不等同于现存正文数量。
        after_assignments = {key: value for key, value in globals_.get("thread-project-assignments", {}).items() if value.get("projectKind") == "local"}  # 显示合并后的本地归属数量。
        changed_threads = sum(before_assignments.get(key) != after_assignments.get(key) for key in set(before_assignments) | set(after_assignments))  # 包括明确移出项目的操作。
        for key in MANAGED:  # 严格按白名单写回本地分类字段。
            if key in globals_:  # 核心未生成的字段不凭空新增。
                destination.globals[key] = copy.deepcopy(globals_[key])  # 不复制账户专属的其他 globals。
        for key in (core.UNIFIED_ORDER_KEY, core.PREFERENCES_KEY):  # 仅写回分组显示和本地排序相关偏好。
            destination.atoms[key] = copy.deepcopy(atoms[key])  # 其他 atoms 保持各自账户原样。
        if destination.current:  # 当前偏好对象原先可能缺失。
            merged[core.ATOM_KEY] = destination.atoms  # 将受限修改后的偏好挂回真实顶层。
        rows.append({"account": owner, "before_projects": len(before_projects), "after_projects": len(after_projects), "before_assignments": len(before_assignments), "after_assignments": len(after_assignments), "changed_assignments": changed_threads, "projects": changes})  # 表格和确认框共用同一组数字。
    return Preview(digest_bytes(original_bytes), merged, summary, rows, conflicts, selected)  # 原配置、登录信息和正文数据库均未写入。
