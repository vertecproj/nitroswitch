"""
tray.py
-------
Ícone na bandeja do sistema (system tray) com menu de contexto:
    - Alternar para Eco
    - Alternar para Boost
    - Abrir Interface
    - Sair

Usa pystray + Pillow para desenhar um ícone simples em runtime
(evita depender de um arquivo .ico externo, mas você pode trocar
por assets/icon.ico se preferir um ícone customizado).
"""

import pystray
from PIL import Image, ImageDraw
import threading

import icons
import power_modes

MODE_COLORS = {"supereco": "#5ad1ff", "eco": "#7ef9d0", "balanced": "#ffd479", "boost": "#ff6b6b"}
MODE_NAMES = {"supereco": "Super Eco", "eco": "Eco", "balanced": "Equilibrado", "boost": "Boost"}


def _generate_icon_image(color="#ffd479") -> Image.Image:
    """Ícone da bandeja: raio neon na cor do modo ativo (ver icons.app_icon)."""
    return icons.app_icon(color, 64)


class TrayIcon:
    def __init__(self, app_window):
        """
        app_window: instância de ui.ModePopup, para poder abrir a janela
                    e acionar trocas de modo a partir do menu.
        """
        self.app_window = app_window
        self.update_version = None
        self.icon = pystray.Icon(
            "nitro_switch",
            _generate_icon_image(),
            "NitroSwitch",
            menu=self._build_menu(),
        )

    def _build_menu(self):
        return pystray.Menu(
            # só aparece quando a versão nova já está baixada (ver updater.py)
            pystray.MenuItem(lambda item: f"Atualizar para v{self.update_version}", self._on_update,
                             visible=lambda item: self.update_version is not None),
            pystray.MenuItem("Alternar para Super Eco", self._on_supereco),
            pystray.MenuItem("Alternar para Eco", self._on_eco),
            pystray.MenuItem("Alternar para Equilibrado", self._on_balanced),
            pystray.MenuItem("Alternar para Boost", self._on_boost),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Abrir Interface", self._on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._on_quit),
        )

    # Os callbacks do pystray rodam em thread própria; para mexer na UI do
    # Tkinter com segurança, usamos `.after(0, ...)` agendado na janela principal.
    def _on_supereco(self, icon, item):
        self.app_window.after(0, lambda: self.app_window.app.apply_mode_from_tray("supereco"))

    def _on_eco(self, icon, item):
        self.app_window.after(0, lambda: self.app_window.app.apply_mode_from_tray("eco"))

    def _on_balanced(self, icon, item):
        self.app_window.after(0, lambda: self.app_window.app.apply_mode_from_tray("balanced"))

    def _on_boost(self, icon, item):
        self.app_window.after(0, lambda: self.app_window.app.apply_mode_from_tray("boost"))

    def _on_update(self, icon, item):
        self.app_window.after(0, lambda: self.app_window.app.install_update())

    def set_update(self, version):
        """Avisa que há uma versão nova pronta (qualquer thread)."""
        self.update_version = version
        try:
            self.icon.update_menu()
            self.icon.notify(f"Versão {version} pronta. Abra o painel e clique em ATUALIZAR.", "NitroSwitch")
        except Exception:
            pass

    def _on_open(self, icon, item):
        self.app_window.show_popup()

    def _on_quit(self, icon, item):
        self.icon.stop()
        self.app_window.destroy()

    def set_mode(self, mode):
        """Troca a cor do ícone e a dica para o modo ativo (qualquer thread)."""
        try:
            self.icon.icon = _generate_icon_image(MODE_COLORS.get(mode, "#ffd479"))
            self.icon.title = f"NitroSwitch · {MODE_NAMES.get(mode, mode)}"
        except Exception:
            pass

    def run_detached(self):
        """Roda o loop do ícone da bandeja em uma thread separada,
        para não bloquear o mainloop do Tkinter."""
        threading.Thread(target=self.icon.run, daemon=True).start()
