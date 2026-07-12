from __future__ import annotations

import logging
import os
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path

from studyflow.infrastructure import Database
from studyflow.main_window import StudyFlowWindow
from studyflow.timer_service import TimerService


def data_dir() -> Path:
    override=os.environ.get("STUDYFLOW_DATA_DIR")
    return Path(override) if override else Path(os.environ.get("LOCALAPPDATA",Path.home()))/"StudyFlow"


def configure_logging(path: Path) -> None:
    path.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(path/"studyflow.log",maxBytes=1_000_000,backupCount=3,encoding="utf-8")],format="%(asctime)s %(levelname)s %(message)s")


def create_app(root: tk.Tk, path: Path | None = None) -> StudyFlowWindow:
    target=path or data_dir(); configure_logging(target/"logs")
    database=Database(target/"studyflow.db"); service=TimerService(database)
    return StudyFlowWindow(root,database,service)


def main() -> int:
    root=tk.Tk(); window=create_app(root)
    # pystray is optional so the stdlib-only application always remains runnable.
    try:
        import pystray
        from PIL import Image, ImageDraw
        image=Image.new("RGB",(64,64),"#6975ef"); draw=ImageDraw.Draw(image); draw.ellipse((18,18,46,46),fill="white")
        tray=pystray.Icon("StudyFlow",image,"StudyFlow",menu=pystray.Menu(pystray.MenuItem("显示",lambda:root.after(0,window.show)),pystray.MenuItem("暂停/恢复",lambda:root.after(0,window.service.toggle_pause)),pystray.MenuItem("退出",lambda:root.after(0,window.quit))))
        tray.run_detached()
    except ImportError:
        logging.info("pystray/Pillow 未安装，系统托盘功能已禁用")
    try: root.mainloop()
    finally:
        if window.service.segment_id: window.service.stop()
    return 0
