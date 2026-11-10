"""全部数据均为合成测试数据，不包含开发者或使用者的真实配置。"""

import hashlib  # 生成不可与真实账户混淆的确定性测试摘要。
import json  # 创建受控测试正文。
from pathlib import Path  # 所有测试数据库都写入临时目录。
import sqlite3  # 构造有明确表结构的 SQLite 数据库。
import uuid  # 为合成会话创建格式完整的 UUID。
from codex_project_sync import legacy_core as core  # 与实际支持的字段名称保持一致。


def owner(index):  # 账户编号只用于测试。
    return hashlib.sha256(f"synthetic-account-{index}".encode()).hexdigest()  # 不复制真实账户摘要。


def thread(index):  # 用确定性 UUID 便于检查归属变化。
    return str(uuid.UUID(int=index + 1))  # 这是合成会话，不对应任何真实聊天。


def project(key, name=None):  # 项目必须具有完整名称和明确 Windows 路径。
    return {"id": key, "name": name or f"合成项目 {key}", "rootPaths": [f"C:\\SyntheticData\\{key}"], "createdAt": 1, "updatedAt": 1}  # 文件夹仅用于字符串测试，不会实际访问或创建。


def state(home, count=4):  # 支持任意数量测试账户。
    snapshots = {}  # 每个账户拥有独立的项目和私有偏好。
    for index in range(count):  # 不把账户数固定为两个。
        key = f"p{index}"  # 每个测试账户有一个独有项目。
        globals_ = {"local-projects": {key: project(key)}, "project-order": [key], "thread-project-assignments": {thread(index): {"projectKind": "local", "projectId": key}}, core.ID_MAP_KEY: {f"local:{home}": {key: f"server-{key}"}}, "selected-project": {"type": "local", "projectId": key}, "queued-follow-ups": {"private": index}}  # 私有字段不得传播。
        atoms = {core.UNIFIED_ORDER_KEY: [f"chatgpt:project:synthetic-cloud-{index}", f"codex:project:{key}"], "private-preference": {"account": index}}  # 云端条目是合成的账户专属数据。
        snapshots[owner(index)] = {"globals": globals_, "atoms": atoms}  # 保存符合实际结构的账户分区。
    active = snapshots.pop(owner(0))  # 测试账户零作为当前账户。
    result = active["globals"]  # 当前账户 globals 位于真实顶层。
    result[core.ATOM_KEY] = active["atoms"]  # 当前账户偏好也位于顶层。
    result[core.ACCOUNT_KEY] = {"owner": owner(0), "inactive": snapshots}  # 其他账户保存为 inactive。
    result["unrelated-top-level"] = {"retain": [1, 2, 3]}  # 用于验证其他顶层字段不变。
    return result  # 每次调用都会构造全新独立对象。


def add_databases(home, state_value, wal=False):  # 为端到端副本测试准备真实 SQLite 文件。
    home = Path(home)  # 只使用测试临时目录。
    home.mkdir(parents=True, exist_ok=True)  # 创建副本资料目录。
    snapshots = [state_value] + [item["globals"] for item in state_value[core.ACCOUNT_KEY]["inactive"].values()]  # 合成元数据涵盖全部测试账户。
    projects = {key for snapshot in snapshots for key in snapshot.get("local-projects", {})}  # 保留原生项目标识。
    threads = {key for snapshot in snapshots for key in snapshot.get("thread-project-assignments", {})}  # 每条合成归属都有正文。
    connections = []  # WAL 测试需要暂时保持来源连接。
    for relative in ("state_99.sqlite", "thread_history_77.sqlite", "sqlite/codex.db"):  # 版本数字特意不同于开发者电脑，验证按结构识别。
        path = home / relative  # 不接触真实 Codex 数据目录。
        path.parent.mkdir(parents=True, exist_ok=True)  # 创建目录数据库所在子目录。
        database = sqlite3.connect(path)  # 只向合成测试来源写入准备数据。
        if wal:  # 可选构造 WAL 中已有提交的情况。
            database.execute("PRAGMA journal_mode=WAL")  # 正式备份必须保护这部分内容。
        if relative.startswith("state_"):  # 元数据数据库。
            database.execute("CREATE TABLE threads (id TEXT PRIMARY KEY,rollout_path TEXT,history_mode TEXT)")  # 字段与受支持结构一致。
            database.executemany("INSERT INTO threads VALUES (?,?,'paginated')", [(key, str(home / "not-a-real-rollout.jsonl")) for key in threads])  # 正文保存在分页数据库。
            database.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")  # 内部项目映射验证所需表。
            database.executemany("INSERT INTO projects VALUES (?)", [(f"server-{key}",) for key in projects])  # 沿用合成内部项目标识。
        elif relative.startswith("thread_history_"):  # 分页正文数据库。
            database.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY)")  # 建立可核验的投影索引。
            database.executemany("INSERT INTO thread_history_projection_state VALUES (?)", [(key,) for key in threads])  # 为每条合成会话建立索引。
            database.execute("CREATE TABLE thread_items (thread_id TEXT,item_json TEXT)")  # 真实提交接口只读核验正文存在性。
            database.executemany("INSERT INTO thread_items VALUES (?,?)", [(key, json.dumps({"synthetic_saved_progress": key})) for key in threads])  # 合成正文明确标为测试资料。
        else:  # 侧栏目录数据库。
            database.execute("CREATE TABLE local_thread_catalog (host_id TEXT,thread_id TEXT)")  # 结构识别所需字段。
            database.executemany("INSERT INTO local_thread_catalog VALUES ('local',?)", [(key,) for key in threads])  # 保存合成目录条目。
        database.commit()  # 备份应包含已提交的数据。
        if wal:  # 保持 WAL 测试场景直至测试完成。
            connections.append(database)  # 由调用者统一关闭。
        else:  # 普通测试关闭连接后再计算来源摘要。
            database.close()  # 不保留多余写入者。
    return connections  # WAL 测试可在 finally 中关闭这些连接。
