"""只处理本地项目分类的纯函数；本模块不会读写真实配置文件。"""

from __future__ import annotations  # 延迟解析类型标注，便于在现有 Python 环境中运行。
import copy  # 深复制配置，保证输入副本保持不变。
import uuid  # 验证本地会话标识，避免把云端会话当作本地会话处理。
from pathlib import PureWindowsPath  # 只解析 Windows 路径，不访问用户工作文件。

ACCOUNT_KEY = "electron-account-renderer-state-v1"  # Codex 保存各账户界面配置的键。
ATOM_KEY = "electron-persisted-atom-state"  # 当前账户的界面偏好所在位置。
ID_MAP_KEY = "app-server-project-id-by-legacy-project-id-by-host"  # 旧项目标识与内部项目标识的对应表。
MIGRATION_KEY = "app-server-projects-migration-by-host"  # 原生程序后续补齐会话归属时使用的检查点。
UNIFIED_ORDER_KEY = "unified-sidebar-project-order-v1"  # 包含本地及云端项目的侧栏顺序。
PREFERENCES_KEY = "flat-project-sidebar-preferences-v1"  # 项目分组显示偏好。
THREAD_MAP_KEYS = ("thread-writable-roots", "thread-workspace-root-hints", "thread-projectless-output-directories")  # 本地会话的既有目录信息。


class SyncError(Exception):  # 使用可直接向用户展示的错误，遇到未知结构时停止。
    """需要停止同步并保留原配置的情况。"""


def require_dict(value, label):  # 对需要理解其结构的对象进行检查。
    if not isinstance(value, dict):  # 未知格式不能按现有版本盲目修改。
        raise SyncError(f"{label} 的结构与已验证版本不同，未执行同步。")  # 给出不含账户敏感标识的说明。
    return value  # 返回经过验证的字典。


def string_list(value, label):  # 验证顺序列表和会话标识列表。
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):  # 拒绝隐式转换损坏的列表。
        raise SyncError(f"{label} 应为文本列表，未执行同步。")  # 保留原文件交由后续核查。
    return list(dict.fromkeys(value))  # 去除重复项并保留原顺序。


def local_thread_id(value):  # 判断记录是否使用本地会话 UUID。
    try:  # 不把外部会话标识转换成本地标识。
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()  # 只接受完整 UUID 文本。
    except (ValueError, AttributeError):  # 非 UUID 表示不属于本工具的共享范围。
        return False  # 该记录继续留在原账户中。


def account_snapshots(state, expected_owners):  # 定位本次已批准的所选账户。
    require_dict(state, "主配置")  # 确认顶层配置有效。
    account = require_dict(state.get(ACCOUNT_KEY), "账户配置")  # 只理解既有账户分区结构。
    owner = account.get("owner")  # 读取当前配置所属的账户摘要；不读取登录凭据。
    inactive = require_dict(account.get("inactive"), "非当前账户配置")  # 获取已登录过的另一账户快照。
    if not isinstance(owner, str) or not owner:  # 登录中间态或已退出登录时无法判断优先来源。
        raise SyncError("没有明确的当前账户；请先登录常用账户，再正常完全退出 Codex。")  # 不推测哪套配置应当优先。
    if owner in inactive or set(inactive) | {owner} != set(expected_owners):  # 新账户和丢失账户需要重新核查。
        raise SyncError("检测到的账户与已核对的所选账户不一致；未自动纳入新账户。")  # 避免把分类写入未经核查的账户。
    snapshots = []  # 按非当前账户在前、当前账户在后的顺序合并。
    for key in sorted(inactive):  # 固定次序，使重复运行可得到相同结果。
        entry = require_dict(inactive[key], "保存的账户快照")  # 验证快照外层。
        snapshots.append((key, require_dict(entry.get("globals"), "保存的项目配置"), require_dict(entry.get("atoms"), "保存的界面偏好")))  # 保留各账户独立的其他设置。
    snapshots.append((owner, state, require_dict(state.get(ATOM_KEY), "当前界面偏好")))  # 当前账户的同标识记录最后覆盖。
    return snapshots  # 向纯合并过程提供经过验证的引用。


