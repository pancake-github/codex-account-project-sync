"""支持结构识别、只读历史核对、备份及受保护的配置提交。"""

import contextlib  # 确保每个数据库连接和工具锁都释放。
from datetime import datetime  # 用本地时间标记备份。
import hashlib  # 将不同 Codex 数据目录的工具资料分开。
import json  # 读取原生主配置。
import os  # 读取当前用户环境变量。
from pathlib import Path  # 处理中文和空格路径。
import shutil  # 检查备份磁盘空间。
import sqlite3  # 以只读方式检查来源，并用原生备份接口保存 WAL 数据。
from .engine import discover  # 不将未选账户纳入同步统计。
from .legacy_core import SyncError, ID_MAP_KEY  # 使用统一错误与内部映射字段。
from .files import atomic_write, digest_bytes, file_digest, json_bytes, read_json, require_closed  # 共用文件校验和进程保护。


def default_home():  # 适配不同使用者，不保留开发者的用户名或盘符。
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()  # 尊重已有 CODEX_HOME 设置。


def profile_directory(home):  # 每个数据目录使用独立备份和别名，避免互相恢复错目录。
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))  # 只使用当前 Windows 用户的资料目录。
    key = hashlib.sha256(os.path.normcase(str(Path(home).resolve())).encode("utf-8")).hexdigest()[:20]  # 路径摘要不包含登录令牌。
    return base / "CodexAccountProjectSync" / "profiles" / key  # 不要求程序目录可写或管理员权限。


def connect_readonly(path):  # 来源数据库永远不使用可写连接。
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)  # 同时读取来源 WAL 中已经提交的内容。


def _supports(path, required):  # 通过实际表和字段识别结构，不靠文件版本号猜测。
    try:  # 不兼容的候选可以被排除，但不会被修改。
        with contextlib.closing(connect_readonly(path)) as database:  # 只读打开候选数据库。
            for table, columns in required.items():  # 表名来自程序内部固定白名单。
                present = {row[1] for row in database.execute(f'PRAGMA table_info("{table}")')}  # 仅查询表结构。
                if not set(columns) <= present:  # 缺少必要字段时不是受支持结构。
                    return False  # 不使用空数据库替代未知版本。
        return True  # 所有必要表和字段均已确认。
    except sqlite3.Error:  # 损坏或非 SQLite 文件不参与后续同步。
        return False  # 最终无兼容候选时会向用户明确停止。


def databases(home):  # 支持不同使用者的文件名，但只接受已经验证的结构。
    home = Path(home).resolve()  # 固定本次选择的数据目录。
    groups = [("会话元数据", list(home.glob("state_*.sqlite")), {"threads": ["id", "rollout_path", "history_mode"], "projects": ["id"]}), ("分页历史", list(home.glob("thread_history_*.sqlite")), {"thread_items": ["thread_id", "item_json"], "thread_history_projection_state": ["thread_id"]})]  # 首版明确支持这两类分页历史结构。
    result = {}  # 角色映射用于核对数据及首次备份。
    for role, candidates, required in groups:  # 每类必须有且只有一个兼容候选。
        matches = [path for path in candidates if _supports(path, required)]  # 不按文件名中的最大数字自动选择。
        if len(matches) != 1:  # 零个或多个匹配都需要核查。
            raise SyncError(f"{role}没有唯一的受支持数据库，已停止；请勿用空数据库替代。")  # 未知结构不能写配置。
        result[role] = matches[0]  # 保留实际识别到的文件路径。
    catalog = home / "sqlite" / "codex.db"  # 已知的侧栏目录数据库可以存在也可以不存在。
    if catalog.exists():  # 存在时同样检查结构并纳入首次备份。
        if not _supports(catalog, {"local_thread_catalog": ["host_id", "thread_id"]}):  # 不跳过一个未知结构的已有目录数据库。
            raise SyncError("侧栏数据库结构与已验证版本不同，已停止。")  # 交由后续版本适配。
        result["侧栏目录"] = catalog  # 使用原生备份接口保护该数据库。
    return result  # 不创建或更新任何来源文件。


