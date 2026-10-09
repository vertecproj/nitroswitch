"""
power_modes.py
--------------
Camada de execução dos modos ECO e BOOST.

Responsabilidades:
    1. Trocar a taxa de atualização do monitor (Hz) via User32.dll (ChangeDisplaySettingsEx)
    2. Trocar o plano de energia do Windows via powercfg / PowerSetActiveScheme
    3. Tentar acionar o perfil do Acer NitroSense (melhor esforço - ver aviso abaixo)

IMPORTANTE sobre o perfil térmico (atualizado 2026-09-15):
    O perfil térmico (a curva de ventoinha) é controlado pela classe WMI
    `AcerGamingFunction`, em `root\\wmi`. Ela vem do BIOS/ACPI, exposta pelo
    driver nativo do Windows (`ACPI\\PNP0C14`) - **não** depende do app
    NitroSense estar instalado (verificado com ele desinstalado: os 3 modos
    continuam funcionando). Ver seção 3 para encoding, valores aceitos por
    este modelo e as armadilhas encontradas.

    EXIGE o processo rodando como Administrador - sem elevação a classe nem
    abre. Por isso o `.exe` é empacotado com `--uac-admin`.
"""

import ctypes
import ctypes.wintypes as wintypes
import subprocess
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("power_modes")

# =============================================================================
# 1. TROCA DE REFRESH RATE (Hz) - via User32.dll
# =============================================================================

# Estruturas necessárias para ChangeDisplaySettingsEx
DM_DISPLAYFREQUENCY = 0x400000
ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x01
DISP_CHANGE_SUCCESSFUL = 0


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def refresh_rates(device_name: str | None = None) -> tuple[int, list[int]]:
    """(taxa atual, taxas que a tela aceita na resolução atual). Cada notebook
    tem as suas (60 / 90 / 120 / 144 / 165 / 240...): nada aqui é fixo."""
    user32 = ctypes.windll.user32
    cur = DEVMODE()
    cur.dmSize = ctypes.sizeof(DEVMODE)
    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(cur)):
        return 0, []
    rates, i, dm = set(), 0, DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    while user32.EnumDisplaySettingsW(device_name, i, ctypes.byref(dm)):
        if (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmBitsPerPel) == (
                cur.dmPelsWidth, cur.dmPelsHeight, cur.dmBitsPerPel):
            rates.add(int(dm.dmDisplayFrequency))
        i += 1
    return int(cur.dmDisplayFrequency), sorted(r for r in rates if r > 1)


def mode_hz(mode: str) -> int | None:
    """Taxa de atualização do modo NESTA tela: boost = a maior que ela aceita;
    os outros = 60 Hz (ou a menor acima de 50, se não houver 60). None = não
    dá para saber, não mexe."""
    _cur, rates = refresh_rates()
    if not rates:
        return None
    if mode == "boost":
        return rates[-1]
    if 60 in rates:
        return 60
    usable = [r for r in rates if r >= 50]
    return usable[0] if usable else None


def set_mode_refresh_rate(mode: str) -> bool | None:
    """Aplica a taxa do modo. None = não se aplica (tela sem escolha de taxa)."""
    hz = mode_hz(mode)
    if hz is None:
        return None
    cur, rates = refresh_rates()
    if cur == hz or len(rates) < 2:
        return True  # já está: não pisca a tela à toa
    return set_refresh_rate(hz)


def set_refresh_rate(hz: int, device_name: str | None = None) -> bool:
    """
    Troca a taxa de atualização do monitor primário (ou de `device_name`, se informado)
    para `hz`. Retorna True se o Windows confirmou a troca.

    Observação: se o monitor não suportar a frequência solicitada, o Windows
    rejeita a troca (retorno != DISP_CHANGE_SUCCESSFUL) e nada acontece.
    """
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)

    # EnumDisplaySettingsW preenche o devmode com as configurações atuais,
    # depois sobrescrevemos só o campo de frequência.
    if not ctypes.windll.user32.EnumDisplaySettingsW(
        device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)
    ):
        log.error("Não foi possível ler as configurações atuais do display.")
        return False

    devmode.dmDisplayFrequency = hz
    devmode.dmFields = DM_DISPLAYFREQUENCY

    result = ctypes.windll.user32.ChangeDisplaySettingsExW(
        device_name, ctypes.byref(devmode), None, CDS_UPDATEREGISTRY, None
    )

    if result == DISP_CHANGE_SUCCESSFUL:
        log.info(f"Refresh rate alterado para {hz} Hz com sucesso.")
        return True
    else:
        log.warning(
            f"Falha ao alterar refresh rate para {hz} Hz (código {result}). "
            "O monitor pode não suportar essa taxa."
        )
        return False


# =============================================================================
# 2. TROCA DE PLANO DE ENERGIA - via powercfg (mais estável que a API C direta)
# =============================================================================

