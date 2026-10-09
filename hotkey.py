"""
hotkey.py
---------
Tecla de atalho para abrir/fechar o painel — a ideia é reaproveitar a tecla
dedicada que abria o NitroSense (desinstalado).

Por que "aprender" em vez de fixar um código: não deu para confirmar o que
essa tecla envia neste modelo. Em Nitros recentes ela chega como scancode de
teclado comum (no Linux, 0xF5 do atkbd = E0 75 no set 1); em outros modelos
chega como evento WMI da Acer (`AcerGenericEvent`). Então o app escuta os DOIS
caminhos e, no modo aprender, grava o primeiro que aparecer.

    binding de teclado: {"type": "key", "vk": int, "scan": int, "ext": bool}
    binding WMI:        {"type": "wmi", "detail": "hex dos bytes do EventDetail"}

O evento WMI só funciona com o processo elevado (a classe exige admin); sem
elevação essa parte é ignorada em silêncio e só o teclado vale.
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger("hotkey")

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10

# teclas que nunca viram atalho no modo aprender (modificadores, Esc)
IGNORE_VK = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x1B, 0x14}

# chute inicial, usado só enquanto nada foi aprendido: E0 75 (o 0xF5 do Linux).
# Não existe em teclado padrão, então o risco de disparar sem querer é baixo.
DEFAULT_BINDING = {"type": "key", "vk": None, "scan": 0x75, "ext": True}

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# Tipos declarados no IMPORT, não dentro do loop do teclado: sem argtypes o
# ctypes converte o LPARAM (ponteiro de 64 bits) para int de 32 e estoura com
# OverflowError. Antes estava dentro de `_keyboard_loop`, o que só funcionava
# por acaso de ordem de execução.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


def describe(binding):
    if not binding:
        return "Atalho: não configurado"
    if binding.get("type") == "wmi":
        return f"Atalho: evento Acer ({binding.get('detail', '')[:11]}…)"
    ext = "E0 " if binding.get("ext") else ""
    return f"Atalho: tecla {ext}{binding.get('scan', 0):02X}"


class HotkeyListener:
    def __init__(self, binding, on_trigger, on_learned=None):
        self.binding = binding
        self.on_trigger = on_trigger
        self.on_learned = on_learned
        self._learning = False
        self._last_fire = 0.0
        self._hook = None
        self._proc = HOOKPROC(self._hook_proc)  # manter referência viva

    # ------------------------------------------------------------------ API
    def start(self):
        threading.Thread(target=self._keyboard_loop, name="hotkey-kbd", daemon=True).start()
        threading.Thread(target=self._wmi_loop, name="hotkey-wmi", daemon=True).start()

    def learn(self, timeout_s=12.0):
        """Liga o modo aprender: a próxima tecla (ou evento Acer) vira o atalho."""
        self._learning = True

        def expira():
            time.sleep(timeout_s)
            if self._learning:
                self._learning = False
                self._learned(None)

        threading.Thread(target=expira, daemon=True).start()

    def set_binding(self, binding):
        self.binding = binding

    # ------------------------------------------------------------- internos
    def _fire(self):
        agora = time.monotonic()
        if agora - self._last_fire < 0.5:  # a tecla pode repetir enquanto segurada
            return
        self._last_fire = agora
        try:
            self.on_trigger()
        except Exception as e:
            log.warning(f"on_trigger falhou: {e}")

    def _learned(self, binding):
        if binding:
            self.binding = binding
        if self.on_learned:
            try:
                self.on_learned(binding)
            except Exception as e:
                log.warning(f"on_learned falhou: {e}")

    def _key_matches(self, b, vk, scan, ext):
        if not b or b.get("type") != "key":
            return False
        if b.get("scan") != scan or bool(b.get("ext")) != ext:
            return False
        return b.get("vk") in (None, vk)

    def _hook_proc(self, n_code, w_param, l_param):
        if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            k = ctypes.cast(l_param, ctypes.POINTER(_KBD)).contents
            if not (k.flags & LLKHF_INJECTED):
                vk, scan, ext = k.vkCode, k.scanCode, bool(k.flags & LLKHF_EXTENDED)
                if self._learning:
                    if vk not in IGNORE_VK:
                        self._learning = False
                        log.info(f"tecla aprendida: vk=0x{vk:02X} scan=0x{scan:02X} ext={ext}")
                        self._learned({"type": "key", "vk": vk, "scan": scan, "ext": ext})
                elif self._key_matches(self.binding or DEFAULT_BINDING, vk, scan, ext):
                    self._fire()
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _keyboard_loop(self):
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            log.warning(f"não foi possível instalar o hook de teclado (erro {ctypes.get_last_error()})")
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _wmi_loop(self):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            svc = win32com.client.Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", r"root\wmi")
            events = svc.ExecNotificationQuery("SELECT * FROM AcerGenericEvent")
        except Exception as e:
            log.info(f"eventos Acer indisponíveis (normal sem admin): {e}")
            return
        while True:
            try:
                ev = events.NextEvent(1000)
            except Exception:
                continue  # timeout sem evento
            try:
                detail = bytes(ev.Properties_("EventDetail").Value or b"").hex()
            except Exception:
                detail = ""
            log.info(f"evento Acer: {detail}")
            if self._learning:
                self._learning = False
                self._learned({"type": "wmi", "detail": detail})
            elif self.binding and self.binding.get("type") == "wmi" and self.binding.get("detail") == detail:
                self._fire()
