# NitroSwitch

App de bandeja para Windows que alterna rapidamente entre os modos **Eco**,
**Equilibrado** e **Boost** no Acer Nitro 5: refresh rate do monitor, plano
de energia do Windows (incluindo o "Modo de energia" moderno), política de
resfriamento do processador e perfil do Acer NitroSense.

## Instalar

Baixe o **NitroSwitch-Setup.exe** em
[Releases](https://github.com/vertecproj/nitroswitch/releases/latest) e clique
em INSTALAR. O Windows pede a permissão de administrador uma única vez.

## Funciona em qualquer notebook (Windows 10 e 11, 64 bits)

| recurso | onde funciona |
|---|---|
| Modos de energia (plano, limites de CPU, Turbo, economia) | todos |
| Taxa de atualização da tela | todos: Boost usa a maior que a tela aceita, os outros 60 Hz |
| Perfil térmico e controle de ventoinha | Acer Nitro/Predator (interface do BIOS); nos demais a ventoinha fica com o firmware |
| Temperatura ao vivo | Acer; nos demais, quando o notebook tem sensor ACPI |

O que o notebook não tem simplesmente não aparece como erro.

## Atualizações

O app procura versões novas sozinho, baixa em segundo plano e confere o
arquivo. Quando está pronto, aparece o botão **ATUALIZAR** no topo do painel
(e no menu do ícone da bandeja): um clique e o app reabre na versão nova.

Para publicar uma versão (quem desenvolve): mude `__version__` em
`version.py`, faça commit e envie a tag `vX.Y.Z`. O GitHub Actions gera o
instalador e cria o Release.

## Por que Python?

Para este caso — interface simples, trocas via API do Windows/CLI, ícone de
bandeja — **Python com CustomTkinter + pystray** é mais rápido de
desenvolver e manter do que C#/WPF, e o resultado empacotado com PyInstaller
funciona como um `.exe` normal, sem precisar do usuário ter Python
instalado.

## ⚠️ O app precisa rodar como Administrador

Desde que o controle do NitroSense passou a usar a interface WMI nativa da
Acer (`root\wmi\AcerGamingFunction`), o `.exe` gerado pede elevação (UAC)
automaticamente ao abrir — sem isso, o Windows bloqueia o acesso a essa
classe WMI. O Windows vai mostrar o prompt de UAC normal na primeira
abertura (e sempre que abrir de novo); isso é esperado, não é bug.

## Perfil térmico (curva de ventoinha)

A Acer não publica API oficial, mas o NitroSwitch controla o perfil térmico
pela interface WMI nativa que o BIOS expõe (`root\wmi\AcerGamingFunction`).
Isso **funciona e foi confirmado**: cada troca de modo escreve o perfil e
relê do firmware para conferir.

**Não depende do app NitroSense estar instalado** — a interface vem do
BIOS/ACPI, não do software da Acer. (Nesta máquina o NitroSense foi
desinstalado e os 3 modos continuam funcionando normalmente.)

| modo no NitroSwitch | perfil térmico aplicado |
|---|---|
| Eco | Silencioso |
| Equilibrado | Equilibrado |
| Boost | Desempenho |

(O firmware deste modelo também aceita um perfil "Eco" extra, que o app não
usa, e **recusa** "Turbo" — Turbo é recurso de linha Predator.)

**Como conferir que está funcionando:** o painel do NitroSwitch mostra o
perfil ativo, lido direto do firmware, logo abaixo do modo:
`NitroSense: Desempenho`. Se aparecer *"sem acesso (requer admin)"*, é
porque o app não está elevado — nesse caso essa parte não funciona.

### Se você tiver o app NitroSense instalado

Duas coisas que vão parecer bug mas não são:

1. **A tela do NitroSense nunca mostra o que o NitroSwitch fez** — nem na
   hora, nem reabrindo o app. Ele não foi feito para perceber mudanças
   vindas de fora. Use o indicador do painel do NitroSwitch (ou observe o
   clock/ruído) para conferir.
2. **Ao iniciar, o NitroSense reimpõe o perfil dele**, sobrescrevendo o do
   NitroSwitch. Depois disso ele não mexe mais — o valor que o NitroSwitch
   escreve fica até o NitroSense ser reiniciado (ou o PC religado).

Dá para conviver com ele; se o item 2 incomodar, deixe o modo do cenário
ativo do NitroSense igual ao modo padrão do NitroSwitch — ou desinstale o
NitroSense (o controle do NitroSwitch não depende dele, e ele é app da
Microsoft Store, reinstalável a qualquer momento).

Se o app não estiver elevado, só essa parte falha (o Windows bloqueia o
acesso à classe WMI) e ele abre o NitroSense para ajuste manual. Hz, plano
de energia e resfriamento continuam funcionando normalmente.

## Modo Super Eco

Para quando o objetivo é **durar**, não render. Além de tudo que o Eco faz,
ele aperta o processador de verdade:

| | Eco | Super Eco |
|---|---|---|
| Estado máximo da CPU | 60% | **30%** |
| Teto de frequência | sem teto | **1,4 GHz** |
| Preferência de eficiência (EPP) | 70 | **100 (máximo)** |
| Estacionamento de núcleos | não | **até metade dos núcleos** |
| Brilho da tela | 40% | **25%** |
| Desligar a tela | 5 min | **2 min** |

Medido neste notebook, com todos os núcleos sob carga total: **Boost
1878 MHz × Super Eco 1056 MHz** de clock real. Não é cosmético — a CPU
realmente fica presa lá embaixo.

Esperado no Super Eco: o PC fica **visivelmente mais lento** para tarefas
pesadas. É o preço do ganho de autonomia. Para uso normal, prefira o Eco.

## O que o modo Eco faz

Além de 60Hz, plano "Economia de energia", CPU limitada a 60% com Turbo
Boost desligado e resfriamento passivo, o Eco aperta o que o plano do
Windows deixa frouxo:

- **brilho da tela em 40%** (vinha 100%, mesmo no plano de economia — é a
  maior economia real num notebook);
- desliga o monitor após 5 min na tomada (vinha "nunca");
- força gráficos de baixo consumo (segura a GPU dedicada);
- Wi-Fi em economia máxima;
- reprodução de vídeo priorizando economia.

Tudo isso vale **só no Eco**: cada modo usa um plano de energia diferente,
então voltar para Equilibrado ou Boost restaura o normal automaticamente —
inclusive o brilho.

## Ventoinha (aba "Ventoinha" no painel)

Cada modo do app (Eco, Equilibrado, Boost) guarda seu próprio modo de
ventoinha. Ex.: ficar no **Boost** com a ventoinha **Equilibrada** limita a
rotação a 70% sem abrir mão do desempenho.

| modo de ventoinha | o que faz |
|---|---|
| Automática | o firmware decide (padrão, o mais seguro) |
| Silenciosa | limita a 50% — uso leve (navegar, vídeo) |
| Equilibrada | limita a 70% |
| Máxima | 100% fixo |
| Personalizada | você escolhe o limite, de 40% a 100% |

A aba mostra ao vivo temperatura e RPM de CPU e GPU.

**Travas de segurança (automáticas, não dá para desligar):**
- até 55 °C a ventoinha fica com o firmware (pode até parar);
- nunca menos de 40% quando o app está no controle;
- a partir de **85 °C o limite é ignorado** e ela vai a 100%;
- a partir de **95 °C**, ou se a leitura de temperatura falhar, o controle
  volta ao firmware;
- ao fechar o app, a ventoinha volta ao automático.

Recomendação: em jogos e renderização, não limite abaixo de 70%. Precisa
rodar como Administrador (o `.exe` já pede).

## Tecla de atalho

A tecla dedicada que abria o NitroSense pode abrir/fechar o painel. Na aba
**Modos**, clique em **Aprender tecla de atalho** e aperte a tecla em até 12
segundos. Serve qualquer tecla, se preferir outra.

---

## 1. Instalar dependências

Requer **Python 3.10+** instalado no Windows (marque "Add to PATH" no
instalador, em https://www.python.org/downloads/).

Abra o PowerShell na pasta do projeto:

```powershell
cd caminho\para\NitroSwitch
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Rodar em modo desenvolvimento

```powershell
python main.py
```

A janela deve aparecer encostada na borda direita da tela. Clique em um dos
3 modos (Eco / Equilibrado / Boost) para testar. Feche a janela (✕) para
minimizar para a bandeja — o app continua rodando, acessível pelo ícone
perto do relógio (pode estar nos "ícones ocultos", clique na setinha ^).

**Refresh rate e plano de energia** (`ChangeDisplaySettingsEx`,
`powercfg /setactive`) não exigem admin. **O controle do NitroSense via WMI
exige Administrador** — rodando `python main.py` direto (sem admin), esse
pedaço especificamente vai falhar e cair no fallback (ver aviso acima); pra
testar o NitroSense de verdade, abra um PowerShell/terminal como
Administrador antes de rodar `python main.py`.

## 3. Gerar o executável (.exe)

Com o venv ativado:

```powershell
pyinstaller --noconfirm --onefile --windowed --name "NitroSwitch" --add-data "assets;assets" --uac-admin --hidden-import win32com --hidden-import win32com.client --hidden-import pythoncom main.py
```

- `--windowed`: não abre janela de console (é um app de bandeja).
- `--onefile`: gera um único `.exe` em `dist\NitroSwitch.exe`.
- `--add-data "assets;assets"`: **obrigatório** — sem isso a fonte Rajdhani
  usada no painel não vai pro `.exe` e os títulos caem pra fonte padrão do
  Windows, silenciosamente (sem erro nenhum).
- `--uac-admin` + os 3 `--hidden-import`: necessários pro NitroSense via
  WMI (ver aviso acima) e pro "Iniciar com o Windows" funcionar (ambos
  exigem o `.exe` rodando elevado). **Não tire essas flags do comando** —
  um `NitroSwitch.spec` com essas opções chegou a existir, mas rodar o
  `pyinstaller` apontando pro `.py` (não pro `.spec`) sempre regenera um
  `.spec` novo a partir das flags de CLI, sobrescrevendo silenciosamente
  qualquer edição manual feita nele. As flags no comando são a fonte da
  verdade, não o `.spec`.

Se quiser um ícone customizado no `.exe`, adicione `--icon assets\icon.ico`
(crie um `.ico` de 256x256 e coloque em `assets/`).

O executável final fica em:

```
dist\NitroSwitch.exe
```

Copie esse arquivo para onde quiser (ex: `C:\Program Files\NitroSwitch\`
ou uma pasta pessoal).

## 4. Gerar um instalador (.exe de instalação)

Depois de ter `dist\NitroSwitch.exe` (passo 3):

1. Garanta que a pasta `dist\NitroSwitch.exe` existe (passo 3).
2. Dê duplo clique em `install.bat` (na mesma pasta do projeto).
3. Ele copia o app para `%LOCALAPPDATA%\NitroSwitch`, cria atalho no
   Menu Iniciar, pergunta se você quer iniciar com o Windows (cria uma
   tarefa no Agendador de Tarefas — ver seção 5), e abre o app.
4. Para desinstalar, rode `uninstall.bat`.

> Não há instalador via Inno Setup neste projeto (nenhum `.iss` incluído) —
> só a via `install.bat`/`setup.bat` acima. Se algum dia isso mudar,
> atualize esta seção.

## 5. Iniciar automaticamente com o Windows

**Pelo próprio app (único jeito suportado):** abra o popup e ative o switch
"Iniciar com o Windows". Isso cria uma tarefa no **Agendador de Tarefas**
do Windows (`schtasks`, gatilho "ao fazer logon", executando com
privilégios mais altos), apontando para o `.exe` gerado com `--tray`.

> Por que Agendador de Tarefas e não a pasta Startup/chave `Run`? O `.exe`
> roda elevado (`--uac-admin`, necessário pro NitroSense via WMI). Um atalho
> na pasta Startup ou uma entrada na chave `Run` apontando pra um `.exe`
> com manifesto de admin faria o Windows mostrar um prompt de UAC visível
> toda vez que você logasse — quebraria a ideia de iniciar em segundo
> plano. A tarefa agendada com "privilégios mais altos" eleva sem mostrar
> esse prompt.
>
> **Atenção**: criar essa tarefa exige que quem está criando já esteja
> elevado. Isso funciona sem problema dentro do próprio app (que já roda
> elevado). Mas se você ligar o switch durante o `install.bat` (que roda
> de propósito sem admin), a criação da tarefa vai falhar — nesse caso
> abra o NitroSwitch depois, aceite o prompt do Windows, e ligue o switch
> de dentro do app.

## 6. Estrutura do projeto

```
NitroSwitch/
├── main.py             # ponto de entrada
├── ui.py               # popup CustomTkinter (canto inferior direito)
├── tray.py             # ícone de bandeja + menu de contexto (pystray)
├── power_modes.py      # Hz, plano de energia, NitroSense (lógica principal)
├── startup.py          # liga/desliga inicialização automática (Agendador de Tarefas)
├── install.bat          # instalador simples via .bat
├── uninstall.bat         # desinstalador correspondente ao install.bat
├── setup.bat             # cria venv + instala deps + builda + instala, tudo em 1 clique
├── requirements.txt
└── README.md
```

## 7. Troubleshooting

- **"Falha ao alterar refresh rate"**: seu monitor/driver pode não suportar
  165Hz na resolução atual, ou o `device_name` não é o monitor certo em
  setups com múltiplos monitores. Rode `python -c "import ctypes; ..."` para
  depurar, ou me avise para eu adaptar a função a multi-monitor.
- **Plano "Alto Desempenho" não aparece**: o app já roda
  `powercfg -duplicatescheme` automaticamente para desbloquear o plano
  oculto antes de ativá-lo.
- **NitroSense não muda**: se a *tela* dele não muda, isso é esperado (ver
  seção "Sobre o NitroSense") — o perfil mudou mesmo assim. Se o perfil de
  verdade não mudou, quase sempre é porque o app não está rodando elevado.
  Vale conferir também o nível da bateria: abaixo de 40% o firmware da Acer
  bloqueia troca de perfil, inclusive pela interface oficial do NitroSense.
- **Antivírus reclama do .exe gerado pelo PyInstaller**: é um falso
  positivo comum (heurística de "unfamiliar binary"). Assine o executável
  ou adicione exceção, ou distribua rodando `python main.py` direto em vez
  de gerar `.exe`.
