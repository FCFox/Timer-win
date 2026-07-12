# StudyFlow 学习计时器

面向 Windows 10/11、使用 Python 标准库 Tkinter 构建的本地学习活动计时器。它只读取系统“最后一次输入”的时间，不记录按键、鼠标位置、窗口标题或任何输入内容。

## 运行

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m studyflow
```

数据保存在 `%LOCALAPPDATA%\StudyFlow\studyflow.db`，日志保存在同目录的 `logs` 文件夹。

## 功能

- 根据全局键鼠活动自动切换“正在学习/空闲”
- 手动暂停和恢复、系统锁屏与休眠感知
- 右下角白底黑字紧凑窗口，仅显示工作时间和空闲时间
- 暂停、历史、设置、清空、隐藏、退出集中在单行工具栏
- 空闲阈值、每日目标、托盘和开机启动设置
- 空闲阈值按秒设置，最小 0 秒，新安装默认 30 秒
- SQLite 本地持久化与异常退出恢复