# GUIDs padrão dos planos de energia nativos do Windows.
# "Alto Desempenho" às vezes está oculto por padrão; garantimos que ele
# apareça na lista com "powercfg -duplicatescheme" antes de ativar, caso
# ainda não exista no sistema.
GUID_ECONOMIA_ENERGIA = "a1841308-3541-4fab-bc81-f71556f20b4a"   # Power saver
GUID_EQUILIBRADO = "381b4222-f694-41f0-9685-ff5bb260df2e"        # Balanced
GUID_ALTO_DESEMPENHO = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"    # High performance


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Executa um comando e retorna (returncode, stdout+stderr) com limite de tempo."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, shell=False, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return proc.returncode, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        log.error(f"Timeout ({timeout}s) ao executar: {' '.join(cmd)}")
        return 1, "TimeoutExpired"
    except Exception as e:
        log.error(f"Erro ao executar {' '.join(cmd)}: {e}")
        return 1, str(e)


def set_power_plan(guid: str) -> bool:
    """Ativa o plano de energia identificado por `guid`."""
    code, out = _run(["powercfg", "/setactive", guid])
    if code == 0:
        log.info(f"Plano de energia ativado: {guid}")
        return True
    log.warning(f"Falha ao ativar plano de energia {guid}: {out}")
    return False


# Planos PRÓPRIOS, usados só onde o plano nativo do Windows não existe (muitos
# notebooks com Modern Standby só trazem o "Equilibrado"; em alguns o fabricante
# apagou os outros). São cópias com GUID fixo, criadas na 1ª vez e removidas
# pelo desinstalador. Onde o plano nativo existe, nada disto é usado.
OWN_PLANS = {
    GUID_ECONOMIA_ENERGIA: ("4e17a05c-0001-4e53-9a00-6e6974726f01", "NitroSwitch Eco"),
    GUID_ALTO_DESEMPENHO: ("4e17a05c-0002-4e53-9a00-6e6974726f02", "NitroSwitch Boost"),
}


def activate_mode_plan(mode: str) -> tuple[bool, bool]:
    """Ativa o plano de energia do modo. Devolve (ativou, exclusivo): `exclusivo`
    diz se o plano ativo é só deste modo - sem isso os extras do Eco (brilho,
    apagar a tela) vazariam para o único plano que o usuário tem."""
    native = {"supereco": GUID_ECONOMIA_ENERGIA, "eco": GUID_ECONOMIA_ENERGIA,
              "balanced": GUID_EQUILIBRADO, "boost": GUID_ALTO_DESEMPENHO}[mode]
    if set_power_plan(native):
        return True, True
    if native not in OWN_PLANS:
        return False, False
    own, name = OWN_PLANS[native]
    if set_power_plan(own):
        return True, True
    for source in (native, GUID_EQUILIBRADO, "SCHEME_CURRENT"):
        if _run(["powercfg", "-duplicatescheme", source, own])[0] == 0:
            _run(["powercfg", "/changename", own, name])
            if set_power_plan(own):
                return True, True
    return False, False


# =============================================================================
# 2c. MODO DE ENERGIA DO WINDOWS (slider "Modo de energia" em Config > Sistema
#     > Energia e bateria) - é uma configuração SEPARADA do "plano de energia"
#     acima (powercfg /setactive troca o esquema/scheme; isto aqui troca o
#     "overlay" que fica por cima do esquema). É o toggle que o Windows chama
#     de "Economia de energia" / "Melhor desempenho" na UI moderna, e É a
#     peça que faltava para o Eco realmente "ligar a economia de energia
#     junto" - antes o app só trocava o scheme antigo, sem tocar nesse
#     overlay, então a UI de Energia do Windows continuava mostrando
#     "Equilibrado" independente do modo escolhido no NitroSwitch.
# =============================================================================
GUID_OVERLAY_ECONOMIA = "961cc777-2547-4f9d-8174-7d86181b8a7a"   # Best power efficiency
GUID_OVERLAY_EQUILIBRADO = "00000000-0000-0000-0000-000000000000"  # Balanced (sem overlay)
GUID_OVERLAY_DESEMPENHO = "ded574b5-45a0-4f42-8737-46345c09c238"  # Best performance


def set_power_overlay(guid: str) -> bool | None:
    """Ativa o "Modo de energia" (overlay) do Windows moderno. Melhor esforço:
    None = este Windows não tem a função; False = recusou (o overlay só vale
    com um plano do tipo Equilibrado ativo). Nunca quebra o resto.

    2026-10-09: `powercfg /setactiveoverlayscheme` NÃO existe (era o que este
    código chamava, sempre falhava). A função real é a exportação não
    documentada `PowerSetActiveOverlayScheme` da powrprof.dll."""
    import uuid
    try:
        fn = ctypes.windll.powrprof.PowerSetActiveOverlayScheme
    except (AttributeError, OSError):
        return None
    buf = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    code = fn(ctypes.byref(buf))
    if code == 0:
        log.info(f"Modo de energia (overlay) ativado: {guid}")
        return True
    log.info(f"Modo de energia (overlay) {guid} não aplicado (código {code}).")
    return False


