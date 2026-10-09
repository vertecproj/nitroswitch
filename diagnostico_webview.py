"""
diagnostico_webview.py
------------------------
Testa se o NitroSense usa WebView2 (motor Chromium embutido) e se dá pra
ligar a porta de depuração remota (Chrome DevTools Protocol) nele.

Se funcionar, abre a porta http://localhost:9333/json com a lista de
"páginas" que o WebView2 está renderizando - daí conseguimos ler o DOM
real e simular cliques via JavaScript, o que seria MUITO mais confiável
que a automação de UI que já tentamos (que falhou por causa do WebView).

Se NÃO funcionar (a Acer pode ter desabilitado o debug em produção),
esse script simplesmente informa isso claramente e a gente desiste dessa
via sem mais rodeios.

COMO USAR:
    1. Feche o NitroSense completamente antes de rodar (Gerenciador de
       Tarefas > NitroSense > Finalizar tarefa, e também o processo de
       serviço "Acer NitroSense Service" se aparecer).
    2. Rode: python diagnostico_webview.py
    3. Ele relança o NitroSense com o debug ligado e verifica a porta.
"""

import os
import subprocess
import time
import urllib.request
import json

NITROSENSE_PATHS = [
    r"C:\Program Files\WindowsApps\ULICTekInc.NitroSenseforNotebook_5.0.1452.0_x64__nt9dgb7efx6bt\app\NitroSense.exe",
    r"C:\Program Files\Acer\NitroSense\NitroSense.exe",
    r"C:\Program Files (x86)\Acer\NitroSense\NitroSense.exe",
]

DEBUG_PORT = 9333


def find_nitrosense_exe():
    for path in NITROSENSE_PATHS:
        if os.path.exists(path):
            return path
    return None


def launch_nitrosense(exe_path: str, env: dict) -> bool:
    """
    Tenta abrir o NitroSense. Como esse app é empacotado como MSIX
    (pasta WindowsApps), a pasta normalmente é protegida contra execução
    direta por outros processos - tentamos mesmo assim (às vezes funciona
    para apps "Desktop Bridge"/Centennial), e se falhar por permissão,
    caímos pro lançamento via protocolo shell:AppsFolder do Windows.
    """
    try:
        subprocess.Popen([exe_path], env=env)
        return True
    except (PermissionError, OSError) as e:
        print(f"Lançamento direto falhou ({e}). Tentando via protocolo do Windows...")

    # Fallback: pede pro Windows abrir o app pelo Family Name do pacote.
    # O AppId exato pode variar - "App" é o mais comum, mas se não for
    # esse, o Windows normalmente ainda abre o app mesmo assim.
    family_name = "ULICTekInc.NitroSenseforNotebook_nt9dgb7efx6bt"
    try:
        os.startfile(f"shell:AppsFolder\\{family_name}!App")
        return True
    except OSError as e:
        print(f"Fallback via shell:AppsFolder também falhou: {e}")
        return False


def main():
    exe_path = find_nitrosense_exe()
    if not exe_path:
        print("ERRO: não achei o NitroSense.exe nos caminhos padrão.")
        print("Me diga o caminho certo (Propriedades do atalho > Local do arquivo).")
        return

    print(f"Executável encontrado: {exe_path}")
    print("Encerrando instâncias abertas do NitroSense (se houver)...")
    subprocess.run(["taskkill", "/IM", "NitroSense.exe", "/F"],
                    capture_output=True)
    time.sleep(1.5)

    # Variável de ambiente que o runtime WebView2 lê para habilitar o
    # protocolo de depuração remota do Chromium nessa instância.
    env = os.environ.copy()
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={DEBUG_PORT}"

    print(f"Relançando o NitroSense com debug remoto na porta {DEBUG_PORT}...")
    launched = launch_nitrosense(exe_path, env)
    if not launched:
        print("Não consegui abrir o NitroSense de nenhuma forma. Abra manualmente e me avise.")
        return

    print("Aguardando o app abrir (10s)...")
    time.sleep(10)

    url = f"http://localhost:{DEBUG_PORT}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"\n❌ NÃO deu certo. A porta de debug não respondeu ({e}).")
        print(
            "Isso indica que a Acer provavelmente desabilitou o debug remoto "
            "nessa build do NitroSense, ou o app não usa WebView2 (pode ser "
            "outra tecnologia, como CEF com nome de flag diferente, ou WinUI "
            "nativo renderizando de um jeito que o UI Automation não capturou "
            "por outro motivo)."
        )
        return

    print(f"\n✅ FUNCIONOU! A porta de debug respondeu com {len(data)} página(s):\n")
    for page in data:
        print(f"  - título: {page.get('title')}")
        print(f"    url: {page.get('url')}")
        print(f"    tipo: {page.get('type')}")
        print(f"    websocket debugger: {page.get('webSocketDebuggerUrl')}")
        print()

    print(
        "Copie e me envie essa saída completa - com isso eu consigo escrever "
        "o código que lê o DOM e simula os cliques certos via JavaScript."
    )


if __name__ == "__main__":
    main()