def inspect_progress(state, selected, home, found=None):  # 核对现存正文，不将历史残留归属误称为恢复成功。
    found = found or databases(home)  # 结构检查可被同一次预览复用。
    accounts = discover(state)  # 只查询已经选择的账户。
    memberships = {}  # 汇总本地归属，避免重复统计同一会话。
    for key in selected:  # 未选择账户的聊天不参与这个统计。
        memberships.update({thread: record for thread, record in accounts[key].globals.get("thread-project-assignments", {}).items() if record.get("projectKind") == "local"})  # 不统计云端和远程归属。
    with contextlib.closing(connect_readonly(found["会话元数据"])) as database:  # 只读核对元数据和内部项目。
        rows = {row[0]: row[1] for row in database.execute("SELECT id,rollout_path FROM threads")}  # 不读取用户聊天正文。
        projects = {row[0] for row in database.execute("SELECT id FROM projects")}  # 内部标识必须继续有效。
    content = set()  # 记录确实存在分页正文的会话标识。
    with contextlib.closing(connect_readonly(found["分页历史"])) as database:  # 分批查询兼容 SQLite 参数数量限制。
        keys = list(memberships)  # 本地会话标识只存在内存中。
        for offset in range(0, len(keys), 400):  # 大量项目归属也能稳定检查。
            batch = keys[offset:offset + 400]  # 每批最多使用 400 个参数。
            marks = ",".join("?" for _ in batch)  # 参数绑定避免把用户数据拼进 SQL。
            content.update(row[0] for row in database.execute(f"SELECT DISTINCT thread_id FROM thread_items WHERE thread_id IN ({marks})", batch))  # 只返回会话标识。
    missing_metadata = sum(key not in rows for key in memberships)  # 残留记录保留而不删除。
    missing_content = sum(key in rows and key not in content and not Path(rows[key]).is_file() for key in memberships)  # 同时兼容尚存的旧 JSONL 正文。
    for key in selected:  # 检查每个所选账户已有的本机内部映射。
        globals_ = accounts[key].globals  # 不触碰另一数据目录的映射。
        for project, internal in globals_.get(ID_MAP_KEY, {}).get(f"local:{home}", {}).items():  # 只验证本机路径对应的映射。
            if project in globals_.get("local-projects", {}) and internal not in projects:  # 失效映射不可通过新建重复项目掩盖。
                raise SyncError("已有内部项目映射失效，请先核查，未自动创建重复项目。")  # 停止真实写入。
    if missing_content:  # 元数据还在但正文无法核对时，需要先检查原始数据。
        raise SyncError(f"有 {missing_content} 条现存关联会话未找到历史内容，已停止写入。")  # 分类修复不能冒充正文恢复。
    return {"local_threads": len(rows), "assigned_records": len(memberships), "assigned_with_content": len(memberships) - missing_metadata - missing_content, "missing_metadata_records": missing_metadata}  # 显示可核实的数量和限制。


def backup_size(found):  # 在确认界面提前显示首次备份的空间需求。
    return sum(path.stat().st_size + (Path(str(path) + "-wal").stat().st_size if Path(str(path) + "-wal").exists() else 0) for path in found.values())  # WAL 也纳入保守估算。


@contextlib.contextmanager  # 锁会在退出或异常时自动释放。
def operation_lock(store):  # 同一数据目录只允许一个本工具实例写入。
    import msvcrt  # Windows 标准库锁，无第三方依赖。
    store.mkdir(parents=True, exist_ok=True)  # 只创建使用者自己的工具数据目录。
    with (store / "operation.lock").open("a+b") as stream:  # 不锁住或修改 Codex 的正文文件。
        stream.seek(0)  # 固定使用第一个字节。
        if not stream.read(1):  # 初始化空锁文件。
            stream.write(b"0")  # 写入无业务含义的锁字节。
            stream.flush()  # 刷新后其他实例可以看到它。
        stream.seek(0)  # 准备加锁。
        try:  # 不阻塞第二个窗口。
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # 进程异常退出后由系统释放。
        except OSError as error:  # 已有实例正在操作。
            raise SyncError("另一个同步或恢复窗口正在操作，请等待它完成。") from error  # 不删除另一个实例的锁。
        try:  # 在锁保护内执行提交。
            yield  # 把控制权交给同步或恢复函数。
        finally:  # 所有路径都释放工具锁。
            stream.seek(0)  # 定位锁定字节。
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # 解锁后关闭文件。