# =============================================================================
# 2b. RUÍDO/CALOR - Política de Resfriamento do Sistema (100% oficial via powercfg)
# =============================================================================
# Esta é uma configuração AVANÇADA nativa do Windows (não é gambiarra nem
# engenharia reversa) - normalmente escondida em "Opções de Energia >
# Configurações avançadas > Processador de energia > Política de
# resfriamento do sistema". Ela diz ao Windows o que priorizar quando o
# processador esquenta:
#     - Ativa  (Active)  -> acelera a ventoinha primeiro, mantém clock alto
#     - Passiva (Passive) -> reduz o clock do processador primeiro,
#                             poupando a ventoinha - resultado: mais silencioso
#
# Usamos os GUIDs diretos (em vez dos aliases em texto) porque os aliases
# do powercfg podem se comportar de forma inconsistente dependendo do
# idioma do Windows - GUID funciona igual em qualquer idioma.
SUBGROUP_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
SETTING_SYSTEM_COOLING_POLICY = "94d3a615-a899-4ac5-ae2b-e4d8f634367f"
SETTING_MAX_PROCESSOR_STATE = "bc5038f7-23e0-4960-96da-33abaf5935ec"
SETTING_MIN_PROCESSOR_STATE = "893dee8e-2bef-41e0-89c6-b55d0929964c"
# "Processor power management > Processor performance boost mode" - liga/desliga o
# Turbo Boost. É o maior fator isolado de consumo/calor/ruído da CPU, então é o que
# mais pesa pra Eco ser "a economia máxima de verdade" (não só trocar o plano).
SETTING_PERF_BOOST_MODE = "be337238-0d82-4146-a960-4f3749d470c7"
# Alavancas OCULTAS do subgrupo do processador (não aparecem em `powercfg /q`,
# mas aceitam escrita normalmente - confirmado neste PC em 2026-09-15).
SETTING_MAX_FREQUENCY = "75b0ae3f-bce0-45a7-8c89-c9611c25e100"   # teto em MHz, 0 = sem teto
SETTING_EPP = "36687f9e-e3a5-4dbf-b1dc-15eb381c6863"             # 0=desempenho ... 100=eficiência
SETTING_MAX_CORES = "ea062031-0e34-4ff1-9b6d-eb1059334028"       # % de núcleos disponíveis
SETTING_MIN_CORES = "0cc5b647-c1df-4637-891a-dec35c318583"       # % mínimo (menor = estaciona mais)

# NOTA (2026-09-15, QA): os índices abaixo são os valores REAIS confirmados
# via `powercfg /q SCHEME_CURRENT SUB_PROCESSOR 94d3a615-...` neste sistema
# (após `powercfg /attributes SUB_PROCESSOR 94d3a615-... -ATTRIB_HIDE` pra
# desconder a configuração, que vem oculta por padrão). O powercfg reporta
# "Índice de Configurações Possíveis: 000 -> Passivo" e "001 -> Ativo" -
# ou seja, o índice 0 é PASSIVO e o índice 1 é ATIVO. A suposição anterior
# (0=Active, 1=Passive, baseada na convenção ACPI "de memória") estava
# TROCADA e causava o bug real: Eco aplicava índice 1 (Ativo - ventoinha
# acelerando à toa) e Boost aplicava índice 0 (Passivo - CPU sendo
# limitada), o oposto do comportamento pretendido em ambos os modos.
# Confirmado empiricamente aplicando eco/boost e lendo o valor de volta via
# powercfg antes de mexer aqui - não reverta sem reconfirmar do mesmo jeito.
COOLING_POLICY_ACTIVE = 1
COOLING_POLICY_PASSIVE = 0

PERF_BOOST_DISABLED = 0
PERF_BOOST_ENABLED = 1
PERF_BOOST_AGGRESSIVE = 2


# --- escrita em lote (2026-10-08, velocidade) ---------------------------------
# Cada ajuste do esquema de energia custava 3 processos `powercfg` (tomada,
# bateria e um /setactive para "fazer valer"): um apply_mode chegava a ~50
# processos e levava de 7 a 11 s. Agora, durante um lote, os ajustes só são
# ANOTADOS (o último valor de cada configuração vence) e gravados de uma vez,
# com UM /setactive no fim. A ordem de aplicação dos modos não muda.
_pending = None  # dict {(subgrupo, config): {"ac": v, "dc": v}} enquanto há lote aberto


def _begin_batch():
    global _pending
    _pending = {}


def _flush_batch() -> bool:
    """Grava tudo o que foi anotado e reativa o esquema uma única vez."""
    global _pending
    batch, _pending = _pending or {}, None
    ok = True
    for (subgroup, setting), vals in batch.items():
        for rail, flag in (("ac", "/setacvalueindex"), ("dc", "/setdcvalueindex")):
            if rail in vals:
                code, out = _run(["powercfg", flag, "SCHEME_CURRENT", subgroup, setting, str(vals[rail])])
                if code != 0:
                    # Várias alavancas não existem em todo notebook (gráficos
                    # alternáveis é só AMD, brilho não existe sem tela interna,
                    # etc.): quem não tem, pula. Só o teto da CPU, que todo
                    # Windows tem, conta como falha de verdade.
                    log.info(f"Ajuste {setting} ({rail}) não disponível aqui: {out.strip()}")
                    if setting == SETTING_MAX_PROCESSOR_STATE:
                        ok = False
    _run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    return ok


def _set_scheme_setting(subgroup: str, setting: str, ac=None, dc=None) -> bool:
    """Escreve uma configuração no esquema de energia ATUAL. `ac`/`dc` em
    None significam "não mexer nesse trilho". Em lote, só anota."""
    if _pending is not None:
        entry = _pending.setdefault((subgroup, setting), {})
        if ac is not None:
            entry["ac"] = ac
        if dc is not None:
            entry["dc"] = dc
        return True
    ok = True
    for rail, flag, v in (("tomada", "/setacvalueindex", ac), ("bateria", "/setdcvalueindex", dc)):
        if v is not None:
            code, out = _run(["powercfg", flag, "SCHEME_CURRENT", subgroup, setting, str(v)])
            if code != 0:
                log.warning(f"falha ao ajustar {setting} ({rail}): {out}")
                ok = False
    _run(["powercfg", "/setactive", "SCHEME_CURRENT"])  # faz valer na hora
    return ok


