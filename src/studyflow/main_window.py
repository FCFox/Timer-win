from __future__ import annotations

import ctypes
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

from studyflow.domain import ActivityState, Snapshot
from studyflow.infrastructure import Database, set_autostart


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    return f"{hours:02d}:{remainder // 60:02d}:{remainder % 60:02d}"


class StudyFlowWindow:
    """A compact, bottom-right desktop timer with a single-row toolbar."""

    WIDTH = 430
    HEIGHT = 150

    def __init__(self, root: tk.Tk, database: Database, service):
        self.root, self.database, self.service = root, database, service
        self._closing = False
        self._after_id: str | None = None
        self._constraining_position = False
        self._shutdown_hook = None
        self._original_wndproc = None
        root.title("StudyFlow")
        root.resizable(False, False)
        root.configure(bg="white")
        root.protocol("WM_DELETE_WINDOW", self.handle_close)
        root.bind("<Configure>", self._on_configure, add="+")
        self._build_menu()

        values = tk.Frame(root, bg="white")
        values.pack(fill="both", expand=True, padx=16, pady=(14, 10))
        work = tk.Frame(values, bg="white")
        work.pack(side="left", fill="both", expand=True)
        idle = tk.Frame(values, bg="white")
        idle.pack(side="left", fill="both", expand=True)
        work_content = tk.Frame(work, bg="white")
        work_content.pack(expand=True)
        idle_content = tk.Frame(idle, bg="white")
        idle_content.pack(expand=True)
        tk.Label(work_content, text="工作时间", bg="white", fg="black",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="center")
        self.work_value = tk.Label(work_content, text="00:00:00", bg="white", fg="black",
                                   font=("Consolas", 23, "bold"))
        self.work_value.pack(anchor="center", pady=(2, 0))
        tk.Label(idle_content, text="空闲时间", bg="white", fg="black",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="center")
        self.idle_value = tk.Label(idle_content, text="00:00:00", bg="white", fg="black",
                                   font=("Consolas", 23, "bold"))
        self.idle_value.pack(anchor="center", pady=(2, 0))

        service.on_state(self.set_state)
        service.on_snapshot(self.set_snapshot)
        self.set_state(service.state)
        service.refresh()
        self._place_bottom_right()
        self._install_shutdown_handler()
        self._schedule_tick()

    def _install_shutdown_handler(self) -> None:
        """Listen for Windows shutdown independently of the close button."""
        if not hasattr(ctypes, "windll"):
            return
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id())
        if not hwnd:
            return
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        )
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t

        def wndproc(window, message, wparam, lparam):
            result = self._handle_shutdown_message(message, wparam)
            if result is not None:
                return result
            return user32.CallWindowProcW(
                self._original_wndproc, window, message, wparam, lparam
            )

        self._shutdown_hook = wndproc_type(wndproc)
        self._original_wndproc = user32.SetWindowLongPtrW(
            hwnd, -4, ctypes.cast(self._shutdown_hook, ctypes.c_void_p)
        )

    def _handle_shutdown_message(self, message: int, wparam: int) -> int | None:
        if message == 0x0011:  # WM_QUERYENDSESSION
            self.service.save_checkpoint()
            return 1
        if message == 0x0016 and wparam:  # WM_ENDSESSION
            self.stop_scheduler()
            self.service.stop()
            return 0
        return None

    def _build_menu(self) -> None:
        self.menu_bar = tk.Menu(self.root, tearoff=False)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="统计", command=self.show_statistics)
        self.file_menu.add_command(label="设置", command=self.show_settings)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="退出", command=self.quit)
        self.menu_bar.add_cascade(label="文件", menu=self.file_menu)
        self.menu_bar.add_command(label="重置时间", command=self.reset_time)
        self.menu_bar.add_command(label="暂停", command=self.service.toggle_pause)
        self.root.configure(menu=self.menu_bar)

    def _place_bottom_right(self) -> None:
        self.root.update_idletasks()
        left, top, right, bottom = self._work_area()
        outer_width, outer_height = self._outer_window_size()
        x = max(left, right - outer_width - 8)
        y = max(top, bottom - outer_height - 8)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _outer_window_size(self) -> tuple[int, int]:
        """Return the full native window size, including title/menu/borders."""
        if hasattr(ctypes, "windll"):
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                ]
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            rect = RECT()
            if hwnd and ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                width, height = rect.right - rect.left, rect.bottom - rect.top
                if width > 0 and height > 0:
                    return width, height
        border_x = max(0, self.root.winfo_rootx() - self.root.winfo_x())
        title_height = max(0, self.root.winfo_rooty() - self.root.winfo_y())
        return self.root.winfo_width() + border_x * 2, self.root.winfo_height() + title_height + border_x

    def _work_area(self) -> tuple[int, int, int, int]:
        """Return the Windows desktop work area, excluding the taskbar."""
        if hasattr(ctypes, "windll"):
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                ]
            rect = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _on_configure(self, _event=None) -> None:
        if self._constraining_position or self.root.state() != "normal":
            return
        self._constrain_to_work_area()

    def _constrain_to_work_area(self) -> None:
        self.root.update_idletasks()
        left, top, right, bottom = self._work_area()
        width, height = self._outer_window_size()
        x = min(max(self.root.winfo_x(), left), max(left, right - width))
        y = min(max(self.root.winfo_y(), top), max(top, bottom - height))
        if (x, y) != (self.root.winfo_x(), self.root.winfo_y()):
            self._constraining_position = True
            try:
                self.root.geometry(f"+{x}+{y}")
                self.root.update_idletasks()
            finally:
                self._constraining_position = False

    def set_state(self, state: ActivityState) -> None:
        self.menu_bar.entryconfigure(2, label="恢复" if state is ActivityState.PAUSED else "暂停")

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self.work_value.configure(text=format_duration(snapshot.working_seconds))
        self.idle_value.configure(text=format_duration(snapshot.idle_seconds))

    def show_statistics(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("每日统计")
        dialog.geometry("520x430")
        dialog.configure(bg="white")
        columns = ("date", "working", "idle", "ratio")
        table = ttk.Treeview(dialog, columns=columns, show="headings", height=16)
        table.heading("date", text="日期")
        table.heading("working", text="工作时间")
        table.heading("idle", text="空闲时间")
        table.heading("ratio", text="工作占比")
        table.column("date", width=110, anchor="center")
        table.column("working", width=125, anchor="center")
        table.column("idle", width=125, anchor="center")
        table.column("ratio", width=90, anchor="center")
        table.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        clear_button = tk.Button(
            dialog, text="清空记录", bg="white", fg="black", relief="solid", bd=1,
            command=lambda: self.clear_statistics(table), padx=16, pady=5,
        )
        clear_button.pack(pady=(0, 12))
        self._populate_statistics(table)

    def _populate_statistics(self, table: ttk.Treeview) -> None:
        for item in table.get_children():
            table.delete(item)
        goal = int(self.database.get_setting("daily_goal_seconds"))
        for offset in range(30):
            day = date.today() - timedelta(days=offset)
            snapshot = self.database.snapshot(day, goal)
            if offset == 0 or snapshot.tracked_seconds or snapshot.paused_seconds:
                table.insert("", "end", values=(
                    day.isoformat(), format_duration(snapshot.working_seconds),
                    format_duration(snapshot.idle_seconds), f"{snapshot.work_ratio:.0%}",
                ))

    def clear_statistics(self, table: ttk.Treeview) -> None:
        if messagebox.askyesno(
            "清空统计记录", "确定永久删除全部统计记录吗？此操作无法撤销。",
            parent=table.winfo_toplevel(),
        ):
            self.service.clear_and_restart()
            self._populate_statistics(table)

    def show_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.resizable(False, False)
        dialog.configure(bg="white")
        dialog.transient(self.root)
        frame = tk.Frame(dialog, bg="white", padx=18, pady=16)
        frame.pack(fill="both", expand=True)
        threshold = tk.IntVar(value=int(self.database.get_setting("idle_threshold_seconds")))
        goal = tk.IntVar(value=int(self.database.get_setting("daily_goal_seconds")) // 3600)
        autostart = tk.BooleanVar(value=bool(self.database.get_setting("launch_at_login")))
        tk.Label(frame, text="空闲阈值（秒）", bg="white", fg="black").grid(row=0, column=0, sticky="w", pady=7)
        tk.Spinbox(frame, from_=0, to=3600, textvariable=threshold, width=8).grid(row=0, column=1, padx=12)
        tk.Label(frame, text="每日目标（小时）", bg="white", fg="black").grid(row=1, column=0, sticky="w", pady=7)
        tk.Spinbox(frame, from_=1, to=16, textvariable=goal, width=8).grid(row=1, column=1, padx=12)
        tk.Checkbutton(frame, text="开机启动", variable=autostart, bg="white", fg="black",
                       activebackground="white").grid(row=2, column=0, sticky="w", pady=7)

        def save() -> None:
            self.database.set_setting("idle_threshold_seconds", max(0, threshold.get()))
            self.database.set_setting("daily_goal_seconds", goal.get() * 3600)
            self.database.set_setting("launch_at_login", autostart.get())
            try:
                set_autostart(autostart.get())
            except OSError as exc:
                messagebox.showwarning("开机启动", f"无法更新开机启动：{exc}", parent=dialog)
            dialog.destroy()

        tk.Button(frame, text="保存", command=save, bg="white", fg="black",
                  relief="solid", bd=1, padx=16).grid(row=3, column=1, pady=(12, 0))

    def clear_data(self) -> None:
        if messagebox.askyesno("确认清空", "确定永久删除所有计时记录吗？", parent=self.root):
            self.service.clear_and_restart()

    def reset_time(self) -> None:
        if messagebox.askyesno(
            "重置时间", "确定将当前工作时间和空闲时间归零并重新开始吗？\n每日统计不会被删除。",
            parent=self.root,
        ):
            self.service.reset_session()

    def _schedule_tick(self) -> None:
        if not self._closing:
            self.service.tick()
            self._after_id = self.root.after(1000, self._schedule_tick)

    def stop_scheduler(self) -> None:
        self._closing = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def hide(self) -> None:
        self.root.withdraw()

    def handle_close(self) -> None:
        choice = messagebox.askyesnocancel(
            "关闭 StudyFlow",
            "是否退出程序？\n\n选择“是”：退出并停止计时\n选择“否”：隐藏到右下角托盘并继续计时",
            parent=self.root,
        )
        if choice is True:
            self.quit()
        elif choice is False:
            self.hide()

    def show(self) -> None:
        self.root.deiconify()
        self._place_bottom_right()
        self.root.lift()

    def quit(self) -> None:
        self.stop_scheduler()
        self.service.stop()
        self.root.destroy()
