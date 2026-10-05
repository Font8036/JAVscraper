@echo off
chcp 65001 > nul
setlocal

echo 正在清理 __pycache__ 目录...

rem 递归扫描当前目录下的所有 __pycache__ 并删除
for /d /r "%~dp0" %%d in (__pycache__) do (
    if exist "%%d" (
        echo   删除: %%d
        rmdir /s /q "%%d"
    )
)

echo.
echo 清理完成。
pause
endlocal