def _set_processor_setting(setting_guid: str, value: int) -> bool:
    """Aplica um valor de configuração do processador na tomada (AC) e na
    bateria (DC), no plano ATUALMENTE ativo (e o reativa, fora de lote)."""
    return _set_scheme_setting(SUBGROUP_PROCESSOR, setting_guid, ac=value, dc=value)


# =============================================================================
# 2d. EXTRAS DO MODO ECO - alavancas de economia fora do subgrupo do processador
# =============================================================================
# Auditoria de 2026-09-15: o plano "Economia de energia" do Windows, sozinho,
# NÃO está no máximo de economia. Estas são as folgas que sobravam nele
# (medidas com `powercfg /q` neste PC). Tudo aqui é escrito no esquema
# ATUAL, e como cada modo do NitroSwitch usa um esquema diferente
# (eco=SCHEME_MAX, balanced=SCHEME_BALANCED, boost=SCHEME_MIN), mexer aqui
# afeta SÓ o Eco - trocar de modo já restaura o comportamento normal, sem
# precisar desfazer nada.
SUB_VIDEO = "7516b95f-f776-4464-8c53-06167f40cc99"
SETTING_BRIGHTNESS = "aded5e82-b909-4619-9949-f5d71dac0bcb"
SETTING_VIDEO_OFF_AFTER = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"

SUB_DYNAMIC_GRAPHICS = "e276e160-7cb0-43c6-b20b-73f5dce39954"
SETTING_DYNAMIC_GRAPHICS = "a1662ab2-9d34-4e53-ba8b-2639b9e20857"  # 0 = forçar gráficos de baixo consumo

SUB_WIRELESS = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1"
SETTING_WIRELESS_POWER = "12bbebe6-58d6-4636-95bb-3217ef867c1a"  # 3 = economia máxima

SUB_MULTIMEDIA = "9596fb26-9850-41fd-ac3e-f7c3c00afd4b"
SETTING_VIDEO_QUALITY_BIAS = "10778347-1370-4ee0-8bbd-33bdacaade49"  # 0 = tende a economizar
SETTING_VIDEO_PLAYBACK = "34c7b99f-9a6d-4b3c-8dc7-b6693b78cef4"      # 2 = otimizar economia

# nível "eco" e nível "supereco" (brilho e tempo até apagar a tela)
ECO_BRIGHTNESS = {"eco": 40, "supereco": 25}
ECO_VIDEO_OFF_AC = {"eco": 300, "supereco": 120}


def set_eco_extras(nivel: str = "eco") -> bool:
    """Aperta as folgas de consumo que o plano Eco do Windows deixa passar.

    Só é chamada no modo eco. Cada item foi conferido antes/depois; ver a
    seção "Eco: o que o plano do Windows deixava passar" no CLAUDE.md.
    """
    resultados = [
        # brilho: vinha 100% mesmo no plano de economia
        _set_scheme_setting(SUB_VIDEO, SETTING_BRIGHTNESS, ac=ECO_BRIGHTNESS[nivel], dc=ECO_BRIGHTNESS[nivel]),
        # na tomada a tela nunca desligava
        _set_scheme_setting(SUB_VIDEO, SETTING_VIDEO_OFF_AFTER, ac=ECO_VIDEO_OFF_AC[nivel]),
        # gráficos: vinha "otimizar desempenho" na tomada (segura a GPU dedicada)
        _set_scheme_setting(SUB_DYNAMIC_GRAPHICS, SETTING_DYNAMIC_GRAPHICS, ac=0, dc=0),
        # Wi-Fi: vinha "desempenho máximo" na tomada
        _set_scheme_setting(SUB_WIRELESS, SETTING_WIRELESS_POWER, ac=3, dc=3),
        # vídeo: preferir economia em vez de qualidade/desempenho
        _set_scheme_setting(SUB_MULTIMEDIA, SETTING_VIDEO_QUALITY_BIAS, ac=0, dc=0),
        _set_scheme_setting(SUB_MULTIMEDIA, SETTING_VIDEO_PLAYBACK, ac=2, dc=2),
    ]
    return all(resultados)


# Limites de CPU por modo. "supereco" existe para quando o objetivo é durar,
# não render: teto duro de frequência + EPP no máximo de eficiência +
# estacionamento de núcleos, coisas que o modo eco normal não faz para não
# estragar o uso do dia a dia.
CPU_LIMITS = {
    #            máx%  mín%  turbo              MHz    EPP  máx núcleos  mín núcleos
    "supereco": (30,   5,    PERF_BOOST_DISABLED, 1400, 100, 50,          0),
    "eco":      (60,   5,    PERF_BOOST_DISABLED, 0,    70,  100,         100),
    "balanced": (100,  5,    PERF_BOOST_ENABLED,  0,    50,  100,         100),
    "boost":    (100,  5,    PERF_BOOST_AGGRESSIVE, 0,  0,   100,         100),
}


