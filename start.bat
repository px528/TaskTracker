@echo off
title TaskTracker
echo ============================================================
echo   TaskTracker - 任务时间线追踪器
echo ============================================================
echo.
echo 正在启动后台追踪服务和 Web 界面...
echo 启动后请在浏览器打开: http://localhost:5000
echo 按 Ctrl+C 停止服务
echo.
python app.py
pause