def _backup_path(store, value):  # 只接受本资料目录下的真实备份文件夹。
    folder = Path(value).resolve()  # 将恢复路径规范化。
    if not folder.is_relative_to((store / "backups").resolve()):  # 防止把其他目录当成回退来源。
        raise SyncError("恢复来源不属于本数据目录的备份。")  # 不跨用户或跨 Codex 目录恢复。
    return folder  # 已通过恢复边界检查。


def _backup_configuration(folder):  # 校验并读取用于精确恢复的原始配置字节。
    manifest = read_json(folder / "manifest.json")  # 仅完成的备份允许使用。
    if not manifest.get("complete"):  # 中途失败的目录不能被当作完整备份。
        raise SyncError("备份未完成，无法恢复。")  # 原配置不受影响。
    result = {}  # 两个配置文件分别保存原始存在状态。
    for name in ("main.json", "bak.json"):  # 主配置与原生备用文件不是同一个历史版本。
        item = manifest["configuration"][name]  # 读取原先是否存在以及 SHA-256。
        data = (folder / name).read_bytes() if item["existed"] else None  # 不为缺失备用文件伪造内容。
        if data is not None and digest_bytes(data) != item["sha256"]:  # 损坏备份不能恢复。
            raise SyncError("配置备份 SHA-256 不匹配，已停止。")  # 不把损坏内容写入原配置。
        result[name] = data  # 返回已经核对的完整字节。
    return result  # 提交错误时也使用这份精确回退数据。


def _first_backup_valid(store, found):  # 首次数据库保护不可因为标记存在就无条件跳过。
    marker = store / "first-database-backup.json"  # 标记只由完整备份生成。
    if not marker.exists():  # 这是首次实际同步。
        return False  # 后续必须执行数据库备份。
    folder = _backup_path(store, read_json(marker)["directory"])  # 校验标记指向自己的目录。
    manifest = read_json(folder / "manifest.json")  # 查询已完成文件清单。
    if not manifest.get("complete") or {item["source"] for item in manifest.get("databases", [])} != {str(path) for path in found.values()}:  # 来源数据库版本变化需要重新首次备份。
        return False  # 不把旧结构的备份用作当前结构的保护。
    for item in manifest["databases"]:  # 每次至少核对文件存在及长度。
        path = (folder / item["file"]).resolve()  # 解析后再确认备份文件没有越出目录。
        if not path.is_relative_to(folder) or not path.is_file() or path.stat().st_size != item["size"]:  # 丢失备份不能静默略过。
            raise SyncError("首次数据库备份缺失或大小变化，请先核查备份位置。")  # 不继续改写配置。
    return True  # 常规同步只需新备份配置。


