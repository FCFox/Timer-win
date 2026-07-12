from __future__ import annotations

import tkinter as tk
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
        root.title("StudyFlow")
        root.resizable(False, False)
        root.configure(bg="white")
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.handle_close)

        self.toolbar = tk.Frame(root, bg="white", height=38)
        self.toolbar.pack(fill="x", padx=8, pady=(7, 2))
        self.pause_button = self._tool_button("暂停", service.toggle_pause)
        self._tool_button("历史", self.show_history)
        self._tool_button("设置", self.show_settings)
        self._tool_button("清空", self.clear_data)
        self._tool_button("隐藏", self.hide)
        self._tool_button("退出", self.quit)

        separator = tk.Frame(root, bg="#dddddd", height=1)
        separator.pack(fill="x", padx=10)
        values = tk.Frame(root, bg="white")
        values.pack(fill="both", expand=True, padx=16, pady=10)
        work = tk.Frame(values, bg="white")
        work.pack(side="left", fill="both", expand=True)
        idle = tk.Frame(values, bg="white")
        idle.pack(side="left", fill="both", expand=True)
        tk.Label(work, text="工作时间", bg="white", fg="black",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        self.work_value = tk.Label(work, text="00:00:00", bg="white", fg="black",
                                   font=("Consolas", 23, "bold"))
        self.work_value.pack(anchor="w", pady=(2, 0))
        tk.Label(idle, text="空闲时间", bg="white", fg="black",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        self.idle_value = tk.Label(idle, text="00:00:00", bg="white", fg="black",
                                   font=("Consolas", 23, "bold"))
        self.idle_value.pack(anchor="w", pady=(2, 0))

        service.on_state(self.set_state)
        service.on_snapshot(self.set_snapshot)
        self.set_state(service.state)
        service.refresh()
        self._place_bottom_right()
        self._schedule_tick()

    def _tool_button(self, text: str, command) -> tk.Button:
        button = tk.Button(
            self.toolbar, text=text, command=command, relief="flat", bd=0,
            bg="white", fg="black", activebackground="#eeeeee",
            activeforeground="black", padx=8, pady=3,
            font=("Microsoft YaHei UI", 9), cursor="hand2",
        )
        button.pack(side="left")
        return button

    def _place_bottom_right(self) -> None:
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(0, screen_width - self.WIDTH - 16)
        y = max(0, screen_height - self.HEIGHT - 56)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def set_state(self, state: ActivityState) -> None:
        self.pause_button.configure(text="恢复" if state is ActivityState.PAUSED else "暂停")

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self.work_value.configure(text=format_duration(snapshot.working_seconds))
        self.idle_value.configure(text=format_duration(snapshot.idle_seconds))

    def show_history(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("历史记录")
        dialog.geometry("430x360")
        dialog.configure(bg="white")
        history = tk.Listbox(dialog, relief="flat", bd=0, bg="white", fg="black",
                             font=("Microsoft YaHei UI", 10))
        history.pack(fill="both", expand=True, padx=14, pady=14)
        for segment in reversed(self.database.segments_for_date(__import__("datetime").date.today())):
            history.insert(
                "end",
                f"{segment.start_utc.astimezone():%H:%M:%S}  "
                f"{self._state_name(segment.state):<4}  {format_duration(segment.duration_seconds)}",
            )

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

    @staticmethod
    def _state_name(state: ActivityState) -> str:
        return {
            ActivityState.WORKING: "工作",
            ActivityState.IDLE: "空闲",
            ActivityState.PAUSED: "暂停",
            ActivityState.UNTRACKED: "未记录",
        }[state]

    def clear_data(self) -> None:
        if messagebox.askyesno("确认清空", "确定永久删除所有计时记录吗？", parent=self.root):
            self.service.clear_and_restart()

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