def set_cpu_limits(profile: str) -> bool:
    """Aplica os limites de CPU do modo no esquema de energia ATUAL."""
    assert profile in CPU_LIMITS, profile
    max_pct, min_pct, boost, mhz, epp, max_cores, min_cores = CPU_LIMITS[profile]
    return all([
        _set_processor_setting(SETTING_MAX_PROCESSOR_STATE, max_pct),
        _set_processor_setting(SETTING_MIN_PROCESSOR_STATE, min_pct),
        _set_processor_setting(SETTING_PERF_BOOST_MODE, boost),
        _set_processor_setting(SETTING_MAX_FREQUENCY, mhz),
        _set_processor_setting(SETTING_EPP, epp),
        _set_processor_setting(SETTING_MAX_CORES, max_cores),
        _set_processor_setting(SETTING_MIN_CORES, min_cores),
    ])


def set_cooling_profile(profile: str) -> bool:
    """
    profile: "eco" | "balanced" | "boost"

    eco      -> Passiva + 60% + Turbo Boost desligado (economia máxima real:
                além de poupar a ventoinha, corta o maior consumidor de
                energia/calor isolado da CPU, que é o boost de clock).
    balanced -> Passiva + 100% + Turbo Boost ligado normal (meio termo:
                prioriza reduzir o clock antes de acelerar a ventoinha - ou
                seja, não fica barulhento à toa - mas sem capar o teto de
                desempenho quando a carga realmente exige).
    boost    -> Ativa + 100% + Turbo Boost agressivo (ventoinha livre pra
                acelerar antes de qualquer coisa, máxima performance).
    """
    assert profile in ("supereco", "eco", "balanced", "boost")

    if profile in ("eco", "supereco"):
        policy, max_state, boost_mode = COOLING_POLICY_PASSIVE, 60, PERF_BOOST_DISABLED
    elif profile == "balanced":
        policy, max_state, boost_mode = COOLING_POLICY_PASSIVE, 100, PERF_BOOST_ENABLED
    else:
        policy, max_state, boost_mode = COOLING_POLICY_ACTIVE, 100, PERF_BOOST_AGGRESSIVE

    ok1 = _set_processor_setting(SETTING_SYSTEM_COOLING_POLICY, policy)
    ok2 = _set_processor_setting(SETTING_MAX_PROCESSOR_STATE, max_state)
    ok3 = _set_processor_setting(SETTING_PERF_BOOST_MODE, boost_mode)
    return ok1 and ok2 and ok3


