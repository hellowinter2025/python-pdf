@echo off
title PDF 压缩工具
cd /d "%~dp0"

REM 检查并安装依赖
py -c "import pikepdf, Pillow, customtkinter" 2>nul
if errorlevel 1 (
    echo 正在检查依赖...
    pip install pikepdf Pillow customtkinter -q
)

start "" pyw pdf_gui.pyw
