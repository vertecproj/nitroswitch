"""
fan_control.py
--------------
Controle de ventoinha por cima da interface WMI da Acer (ver power_modes.py,
seção 3, classe `_AcerWmi`).

O firmware só oferece duas coisas: "automático" (ele decide pela temperatura
e pelo perfil térmico) ou "velocidade FIXA em %". Não existe "automático com
teto". Então o "limitar a rotação máxima" é feito AQUI, em software: uma
thread lê a temperatura a cada `TICK_S` segundos e ajusta a velocidade dentro
do teto escolhido.

Consequência que dita todo o desenho abaixo: com a ventoinha em % fixo o
firmware NÃO reage sozinho a aquecimento. Quem reage é esta thread. Por isso
as travas de segurança não são opcionais.

Tudo aqui foi confirmado medindo RPM real neste Nitro 5 (2026-09-15):
    automático ~2000 rpm | manual 50% ~3500 rpm | 75% ~5200 | 100% ~6900
"""

import logging
import threading
import time

import power_modes

log = logging.getLogger("fan_control")

# --- interface do firmware (confirmado empiricamente) -----------------------
FAN_CPU = 0x01
FAN_GPU = 0x04  # único outro id aceito; não deu p/ medir RPM com a GPU dedicada desligada
BEHAVIOR_AUTO = 0x0101010101010101    # valor empacotado: modo 1 em todos os slots
BEHAVIOR_MANUAL = 0x0303030303030303  # modo 3 (manual). Modo 2 é recusado neste modelo.

SENSOR_CMD = 0x01
SENSOR_CPU_TEMP = 0x01
SENSOR_CPU_FAN = 0x02
SENSOR_GPU_FAN = 0x06
SENSOR_GPU_TEMP = 0x0A  # devolve 0 quando a GPU dedicada está desligada

# --- modos de ventoinha ------------------------------------------------------
# valor = teto de rotação em %. None = firmware decide. "maxima" = 100% fixo.
FAN_MODES = {
    "auto": None,
    "silenciosa": 50,
    "equilibrada": 70,
    "maxima": 100,
    "personalizada": None,  # teto vem de `custom_cap`
}
FAN_MODE_LABELS = {
    "auto": "Automática",
    "silenciosa": "Silenciosa",
    "equilibrada": "Equilibrada",
    "maxima": "Máxima",
    "personalizada": "Personalizada",
}

