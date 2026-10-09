@echo off
:: install.bat - instala o NitroSwitch. Pede a elevacao do Windows UMA vez (agora);
:: depois disso o app abre sempre como administrador, sem nenhum pedido.
:: Uso: gere o build (README.md / CLAUDE.md) e de dois cliques aqui.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Pedindo permissao de administrador (so desta vez)...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
