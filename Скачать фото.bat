@echo off
title Скачивание фото с Яндекс.Диска
cd /d "%~dp0"
setlocal

if not exist "%~dp0yadisk_gui.py" goto nofile

set "LAUNCHER="
pythonw --version >nul 2>nul && set "LAUNCHER=pythonw"
if not defined LAUNCHER pyw --version >nul 2>nul && set "LAUNCHER=pyw"

if not defined LAUNCHER goto byassoc

%LAUNCHER% -c "import yadisk_gui" >nul 2>nul
if errorlevel 1 goto fail
start "" %LAUNCHER% "%~dp0yadisk_gui.py"
exit /b 0

:byassoc
rem pythonw не найден в PATH - запускаем через ассоциацию .py (как по двойному клику)
start "" "%~dp0yadisk_gui.py"
exit /b 0

:fail
echo.
echo  Python найден, но запустить программу не удалось.
echo  Скорее всего, не установлен tkinter. Переустановите Python 3 с python.org.
echo  Либо просто двойным кликом откройте yadisk_gui.py.
echo.
pause
exit /b 1

:nofile
echo.
echo  Рядом с этим батником нет файла yadisk_gui.py.
echo  Положите оба файла в одну папку и запустите снова.
echo.
pause
exit /b 1
