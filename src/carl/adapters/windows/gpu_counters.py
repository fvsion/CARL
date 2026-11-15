"""What Windows itself knows about the GPUs, any vendor, through ctypes (no tool to start, no language-dependent
names):

  adapters()        the display adapters from D3DKMT (gdi32): each one's name, LUID, PCI address, own memory
                    (DedicatedVideoMemorySize) and shared system memory (SharedSystemMemorySize), PCI vendor and kind (software, render, hybrid
                    integrated)
  counter_values()  a GPU performance counter by its English path through PDH (pdh.dll): {instance: value}.
                    "\\GPU Adapter Memory(*)\\Dedicated Usage" is the VRAM in use per adapter (instance
                    luid_0x..._0x..._phys_0), "\\GPU Process Memory(*)\\Dedicated Usage" per process
                    (pid_N_luid_..._phys_0): nvidia-smi says [N/A] per process on Windows (WDDM).
  counter_total()   counters without instances added up, by their English paths (the standby list's three:
                    "\\Memory\\Standby Cache Core Bytes" ...; W1.6); None when one of them gives nothing.

Each call gives [] or {} when Windows does not answer, never an error. Plain values only: adapters/gpu/
windows_counters.py turns them into Gpu values (the composition wires the two, rule R3).
"""
from __future__ import annotations

import ctypes
from typing import Dict, List, Optional, Sequence, Union

Value = Union[str, int, bool]

# D3DKMT (d3dkmthk.h)
KMTQAITYPE_GETSEGMENTSIZE = 3
KMTQAITYPE_ADAPTERADDRESS = 6
KMTQAITYPE_ADAPTERREGISTRYINFO = 8
KMTQAITYPE_ADAPTERTYPE = 15
KMTQAITYPE_PHYSICALADAPTERDEVICEIDS = 31
MAX_PATH = 260
# PDH (pdh.h)
PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_LARGE = 0x00000400
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA = 0, 1


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _AdapterInfo(ctypes.Structure):          # D3DKMT_ADAPTERINFO
    _fields_ = [("hAdapter", ctypes.c_uint), ("AdapterLuid", _LUID), ("NumOfSources", ctypes.c_ulong),
                ("bPrecisePresentRegionsPreferred", ctypes.c_int32)]


class _EnumAdapters2(ctypes.Structure):        # D3DKMT_ENUMADAPTERS2
    _fields_ = [("NumAdapters", ctypes.c_ulong), ("pAdapters", ctypes.POINTER(_AdapterInfo))]


class _QueryAdapterInfo(ctypes.Structure):     # D3DKMT_QUERYADAPTERINFO
    _fields_ = [("hAdapter", ctypes.c_uint), ("Type", ctypes.c_int), ("pPrivateDriverData", ctypes.c_void_p),
                ("PrivateDriverDataSize", ctypes.c_uint)]


class _CloseAdapter(ctypes.Structure):         # D3DKMT_CLOSEADAPTER
    _fields_ = [("hAdapter", ctypes.c_uint)]


class _AdapterAddress(ctypes.Structure):       # D3DKMT_ADAPTERADDRESS
    _fields_ = [("BusNumber", ctypes.c_uint), ("DeviceNumber", ctypes.c_uint), ("FunctionNumber", ctypes.c_uint)]


class _SegmentSizeInfo(ctypes.Structure):      # D3DKMT_SEGMENTSIZEINFO
    _fields_ = [("DedicatedVideoMemorySize", ctypes.c_ulonglong), ("DedicatedSystemMemorySize", ctypes.c_ulonglong),
                ("SharedSystemMemorySize", ctypes.c_ulonglong)]


class _RegistryInfo(ctypes.Structure):         # D3DKMT_ADAPTERREGISTRYINFO
    _fields_ = [("AdapterString", ctypes.c_wchar * MAX_PATH), ("BiosString", ctypes.c_wchar * MAX_PATH),
                ("DacType", ctypes.c_wchar * MAX_PATH), ("ChipType", ctypes.c_wchar * MAX_PATH)]


class _DeviceIds(ctypes.Structure):            # D3DKMT_QUERY_DEVICE_IDS (D3DKMT_DEVICE_IDS inside)
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint), ("VendorID", ctypes.c_uint), ("DeviceID", ctypes.c_uint),
                ("SubVendorID", ctypes.c_uint), ("SubSystemID", ctypes.c_uint), ("RevisionID", ctypes.c_uint),
                ("BusType", ctypes.c_uint)]


class _Value(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong),
                ("AnsiStringValue", ctypes.c_char_p), ("WideStringValue", ctypes.c_wchar_p)]


class _CounterValue(ctypes.Structure):         # PDH_FMT_COUNTERVALUE
    _fields_ = [("CStatus", ctypes.c_uint32), ("value", _Value)]


class _CounterItem(ctypes.Structure):          # PDH_FMT_COUNTERVALUE_ITEM_W
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _CounterValue)]