def make_backup(home, store, found, first, progress):  # 所有实际修改前建立独立回退点。
    needed = backup_size(found) if first else 0  # 大数据库只在首次或来源改变时备份。
    store.mkdir(parents=True, exist_ok=True)  # 备份放在本机工具资料目录。
    if shutil.disk_usage(store).free < needed + 256 * 1024 * 1024:  # 在开始前保留额外写入空间。
        raise SyncError("备份磁盘空间不足，未修改配置。")  # 不省略备份来追求成功提示。
    folder = store / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # 每次使用新的时间戳目录。
    folder.mkdir(parents=True, exist_ok=False)  # 不覆盖历史备份。
    manifest = {"schema_version": 1, "complete": False, "configuration": {}, "databases": []}  # 未完成目录不会成为恢复来源。
    state_path = home / ".codex-global-state.json"  # 仅这份分类配置和备用文件会被改写。
    for path, name in ((state_path, "main.json"), (Path(str(state_path) + ".bak"), "bak.json")):  # 主配置和备用配置逐个保护。
        data = path.read_bytes() if path.exists() else None  # 保留同步前的精确字节。
        manifest["configuration"][name] = {"existed": data is not None, "sha256": digest_bytes(data) if data is not None else None}  # 记录原始存在状态和摘要。
        if data is not None:  # 只有原本存在的文件才产生内容备份。
            (folder / name).write_bytes(data)  # 不重新序列化同步前的原文件。
    if first:  # 首次实际提交前保护全部已识别数据库。
        for role, path in found.items():  # 逐个数据库备份并显示实际进度。
            require_closed()  # 备份过程中重新打开 Codex 会阻止后续提交。
            progress(f"首次备份：{role}（{path.name}），请等待…")  # 向界面报告当前工作。
            relative = path.resolve().relative_to(home.resolve())  # 先统一 Windows 长路径和 8.3 短路径，再确认数据库位于所选目录内。
            destination = folder / "databases" / relative  # 数据库副本只写入备份目录。
            destination.parent.mkdir(parents=True, exist_ok=True)  # 创建必要的内部目录。
            with contextlib.closing(connect_readonly(path)) as source:  # 来源始终只读。
                with contextlib.closing(sqlite3.connect(destination)) as target:  # 只允许备份文件可写。
                    source.backup(target, pages=2048)  # 包含 WAL 中已经提交的事务。
                    if target.execute("PRAGMA quick_check").fetchone() != ("ok",):  # 副本需要通过完整性检查。
                        raise SyncError("数据库备份完整性检查失败，未修改配置。")  # 不提交分类修改。
            manifest["databases"].append({"source": str(path), "file": str(destination.relative_to(folder)), "size": destination.stat().st_size, "sha256": file_digest(destination)})  # 为副本保存校验信息。
    manifest["complete"] = True  # 所有要求的备份均已完成。
    atomic_write(folder / "manifest.json", json_bytes(manifest))  # 完整清单最后原子写入。
    if first:  # 完整副本保存后才记为首次数据库备份完成。
        atomic_write(store / "first-database-backup.json", json_bytes({"directory": str(folder)}))  # 不在未知状态提前打完成标记。
    return folder  # 将精确回退点交给后续提交。


def commit(home, data, bak_data, expected_hash, rollback):  # 同步和恢复共用一次受保护的双文件提交。
    require_closed()  # 最后提交前再次检查应用已退出。
    path = home / ".codex-global-state.json"  # 写入明确的数据目录。
    previous = _backup_configuration(rollback)  # 提交前确认精确回退内容可用。
    current_bak = Path(str(path) + ".bak").read_bytes() if Path(str(path) + ".bak").exists() else None  # 也检测备用文件是否被其他写入者改变。
    if digest_bytes(path.read_bytes()) != expected_hash or current_bak != previous["bak.json"]:  # 用户预览后或备份过程中有变化就失效。
        raise SyncError("配置已经变化，请重新扫描、预览并确认；本次未覆盖。")  # 不覆盖更新的配置。
    try:  # 失败时按本次备份精确回退。
        atomic_write(path, data)  # 原子更新完整主配置。
        if bak_data is None:  # 恢复原来没有备用文件的状态。
            Path(str(path) + ".bak").unlink(missing_ok=True)  # 仅删除明确的原生备用文件。
        else:  # 正常同步同时更新原生备用文件。
            atomic_write(Path(str(path) + ".bak"), bak_data)  # 备用文件也原子替换。
    except Exception as error:  # 文件系统错误不会被当成成功。
        try:  # 在应用仍退出的前提下自动回退。
            require_closed()  # 不与已经启动的 Codex 争写。
            atomic_write(path, previous["main.json"])  # 恢复主文件原始字节。
            if previous["bak.json"] is None:  # 恢复备用文件原始存在状态。
                Path(str(path) + ".bak").unlink(missing_ok=True)  # 不留下新增备用文件。
            else:  # 原备用文件存在时还原它。
                atomic_write(Path(str(path) + ".bak"), previous["bak.json"])  # 使用已验证的完整备份。
        except Exception as rollback_error:  # 无法回退时保留清楚的恢复来源。
            raise SyncError(f"提交失败且自动回退未完成。请保留备份 {rollback}：{rollback_error}") from error  # 不掩盖部分提交状态。
        raise SyncError("提交失败，已经恢复本次操作前的配置。") from error  # 清楚报告回退完成。
    if path.read_bytes() != data or (bak_data is not None and Path(str(path) + ".bak").read_bytes() != bak_data):  # 提交后核对真实文件字节。
        raise SyncError(f"提交后文件发生变化，无法确认结果；备份：{rollback}")  # 不声称已通过验收。


