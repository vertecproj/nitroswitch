# install.ps1 - instala o NitroSwitch (pasta onedir) com UM único pedido de UAC.
#
# Depois de instalado, o app abre SEMPRE como administrador e SEM prompt:
#   - Menu Iniciar / Área de Trabalho -> abrir.vbs -> tarefa "NitroSwitchAbrir"
#     (privilégio máximo, sem gatilho)
#   - Iniciar com o Windows -> tarefa "NitroSwitch" (gatilho de logon, --tray)
# Rode pelo install.bat (ele pede a elevação). Ver CLAUDE.md, "Abrir sem UAC".

param(
    [string]$Source = (Join-Path $PSScriptRoot "dist_v2\NitroSwitch"),
    [switch]$SemIniciarComWindows
)
$ErrorActionPreference = 'Stop'
$dir = 'C:\NitroSwitch\app'
$exe = Join-Path $dir 'NitroSwitch.exe'

if (-not (Test-Path (Join-Path $Source 'NitroSwitch.exe'))) {
    Write-Host "[ERRO] Nao encontrei $Source\NitroSwitch.exe. Gere o build primeiro (README.md)." -ForegroundColor Red
    exit 1
}

Get-Process NitroSwitch -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
New-Item -ItemType Directory -Force $dir | Out-Null
robocopy $Source $dir /MIR /NFL /NDL /NJH /NJS | Out-Null
Write-Host "[OK] Aplicativo copiado para $dir"

# abrir.vbs: dispara a tarefa elevada sem abrir janela de console
$vbs = Join-Path $dir 'abrir.vbs'
[IO.File]::WriteAllText($vbs, 'CreateObject("WScript.Shell").Run "schtasks /run /tn NitroSwitchAbrir", 0, False' + "`r`n", [Text.Encoding]::ASCII)

$user = "$env:USERDOMAIN\$env:USERNAME"
function Register-NS($name, $arg, $logon) {
    $params = @{ Execute = $exe; WorkingDirectory = $dir }
    if ($arg) { $params.Argument = $arg }
    $a = New-ScheduledTaskAction @params
    $p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
    if ($logon) {
        $t = New-ScheduledTaskTrigger -AtLogOn -User $user
        Register-ScheduledTask -TaskName $name -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $name -Action $a -Principal $p -Settings $s -Force | Out-Null
    }
}
Register-NS 'NitroSwitchAbrir' '' $false
Write-Host "[OK] Tarefa 'NitroSwitchAbrir' criada (abre o app como admin, sem UAC)"
if (-not $SemIniciarComWindows) {
    Register-NS 'NitroSwitch' '--tray' $true
    Write-Host "[OK] Tarefa 'NitroSwitch' criada (inicia com o Windows, na bandeja)"
}

# atalhos -> wscript abrir.vbs
$w = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'NitroSwitch.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'NitroSwitch.lnk')
)
foreach ($lnk in $targets) {
    $s = $w.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $s.Arguments = '//B //nologo "' + $vbs + '"'
    $s.WorkingDirectory = $dir
    $s.IconLocation = "$exe,0"
    $s.Description = 'NitroSwitch'
    $s.Save()
}
Write-Host "[OK] Atalhos criados (Menu Iniciar e Area de Trabalho)"

schtasks /run /tn NitroSwitchAbrir | Out-Null
Write-Host "[OK] NitroSwitch aberto."
