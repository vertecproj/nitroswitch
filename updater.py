"""
updater.py
----------
Atualização automática pelo GitHub Releases, sem o usuário instalar nada à mão.

Em segundo plano (thread própria, nunca atrasa a abertura):
    1. consulta a última versão publicada (releases/latest do repositório);
    2. se for mais nova que a instalada, BAIXA o instalador e confere o SHA-256
       com o que o GitHub informa para o arquivo;
    3. só então avisa a interface (`on_ready`), que mostra o botão ATUALIZAR.

O clique chama `install()`: roda o instalador baixado em modo silencioso
(`/silent /update`), que fecha o app, troca os arquivos e o abre de novo.
Como o app já roda elevado, não aparece nenhum pedido de permissão.

O arquivo baixado fica em %ProgramData%\\NitroSwitch\\update, pasta que só
administradores podem alterar: o app elevado vai EXECUTAR esse arquivo, então
ele não pode estar onde um programa comum consiga trocá-lo.
"""

import ctypes
import hashlib
import json
import logging
import os
import subprocess
import threading
import urllib.request

from version import REPO, __version__

log = logging.getLogger("updater")

ASSET = "NitroSwitch-Setup.exe"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
UPDATE_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "NitroSwitch", "update")
FIRST_CHECK_S = 20          # depois de abrir (não disputa o boot)
CHECK_EVERY_S = 6 * 3600
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def parse_version(text):
    """'v2.10.1' -> (2, 10, 1). Qualquer coisa fora desse formato vira ()."""
    try:
        return tuple(int(p) for p in text.strip().lstrip("vV").split("."))
    except ValueError:
        return ()


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "NitroSwitch/" + __version__,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def latest_release():
    """(versão, url do instalador, sha256) da última versão publicada, ou None."""
    with _get(API_URL) as r:
        data = json.load(r)
    tag = data.get("tag_name", "")
    assets = {a.get("name"): a for a in data.get("assets", [])}
    asset = assets.get(ASSET)
    if not parse_version(tag) or not asset:
        return None
    url = asset.get("browser_download_url", "")
    if not url.startswith(DOWNLOAD_PREFIX):
        return None
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if not digest and ASSET + ".sha256" in assets:  # publicado pelo build, por garantia
        with _get(assets[ASSET + ".sha256"]["browser_download_url"]) as r:
            digest = r.read().decode("ascii", "ignore").split()[0]
    if len(digest) != 64:
        return None
    return tag.lstrip("vV"), url, digest.lower()


def _prepare_dir():
    os.makedirs(UPDATE_DIR, exist_ok=True)
    # só Administradores e SISTEMA escrevem; usuários comuns só leem (SIDs: valem em qualquer idioma)
    subprocess.run(["icacls", UPDATE_DIR, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"],
                   capture_output=True, creationflags=NO_WINDOW)


def download(version, url, digest):
    """Baixa o instalador (se ainda não estiver lá) e confere o hash. Devolve o
    caminho, ou None se o arquivo não bater."""
    _prepare_dir()
    path = os.path.join(UPDATE_DIR, f"NitroSwitch-Setup-{version}.exe")
    if os.path.exists(path) and _sha256(path) == digest:
        return path
    for old in os.listdir(UPDATE_DIR):
        try:
            os.remove(os.path.join(UPDATE_DIR, old))
        except OSError:
            pass
    part = path + ".part"
    h = hashlib.sha256()
    with _get(url, timeout=60) as r, open(part, "wb") as f:
        for chunk in iter(lambda: r.read(1 << 18), b""):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != digest:
        os.remove(part)
        log.warning("Atualização descartada: o arquivo baixado não confere com o publicado.")
        return None
    os.replace(part, path)
    return path


class Updater:
    """Procura, baixa e deixa pronta a atualização. `on_ready(versão)` é chamado
    (de uma thread) quando basta um clique para instalar."""

    def __init__(self, on_ready):
        self.on_ready = on_ready
        self.ready = None  # (versão, caminho, sha256)
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="updater", daemon=True).start()

    def _loop(self):
        wait = FIRST_CHECK_S
        while not self._stop.wait(wait):
            wait = CHECK_EVERY_S
            try:
                if self.check():
                    return
            except Exception as e:  # sem internet, GitHub fora do ar...: tenta de novo depois
                log.info(f"Não consegui procurar atualização: {e}")

    def check(self):
        rel = latest_release()
        if not rel or parse_version(rel[0]) <= parse_version(__version__):
            return False
        version, url, digest = rel
        path = download(version, url, digest)
        if not path:
            return False
        self.ready = (version, path, digest)
        self.on_ready(version)
        return True

    def install(self):
        """Dispara o instalador silencioso. Quem chama deve ENCERRAR o app logo
        em seguida (o instalador espera o app fechar). False = não deu."""
        if not self.ready:
            return False
        _version, path, digest = self.ready
        if _sha256(path) != digest:  # confere de novo na hora de executar
            self.ready = None
            return False
        env = dict(os.environ)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # o instalador é outro .exe PyInstaller
        env.pop("_MEIPASS2", None)
        args = [path, "/silent", "/update"]
        detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        # 1ª tentativa fora do "job" do Agendador de Tarefas (o app foi aberto
        # por uma tarefa): o instalador precisa sobreviver ao fim do app.
        for flags in (detached | 0x01000000, detached):  # CREATE_BREAKAWAY_FROM_JOB
            try:
                subprocess.Popen(args, creationflags=flags, env=env, cwd=UPDATE_DIR, close_fds=True)
                return True
            except OSError:
                continue
        # app aberto sem elevação: pede a permissão do Windows (única situação com prompt)
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", path, "/silent /update", UPDATE_DIR, 0) > 32
