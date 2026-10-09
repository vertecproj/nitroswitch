"""
startup.py
----------
Inicialização automática com o Windows e abertura do app SEM pedir UAC.

O app precisa rodar como Administrador (a interface WMI da Acer só aceita
processo elevado). Um .exe com manifesto de admin SEMPRE mostra o prompt de UAC
quando aberto direto (atalho, duplo clique, chave Run). A saída é o Agendador de
Tarefas: uma tarefa com "Executar com privilégios mais altos" abre o programa
elevado e sem prompt. Por isso existem DUAS tarefas:

    NitroSwitch        gatilho de logon, `--tray` (liga/desliga no painel)
    NitroSwitchAbrir   sem gatilho: o atalho do Menu Iniciar a dispara
                       (via abrir.vbs, sem janela de console) - é o "abrir o
                       app" do dia a dia, elevado e sem UAC

Criar/alterar tarefa com RunLevel Highest exige processo já elevado: o app é, e
se auto-repara a cada início (`ensure_launcher`). O instalador (install.ps1)
faz a mesma coisa com um único UAC, na hora de instalar.

2026-10-08: as tarefas são registradas por PowerShell (Register-ScheduledTask)
porque `schtasks /create` pela linha de comando cria a tarefa com "não iniciar
na bateria" e "parar ao desplugar" ligados (ver CLAUDE.md).
"""

import base64
import os
import subprocess
import sys

APP_NAME = "NitroSwitch"
LAUNCHER_TASK = "NitroSwitchAbrir"


def _get_run_command() -> tuple[str, str]:
    """
    Retorna (programa, argumentos) a registrar na tarefa agendada.
    Se rodando como .exe (PyInstaller), usa sys.executable diretamente
    (já é o NitroSwitch.exe, com manifesto de admin embutido).
    Se rodando como script .py, monta "pythonw.exe caminho\\main.py --tray"
    (pythonw evita abrir uma janela de console).
    """
    if getattr(sys, "frozen", False):
        return sys.executable, "--tray"
    else:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
        return pythonw, f'"{script}" --tray'


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def _powershell(script: str) -> tuple[int, str]:
    """Roda um script PowerShell sem janela. -EncodedCommand evita qualquer
    problema de aspas/barras na linha de comando."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc])


def _q(s: str) -> str:
    """Texto entre aspas simples do PowerShell (um nome de usuário como
    O'Brien no caminho quebrava o comando)."""
    return "'" + s.replace("'", "''") + "'"


def shortcut_script(lnk: str, vbs: str, folder: str, exe: str) -> str:
    """PowerShell que cria o atalho `lnk` -> wscript abrir.vbs (abre sem UAC)."""
    return (
        "$w = New-Object -ComObject WScript.Shell; "
        "$s = $w.CreateShortcut(" + _q(lnk) + "); "
        "$s.TargetPath = $env:WINDIR + '\\System32\\wscript.exe'; "
        "$s.Arguments = " + _q('//B //nologo "' + vbs + '"') + "; "
        "$s.WorkingDirectory = " + _q(folder) + "; "
        "$s.IconLocation = " + _q(exe + ",0") + "; "
        "$s.Description = 'NitroSwitch'; $s.Save()"
    )


def _register_task(name: str, program: str, args: str, at_logon: bool) -> bool:
    """Cria/atualiza uma tarefa ELEVADA (RunLevel Highest) para o usuário atual,
    com as configurações certas para notebook: roda na bateria, NÃO é morta ao
    desplugar, sem limite de tempo, sem segunda instância."""
    q = _q
    arg_part = (" -Argument " + q(args)) if args else ""
    ps = (
        # o próprio Windows diz quem é o usuário (conta local, de domínio ou
        # Microsoft/AzureAD), em vez de montar o nome por variáveis de ambiente
        "$u = [Security.Principal.WindowsIdentity]::GetCurrent().Name; "
        "$a = New-ScheduledTaskAction -Execute " + q(program) + arg_part
        + " -WorkingDirectory " + q(os.path.dirname(program)) + "; "
        "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable; "
    )
    if at_logon:
        ps += "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; "
        ps += ("Register-ScheduledTask -TaskName '" + name
               + "' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null")
    else:
        ps += ("Register-ScheduledTask -TaskName '" + name
               + "' -Action $a -Principal $p -Settings $s -Force | Out-Null")
    code, _ = _powershell(ps)
    return code == 0


def is_startup_enabled() -> bool:
    code, _ = _run(["schtasks", "/query", "/tn", APP_NAME])
    return code == 0


def enable_startup() -> None:
    program, args = _get_run_command()
    _register_task(APP_NAME, program, args, at_logon=True)


def disable_startup() -> None:
    _run(["schtasks", "/delete", "/tn", APP_NAME, "/f"])


def ensure_launcher() -> None:
    """Auto-reparo (só no .exe empacotado): garante `abrir.vbs`, a tarefa
    `NitroSwitchAbrir` e o atalho do Menu Iniciar apontando para eles."""
    if not getattr(sys, "frozen", False):
        return
    program = sys.executable
    folder = os.path.dirname(program)
    vbs = os.path.join(folder, "abrir.vbs")
    want = 'CreateObject("WScript.Shell").Run "schtasks /run /tn ' + LAUNCHER_TASK + '", 0, False\r\n'
    try:
        current = open(vbs, encoding="ascii", errors="ignore", newline="").read() if os.path.exists(vbs) else ""
        if current != want:
            with open(vbs, "w", encoding="ascii", newline="") as f:
                f.write(want)
    except OSError:
        return
    code, _ = _run(["schtasks", "/query", "/tn", LAUNCHER_TASK])
    if code != 0:
        _register_task(LAUNCHER_TASK, program, "", at_logon=False)
    lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                       "Programs", "NitroSwitch.lnk")
    ps = shortcut_script(lnk, vbs, folder, program)
    _powershell(ps)


def toggle_startup() -> bool:
    """Alterna o estado e retorna o novo estado REAL (True = habilitado).

    NOTA (2026-09-15, QA, bug real corrigido): antes esta função assumia
    que enable_startup()/disable_startup() tinham funcionado e retornava
    True/False só com base em qual delas foi chamada - mas criar tarefa com
    privilégio máximo falha quando o processo que chama não está elevado, e
    nem enable_startup() nem esta função checavam o retorno. Resultado: a UI
    achava que o switch "Iniciar com o Windows" tinha ligado com sucesso
    quando nenhuma tarefa foi criada. Fix: reconsultar is_startup_enabled() de
    verdade depois da tentativa, em vez de supor.
    """
    if is_startup_enabled():
        disable_startup()
    else:
        enable_startup()
    return is_startup_enabled()