def replace_local_order(original, project_ids):  # 只替换混合侧栏顺序中的本地项目部分。
    original = string_list(original, "侧栏项目顺序")  # 验证原列表，保留非本地项目的相对位置。
    local_keys = {f"codex:project:{key}" for key in project_ids}  # 使用已有项目标识，不重新创建项目。
    replacement = [f"codex:project:{key}" for key in project_ids]  # 生成所选账户共用的本地项目顺序。
    result = []  # 保存完整侧栏顺序。
    inserted = False  # 只在原来第一个本地项目的位置插入一次。
    for key in original:  # 按原顺序处理云端和远程项目。
        if key in local_keys:  # 遇到共享范围内的本地项目。
            if not inserted:  # 第一个本地项目是替换锚点。
                result.extend(replacement)  # 放入完整的本地项目列表。
                inserted = True  # 后续本地项目不再重复插入。
        else:  # 其他项目不属于本工具的修改范围。
            result.append(key)  # 原样保留云端和远程项目。
    return result if inserted else replacement + result  # 新账户没有本地排序记录时，将本地项目放在前面。


def merge_state(original, expected_owners, codex_home):  # 将所选账户的本地分类合并到新副本。
    state = copy.deepcopy(original)  # 后续所有修改只发生在新副本上。
    snapshots = account_snapshots(state, expected_owners)  # 同时确认账户范围和优先次序。
    host_key = f"local:{codex_home}"  # 只同步本次选定的 Windows 数据目录的内部项目映射。
    projects = {}  # 合并后的本地项目定义。
    order = []  # 合并后的项目顺序。
    appearances = {}  # 合并后的本地项目外观。
    assignments = {}  # 合并后的本地会话项目归属。
    projectless = []  # 显式移出项目的本地会话，仍然保留其历史。
    directory_maps = {key: {} for key in THREAD_MAP_KEYS}  # 保存已存在的本地会话目录提示。
    internal_ids = {}  # 保存已有内部项目标识，避免生成重复项目。
    for _, globals_, _ in snapshots:  # 当前账户位于最后，拥有同标识记录的优先权。
        definitions = require_dict(globals_.get("local-projects", {}), "本地项目清单")  # 读取本地项目定义。
        for key, value in definitions.items():  # 验证每个即将共享的本地项目。
            project = require_dict(value, "本地项目")  # 拒绝损坏的单个项目记录。
            roots = string_list(project.get("rootPaths"), "项目文件夹")  # 本方案只支持有明确本地文件夹的项目。
            if project.get("id") != key or not isinstance(project.get("name"), str) or not roots:  # 验证可供原生程序继续使用的基本字段。
                raise SyncError("发现缺少标识、名称或本地文件夹的项目，需先核查。")  # 不用占位符补齐真实数据。
            if any(not PureWindowsPath(root).is_absolute() for root in roots):  # 本机方案不接受相对路径或其他平台路径。
                raise SyncError("发现非绝对 Windows 项目路径，需先核查。")  # 不擅自推测工作目录。
            if any(project.get(field) is not None for field in ("chatGptBacking", "chatGptBackingCreation", "retiredChatGptBacking")):  # 云端关联不能跨账户复制。
                raise SyncError("发现与 ChatGPT 云端关联的本地项目，需单独核查后再同步。")  # 保持本工具的本地项目边界。
            projects[key] = copy.deepcopy(project)  # 同项目标识时保留当前账户的完整定义。
    for _, globals_, _ in reversed(snapshots):  # 先采用当前账户的项目顺序，再补齐其他账户的项目。
        keys = string_list(globals_.get("project-order", []), "项目顺序")  # 校验各账户已有的顺序。
        order.extend(key for key in keys if key in projects and key not in order)  # 不移动已经确定的位置。
    order.extend(key for key in projects if key not in order)  # 补齐尚未进入排序列表的项目。
    for _, globals_, _ in snapshots:  # 合并只属于本地项目的附属记录。
        looks = require_dict(globals_.get("project-appearances", {}), "项目外观")  # 不共享其他类型项目的外观。
        appearances.update({key: copy.deepcopy(value) for key, value in looks.items() if key in projects})  # 当前账户外观优先。
        memberships = require_dict(globals_.get("thread-project-assignments", {}), "会话归属")  # 验证会话归属表。
        for thread, value in memberships.items():  # 对每条会话归属分别处理。
            record = require_dict(value, "会话归属记录")  # 未知结构会停止同步。
            if record.get("projectKind") != "local":  # 云端和远程归属保持各自账户独立。
                continue  # 不把非本地会话移入共享范围。
            if not local_thread_id(thread) or record.get("projectId") not in projects:  # 有效本地归属必须指向已有项目。
                raise SyncError("发现无法对应本地项目的会话归属，需先核查。")  # 不删除无法解析的记录。
            if record.get("projectOrigin") == "chatgpt":  # 这类会话归属与云端账户关联。
                raise SyncError("发现来自 ChatGPT 项目的归属记录，需单独核查。")  # 不把云端关联静默传播给其他账户。
            assignments[thread] = copy.deepcopy(record)  # 合并本地归属并让当前账户优先。
            projectless = [item for item in projectless if item != thread]  # 当前明确归入项目时覆盖较旧的无项目状态。
        for thread in string_list(globals_.get("projectless-thread-ids", []), "无项目会话列表"):  # 同步显式移出项目的本地会话。
            if local_thread_id(thread):  # 只处理本地 UUID。
                assignments.pop(thread, None)  # 尊重当前账户明确移出项目的操作。
                if thread not in projectless:  # 避免出现重复的无项目记录。
                    projectless.append(thread)  # 保留会话本身和原始历史。
        for key in THREAD_MAP_KEYS:  # 合并已有目录提示，不创建或移动工作文件。
            values = require_dict(globals_.get(key, {}), "本地会话目录提示")  # 验证目录映射格式。
            directory_maps[key].update({thread: copy.deepcopy(value) for thread, value in values.items() if local_thread_id(thread)})  # 非本地记录留在原账户。
        maps = require_dict(globals_.get(ID_MAP_KEY, {}), "内部项目映射")  # 各远程主机映射不参与共享。
        local_map = require_dict(maps.get(host_key, {}), "本机内部项目映射")  # 只读取已验证的本机映射。
        for project_id, server_id in local_map.items():  # 保留项目原有的服务端标识。
            if project_id in projects:  # 不携带无对应本地项目的其他记录。
                if not isinstance(server_id, str) or not server_id:  # 不允许使用空内部标识继续迁移。
                    raise SyncError("内部项目标识不完整，需先核查。")  # 停止而不是新建占位项目。
                internal_ids[project_id] = server_id  # 同标识映射以当前账户为准。
    if len(set(internal_ids.values())) != len(internal_ids):  # 多个别名指向同一内部项目需要单独处理。
        raise SyncError("存在多个本地项目指向同一个内部项目，需先核查，未自动去重。")  # 避免错误合并不同分类。
    thread_orders = {}  # 保存合并后的本地项目内会话顺序。
    for _, globals_, _ in snapshots:  # 当前账户的顺序记录优先。
        for key, record in require_dict(globals_.get("sidebar-project-thread-orders", {}), "项目内会话顺序").items():  # 非本地项目顺序不参与合并。
            if key in projects:  # 只同步当前已有的本地项目。
                require_dict(record, "项目内顺序记录")  # 验证顺序记录结构。
                threads = string_list(record.get("threadIds", []), "项目内会话顺序")  # 校验会话列表。
                previous = thread_orders.get(key, {}).get("threadIds", [])  # 合并时补齐其他账户已有会话。
                thread_orders[key] = {**copy.deepcopy(record), "threadIds": list(dict.fromkeys(threads + previous))}  # 当前账户会话顺序在前。
    for _, globals_, atoms in snapshots:  # 将共享分类写入所选账户各自的副本。
        globals_["local-projects"] = copy.deepcopy(projects)  # 所选账户显示相同的本地项目。
        globals_["project-order"] = list(order)  # 所选账户保留相同的本地项目顺序。
        for key, shared, selected in (("project-appearances", appearances, lambda item: item in projects), ("sidebar-project-thread-orders", thread_orders, lambda item: item in projects)):  # 限定只覆盖本地项目对应项。
            current = require_dict(globals_.get(key, {}), key)  # 保留其他项目类型的记录。
            globals_[key] = {**{item: value for item, value in current.items() if not selected(item)}, **copy.deepcopy(shared)}  # 将共享项与账户专属项合并。
        current = require_dict(globals_.get("thread-project-assignments", {}), "会话归属")  # 获取账户自己的非本地归属。
        globals_["thread-project-assignments"] = {**{key: value for key, value in current.items() if value.get("projectKind") != "local"}, **copy.deepcopy(assignments)}  # 本地归属统一，其他归属原样保留。
        hosts = require_dict(globals_.get("thread-project-membership-host-ids", {}), "会话主机映射")  # 原远程会话主机关系保留。
        globals_["thread-project-membership-host-ids"] = {**hosts, **{thread: "local" for thread in assignments}}  # 为共享的本地归属明确记录本机主机。
        existing_projectless = string_list(globals_.get("projectless-thread-ids", []), "无项目会话列表")  # 获取账户专属的其他标识。
        globals_["projectless-thread-ids"] = [item for item in existing_projectless if not local_thread_id(item)] + list(projectless)  # 不删除任何聊天正文。
        for key in THREAD_MAP_KEYS:  # 共享本地目录提示但不修改文件夹内容。
            globals_[key] = {**require_dict(globals_.get(key, {}), key), **copy.deepcopy(directory_maps[key])}  # 保留账户专属的非本地记录。
        maps = require_dict(globals_.get(ID_MAP_KEY, {}), "内部项目映射")  # 获取当前账户原映射。
        globals_[ID_MAP_KEY] = {**maps, host_key: {**require_dict(maps.get(host_key, {}), "本机内部项目映射"), **internal_ids}}  # 保留原生标识和其他主机的数据。
        migrations = require_dict(globals_.get(MIGRATION_KEY, {}), "项目迁移检查点")  # 每个账户保留独立的未知检查点字段。
        checkpoint = copy.deepcopy(require_dict(migrations.get(host_key, {}), "本机项目迁移检查点"))  # 不复制另一个账户的完成标记。
        if checkpoint.get("version", 1) != 1:  # 未来版本迁移规则需要重新验证。
            raise SyncError("原生项目迁移规则版本已变化，未执行同步。")  # 避免误用当前版本的检查点。
        pending = string_list(checkpoint.get("pendingThreadAssignmentIds", []), "待补齐归属列表")  # 已有待处理归属继续保留。
        checkpoint.update(version=1, projectsMigrated=len(internal_ids) == len(projects), threadAssignmentsMigrated=False, pendingThreadAssignmentIds=sorted(set(pending) | set(assignments) | set(projectless)))  # 交给原生程序补齐归属，不伪造迁移完成。
        globals_[MIGRATION_KEY] = {**migrations, host_key: checkpoint}  # 只更新本机检查点。
        atoms[UNIFIED_ORDER_KEY] = replace_local_order(atoms.get(UNIFIED_ORDER_KEY, []), order)  # 保留各账户自己的云端及远程项目。
        preferences = copy.deepcopy(require_dict(atoms.get(PREFERENCES_KEY, {}), "项目显示偏好"))  # 其他排序偏好继续由各账户保留。
        preferences.update(mode="project", initialized=True)  # 明确启用按项目分组。
        atoms[PREFERENCES_KEY] = preferences  # 保存本账户的分组偏好副本。
    summary = {"accounts": len(snapshots), "projects": len(projects), "assignments": len(assignments), "projectless": len(projectless), "internal_mappings": len(internal_ids)}  # 仅输出统计，不泄露账户标识。
    return state, summary  # 返回副本和可供核验的合并统计。