def synchronize(plan, home, store, confirmed=False, progress=lambda text: None):  # GUI 明确确认后才调用的写入接口。
    if not confirmed or plan.unresolved:  # 未确认或有未解决冲突时完全不进入文件操作。
        raise SyncError("请先解决冲突并确认账户、项目数量，再执行同步。")  # 防止直接调用绕过预览。
    require_closed()  # 运行中应用在任何配置备份之前被阻止。
    with operation_lock(store):  # 防止两个工具实例同时提交。
        path = home / ".codex-global-state.json"  # 当前选定的数据目录。
        raw = path.read_bytes()  # 获取提交前的完整字节。
        if digest_bytes(raw) != plan.original_hash:  # 检测确认后配置变化。
            raise SyncError("配置已经变化，请重新扫描并确认；本次未覆盖。")  # 不沿用过时选择。
        original = json.loads(raw.decode("utf-8-sig"))  # 只解析本机分类文件。
        found = databases(home)  # 重新验证数据库结构，避免预览后发生升级。
        baseline = inspect_progress(plan.merged, plan.selected, home, found)  # 核对最终所选归属中的现存正文。
        if original == plan.merged:  # 已经一致时无需重复写入。
            return {"changed": False, "summary": plan.summary, "baseline": baseline}  # 不制造新的成功同步回执。
        folder = make_backup(home, store, found, not _first_backup_valid(store, found), progress)  # 备份完成后才允许提交。
        progress("备份已校验，正在提交本地项目分类…")  # 清楚说明当前阶段。
        data = json_bytes(plan.merged)  # 主配置与原生备用配置使用同一完整结果。
        commit(home, data, data, plan.original_hash, folder)  # 真实提交依旧有最后一次进程及变更检查。
        receipt = {"changed": True, "summary": plan.summary, "baseline": baseline, "backup": str(folder), "completed_at": datetime.now().astimezone().isoformat(), "configuration_sha256": digest_bytes(data)}  # 回执不包含聊天正文或令牌。
        try:  # 配置提交完成和回执保存失败必须区分。
            atomic_write(store / "last-sync.json", json_bytes(receipt))  # 只有实际提交核验成功后生成成功回执。
        except OSError as error:  # 此时两个分类配置已经写入。
            raise SyncError(f"配置已同步，但回执保存失败；同步前备份：{folder}") from error  # 不误报为没有改动。
        return receipt  # 界面显示具体结果与备份位置。


def restore(home, store, confirmed=False, progress=lambda text: None):  # 只恢复最近一次本工具同步前的分类配置。
    if not confirmed:  # 用户必须先看见恢复来源并明确确认。
        raise SyncError("请先确认恢复操作。")  # 默认调用不产生修改。
    require_closed()  # 恢复同样不能与 Codex 并发写入。
    with operation_lock(store):  # 防止与同步同时执行。
        receipt = read_json(store / "last-sync.json")  # 没有成功回执时不会猜测恢复来源。
        source = _backup_path(store, receipt["backup"])  # 严格限定为本资料目录的备份。
        previous = _backup_configuration(source)  # 恢复前重新核验 SHA-256。
        path = home / ".codex-global-state.json"  # 聊天正文数据库从未被本工具修改，也不参与普通恢复。
        current_hash = digest_bytes(path.read_bytes())  # 保留恢复前的并发基线。
        folder = make_backup(home, store, {}, False, progress)  # 恢复前也保留当前配置。
        commit(home, previous["main.json"], previous["bak.json"], current_hash, folder)  # 精确恢复两份文件及原存在状态。
        atomic_write(store / "last-restore.json", json_bytes({"restored_from": str(source), "before_restore_backup": str(folder)}))  # 留下可追溯的恢复记录。
        return {"restored_from": str(source), "before_restore_backup": str(folder)}  # 供界面展示完成结果。
