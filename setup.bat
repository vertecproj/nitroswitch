@echo off
:: ============================================================================
:: setup_completo.bat - Faz TUDO em um clique:
::   1. Cria o ambiente virtual (venv), se ainda não existir
::   2. Ativa o venv
::   3. Instala as dependências (requirements.txt)
::   4. Gera o executável com PyInstaller
::   5. Roda o install.bat para instalar o app de verdade
::
:: Rode este arquivo sempre que quiser (re)instalar do zero ou aplicar uma
:: atualização de código: só substituir os .py atualizados na pasta e
:: dar 2 cliques aqui de novo.
::
:: Não precisa rodar como Administrador.
:: ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ================================================
echo   NitroSwitch - Setup Completo
echo ================================================
echo.

:: ---------------------------------------------------------------------
:: 1. Verifica se o Python esta instalado
:: ---------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale em https://www.python.org/downloads/ marcando "Add to PATH"
    echo e rode este arquivo de novo.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------
:: 2. Cria o venv, se ainda nao existir
:: ---------------------------------------------------------------------
if not exist "venv\Scripts\activate.bat" (
    echo [1/5] Criando ambiente virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o venv.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Ambiente virtual ja existe, pulando criacao.
)

:: ---------------------------------------------------------------------
:: 3. Ativa o venv (via .bat, sem problema de politica de execucao)
:: ---------------------------------------------------------------------
echo [2/5] Ativando ambiente virtual...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERRO] Falha ao ativar o venv.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------
:: 4. Instala/atualiza as dependencias
:: ---------------------------------------------------------------------
echo [3/5] Instalando dependencias (isso pode demorar um pouco)...
pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias. Veja o erro acima.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------
:: 5. Limpa builds antigos e gera o executavel
:: ---------------------------------------------------------------------
echo [4/5] Gerando o executavel com PyInstaller...
if exist "build" rmdir /S /Q "build"
if exist "dist" rmdir /S /Q "dist"
if exist "NitroSwitch.spec" del /Q "NitroSwitch.spec"

pyinstaller --noconfirm --onefile --windowed --name "NitroSwitch" --add-data "assets;assets" --uac-admin --hidden-import win32com --hidden-import win32com.client --hidden-import pythoncom main.py
if errorlevel 1 (
    echo [ERRO] Falha ao gerar o executavel. Veja o erro acima.
    pause
    exit /b 1
)

if not exist "dist\NitroSwitch.exe" (
    echo [ERRO] O executavel nao foi gerado. Verifique os logs acima.
    pause
    exit /b 1
)
echo [OK] Executavel gerado em dist\NitroSwitch.exe

:: ---------------------------------------------------------------------
:: 6. Roda o instalador
:: ---------------------------------------------------------------------
echo [5/5] Instalando o aplicativo...
echo.
call install.bat

endlocal
