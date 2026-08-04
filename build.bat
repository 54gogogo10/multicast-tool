@echo off
REM Build the Multicast Test Tool exe.
REM
REM Usage:
REM     build.bat            -> default (PySide6 / Windows 10+)
REM     build.bat win10      -> same as default
REM     build.bat win7       -> PySide2 / Windows 7 build (uses venv-win7)
REM     build.bat onefile    -> single-file PySide6 build
REM     build.bat onefile-win7  -> single-file PySide2 build

setlocal

set MODE=%1
if "%MODE%"=="" set MODE=win10

if /I "%MODE%"=="win7" goto :win7
if /I "%MODE%"=="onefile-win7" goto :onefile_win7
if /I "%MODE%"=="onefile" goto :onefile
if /I "%MODE%"=="win10" goto :win10

echo Unknown mode: %MODE%
exit /b 1

:win10
echo === Multicast Test Tool build (PySide6 / Windows 10+) ===
where pyinstaller >nul 2>nul
if errorlevel 1 (
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install pyinstaller.
        exit /b 1
    )
)
pyinstaller --noconfirm --clean multicast_tool.spec
goto :done

:onefile
echo === Multicast Test Tool build (PySide6, onefile) ===
where pyinstaller >nul 2>nul
if errorlevel 1 python -m pip install pyinstaller
pyinstaller --noconfirm --clean --name MulticastTool --windowed --onefile run.py
goto :done

:win7
echo === Multicast Test Tool build (PySide2 / Windows 7) ===
if not exist venv-win7\Scripts\pyinstaller.exe (
    echo venv-win7 not found or pyinstaller missing. See README "Windows 7 build".
    exit /b 1
)
venv-win7\Scripts\pyinstaller.exe --noconfirm --clean multicast_tool.win7.spec
goto :done

:onefile_win7
echo === Multicast Test Tool build (PySide2, onefile) ===
if not exist venv-win7\Scripts\pyinstaller.exe (
    echo venv-win7 not found or pyinstaller missing. See README "Windows 7 build".
    exit /b 1
)
venv-win7\Scripts\pyinstaller.exe --noconfirm --clean --name MulticastTool-Win7 --windowed --onefile run.py
goto :done

:done
if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    exit /b 1
)
echo.
echo BUILD OK.
echo Output: dist\
echo.
endlocal