# =============================================================================
# 3. INTEGRAÇÃO COM ACER NITROSENSE - via WMI nativo (root\wmi\AcerGamingFunction)
# =============================================================================
# Substitui a tentativa antiga via registro (não fazia efeito - o NitroSense
# moderno é um app MSIX/Store que não lê mais HKCU\SOFTWARE\Acer\NitroSense).
#
# Descoberto e CONFIRMADO nesta máquina (Acer Nitro 5, NitroSense
# ULICTekInc.NitroSenseforNotebook 5.0.1452.0):
#   - Existe uma classe WMI nativa `AcerGamingFunction` em `root\wmi`
#     (GUID da interface ACPI: 7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56, mesma
#     classe documentada por reverse-engineering de terceiros - ver fontes
#     abaixo), com métodos Set/GetGamingMiscSetting entre outros.
#   - `SetGamingMiscSetting` tem `WmiMethodId = 22` e `GetGamingMiscSetting`
#     tem `WmiMethodId = 23` (confirmado com `Get-CimClass` nesta máquina)
#     - EXATAMENTE os IDs que o driver Linux Linuwu-Sense usa para
#       `ACER_WMID_SET/GET_GAMING_MISC_SETTING_METHODID`, o que é uma
#       confirmação forte (não só documentação de terceiros) de que o
#       mapeamento de valores abaixo está correto para esta geração de
#       hardware/firmware Acer.
#   - Encoding do `gmInput` (UInt64) para SetGamingMiscSetting, reverse-
#     engineered do próprio NitroSense (decompilado com dotPeek) pelo
#     projeto Linuwu-Sense, e usado por FIELD_PREP em C:
#       bits 0-7  = índice da configuração (setting index)
#       bits 8-15 = valor a aplicar
#     Para o perfil térmico/plataforma, o índice é 0x0B, e os valores são:
#       Quiet=0x00, Balanced=0x01, Performance=0x04, Turbo=0x05, Eco=0x06
#     Logo: gmInput = 0x0B | (valor << 8) -> ex. Turbo = 0x0B | (5<<8) = 0x050B
#   - GetGamingMiscSetting espera gmInput = 0x0B (UInt32, só o índice) e
#     retorna um UInt64 com o valor atual embutido nos mesmos bits 8-15.
#
# Fontes (reverse engineering de terceiros, cruzadas para confirmar os
# valores - nenhuma delas expõe API/SDK oficial da Acer):
#   - https://github.com/0x7375646F/Linuwu-Sense (src/linuwu_sense.c,
#     WMID_gaming_set_misc_setting / ACER_WMID_MISC_SETTING_PLATFORM_PROFILE
#     / ACER_PREDATOR_V4_THERMAL_PROFILE_* - fonte primária dos valores 0x00.
#     .0x06 acima; nota do próprio projeto: obtidos decompilando o NitroSense
#     oficial com dotPeek, cruzados com o módulo de kernel acer-wmi)
#   - https://github.com/vicentetob/acer-aspire-fan-control (confirma
#     independentemente namespace root\wmi, classe AcerGamingFunction,
#     mesmo GUID 7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56, e que chamadas
#     "estáticas" (sem instância) falham com "Invalid method parameter(s)")
#   - https://github.com/RedStiff/Acer_Predator_Tool-PHN18-71 (confirma que
#     a ferramenta fala diretamente com a mesma classe AcerGamingFunction e
#     que EXIGE elevação/"Run as Administrator")
#
# ✅ CONFIRMADO EMPIRICAMENTE EM 2026-09-15 (escrita real, lida de volta):
#   Rodando ELEVADO, a escrita funciona e persiste. Valores testados um a um
#   neste modelo, com leitura de volta a cada escrita:
#       0x00 Quiet       -> status 0, lido 0x00  ACEITO
#       0x01 Balanced    -> status 0, lido 0x01  ACEITO
#       0x02, 0x03       -> status 1             REJEITADOS pelo firmware
#       0x04 Performance -> status 0, lido 0x04  ACEITO
#       0x05 Turbo       -> status 1             REJEITADO (não existe neste
#                                                modelo; é coisa de Predator)
#       0x06 Eco         -> status 0, lido 0x06  ACEITO (perfil extra, sem
#                                                botão na UI do NitroSense)
#       0x07             -> status 2             REJEITADO
#   Ou seja: gmOutput == 0 significa sucesso; != 0 significa recusa.
#
# DUAS descobertas práticas que valem ouro aqui:
#   1. O NitroSense NÃO reflete mudanças feitas por fora na UI dele (a tela
#      fica desatualizada), mas o perfil MUDA de verdade no firmware. Não use
#      a UI do NitroSense como fonte de verdade - use `get_nitrosense_profile`.
#   2. O NitroSense REIMPÕE o próprio perfil quando ELE inicia (no logon, ou
#      ao ser reaberto), sobrescrevendo o nosso. Mas NÃO fica vigiando: uma
#      vez escrito com o NitroSense já rodando, o valor persiste (verificado
#      por 90s de monitoramento contínuo). Conviver com ele é seguro - não é
#      necessário desinstalar o NitroSense.
#
# POR QUE win32com E NÃO O PACOTE `wmi`:
#   O pacote `wmi` ATÉ funciona, mas devolve os valores [out] como STRING
#   dentro de tupla (ex. `('0',)`) em vez de inteiro. A implementação
#   anterior fazia `(raw >> 8) & 0xFF` em cima disso e estourava TypeError,
#   que era engolido pelo `except Exception` e virava "NitroSense falhou" -
#   por isso parecia que a via WMI não funcionava, quando na verdade era um
#   bug de tipo do lado do Python. Com `win32com` + `InParameters` explícito
#   os valores vêm como inteiros e o contrato fica claro.

NITROSENSE_WMI_NAMESPACE = r"root\wmi"
NITROSENSE_WMI_CLASS = "AcerGamingFunction"

NITROSENSE_MISC_SETTING_PLATFORM_PROFILE = 0x0B  # índice da configuração "perfil térmico"

NITROSENSE_PROFILE_QUIET = 0x00
NITROSENSE_PROFILE_BALANCED = 0x01
NITROSENSE_PROFILE_PERFORMANCE = 0x04
NITROSENSE_PROFILE_TURBO = 0x05
NITROSENSE_PROFILE_ECO = 0x06

# Mapeamento dos 3 modos do NitroSwitch para os perfis do NitroSense.
#
# CORRIGIDO 2026-09-15 (confirmado abrindo o NitroSense de verdade, via
# computer-use, autorizado pelo usuário): a suposição original (baseada só
# no driver Linux) de que existiriam 5 perfis (Quiet/Balanced/Performance/
# Turbo/Eco) estava incompleta PARA ESTE MODELO. A UI real do NitroSense
# neste Nitro 5 (aba "Cenário", qualquer perfil - testado em "Utilização
# diária" E "Jogos") só oferece 3 botões de modo: **Silencioso, Equilibrado,
# Desempenho**. Não existe "Turbo" nem "Eco" como opção separada em nenhum
# dropdown/botão da interface - "Turbo" é recurso de linha Predator, não
# aparece neste Nitro 5. Portanto o mapeamento correto é 1:1 com os 3 botões
# reais, não com os 5 valores que o driver Linux suporta genericamente
# (alguns desses valores provavelmente nem são aceitos pelo firmware deste
# modelo específico):
#   eco      -> Silencioso/Quiet (0x00)
#   balanced -> Equilibrado/Balanced (0x01)
#   boost    -> Desempenho/Performance (0x04)
NITROSENSE_MODE_TO_PROFILE = {
    "supereco": NITROSENSE_PROFILE_QUIET,
    "eco": NITROSENSE_PROFILE_QUIET,
    "balanced": NITROSENSE_PROFILE_BALANCED,
    "boost": NITROSENSE_PROFILE_PERFORMANCE,
}

NITROSENSE_STATUS_OK = 0  # gmOutput == 0 = firmware aceitou; != 0 = recusou