def _query(gdi: ctypes.CDLL, handle: int, kind: int, out: ctypes.Structure) -> bool:
    q = _QueryAdapterInfo(handle, kind, ctypes.cast(ctypes.byref(out), ctypes.c_void_p), ctypes.sizeof(out))
    return int(gdi.D3DKMTQueryAdapterInfo(ctypes.byref(q))) == 0


def adapters() -> List[Dict[str, Value]]:
    """Every display adapter D3DKMT lists: name, luid_high, luid_low, bus, device, function, dedicated and shared
    (bytes), vendor_id (0 when unknown), software, render, hybrid_integrated. [] when Windows does not answer."""
    try:
        gdi = ctypes.WinDLL("gdi32")
        enum = _EnumAdapters2(0, None)
        if int(gdi.D3DKMTEnumAdapters2(ctypes.byref(enum))) != 0 or not enum.NumAdapters:
            return []
        table = (_AdapterInfo * enum.NumAdapters)()
        enum.pAdapters = table
        if int(gdi.D3DKMTEnumAdapters2(ctypes.byref(enum))) != 0:
            return []
    except (AttributeError, OSError):
        return []
    out: List[Dict[str, Value]] = []
    for a in table[:enum.NumAdapters]:
        h = a.hAdapter
        addr, seg, reg, ids = _AdapterAddress(), _SegmentSizeInfo(), _RegistryInfo(), _DeviceIds()
        kind = ctypes.c_uint()
        kind_ok = _query(gdi, h, KMTQAITYPE_ADAPTERTYPE, kind)              # type: ignore[arg-type]
        flags = kind.value if kind_ok else 0
        has_name = _query(gdi, h, KMTQAITYPE_ADAPTERREGISTRYINFO, reg)
        has_pci = _query(gdi, h, KMTQAITYPE_ADAPTERADDRESS, addr)
        has_seg = _query(gdi, h, KMTQAITYPE_GETSEGMENTSIZE, seg)
        has_ids = _query(gdi, h, KMTQAITYPE_PHYSICALADAPTERDEVICEIDS, ids)
        out.append({"name": reg.AdapterString if has_name else "",
                    "luid_high": int(a.AdapterLuid.HighPart), "luid_low": int(a.AdapterLuid.LowPart), "pci": has_pci,
                    "bus": int(addr.BusNumber), "device": int(addr.DeviceNumber), "function": int(addr.FunctionNumber),
                    "dedicated": int(seg.DedicatedVideoMemorySize) if has_seg else 0,
                    "shared": int(seg.SharedSystemMemorySize) if has_seg else 0,
                    "vendor_id": int(ids.VendorID) if has_ids else 0,
                    "software": bool(flags & 4), "render": bool(flags & 1) or not kind_ok,
                    "hybrid_integrated": bool(flags & 32)})
        gdi.D3DKMTCloseAdapter(ctypes.byref(_CloseAdapter(h)))
    return out


def counter_values(path: str, double: bool = False) -> Dict[str, float]:
    """{instance: value} of a performance counter with a * instance, by its English path ({} when it has none).
    double: as a fraction (a percentage below 1, such as the page file's use), not a whole number."""
    try:
        pdh = ctypes.WinDLL("pdh")
    except (AttributeError, OSError):
        return {}
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {}
    out: Dict[str, float] = {}
    try:
        if pdh.PdhAddEnglishCounterW(query, ctypes.c_wchar_p(path), None, ctypes.byref(counter)) != 0:
            return {}
        if pdh.PdhCollectQueryData(query) != 0:
            return {}
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        fmt = (PDH_FMT_DOUBLE if double else PDH_FMT_LARGE) | PDH_FMT_NOCAP100
        rc = pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if (rc & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_CounterItem))
        for i in range(count.value):
            item = items[i]
            if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA) and item.szName:
                v = item.FmtValue.value
                out[item.szName] = float(v.doubleValue if double else v.largeValue)
    finally:
        pdh.PdhCloseQuery(query)
    return out


def counter_total(paths: Sequence[str]) -> Optional[float]:
    """The sum of counters without instances, by their English paths, in one query (an instantaneous counter needs
    one collection); None when Windows does not answer or one of them has no valid value."""
    try:
        pdh = ctypes.WinDLL("pdh")
    except (AttributeError, OSError):
        return None
    query = ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return None
    try:
        counters = []
        for path in paths:
            counter = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(query, ctypes.c_wchar_p(path), None, ctypes.byref(counter)) != 0:
                return None
            counters.append(counter)
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        total = 0.0
        for counter in counters:
            value = _CounterValue()
            if pdh.PdhGetFormattedCounterValue(counter, PDH_FMT_LARGE, None, ctypes.byref(value)) != 0 or \
                    value.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                return None
            total += float(value.value.largeValue)
        return total
    finally:
        pdh.PdhCloseQuery(query)
