"""
main.py
-------
Ponto de entrada do NitroSwitch.

Fluxo (reordenado em 2026-10-08 para abrir mais rápido no logon):
    1. Lê a config e, SE houver modo padrão, já dispara a aplicação dele numa
       thread - em paralelo com o resto (antes só começava depois da janela
       inteira estar construída, uns 4-6 s depois)
    2. Importa a interface (CustomTkinter é o import mais pesado) e sobe o
       ícone da bandeja, que já responde mesmo antes da janela existir
    3. Constrói a janela (oculta) e entrega a ela o resultado do modo padrão
    4. Inicia o loop principal do Tkinter

NOTA sobre DPI: uma versão anterior chamava manualmente
ctypes.windll.shcore.SetProcessDpiAwareness() aqui. Isso foi REMOVIDO de
propósito: o CustomTkinter já ativa e gerencia DPI awareness internamente
por conta própria (ScalingTracker), dentro do construtor de CTk(). Chamar
isso de novo manualmente ANTES do primeiro Tk()/CTk() faz o próprio Tk
(não o CustomTkinter) aplicar sua própria auto-escala de HiDPI por baixo
dos panos, empilhando com a escala manual do CustomTkinter - foi assim que
o bug "janela cresce e sai da tela" apareceu de novo quando testamos isso
em 2026-09-14 (ver CLAUDE.md, seção DPI awareness, para a causa raiz real
do bug de posicionamento e onde ela foi corrigida - em ui.py, não aqui).
Deixe o CustomTkinter cuidar da ativação de DPI awareness sozinho.

Uso:
    python main.py            -> roda a janela já visível na primeira vez
    python main.py --tray     -> inicia direto minimizado na bandeja
                                  (usado na inicialização automática do Windows)
"""

import sys
import threading

import config
import power_modes


class _LazyWindow:
    """Stand-in da janela para o ícone da bandeja enquanto ela ainda está
    sendo construída: guarda os pedidos (abrir, trocar de modo) e os executa
    assim que a janela de verdade existir."""

    def __init__(self):
        self.app = None
        self._pending = []
        self._lock = threading.Lock()

    def after(self, ms, fn):
        with self._lock:
            if self.app is None:
                self._pending.append(fn)
                return
            app = self.app
        app.after(ms, fn)

    def show_popup(self):
        self.after(0, lambda: self.app.show_popup())

    def destroy(self):
        self.after(0, lambda: self.app.destroy())

    def attach(self, app):
        with self._lock:
            self.app = app
            pending, self._pending = self._pending, []
        for fn in pending:
            app.after(0, fn)


def _single_instance(start_minimized):
    """Garante UMA instância (duas brigariam pela ventoinha). Se já existe uma,
    pede a ela que abra o painel (exceto quando isto é o início automático,
    `--tray`) e devolve None para esta sair. Mutex/evento `Local\\` bastam: as
    duas instâncias rodam elevadas, na mesma sessão."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    mutex = k.CreateMutexW(None, False, "Local\\NitroSwitch_single")
    if ctypes.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if not start_minimized:
            ev = k.OpenEventW(0x0002, False, "Local\\NitroSwitch_show")  # EVENT_MODIFY_STATE
            if ev:
                k.SetEvent(ev)
        return None
    show_event = k.CreateEventW(None, False, False, "Local\\NitroSwitch_show")
    return mutex, show_event, k


def main():
    start_minimized = "--tray" in sys.argv
    inst = _single_instance(start_minimized)
    if inst is None:
        return
    _mutex, show_event, k32 = inst
    cfg = config.load()

    # 1) modo padrão: começa já, sem esperar a interface
    early = {"mode": cfg.get("default_mode", "eco") if cfg.get("apply_on_startup") else None, "result": None}
    if early["mode"]:
        def _early_apply():
            try:
                early["result"] = power_modes.apply_mode(early["mode"])
            except Exception:
                early["result"] = {"apply_mode": False}
        threading.Thread(target=_early_apply, daemon=True, name="modo-padrao").start()

    # 2) interface + bandeja
    from ui import ModePopup
    from tray import TrayIcon

    lazy = _LazyWindow()
    tray = TrayIcon(lazy)
    tray.run_detached()

    app = ModePopup(on_close_to_tray=lambda: None)
    app.on_mode_changed = tray.set_mode
    app.on_update_ready = tray.set_update
    lazy.attach(app)

    # outra tentativa de abrir o app (atalho do Menu Iniciar) manda abrir o painel
    def _watch_show():
        while True:
            if k32.WaitForSingleObject(show_event, 0xFFFFFFFF) == 0:
                lazy.show_popup()

    threading.Thread(target=_watch_show, daemon=True, name="abrir-painel").start()

    # auto-reparo do atalho "abrir sem UAC" (ver startup.py); fora da thread da UI
    import startup
    threading.Thread(target=startup.ensure_launcher, daemon=True, name="launcher").start()

    # 3) mostra o modo padrão na interface. Sem chamar Tk de outra thread:
    # a própria janela consulta o resultado até ele chegar.
    if early["mode"]:
        app._begin_applying(early["mode"])

        def _poll():
            if early["result"] is not None:
                app.adopt_mode(early["mode"], early["result"])
            else:
                app.after(200, _poll)

        app.after(200, _poll)

    if start_minimized:
        app.withdraw()
    else:
        app.show_popup()

    try:
        app.mainloop()
    finally:
        # Devolve a ventoinha ao firmware ao sair. Se ela ficasse em % fixo
        # sem o app vigiando a temperatura, o firmware não reagiria a
        # aquecimento (ver fan_control.py).
        app.fan_ctrl.stop()
        try:
            tray.icon.stop()  # sem isto o ícone fica "fantasma" na bandeja
        except Exception:
            pass


if __name__ == "__main__":
    main()
