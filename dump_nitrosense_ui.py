"""
dump_nitrosense_ui.py
----------------------
Script de DIAGNÓSTICO (não faz parte do app final). Serve só para
descobrirmos os nomes/identificadores REAIS dos controles da janela do
NitroSense no seu PC, em vez de eu ficar chutando.

COMO USAR:
    1. Abra o NitroSense e deixe ele na tela "Cenário" (Gestor de Cenário),
       igual ao print que você me mandou.
    2. Com o venv ativado, rode:
         pip install pywinauto
         python dump_nitrosense_ui.py
    3. Ele vai gerar um arquivo "nitrosense_ui_dump.txt" na mesma pasta.
    4. Me envie esse arquivo (ou cole o conteúdo aqui no chat).

Com esse dump eu vejo o nome exato da aba "Cenário", dos itens "Jogos" /
"Utilização diária" na lista, e o "control_type" certo de cada um -
daí a automação funciona de primeira, sem tentativa e erro.
"""

import io
import sys

try:
    from pywinauto import Desktop
except ImportError:
    print("ERRO: pywinauto não está instalado. Rode: pip install pywinauto")
    sys.exit(1)

WINDOW_TITLE_RE = ".*NitroSense.*"
OUTPUT_FILE = "nitrosense_ui_dump.txt"


def main():
    print("Procurando a janela do NitroSense (ela precisa estar aberta)...")

    # Desktop(...).window(...) retorna uma WindowSpecification, que tem o
    # método print_control_identifiers(). Desktop(...).windows()[0] retorna
    # um wrapper "cru" que não tem esse método - por isso o ajuste aqui.
    window = Desktop(backend="uia").window(title_re=WINDOW_TITLE_RE)

    if not window.exists(timeout=3):
        print(
            "Não encontrei nenhuma janela com 'NitroSense' no título.\n"
            "Confirme que o NitroSense está aberto e visível (não minimizado)."
        )
        sys.exit(1)

    print(f"Encontrada: '{window.window_text()}'. Gerando o dump...")

    # Captura a saída de print_control_identifiers() num buffer, porque
    # por padrão ela só imprime no console.
    buffer = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buffer
    try:
        window.print_control_identifiers(depth=12)
    finally:
        sys.stdout = old_stdout

    dump_text = buffer.getvalue()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(dump_text)

    print(f"Pronto! Dump salvo em: {OUTPUT_FILE}")
    print("Me envie o conteúdo desse arquivo para eu ajustar a automação.")


if __name__ == "__main__":
    main()
