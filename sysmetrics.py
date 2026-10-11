"""
sysmetrics.py
-------------
Métricas ao vivo do PC (uso de CPU e memória) só com ctypes - sem psutil, para
não pesar o app nem o tempo de abertura. A temperatura e a rotação das
ventoinhas vêm do fan_control (WMI da Acer), não daqui.
"""

import ctypes
from ctypes import wintypes


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


_k32 = ctypes.windll.kernel32


def device_name(max_len=16):
    """Modelo do notebook como o BIOS informa (ex.: "Nitro ANV15-41"), para o
    cabeçalho. Vazio se o fabricante não preencheu."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
            name = str(winreg.QueryValueEx(k, "SystemProductName")[0]).strip()
    except OSError:
        return ""
    if name.lower() in ("", "system product name", "to be filled by o.e.m.", "default string"):
        return ""
    return name[:max_len].strip().upper()


def _ft(f):
    return (f.hi << 32) | f.lo


_prev = None


def cpu_percent():
    """Uso total da CPU (0-100) desde a última chamada. Na 1ª chamada devolve 0."""
    global _prev
    idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return 0.0
    cur = (_ft(idle), _ft(kern), _ft(user))
    if _prev is None:
        _prev = cur
        return 0.0
    d_idle, d_kern, d_user = (cur[i] - _prev[i] for i in range(3))
    _prev = cur
    total = d_kern + d_user  # o tempo de "kernel" já inclui o ocioso
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (total - d_idle) * 100.0 / total))


def memory():
    """(usada_GB, total_GB, percentual)."""
    m = _MEMSTATUS()
    m.dwLength = ctypes.sizeof(_MEMSTATUS)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return 0.0, 0.0, 0
    total = m.ullTotalPhys / 1024 ** 3
    used = (m.ullTotalPhys - m.ullAvailPhys) / 1024 ** 3
    return used, total, int(m.dwMemoryLoad)


class _POWERSTATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class _BATTERYSTATE(ctypes.Structure):
    _fields_ = [("AcOnLine", ctypes.c_ubyte), ("BatteryPresent", ctypes.c_ubyte),
                ("Charging", ctypes.c_ubyte), ("Discharging", ctypes.c_ubyte),
                ("Spare", ctypes.c_ubyte * 3), ("Tag", ctypes.c_ubyte),
                ("MaxCapacity", wintypes.ULONG), ("RemainingCapacity", wintypes.ULONG),
                ("Rate", wintypes.LONG), ("EstimatedTime", wintypes.ULONG),
                ("DefaultAlert1", wintypes.ULONG), ("DefaultAlert2", wintypes.ULONG)]


def battery():
    """(percentual, na_tomada, minutos) ou None se não há bateria. `minutos` =
    tempo restante na bateria, ou até carregar na tomada; None = ainda sem
    estimativa. O Windows demora a estimar depois de desplugar, então quando ele
    não sabe a conta é feita aqui: carga restante / consumo atual."""
    ps = _POWERSTATUS()
    if not _k32.GetSystemPowerStatus(ctypes.byref(ps)) or ps.BatteryFlag & 128 or ps.BatteryLifePercent > 100:
        return None
    plugged = ps.ACLineStatus == 1
    minutes = None
    if not plugged and ps.BatteryLifeTime != 0xFFFFFFFF:
        minutes = ps.BatteryLifeTime // 60
    else:
        st = _BATTERYSTATE()
        try:
            ok = ctypes.windll.powrprof.CallNtPowerInformation(5, None, 0, ctypes.byref(st), ctypes.sizeof(st)) == 0
        except (AttributeError, OSError):
            ok = False
        if ok and st.Rate and st.MaxCapacity:
            if not plugged and st.Rate < 0:
                minutes = st.RemainingCapacity * 60 // -st.Rate
            elif plugged and st.Rate > 0 and st.MaxCapacity > st.RemainingCapacity:
                minutes = (st.MaxCapacity - st.RemainingCapacity) * 60 // st.Rate
    if minutes is not None and not 0 < minutes < 48 * 60:
        minutes = None
    return int(ps.BatteryLifePercent), plugged, minutes


def battery_text():
    """Linha pronta para o painel; vazia em PC sem bateria."""
    b = battery()
    if b is None:
        return ""
    pct, plugged, minutes = b
    tempo = f"{minutes // 60}h{minutes % 60:02d}" if minutes is not None else None
    if plugged:
        return f"BATERIA {pct}% · " + (f"carrega em {tempo}" if tempo else "na tomada")
    return f"BATERIA {pct}% · " + (f"{tempo} restantes" if tempo else "calculando…")