# --- travas de segurança -----------------------------------------------------
MIN_MANUAL_PERCENT = 40   # nunca abaixo disso em manual (~2700 rpm medidos)
MIN_CUSTOM_CAP = 40
KNEE_TEMP = 55            # abaixo: devolve ao firmware (pode até parar a ventoinha, silêncio total)
HYSTERESIS = 4            # evita ficar alternando auto/manual em volta do joelho
CAP_REACHED_TEMP = 80     # temperatura em que a curva chega no teto
OVERRIDE_TEMP = 85        # a partir daqui IGNORA o teto: 100%
EMERGENCY_TEMP = 95       # desiste do controle e devolve ao firmware
MAX_SENSOR_FAILS = 2      # sem temperatura confiável = sem controle manual
TICK_S = 2.0


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FanController:
    """Thread única que lê sensores e aplica o modo de ventoinha escolhido.

    Roda sempre (inclusive em "auto") porque também alimenta a leitura ao
    vivo de temperatura/RPM no painel. Usa UMA sessão COM para a vida toda
    da thread: abrir e fechar `_AcerWmi` em sequência na mesma thread faz a
    segunda sessão falhar (ver power_modes.apply_nitrosense_profile).
    """

    def __init__(self, on_status=None):
        self.on_status = on_status
        self._lock = threading.Lock()
        self._mode = "auto"
        self._custom_cap = 70
        self._stop = threading.Event()
        self._thread = None
        self._manual = False
        self._last_speed = None
        self._sensor_fails = 0

    # ------------------------------------------------------------------ API
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fan-control", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        """Para a thread e DEVOLVE a ventoinha ao firmware. Chame ao sair do app."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def set_mode(self, mode, custom_cap=None):
        assert mode in FAN_MODES, mode
        with self._lock:
            self._mode = mode
            if custom_cap is not None:
                self._custom_cap = int(_clamp(custom_cap, MIN_CUSTOM_CAP, 100))

    def _cap(self):
        with self._lock:
            mode, custom = self._mode, self._custom_cap
        return mode, (custom if mode == "personalizada" else FAN_MODES[mode])

    # ------------------------------------------------------------ firmware
    @staticmethod
    def _sensor(acer, sensor_id):
        raw = acer.call("GetGamingSysInfo", SENSOR_CMD | (sensor_id << 8))
        if raw is None or (raw & 0xFF) != 0:
            return None
        return (raw >> 8) & 0xFFFF

    def _to_auto(self, acer):
        if self._manual or self._last_speed is not None:
            acer.call("SetGamingFanBehavior", BEHAVIOR_AUTO)
        self._manual = False
        self._last_speed = None

    def _to_manual(self, acer, percent):
        percent = int(_clamp(percent, MIN_MANUAL_PERCENT, 100))
        if self._last_speed is None or abs(percent - self._last_speed) >= 2:
            # ordem importa: velocidade ANTES de trocar para manual, para nunca
            # passar por um instante de ventoinha num valor antigo/baixo
            acer.call("SetGamingFanSpeed", FAN_CPU | (percent << 8))
            acer.call("SetGamingFanSpeed", FAN_GPU | (percent << 8))
            self._last_speed = percent
        if not self._manual:
            acer.call("SetGamingFanBehavior", BEHAVIOR_MANUAL)
            self._manual = True
        return percent

    # ---------------------------------------------------------------- loop
    def _run(self):
        with power_modes._AcerWmi() as acer:
            if acer.instance is None:
                if acer.reason == "sem_acesso":
                    self._emit({"state": "indisponivel",
                                "warning": "Sem acesso à ventoinha (o app precisa rodar como Administrador)."})
                else:
                    self._run_generic(acer)
                return
            # o firmware pode ter ficado em % fixo de uma instância anterior que
            # morreu (kill, crash, desligamento): `_manual` nasce False, então
            # sem isto `_to_auto` nunca escreveria e a ventoinha ficava presa
            try:
                acer.call("SetGamingFanBehavior", BEHAVIOR_AUTO)
            except Exception:
                pass
            try:
                while not self._stop.is_set():
                    self._tick(acer)
                    self._stop.wait(TICK_S)
            finally:
                # saiu (stop ou exceção): nunca deixa a ventoinha em % fixo sem ninguém vigiando
                try:
                    acer.call("SetGamingFanBehavior", BEHAVIOR_AUTO)
                except Exception:
                    pass

    def _run_generic(self, acer):
        """Notebook sem a interface da Acer: a ventoinha fica com o firmware
        (não existe forma padrão de controlá-la no Windows) e a temperatura vem
        do sensor ACPI genérico, quando o notebook tem um que funcione."""
        status = {"state": "sem_controle", "mode": "auto", "cpu_temp": None, "gpu_temp": None,
                  "cpu_rpm": None, "gpu_rpm": None, "speed": None,
                  "warning": "Este notebook não expõe o controle da ventoinha."}
        while not self._stop.is_set():
            temp = None
            try:
                zones = acer.service.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
                vals = [z.CurrentTemperature / 10.0 - 273.15 for z in zones]
                vals = [v for v in vals if 1 < v < 120]
                temp = round(max(vals)) if vals else None
            except Exception:
                pass
            self._emit(dict(status, cpu_temp=temp))
            if temp is None:
                return  # sem sensor: nada a atualizar
            self._stop.wait(TICK_S)

    def _tick(self, acer):
        mode, cap = self._cap()
        cpu_t = self._sensor(acer, SENSOR_CPU_TEMP)
        gpu_t = self._sensor(acer, SENSOR_GPU_TEMP)
        cpu_rpm = self._sensor(acer, SENSOR_CPU_FAN)
        gpu_rpm = self._sensor(acer, SENSOR_GPU_FAN)

        temps = [t for t in (cpu_t, gpu_t) if t]  # 0 = GPU dedicada desligada
        status = {"mode": mode, "cap": cap, "cpu_temp": cpu_t, "gpu_temp": gpu_t or None,
                  "cpu_rpm": cpu_rpm, "gpu_rpm": gpu_rpm, "speed": None,
                  "state": "auto", "warning": None}

        if not temps:
            self._sensor_fails += 1
        else:
            self._sensor_fails = 0

        if mode == "auto":
            self._to_auto(acer)
        elif self._sensor_fails >= MAX_SENSOR_FAILS:
            self._to_auto(acer)
            status["state"] = "seguranca"
            status["warning"] = "Sem leitura de temperatura: ventoinha devolvida ao automático."
        elif temps:
            t = max(temps)
            if t >= EMERGENCY_TEMP:
                self._to_auto(acer)
                status["state"] = "seguranca"
                status["warning"] = f"{t}°C: controle devolvido ao firmware."
            elif t >= OVERRIDE_TEMP:
                status["speed"] = self._to_manual(acer, 100)
                status["state"] = "limite_ignorado"
                status["warning"] = f"{t}°C: limite ignorado, ventoinha no máximo."
            elif mode == "maxima":
                status["speed"] = self._to_manual(acer, 100)
                status["state"] = "manual"
            else:
                knee = KNEE_TEMP - HYSTERESIS if self._manual else KNEE_TEMP
                if t < knee:
                    self._to_auto(acer)
                else:
                    frac = (t - KNEE_TEMP) / (CAP_REACHED_TEMP - KNEE_TEMP)
                    target = MIN_MANUAL_PERCENT + frac * (cap - MIN_MANUAL_PERCENT)
                    status["speed"] = self._to_manual(acer, _clamp(target, MIN_MANUAL_PERCENT, cap))
                    status["state"] = "manual"

        self._emit(status)

    def _emit(self, status):
        if self.on_status:
            try:
                self.on_status(status)
            except Exception as e:
                log.warning(f"on_status falhou: {e}")
