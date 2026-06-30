@echo off
chcp 65001 >nul
echo ============================================
echo  电力数据采集工具 - Exe打包脚本
echo ============================================
echo.

echo [1/3] 清理旧的打包产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo 清理完成
echo.

echo [2/3] 开始打包 (PyInstaller)...
python -m PyInstaller ln_grid_crawler.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败！
    pause
    exit /b 1
)
echo.

echo [3/3] 打包完成！
echo.
echo 输出目录: dist\电力数据采集工具\
echo 主程序: dist\电力数据采集工具\电力数据采集工具.exe
echo.
pause
