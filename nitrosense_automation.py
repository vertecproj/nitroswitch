"""
nitrosense_automation.py
-------------------------
Troca o "Perfil de Cenário" do NitroSense (Utilização diária / Jogos)
via automação de UI (pywinauto), já que a Acer não oferece API/CLI oficial.

Como funciona:
    1. Localiza a janela do NitroSense já aberta (ou abre o executável se
       não estiver rodando).
    2. Clica na aba "Cenário" no menu superior.
    3. Clica no item da lista à esquerda: "Jogos" (Boost) ou
       "Utilização diária" (Eco).
    4. Devolve o foco para a janela que estava ativa antes (para não
       atrapalhar o que você estava fazendo).

Isso é uma automação de UI real - ou seja, a janela do NitroSense
precisa existir e os textos "Jogos" / "Utilização diária" precisam bater
com os nomes exibidos na versão instalada no seu PC (no seu caso, já
confirmamos pelo print que são exatamente esses).

Requer: pip install pywinauto
"""

import logging
import subprocess
import time
import os

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("nitrosense_automation")

try:
    from pywinauto import Desktop
    from pywinauto.findwindows import ElementNotFoundError
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False
    log.warning("pywinauto não está instalado. Rode: pip install pywinauto")

# Nomes exibidos na UI do NitroSense (conforme print enviado pelo usuário).
# Se a Acer atualizar o app e mudar esses textos, ajuste aqui.
TAB_CENARIO = "Cenário"
PERFIL_ECO = "Utilização diária"
PERFIL_BOOST = "Jogos"

NITROSENSE_PATHS = [
    r"C:\Program Files\Acer\NitroSense\NitroSense.exe",
    r"C:\Program Files (x86)\Acer\NitroSense\NitroSense.exe",
]

# Título da janela pode variar um pouco (ex: "NitroSense", "Acer NitroSense").
# Usamos regex para casar qualquer variação que contenha "NitroSense".
WINDOW_TITLE_RE = ".*NitroSense.*"


def _find_nitrosense_window(timeout: float = 5.0):
    """Tenta localizar a janela do NitroSense já aberta. Retorna None se não achar."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            windows = Desktop(backend="uia").windows(title_re=WINDOW_TITLE_RE)
            if windows:
                return windows[0]
        except ElementNotFoundError:
            pass
        time.sleep(0.3)
    return None


def _launch_nitrosense() -> bool:
    for path in NITROSENSE_PATHS:
        if os.path.exists(path):
            subprocess.Popen([path])
            log.info(f"NitroSense iniciado a partir de {path}.")
            return True
    log.error("Não encontrei o executável do NitroSense nos caminhos padrão.")
    return False


def set_scenario_profile(mode: str) -> bool:
    """
    mode: "eco" ou "boost".
    Retorna True se conseguiu clicar no perfil correspondente.
    """
    if not PYWINAUTO_AVAILABLE:
        log.error("pywinauto ausente - não é possível automatizar o NitroSense.")
        return False

    assert mode in ("eco", "boost")
    target_profile = PERFIL_ECO if mode == "eco" else PERFIL_BOOST

    window = _find_nitrosense_window(timeout=1.0)
    if window is None:
        log.info("NitroSense não estava aberto. Iniciando...")
        if not _launch_nitrosense():
            return False
        window = _find_nitrosense_window(timeout=8.0)
        if window is None:
            log.error("NitroSense não abriu a tempo.")
            return False

    try:
        # Guarda a janela ativa antes de mexer, para devolver o foco depois
        try:
            previously_active = Desktop(backend="uia").top_window()
        except Exception:
            previously_active = None

        window.set_focus()
        time.sleep(0.3)

        # Clica na aba "Cenário" no menu superior
        cenario_tab = window.child_window(title=TAB_CENARIO, control_type="TabItem")
        if not cenario_tab.exists(timeout=2):
            # Alguns builds do NitroSense usam control_type diferente (Text/Button)
            cenario_tab = window.child_window(title=TAB_CENARIO)
        cenario_tab.click_input()
        time.sleep(0.5)

        # Clica no item de perfil desejado na lista da esquerda
        profile_item = window.child_window(title=target_profile, control_type="ListItem")
        if not profile_item.exists(timeout=2):
            profile_item = window.child_window(title=target_profile)
        profile_item.click_input()
        time.sleep(0.3)

        log.info(f"NitroSense: perfil '{target_profile}' aplicado com sucesso.")

        # Devolve o foco para a janela anterior, se conseguimos identificá-la
        if previously_active is not None:
            try:
                previously_active.set_focus()
            except Exception:
                pass

        return True

    except Exception as e:
        log.error(f"Falha ao automatizar o NitroSense: {e}")
        return False
