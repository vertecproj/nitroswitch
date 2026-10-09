@echo off
:: uninstall.bat - remove o que o install.bat instalou.

setlocal
set "APP_NAME=NitroSwitch"
set "INSTALL_DIR=%LOCALAPPDATA%\%APP_NAME%"
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

echo Removendo NitroSwitch...

taskkill /IM "%APP_NAME%.exe" /F >nul 2>&1

if exist "%INSTALL_DIR%" rmdir /S /Q "%INSTALL_DIR%"
if exist "%START_MENU%\%APP_NAME%.lnk" del /Q "%START_MENU%\%APP_NAME%.lnk"

schtasks /delete /tn "NitroSwitch" /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "NitroSwitch" /f >nul 2>&1

echo Concluido.
pause
endlocal