# Nomes como aparecem na interface do NitroSense - usados pra mostrar o perfil
# ativo dentro do painel do NitroSwitch (a tela do NitroSense não atualiza
# sozinha, então o painel é o único lugar onde dá pra ver isso em tempo real).
NITROSENSE_PROFILE_NAMES = {
    NITROSENSE_PROFILE_QUIET: "Silencioso",
    NITROSENSE_PROFILE_BALANCED: "Equilibrado",
    NITROSENSE_PROFILE_PERFORMANCE: "Desempenho",
    NITROSENSE_PROFILE_ECO: "Eco",
}


# Último estado conhecido da interface da Acer (ver _AcerWmi.reason); None =
# ainda não testado. A interface usa isto para não mostrar "erro" num notebook
# que simplesmente não é Acer.
ACER_STATE = None


class _AcerWmi:
    """Contexto que abre a classe WMI da Acer via win32com, cuidando do
    CoInitialize/CoUninitialize.

    O CoInitialize é obrigatório porque `apply_mode()` roda numa thread de
    trabalho (ver ui.py `_apply_mode_async`), e COM precisa ser inicializado
    por thread. Sem isso a chamada falha com erro de COM não inicializado.
    """

    def __init__(self):
        self.instance = None
        self.service = None
        # "ok" | "ausente" (o notebook não tem a interface: não é Acer gamer)
        # | "sem_acesso" (tem, mas o processo não está elevado)
        self.reason = "ausente"
        self._com_ready = False

    def __enter__(self):
        global ACER_STATE
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            log.warning("pywin32 não instalado: sem acesso a WMI.")
            return self

        try:
            pythoncom.CoInitialize()
            self._com_ready = True
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            self.service = locator.ConnectServer(".", NITROSENSE_WMI_NAMESPACE)
            # ler o ESQUEMA da classe não exige elevação: se nem ele existe, o
            # notebook simplesmente não tem a interface (qualquer não-Acer)
            self.service.Get(NITROSENSE_WMI_CLASS)
        except Exception:
            ACER_STATE = self.reason
            return self
        try:
            for found in self.service.ExecQuery(f"SELECT * FROM {NITROSENSE_WMI_CLASS}"):
                self.instance = found
                break
            self.reason = "ok" if self.instance is not None else "ausente"
        except Exception as e:
            self.reason = "sem_acesso"
            log.warning(
                f"NitroSense: não foi possível abrir {NITROSENSE_WMI_CLASS}: {e}. "
                "Quase sempre isso é falta de elevação - esta classe só aceita "
                "processo rodando como Administrador."
            )
        ACER_STATE = self.reason
        return self

    def __exit__(self, *exc):
        # Soltar a referência COM ANTES do CoUninitialize, senão o Python
        # libera o IUnknown depois do apartment já ter sido finalizado e o
        # Windows reclama ("Win32 exception occurred releasing IUnknown").
        self.instance = None
        self.service = None
        if self._com_ready:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
        return False

    def call(self, method_name: str, gm_input: int) -> int | None:
        """Chama um método da classe passando gmInput, devolve gmOutput (int)
        ou None se falhar. Usa InParameters explícito (o pacote `wmi` devolvia
        os valores como string, ver nota no topo desta seção)."""
        if self.instance is None:
            return None
        try:
            method = self.instance.Methods_(method_name)
            params = method.InParameters.SpawnInstance_()
            params.Properties_("gmInput").Value = gm_input
            result = self.instance.ExecMethod_(method_name, params)
            return int(result.Properties_("gmOutput").Value)
        except Exception as e:
            log.warning(f"NitroSense: falha em {method_name}(0x{gm_input:X}): {e}")
            return None


def get_nitrosense_profile() -> int | None:
    """Lê o perfil térmico atual do firmware (0x00 Quiet, 0x01 Balanced,
    0x04 Performance, 0x06 Eco). Retorna None se não conseguir ler
    (tipicamente falta de elevação).

    ATENÇÃO: esta é a fonte de verdade, NÃO a interface do NitroSense - ela
    não atualiza quando o perfil muda por fora (ver nota no topo da seção).
    """
    with _AcerWmi() as acer:
        raw = acer.call("GetGamingMiscSetting", NITROSENSE_MISC_SETTING_PLATFORM_PROFILE)
    if raw is None:
        return None
    return (raw >> 8) & 0xFF


def set_nitrosense_profile(mode: str) -> bool:
    """
    Troca o perfil térmico do NitroSense (curva de ventoinha no firmware).

    mode: "eco", "balanced" ou "boost".

    Só existe uma via, e ela funciona (confirmada por escrita + leitura de
    volta, ver o bloco de comentários no topo desta seção): WMI nativo
    `root\\wmi\\AcerGamingFunction.SetGamingMiscSetting`, com o processo
    rodando ELEVADO. Sem elevação a classe nem abre - por isso o `.exe` é
    empacotado com manifesto de admin (`--uac-admin`, ver CLAUDE.md).

    A escrita é confirmada de duas formas: pelo `gmOutput` (0 = firmware
    aceitou) e relendo o valor de volta. Só retorna True se as duas baterem.
    """
    ok, _ = apply_nitrosense_profile(mode)
    return ok


