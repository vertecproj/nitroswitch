# build.ps1 - gera o app (onedir) e o instalador unico NitroSwitch-Setup.exe.
# Usado igual no PC (.\build.ps1) e no GitHub Actions (.github\workflows\release.yml).
# Saida: installer\NitroSwitch-Setup.exe (+ .sha256). Ver CLAUDE.md.
param([string]$Python = "")
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not $Python) {
    $Python = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" } else { "python" }
}

function Invoke-PyInstaller([string[]]$BuildArgs) {
    # Start-Process + arquivos de log: canalizar a saida no PowerShell 5.1
    # transforma o stderr do PyInstaller em "erro" e mente o codigo de saida
    $quoted = $BuildArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
    $p = Start-Process -FilePath $Python -NoNewWindow -Wait -PassThru `
        -ArgumentList (@("-m", "PyInstaller", "--noconfirm", "--windowed", "--noupx", "--uac-admin",
                         "--icon", "$PSScriptRoot\assets\nitroswitch.ico", "--paths", "$PSScriptRoot") + $quoted) `
        -RedirectStandardOutput build_out.log -RedirectStandardError build_err.log
    if ($p.ExitCode -ne 0) {
        Get-Content build_err.log -Tail 40
        throw "PyInstaller falhou (codigo $($p.ExitCode))"
    }
}

Write-Host "1/2 aplicativo..."
Invoke-PyInstaller @("--onedir", "--name", "NitroSwitch", "--distpath", "dist_v2", "--workpath", "build_v2",
    "--specpath", "build_v2", "--add-data", "$PSScriptRoot\assets;assets",
    "--hidden-import", "win32com", "--hidden-import", "win32com.client",
    "--hidden-import", "pythoncom", "--hidden-import", "pywintypes", "main.py")

Write-Host "2/2 instalador..."
Invoke-PyInstaller @("--onefile", "--name", "NitroSwitch-Setup", "--distpath", "installer", "--workpath", "build_setup",
    "--specpath", "build_setup", "--add-data", "$PSScriptRoot\dist_v2\NitroSwitch;payload", "setup_app.py")

$setup = Join-Path $PSScriptRoot "installer\NitroSwitch-Setup.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$setup.sha256", "$hash  NitroSwitch-Setup.exe`n")
Write-Host "OK: $setup ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB) sha256=$hash"
