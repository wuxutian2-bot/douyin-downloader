@echo off
setlocal
cd /d "%~dp0"

rem ---- drag/drop or command-line argument: run directly ----
set "LINK=%~1"
if not "%LINK%"=="" goto :run

echo.
echo  ========================================
echo    Douyin Original Video Downloader
echo  ========================================
echo  Paste the Douyin link here, then press Enter:
echo  (short link, web link, or a whole share message)
echo.

set "LINK="
set /p "LINK= > "

if "%LINK%"=="" goto :empty

:run
echo.
python "%~dp0douyin_dl.py" "%LINK%" -o "%~dp0downloads"
echo.
echo  Done. Files are in the downloads folder.
goto :end

:empty
echo  [!] Nothing entered.

:end
echo.
pause
