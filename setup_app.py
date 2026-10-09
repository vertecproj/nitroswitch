"""
setup_app.py
------------
Instalador (e desinstalador) do NitroSwitch, empacotado como um .exe único:
`NitroSwitch-Setup.exe`. Sem Inno Setup nem dependências extras.

O .exe pede a permissão de administrador UMA vez (manifesto uac_admin) e então:
  1. copia o aplicativo para C:\\NitroSwitch\\app (caminho fixo e visível para o
     Agendador de Tarefas - ver CLAUDE.md, "%LOCALAPPDATA% do agente");
  2. cria as tarefas elevadas (abrir sem UAC + iniciar com o Windows);
  3. cria os atalhos (Menu Iniciar e Área de Trabalho) que abrem o app como
     administrador SEM pedir permissão;
  4. registra em "Aplicativos instalados" (desinstalar pelo Windows);
  5. abre o app.

Uso:
    NitroSwitch-Setup.exe               janela de instalação
    NitroSwitch-Setup.exe /silent       instala sem janela (testes/automação)
    desinstalar.exe /uninstall          janela de desinstalação (cópia deste .exe)
    desinstalar.exe /uninstall /silent  desinstala sem janela
    NitroSwitch-Setup.exe /silent /update   atualização disparada pelo próprio app
                                        (updater.py): espera o app fechar, troca os
                                        arquivos, mantém a escolha de "iniciar com o
                                        Windows" e abre o app de novo
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk

import startup
from version import __version__ as VERSION

APP = "NitroSwitch"
INSTALL_DIR = r"C:\NitroSwitch\app"
EXE = os.path.join(INSTALL_DIR, "NitroSwitch.exe")
UNINSTALLER = os.path.join(INSTALL_DIR, "desinstalar.exe")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\NitroSwitch"
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def _payload():
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "payload")


def _sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)


def _app_running():
    return "NitroSwitch.exe" in _sh(["tasklist", "/FI", "IMAGENAME eq NitroSwitch.exe", "/NH"]).stdout


def _stop_app(grace=0.0):
    """Fecha o app. Na atualização ele se encerra sozinho (devolvendo a
    ventoinha ao firmware): `grace` é quanto esperar por isso antes de forçar."""
    limit = time.time() + grace
    while time.time() < limit and _app_running():
        time.sleep(0.3)
    _sh(["taskkill", "/F", "/IM", "NitroSwitch.exe"])
    time.sleep(1.5)


def _lock_down(folder):
    """Só Administradores e SISTEMA alteram a pasta do app (usuários só leem e
    executam). Pastas criadas na raiz de C: nascem graváveis por qualquer
    programa, e este app roda ELEVADO: sem isto, um programa comum poderia
    trocar o .exe e ganhar privilégio de administrador. SIDs valem em qualquer idioma."""
    _sh(["icacls", folder, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
         "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"])


def _shortcut_paths():
    prog = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs")
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    # o Desktop pode estar redirecionado (OneDrive): pergunta ao Windows
    ps = "[Environment]::GetFolderPath('Desktop')"
    out = _sh(["powershell", "-NoProfile", "-Command", ps]).stdout.strip()
    if out:
        desk = out
    return [os.path.join(prog, "NitroSwitch.lnk"), os.path.join(desk, "NitroSwitch.lnk")]


def _make_shortcuts():
    vbs = os.path.join(INSTALL_DIR, "abrir.vbs")
    for lnk in _shortcut_paths():
        ps = startup.shortcut_script(lnk, vbs, INSTALL_DIR, EXE)
        startup._powershell(ps)


def _register_uninstall():
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, APP)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, EXE)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, INSTALL_DIR)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{UNINSTALLER}" /uninstall')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def install(log, start_with_windows=True, update=False):
    payload = _payload()
    if not os.path.exists(os.path.join(payload, "NitroSwitch.exe")):
        raise RuntimeError("Arquivos do aplicativo não encontrados dentro do instalador.")
    log("Fechando o NitroSwitch, se estiver aberto…")
    _stop_app(grace=8.0 if update else 0.0)
    log("Copiando arquivos…")
    if os.path.isdir(INSTALL_DIR):
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    shutil.copytree(payload, INSTALL_DIR, dirs_exist_ok=True)
    _lock_down(INSTALL_DIR)
    with open(os.path.join(INSTALL_DIR, "abrir.vbs"), "w", encoding="ascii", newline="") as f:
        f.write('CreateObject("WScript.Shell").Run "schtasks /run /tn ' + startup.LAUNCHER_TASK + '", 0, False\r\n')
    if getattr(sys, "frozen", False):
        shutil.copy2(sys.executable, UNINSTALLER)
    log("Criando as tarefas (abrir sem pedir permissão)…")
    if not startup._register_task(startup.LAUNCHER_TASK, EXE, "", at_logon=False):
        raise RuntimeError("Não consegui criar a tarefa de abertura.")
    if update:
        pass  # mantém a escolha do usuário (a tarefa de logon aponta para o mesmo .exe)
    elif start_with_windows:
        startup._register_task(startup.APP_NAME, EXE, "--tray", at_logon=True)
    else:
        _sh(["schtasks", "/delete", "/tn", startup.APP_NAME, "/f"])
    log("Criando atalhos…")
    _make_shortcuts()
    _register_uninstall()
    log("Abrindo o NitroSwitch…")
    if update:
        # Abre direto (este processo já é elevado): na atualização a tarefa
        # "NitroSwitchAbrir" pode ainda constar como em execução e ignorar o pedido.
        env = dict(os.environ)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        env.pop("_MEIPASS2", None)
        for flags in (0x01000208, 0x00000208):  # DETACHED | NEW_PROCESS_GROUP (+ BREAKAWAY_FROM_JOB)
            try:
                subprocess.Popen([EXE], cwd=INSTALL_DIR, env=env, creationflags=flags, close_fds=True)
                break
            except OSError:
                continue
    else:
        _sh(["schtasks", "/run", "/tn", startup.LAUNCHER_TASK])


def uninstall(log):
    log("Fechando o NitroSwitch…")
    _stop_app()
    log("Removendo tarefas e atalhos…")
    for t in (startup.APP_NAME, startup.LAUNCHER_TASK):
        _sh(["schtasks", "/delete", "/tn", t, "/f"])
    for lnk in _shortcut_paths():
        try:
            os.remove(lnk)
        except OSError:
            pass
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except OSError:
        pass
    # devolve o Windows ao plano Equilibrado sem os limites de CPU dos modos e
    # apaga os planos próprios (ver power_modes.OWN_PLANS)
    try:
        import power_modes
        power_modes.activate_mode_plan("balanced")
        power_modes._begin_batch()
        power_modes.set_cooling_profile("balanced")
        power_modes.set_cpu_limits("balanced")
        power_modes._flush_batch()
        for own, _name in power_modes.OWN_PLANS.values():
            _sh(["powercfg", "/delete", own])
    except Exception:  # noqa: BLE001
        pass
    log("Apagando arquivos…")
    # este .exe roda de dentro da pasta: a pasta é apagada por um cmd à parte
    bat = os.path.join(os.environ.get("TEMP", "C:\\Windows\\Temp"), "nitroswitch_limpeza.bat")
    with open(bat, "w", encoding="ascii", newline="") as f:
        f.write("@echo off\r\nping -n 4 127.0.0.1 >nul\r\n"
                'rmdir /s /q "' + INSTALL_DIR + '"\r\n'
                'rmdir "C:\\NitroSwitch" 2>nul\r\n'
                'del "%~f0"\r\n')
    subprocess.Popen(["cmd", "/c", bat], creationflags=NO_WINDOW)


# ----------------------------------------------------------------------
# Janela
# ----------------------------------------------------------------------
BG, FG, MUT, ACC, LINE = "#0a0a0e", "#f2f2f5", "#8d8d96", "#ffd479", "#25252f"


def run_gui(mode):
    import ctypes
    for ttf in ("Rajdhani-Bold.ttf", "Rajdhani-SemiBold.ttf"):
        path = os.path.join(_payload(), "_internal", "assets", ttf)
        if os.path.exists(path):
            ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
    root = tk.Tk()
    root.title("NitroSwitch — " + ("Desinstalar" if mode == "uninstall" else "Instalar"))
    root.configure(bg=BG)
    root.resizable(False, False)
    w, h = 440, 300
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}")
    try:
        root.iconbitmap(default=os.path.join(_payload(), "_internal", "assets", "nitroswitch.ico"))
    except Exception:
        pass

    tk.Label(root, text="NITRO", bg=BG, fg=FG, font=("Rajdhani", 26, "bold")).place(x=28, y=22)
    tk.Label(root, text="SWITCH", bg=BG, fg=ACC, font=("Rajdhani", 26, "bold")).place(x=104, y=22)
    tk.Label(root, text="v" + VERSION, bg=BG, fg=MUT, font=("Consolas", 9)).place(x=w - 60, y=36)
    tk.Frame(root, bg=LINE, height=1).place(x=28, y=74, width=w - 56)

    if mode == "uninstall":
        text = "Remover o NitroSwitch deste computador?\n(Suas configurações são mantidas.)"
    else:
        text = ("Modos de energia do notebook num painel só\n(e ventoinha/perfil térmico nos Acer Nitro).\n"
                "Abre como administrador sem pedir permissão\ne se atualiza sozinho.")
    tk.Label(root, text=text, bg=BG, fg=FG, font=("Segoe UI", 10), justify="left").place(x=28, y=88)

    startup_var = tk.BooleanVar(value=True)
    if mode != "uninstall":
        tk.Checkbutton(root, text="Iniciar com o Windows", variable=startup_var, bg=BG, fg=FG, selectcolor=BG,
                       activebackground=BG, activeforeground=FG, font=("Segoe UI", 10)).place(x=26, y=170)

    status = tk.Label(root, text="", bg=BG, fg=MUT, font=("Consolas", 9), anchor="w")
    status.place(x=28, y=212, width=w - 56)
    btn = tk.Button(root, text="DESINSTALAR" if mode == "uninstall" else "INSTALAR", bg=ACC, fg="#0a0a0e",
                    activebackground="#ffe29a", relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2")
    btn.place(x=w - 160, y=h - 62, width=132, height=36)
    cancel = tk.Button(root, text="Cancelar", bg=BG, fg=MUT, activebackground=BG, activeforeground=FG,
                       relief="flat", font=("Segoe UI", 10), command=root.destroy, cursor="hand2")
    cancel.place(x=w - 260, y=h - 62, width=90, height=36)

    def log(msg):
        root.after(0, lambda: status.config(text=msg))

    def work():
        try:
            if mode == "uninstall":
                uninstall(log)
                done = "NitroSwitch removido."
            else:
                install(log, start_with_windows=startup_var.get())
                done = "Pronto! O NitroSwitch está aberto na bandeja."
            root.after(0, lambda: finish(done, ok=True))
        except Exception as e:  # noqa: BLE001
            root.after(0, lambda: finish(f"Erro: {e}", ok=False))

    def finish(msg, ok):
        status.config(text=msg, fg=ACC if ok else "#ff6b6b")
        cancel.place_forget()
        btn.config(text="FECHAR", state="normal", command=root.destroy)

    def start():
        btn.config(state="disabled", text="AGUARDE…")
        cancel.place_forget()
        threading.Thread(target=work, daemon=True).start()

    btn.config(command=start)
    root.mainloop()


def main():
    args = [a.lower() for a in sys.argv[1:]]
    mode = "uninstall" if "/uninstall" in args else "install"
    if "/silent" in args:
        try:
            if mode == "uninstall":
                uninstall(print)
            else:
                install(print, update="/update" in args)
        except Exception as e:  # noqa: BLE001
            print("ERRO:", e)
            sys.exit(1)
        return
    run_gui(mode)


if __name__ == "__main__":
    main()
