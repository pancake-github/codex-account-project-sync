"""Windows 双击入口：账户选择、数量预览、冲突确认及手动恢复。"""

import argparse  # 支持显式指定自定义 Codex 数据目录。
import json  # 展示本地预览详情。
from pathlib import Path  # 统一路径处理。
import queue  # 后台备份线程通过队列向界面报告进度。
import threading  # 大数据库备份期间保持窗口可响应。
import tkinter as tk  # Python 标准图形界面，无需额外界面依赖。
from tkinter import filedialog, messagebox, simpledialog, ttk  # 提供目录选择、明确确认和标准控件。
from . import __version__  # 显示实际发布版本。
from .engine import discover, preview  # 只读发现和内存合并预演。
from .files import atomic_write, json_bytes, read_json, running_codex_processes  # 不读取任何登录凭据。
from .legacy_core import SyncError  # 向用户展示明确的停止原因。
from . import storage  # 实际写入只从经过确认的操作进入。


class App:
    """只在用户主动运行时存在的本地图形工具。"""

    def __init__(self, root, home=None, smoke=False):  # 无参数启动图形界面。
        self.root = root  # 保存 Tk 主窗口。
        self.smoke = smoke  # 构建验证模式不会扫描真实账户。
        self.events = queue.Queue()  # 工作线程不直接访问 Tk 控件。
        self.busy = False  # 长任务期间禁止关闭或重复提交。
        self.accounts = {}  # 只保存当前扫描到的本机账户。
        self.aliases = {}  # 用户自定义别名仅存本机。
        self.targets = set()  # 默认不自动勾选任何目标账户。
        self.resolutions = {}  # 保存用户对具体冲突的选择。
        self.plan = None  # 没有有效预览就不能同步。
        self.labels = {}  # 界面显示名与不可逆账户摘要对应。
        self.home_var = tk.StringVar(value=str(home or storage.default_home()))  # 自动适配当前用户目录。
        self.source_var = tk.StringVar()  # 源账户由用户看见并确认。
        self.status = tk.StringVar(value="先检查账户，再选择源账户和目标账户；退出 Codex 后才可同步。")  # 不把配置同步写成聊天内容恢复。
        root.title(f"Codex 账户项目同步 · {__version__}")  # 显示非官方独立工具名称和 Beta 版本。
        root.geometry("1080x800")  # 适合项目和数量表格的默认大小。
        root.minsize(880, 650)  # 避免关键确认按钮被压缩隐藏。
        root.protocol("WM_DELETE_WINDOW", self.close)  # 备份或提交期间不能误关窗口。
        style = ttk.Style(root)  # 使用系统可用字体与标准控件。
        style.configure("Treeview", rowheight=28)  # 给中文账户行保留足够高度。
        outer = ttk.Frame(root, padding=14)  # 为所有界面元素保留留白。
        outer.pack(fill="both", expand=True)  # 窗口变化时内容可以扩展。
        ttk.Label(outer, text="Codex 切换账户后项目消失、分类丢失", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")  # 直接说明要解决的问题。
        ttk.Label(outer, text="仅整理仍保存在本机的项目分类与会话归属；不同账户不会自动全选。").pack(anchor="w", pady=(4, 12))  # 说明真实作用范围。
        directory = ttk.Frame(outer)  # 自定义数据目录入口。
        directory.pack(fill="x")  # 目录输入框使用可用横向空间。
        ttk.Label(directory, text="Codex 数据目录：").pack(side="left")  # 避免用户把仓库目录当作 Codex 数据目录。
        self.home_entry = ttk.Entry(directory, textvariable=self.home_var)  # 显示实际选中的路径。
        self.home_entry.pack(side="left", fill="x", expand=True)  # 支持长中文路径。
        self.browse_button = ttk.Button(directory, text="选择目录", command=self.browse)  # 支持自定义 CODEX_HOME。
        self.browse_button.pack(side="left", padx=6)  # 保留与检查按钮之间的间隔。
        self.scan_button = ttk.Button(directory, text="检查账户", command=self.scan)  # 扫描本身不修改 Codex 配置。
        self.scan_button.pack(side="left")  # 放在目录选择之后。
        self.tree = ttk.Treeview(outer, columns=("alias", "current", "projects", "records", "target"), show="headings", height=7)  # 同时显示账户及实际数量。
        for key, text, width in (("alias", "账户别名 / 本机短标识", 350), ("current", "当前账户", 85), ("projects", "本地项目数", 100), ("records", "本地归属记录数", 130), ("target", "选为目标", 90)):  # 所有数量均来自当前扫描。
            self.tree.heading(key, text=text)  # 设置中文列名。
            self.tree.column(key, width=width, anchor="w" if key == "alias" else "center")  # 区分名称与数量的对齐方式。
        self.tree.pack(fill="x", pady=12)  # 多账户列表占据固定且可读区域。
        self.tree.bind("<Double-1>", lambda event: self.toggle_target())  # 双击某行切换目标勾选。
        selection = ttk.Frame(outer)  # 将源账户与目标操作放在同一行。
        selection.pack(fill="x")  # 保持界面简洁。
        ttk.Label(selection, text="源账户：").pack(side="left")  # 明确冲突默认优先的来源。
        self.source_box = ttk.Combobox(selection, textvariable=self.source_var, state="readonly", width=35)  # 只能选择已发现账户。
        self.source_box.pack(side="left")  # 便于与上方账户表格核对。
        self.source_box.bind("<<ComboboxSelected>>", self.source_changed)  # 源变化后预览立即失效。
        self.target_button = ttk.Button(selection, text="勾选 / 取消目标", command=self.toggle_target)  # 不依赖双击操作也可使用。
        self.target_button.pack(side="left", padx=8)  # 与源选择区隔开。
        self.alias_button = ttk.Button(selection, text="修改所选账户别名", command=self.rename)  # 不冒充自动识别真实姓名或邮箱。
        self.alias_button.pack(side="left")  # 别名只帮助本机使用者辨认。
        controls = ttk.Frame(outer)  # 合并及恢复的主要操作入口。
        controls.pack(fill="x", pady=12)  # 与账户选择区分开。
        self.preview_button = ttk.Button(controls, text="生成合并预览", command=self.make_preview)  # 数量与变更在确认前可查看。
        self.preview_button.pack(side="left")  # 按预览、处理、确认的顺序排列。
        self.conflict_button = ttk.Button(controls, text="查看 / 处理冲突", command=self.conflicts, state="disabled")  # 没有预览时不可处理冲突。
        self.conflict_button.pack(side="left", padx=8)  # 避免误点到写入按钮。
        self.sync_button = ttk.Button(controls, text="确认账户与数量后同步", command=self.confirm_sync, state="disabled")  # 只有完整预览才能启用。
        self.sync_button.pack(side="left")  # 点击后仍显示明确的最终确认摘要。
        self.restore_button = ttk.Button(controls, text="恢复上次同步前配置", command=self.confirm_restore)  # 恢复同样需要应用退出和确认。
        self.restore_button.pack(side="right")  # 与正常同步操作保持视觉区分。
        self.output = tk.Text(outer, wrap="word", height=17, font=("Microsoft YaHei UI", 10))  # 展示逐账户变化与备份进度。
        self.output.pack(fill="both", expand=True)  # 长项目名称和路径可以换行。
        ttk.Label(outer, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=(10, 0))  # 显示当前状态和实际停止原因。
        self.home_var.trace_add("write", lambda *args: self.invalidate())  # 路径改动必须重新扫描确认。
        root.after(150, self.poll)  # 定期处理线程结果，不让工作线程访问控件。
        if smoke:  # CI 和本机 EXE 启动检查使用不接触真实账户的模式。
            self.log("图形界面启动检查通过；此模式没有扫描或修改任何 Codex 资料。")  # 明确测试模式边界。
            root.after(800, root.destroy)  # 检查完成后自动关闭自己的测试窗口。

    def log(self, message):  # 主线程统一更新结果区域。
        self.output.insert("end", message + "\n")  # 保留当前操作过程以便用户查看。
        self.output.see("end")  # 自动显示最新进度。

    def invalidate(self):  # 任何选择变化都不能沿用旧的写入计划。
        self.plan = None  # 旧预览失效。
        self.sync_button.configure(state="disabled")  # 禁止在无有效预览时提交。
        self.conflict_button.configure(state="disabled")  # 需要重新生成冲突列表。

    def source_key(self):  # 从界面显示名取得实际账户摘要。
        return next((key for key, label in self.labels.items() if label == self.source_var.get()), None)  # 不通过邮箱或令牌推断账户。

    def current_home(self):  # 所有操作都从当前显示的明确目录出发。
        home = Path(self.home_var.get()).expanduser().resolve()  # 支持 Unicode 路径。
        if not self.accounts or home != self.scanned_home:  # 路径变化后不能使用旧账户列表。
            raise SyncError("数据目录已改变，请先重新检查账户。")  # 阻止误将旧选择写入另一资料目录。
        return home  # 该目录与已扫描状态一致。

    def browse(self):  # 用户明确选择自己的 Codex 资料目录。
        value = filedialog.askdirectory(parent=self.root, title="选择包含 .codex-global-state.json 的目录")  # 不扫描其他 Windows 用户目录。
        if value:  # 取消选择时不改变已有路径。
            self.home_var.set(value)  # 新路径会自动使旧预览失效。

    def refresh_table(self):  # 表格数量始终使用当前扫描状态。
        self.tree.delete(*self.tree.get_children())  # 清除上一次扫描的显示行。
        for key, account in self.accounts.items():  # 任意数量账户都可以展示。
            self.tree.insert("", "end", iid=key, values=(self.labels[key], "是" if account.current else "", len(account.globals.get("local-projects", {})), sum(record.get("projectKind") == "local" for record in account.globals.get("thread-project-assignments", {}).values()), "☑" if key in self.targets else "☐"))  # 归属记录不等同于聊天正文数量。
        self.source_box.configure(values=list(self.labels.values()))  # 保持源选择与当前账户列表一致。

    def scan(self):  # 扫描与统计不会写入原生 Codex 配置。
        try:  # 可理解的读取错误在界面上说明。
            home = Path(self.home_var.get()).expanduser().resolve()  # 使用界面显示的资料目录。
            raw = (home / ".codex-global-state.json").read_bytes()  # 只读取分类配置。
            state = json.loads(raw.decode("utf-8-sig"))  # 不读取 auth.json。
            accounts = discover(state)  # 识别本机已经出现的账户分区。
            self.scanned_home, self.raw, self.state, self.accounts = home, raw, state, accounts  # 保留预览输入的精确版本。
            self.store = storage.profile_directory(home)  # 不同资料目录使用不同别名和备份。
            self.aliases = read_json(self.store / "aliases.json") if (self.store / "aliases.json").exists() else {}  # 仅加载使用者此前主动保存的别名。
            self.labels = {key: f"{self.aliases[key]} · {key[:12]}" if self.aliases.get(key) else account.label for key, account in accounts.items()}  # 短标识始终可见，避免同别名混淆。
            self.targets, self.resolutions = set(), {}  # 新扫描不会自动选中旧目标。
            self.source_var.set(next((self.labels[key] for key, account in accounts.items() if account.current), ""))  # 当前账户只作为可见的默认源。
            self.invalidate()  # 用户需要重新查看本次预览。
            self.refresh_table()  # 显示每个账户自己的项目数。
            self.output.delete("1.0", "end")  # 新扫描使用清楚的独立结果区。
            self.log(f"发现 {len(accounts)} 个已登录过的本机账户。备份及别名位置：{self.store}")  # 不把多个本地账户称为多个已识别自然人。
            self.log("请辨认账户，选择一个源账户，并勾选一个或多个目标。源账户也会补入目标独有的项目。")  # 明确合并对源账户同样产生影响。
            self.log("新账户若没有出现在列表中，请先在 Codex 登录一次，再退出并重新检查。")  # 不承诺恢复尚未存在的账户配置。
            self.status.set("Codex 正在运行：可以检查和预览；实际同步前必须正常完全退出。" if running_codex_processes() else "检查完成。选择账户后生成合并预览。")  # 只读检查与真实写入明确区分。
        except Exception as error:  # 损坏或未知配置不会生成空列表伪装成功。
            self.invalidate()  # 移除可能残留的提交计划。
            messagebox.showerror("检查停止", str(error), parent=self.root)  # 当前步骤给出具体原因。

    def source_changed(self, event=None):  # 源账户不可同时作为目标。
        self.targets.discard(self.source_key())  # 避免自己向自己同步。
        self.resolutions = {}  # 源优先规则改变后旧冲突选择需重新核对。
        self.invalidate()  # 旧数量和计划不再有效。
        self.refresh_table()  # 目标勾选状态随之更新。

    def toggle_target(self):  # 用户逐个明确选择目标。
        if self.busy:  # 禁止备份期间通过表格双击改变选择。
            return  # 保持已经确认的界面输入冻结。
        items = self.tree.selection()  # 使用表格中的实际选中行。
        if not items:  # 没有行时不隐式选择任何账户。
            return  # 等待用户选择。
        key = items[0]  # 每次切换一个可见目标。
        if key == self.source_key():  # 源账户已包含在合并范围中。
            messagebox.showinfo("选择目标", "源账户不需要再勾选为目标。", parent=self.root)  # 解释当前操作。
            return  # 不修改目标列表。
        self.targets.symmetric_difference_update({key})  # 切换该账户的明确勾选状态。
        self.resolutions = {}  # 目标集合变化后冲突可能变化。
        self.invalidate()  # 禁止沿用旧预览提交。
        self.refresh_table()  # 显示新的勾选状态。

    def rename(self):  # 别名帮助使用者辨认自己的账户。
        items = self.tree.selection()  # 只修改明确选择的账户。
        if not items:  # 没有选中行时不猜测。
            return  # 用户可以先点击账户行。
        key = items[0]  # 取得稳定的本地账户摘要。
        name = simpledialog.askstring("账户别名", "给这个账户填写一个便于辨认的本机别名：", initialvalue=self.aliases.get(key, ""), parent=self.root)  # 不要求真实姓名或邮箱。
        if name is None:  # 取消时保持原别名。
            return  # 不写入任何工具设置。
        self.aliases[key] = name.strip()[:80]  # 限制过长别名，避免表格被撑坏。
        self.store.mkdir(parents=True, exist_ok=True)  # 设置只写在本机工具资料目录。
        atomic_write(self.store / "aliases.json", json_bytes(self.aliases))  # 不改 Codex 账户设置。
        source = self.source_key()  # 更新显示名之前保存源账户摘要。
        self.labels[key] = f"{self.aliases[key]} · {key[:12]}" if self.aliases[key] else self.accounts[key].label  # 短标识继续保留。
        self.source_var.set(self.labels.get(source, ""))  # 改别名不改变实际源账户选择。
        self.refresh_table()  # 刷新可见名称。

    def make_preview(self):  # 纯合并和只读历史核对都在确认写入之前。
        try:  # 无有效选择时给出具体说明。
            home = self.current_home()  # 路径必须仍与当前扫描一致。
            self.plan = preview(self.state, self.raw, home, self.source_key(), list(self.targets), self.resolutions)  # 生成完整且有摘要绑定的内存候选。
            found = storage.databases(home)  # 识别实际支持的数据库结构。
            baseline = storage.inspect_progress(self.plan.merged, self.plan.selected, home, found)  # 核对真实存在的本地正文。
            self.output.delete("1.0", "end")  # 清晰显示本次合并结果。
            for row in self.plan.rows:  # 每个所选账户都列出前后变化。
                self.log(f"{self.labels[row['account']]}：项目 {row['before_projects']} → {row['after_projects']}；归属记录 {row['before_assignments']} → {row['after_assignments']}；改变归属 {row['changed_assignments']} 条。")  # 不把记录数量误称为恢复对话数量。
                for change in row["projects"]:  # 用户可以核对具体项目。
                    self.log(f"  {change['kind']}项目：{change['name']}")  # 不只显示无法辨认的标识。
            self.log(f"所选账户预计共用 {self.plan.summary['projects']} 个本地项目；冲突 {len(self.plan.conflicts)} 项，其中 {len(self.plan.unresolved)} 项必须选择。")  # 未解决时数量是待确认预览。
            self.log(f"已核对有历史内容的关联会话：{baseline['assigned_with_content']} 条；未找到元数据的历史归属：{baseline['missing_metadata_records']} 条，保留而不宣称已恢复。")  # 清楚说明内容核对边界。
            self.log(f"首次数据库备份空间估算：{storage.backup_size(found) / (1024 ** 3):.2f} GiB。备份位置：{self.store}")  # 确认前让用户了解实际空间需求。
            self.conflict_button.configure(state="normal" if self.plan.conflicts else "disabled")  # 有冲突即可查看完整候选。
            self.sync_button.configure(state="disabled" if self.plan.unresolved else "normal")  # 未解决冲突禁止提交。
            self.status.set("还有冲突必须明确选择。" if self.plan.unresolved else "请核对上方账户和数量；点击同步后还会显示最终确认。")  # 让用户知道当前下一步。
        except Exception as error:  # 未知结构或失效映射同样停止。
            self.invalidate()  # 无效预览不能被提交。
            messagebox.showerror("预览停止", str(error), parent=self.root)  # 向用户显示当前实际问题。

    def conflicts(self):  # 显示每一条候选而不是自动使用哈希排序的目标。
        if not self.plan:  # 没有预览时不能操作。
            return  # 等待用户先生成预览。
        window = tk.Toplevel(self.root)  # 创建独立但归属于当前工具的确认窗口。
        window.title("逐项核对合并冲突")  # 明确该窗口的作用。
        window.geometry("920x620")  # 允许显示多个账户的完整候选记录。
        canvas = tk.Canvas(window)  # 使用可滚动容器容纳任意数量冲突。
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)  # 长列表可逐项浏览。
        canvas.configure(yscrollcommand=scrollbar.set)  # 保持滚动条状态一致。
        scrollbar.pack(side="right", fill="y")  # 滚动条放在容器右侧。
        canvas.pack(side="top", fill="both", expand=True)  # 主体区域随窗口扩展。
        body = ttk.Frame(canvas, padding=10)  # 每个冲突使用独立小区块。
        canvas.create_window((0, 0), window=body, anchor="nw")  # 将表单放入滚动区域。
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))  # 内容改变时更新滚动范围。
        choices = []  # 临时保存此窗口的每个候选选择。
        for item in self.plan.conflicts:  # 默认解决的源冲突也让用户看见。
            frame = ttk.LabelFrame(body, text=f"{item.category} · {item.record}", padding=8)  # 同一记录可供准确核对。
            frame.pack(fill="x", pady=5)  # 冲突之间留白。
            var = tk.StringVar(value=self.labels.get(item.chosen, ""))  # 源缺失的冲突保持空选项。
            ttk.Combobox(frame, textvariable=var, state="readonly", values=[self.labels[key] for key in item.candidates], width=65).pack(anchor="w")  # 只能选择真实候选账户。
            details = "\n".join(f"{self.labels[key]}：{json.dumps(value, ensure_ascii=False)}" for key, value in item.candidates.items())  # 向本机用户完整展示候选内容。
            ttk.Label(frame, text=details, wraplength=820).pack(anchor="w", pady=5)  # 路径和项目名可换行。
            choices.append((item, var))  # 保存下拉框与对应冲突的关系。
        def apply_choices():  # 用户明确保存这个窗口里的选择。
            for item, var in choices:  # 逐条收集候选账户。
                chosen = next((key for key in item.candidates if self.labels[key] == var.get()), None)  # 空选项仍保持未解决。
                if chosen is None:  # 不能把尚未选择的记录当成默认目标。
                    self.resolutions.pop(item.key, None)  # 清除失效的旧选择。
                else:  # 保存用户选择的真实候选。
                    self.resolutions[item.key] = chosen  # 重新预览时应用这一条结果。
            window.destroy()  # 返回主界面核对更新后的数量。
            self.make_preview()  # 使用确认过的选择重新生成计划。
        ttk.Button(window, text="应用选择并更新预览", command=apply_choices).pack(side="bottom", pady=10)  # 处理冲突本身不会提交配置。

    def confirm_sync(self):  # 真正写入之前再显示完整的最终确认摘要。
        if not self.plan or self.plan.unresolved:  # 不允许绕过有效预览。
            return  # 用户必须先生成预览并处理冲突。
        try:  # 应用运行时直接解释停止原因。
            home = self.current_home()  # 路径变化会要求重新扫描。
            storage.require_closed()  # 不自动结束 Codex 或正在执行的工作。
            names = "\n".join(self.labels[key] for key in self.plan.selected)  # 确认框清楚列出所有将受影响的账户。
            text = f"将合并以下 {len(self.plan.selected)} 个账户（源账户也会补入项目）：\n{names}\n\n合并后共 {self.plan.summary['projects']} 个本地项目、{self.plan.summary['assignments']} 条归属记录。\n备份位置：{self.store}\n\n是否已核对账户和数量，并确认备份后同步？"  # 确认内容来自当前计划。
            if messagebox.askyesno("确认账户、数量与同步", text, parent=self.root):  # 只有明确点击确认才进入写入接口。
                plan = self.plan  # 冻结已经确认的内存计划。
                self.start(lambda progress: storage.synchronize(plan, home, self.store, confirmed=True, progress=progress))  # 长备份放到工作线程。
        except Exception as error:  # 保护失败时不开始备份或写入。
            messagebox.showerror("同步停止", str(error), parent=self.root)  # 当前只给出需要解决的问题。

    def confirm_restore(self):  # 恢复只处理最近一次成功同步的分类配置。
        try:  # 没有成功回执时清楚说明。
            home = Path(self.home_var.get()).expanduser().resolve()  # 恢复使用当前明确显示的数据目录。
            store = storage.profile_directory(home)  # 恢复来源不会跨资料目录。
            storage.require_closed()  # 同步和恢复使用相同退出要求。
            receipt = read_json(store / "last-sync.json")  # 展示实际存在的同步前备份。
            if messagebox.askyesno("确认恢复分类配置", f"恢复最近一次同步前的两个配置文件：\n{receipt['backup']}\n\n分类和界面偏好将整体回到备份时刻；聊天正文和工作文件不被覆盖。恢复前会再次备份当前配置。确认恢复？", parent=self.root):  # 用户看见恢复来源和具体影响后才决定。
                self.start(lambda progress: storage.restore(home, store, confirmed=True, progress=progress))  # 不覆盖聊天正文或工作文件。
        except Exception as error:  # 无成功记录或应用运行时都会停止。
            messagebox.showerror("恢复停止", str(error), parent=self.root)  # 不猜测其他备份文件。

    def start(self, action):  # 大型数据库备份异步执行，主线程继续显示进度。
        self.busy = True  # 禁止重复提交和误关窗口。
        for widget in (self.scan_button, self.browse_button, self.home_entry, self.source_box, self.target_button, self.alias_button, self.preview_button, self.conflict_button, self.sync_button, self.restore_button):  # 冻结所有可能改变已确认输入的控件。
            widget.configure(state="disabled")  # 防止执行中修改路径或账户。
        self.status.set("正在备份或提交，请等待；不要启动 Codex。")  # 说明本阶段需要等待。
        def worker():  # 线程内不调用任何 Tk 方法。
            try:  # 完成或停止都会通过队列送回主线程。
                result = action(lambda message: self.events.put(("progress", message)))  # 文件处理只报告文字进度。
                self.events.put(("done", result))  # 发送经过实际核验的结果。
            except Exception as error:  # 保留具体停止原因供用户查看。
                self.events.put(("error", str(error)))  # 不把异常伪装成成功。
        threading.Thread(target=worker, daemon=False).start()  # 非守护线程避免界面退出时静默丢弃提交。

    def poll(self):  # 所有 Tk 更新都在主线程进行。
        try:  # 队列为空时立即返回事件循环。
            while True:  # 一次处理已经到达的所有进度信息。
                kind, value = self.events.get_nowait()  # 不阻塞界面。
                if kind == "progress":  # 备份阶段可以持续显示当前数据库。
                    self.log(value)  # 不输出聊天正文。
                else:  # 完成或错误都恢复可操作状态。
                    self.busy = False  # 允许用户查看结果后关闭。
                    for widget in (self.scan_button, self.browse_button, self.home_entry, self.target_button, self.alias_button, self.preview_button, self.restore_button):  # 只有重新预览后才能再次提交。
                        widget.configure(state="normal")  # 不恢复旧同步按钮。
                    self.source_box.configure(state="readonly")  # 源账户仍只能从实际列表选择。
                    self.invalidate()  # 已提交或失败的计划都不能直接重用。
                    if kind == "error":  # 错误结果明确保留在界面中。
                        self.log("操作停止：" + value)  # 用户可以复制当前结果反馈。
                        self.status.set("操作未完成，请查看上方具体原因。")  # 不笼统声称没有改动或已经成功。
                        messagebox.showerror("操作停止", value, parent=self.root)  # 提醒用户核对备份及停止原因。
                    else:  # 结果已经由提交层核验。
                        self.log(json.dumps(value, ensure_ascii=False, indent=2))  # 展示实际统计和备份路径。
                        self.status.set("本次操作完成。请正常打开 Codex，再核对账户切换后的项目显示。")  # 不提前声称界面验收通过。
        except queue.Empty:  # 当前没有更多消息。
            pass  # 保持正常界面事件循环。
        self.root.after(150, self.poll)  # 继续监听后续进度。

    def close(self):  # 不允许用户在备份或提交过程中误关。
        if self.busy:  # 工作线程仍在执行。
            messagebox.showinfo("请等待", "备份或提交尚未结束，请等待窗口显示完成或停止结果。", parent=self.root)  # 不强行终止工作。
        else:  # 空闲时退出自己的界面，不影响 Codex。
            self.root.destroy()  # 工具不留后台进程。


def main():  # 安装入口和独立 EXE 共用这一入口。
    parser = argparse.ArgumentParser(description="Codex 本地账户项目同步")  # 命令行仅提供路径和无数据启动测试。
    parser.add_argument("--codex-home", type=Path, help="明确选择 Codex 数据目录")  # 路径不再固定到开发者电脑。
    parser.add_argument("--smoke-test", action="store_true", help="只启动图形界面并自动关闭，不扫描真实资料")  # 用于发布前 EXE 启动验收。
    args = parser.parse_args()  # 没有参数时直接进入正常界面。
    root = tk.Tk()  # 使用标准 Tkinter 创建 Windows 窗口。
    App(root, args.codex_home, args.smoke_test)  # 初始化当前用户自己的工具实例。
    root.mainloop()  # 窗口关闭后不保留后台线程。
