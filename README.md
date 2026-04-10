# TaskTracker

一款 Windows 应用程序，用于监控活动窗口使用情况，并将时间归类到用户自定义的任务中。原始窗口数据存储在 SQLite 中，任务通过可配置的关键词匹配在查询时动态生成——因此你可以随时重新定义任务，而不会丢失历史数据。

## 功能特性

- **实时追踪**：每隔 N 秒轮询活动窗口，记录进程名称、窗口标题和时间戳
- **灵活的任务映射**：任务标签在查询时应用，而非存储时——可随时修改定义，不影响历史数据
- **交互式甘特图**：基于 Canvas 的时间轴，支持缩放、平移、悬停提示和贝塞尔过渡曲线
- **任务管理**：通过 Web UI 创建、编辑、删除和重排任务
- **手动时间段**：手动添加或删除时间段
- **日期范围筛选**：快捷按钮支持今天 / 昨天 / 本周 / 本月
- **自动刷新**：仪表盘每 10 秒自动更新

## 演示

![TaskTracker 仪表盘](pic/ui.png)

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3, Flask |
| 数据库 | SQLite3（内置） |
| 窗口检测 | pywin32 |
| 前端 | 原生 JS, HTML5 Canvas |

## 安装

```bash
pip install -r requirements.txt
```

## 使用方法

```bash
start.bat
```

然后在浏览器中打开 `http://localhost:5000`。

也可以直接运行：

```bash
python app.py
```

这将同时启动后台追踪线程和 Flask Web 服务器。

## 配置

编辑 `config.json` 来定义任务和追踪设置：

```json
{
  "tasks": [
    {
      "id": "coding",
      "name": "Coding",
      "color": "#e74c3c",
      "keywords": ["Code.exe", "vscode", "PyCharm"]
    },
    {
      "id": "others",
      "name": "Others",
      "color": "#95a5a6",
      "keywords": []
    }
  ],
  "settings": {
    "poll_interval_seconds": 3,
    "idle_threshold_seconds": 120,
    "min_segment_seconds": 5
  }
}
```

- **keywords**：大小写不敏感的子字符串，与进程名称或窗口标题进行匹配。关键词列表为空的任务作为兜底回退。
- **min_segment_seconds**：短于此时长的时间段将被忽略。
- **idle_threshold_seconds**：无操作持续时间超过此值后，当前时间段将被关闭。

配置可通过 API 实时更新，无需重启。

## 数据库结构

```sql
CREATE TABLE process_segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    process_name  TEXT NOT NULL,
    start_time    REAL NOT NULL,   -- Unix 时间戳
    end_time      REAL,            -- 时间段仍开放时为 NULL
    window_title  TEXT
)
```

## API 参考

| 方法 | 端点 | 描述 |
|------|------|------|
| GET | `/api/status` | 当前追踪状态和活动窗口 |
| GET | `/api/segments` | 列出时间段（支持 `from`/`to` Unix 时间戳参数） |
| GET | `/api/tasks` | 列出所有已配置的任务 |
| POST | `/api/tasks` | 创建或更新任务 |
| DELETE | `/api/tasks/<id>` | 删除任务 |
| POST | `/api/tasks/reorder` | 重排任务顺序 |

## 文件说明

| 文件 | 描述 |
|------|------|
| `app.py` | Flask Web 服务器和 REST API |
| `tracker.py` | 后台窗口监控守护进程 |
| `config.json` | 任务定义和追踪设置 |
| `static/index.html` | 单页 Web 仪表盘 |
| `tasktracker.db` | SQLite 数据库（首次运行时自动创建） |
| `tracker.log` | 追踪守护进程日志 |
| `start.bat` | 启动追踪器和 Web 服务器 |

## 故障排查

- **追踪器未记录**：检查 `tracker.log` 中的错误；确保 pywin32 已正确安装。
- **任务未匹配**：使用仪表盘上的状态指示器查看正在捕获的确切进程名称和窗口标题，然后相应更新关键词。
- **数据库错误**：删除 `tasktracker.db` 并重启以重新创建（所有历史记录将丢失）。

## 许可证
MIT