def apply_nitrosense_profile(mode: str) -> tuple[bool, int | None]:
    """Igual a `set_nitrosense_profile`, mas devolve `(sucesso, perfil_lido)`.

    Escrita e releitura acontecem na MESMA sessão COM de propósito: abrir um
    `_AcerWmi()` para escrever, fechá-lo (CoUninitialize) e abrir outro para
    ler faz a segunda sessão falhar - o resultado era `nitrosense=True` com
    perfil `None` logo em seguida. Uma sessão só também é mais rápido.
    """
    assert mode in NITROSENSE_MODE_TO_PROFILE
    profile = NITROSENSE_MODE_TO_PROFILE[mode]
    gm_input = NITROSENSE_MISC_SETTING_PLATFORM_PROFILE | (profile << 8)

    with _AcerWmi() as acer:
        if acer.reason == "ausente":
            return None, None  # não é Acer: não se aplica, não é falha
        status = acer.call("SetGamingMiscSetting", gm_input)
        if status == NITROSENSE_STATUS_OK:
            confirm = acer.call("GetGamingMiscSetting", NITROSENSE_MISC_SETTING_PLATFORM_PROFILE)
            applied = ((confirm >> 8) & 0xFF) if confirm is not None else None
            if applied == profile:
                log.info(
                    f"NitroSense: perfil '{mode}' aplicado e confirmado "
                    f"(0x{profile:02X})."
                )
                return True, applied
            log.warning(
                f"NitroSense: firmware aceitou 0x{profile:02X} mas a leitura de "
                f"volta deu {applied!r} - não confirmado."
            )
            return False, applied
        if status is not None:
            log.warning(
                f"NitroSense: firmware RECUSOU o perfil 0x{profile:02X} "
                f"(gmOutput={status}). Esse valor não é suportado neste modelo."
            )
            return False, None

    # status None = não conseguiu nem falar com a classe (quase sempre: sem admin)
    log.warning(
        "NitroSense: sem acesso à interface WMI - o perfil térmico NÃO foi "
        "alterado. Rode o NitroSwitch como Administrador (o .exe empacotado "
        "já pede elevação sozinho)."
    )
    return False, None


# =============================================================================
# 4. FUNÇÃO DE ALTO NÍVEL - aplica um modo completo
# =============================================================================

_APPLY_LOCK = threading.Lock()  # nunca duas trocas de modo ao mesmo tempo


def apply_mode(mode: str) -> dict:
    """
    Aplica um dos 3 modos: "eco", "balanced" ou "boost".

    - eco:      60Hz + plano Economia de Energia + modo de energia "Economia"
                (overlay) + resfriamento Passivo/60%/Turbo desligado
    - balanced: 60Hz (ver nota abaixo) + plano Equilibrado nativo + modo de
                energia "Equilibrado" (overlay) + resfriamento Passivo/100%/
                Turbo normal (meio-termo: não deixa a ventoinha acelerar à
                toa, mas não capa o teto de desempenho)
    - boost:    165Hz (ou o máximo suportado) + plano Alto Desempenho + modo
                de energia "Melhor desempenho" (overlay) + resfriamento
                Ativo/100%/Turbo agressivo

    2026-09-15: o modo "balanced" passou a forçar 60Hz (antes deliberadamente
    não mexia no Hz - ver histórico no CLAUDE.md). Pedido explícito: o
    Equilibrado precisa ser silencioso/meio-termo de verdade, e um refresh
    rate alto (120/144/165Hz) sozinho já teria custo de energia/calor
    perceptível mesmo com CPU limitada, então força 60Hz igual ao Eco -
    quem quer o painel em alta taxa usa o Boost.

    NitroSense: ver `set_nitrosense_profile` - controlado via WMI
    (`root\\wmi` classe `AcerGamingFunction`) quando disponível; melhor
    esforço, pode retornar False se o provider exigir elevação (ver
    CLAUDE.md, seção "NitroSense").
    """
    assert mode in ("supereco", "eco", "balanced", "boost")
    with _APPLY_LOCK:
        return _apply_mode_locked(mode)


def _apply_mode_locked(mode: str) -> dict:
    # Em cada resultado: True = feito, False = falhou, None = não se aplica a
    # este notebook (ex.: perfil térmico fora de um Acer) - None não é erro.
    results = {}

    # Em paralelo com a cadeia de powercfg (são coisas independentes):
    # perfil térmico do NitroSense (WMI/COM, thread própria) e taxa de
    # atualização da tela. Antes tudo rodava em fila (7-11 s por troca).
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_nitro = pool.submit(apply_nitrosense_profile, mode)
        f_hz = pool.submit(set_mode_refresh_rate, mode)

        overlay = {"supereco": GUID_OVERLAY_ECONOMIA, "eco": GUID_OVERLAY_ECONOMIA,
                   "balanced": GUID_OVERLAY_EQUILIBRADO, "boost": GUID_OVERLAY_DESEMPENHO}[mode]
        results["power_plan"], exclusive = activate_mode_plan(mode)
        results["power_overlay"] = set_power_overlay(overlay)

        # ajustes do esquema ATIVO anotados em lote e gravados de uma vez
        _begin_batch()
        try:
            set_cooling_profile(mode)
            set_cpu_limits(mode)
            if mode in ("supereco", "eco") and exclusive:
                set_eco_extras(mode)
        finally:
            results["power_settings"] = _flush_batch()

        results["refresh_rate"] = f_hz.result()
        results["nitrosense"], results["nitrosense_profile"] = f_nitro.result()

    return results