"""This Windows computer's logical processors and physical cores (implements CpuLayout, contract C-058):
GetLogicalProcessorInformationEx with RelationProcessorCore, whose records are parsed by a pure function (so the
parser runs on any OS over made-up bytes). One record is one physical core:

  offset 0   Relationship (DWORD)      0 for a processor core
  offset 4   Size of the record (DWORD)
  offset 8   Flags (BYTE)              1: the core has more than one logical processor (SMT)
  offset 9   EfficiencyClass (BYTE)    higher is faster: a hybrid CPU's P-cores above its E-cores; 0 on the others
  offset 30  GroupCount (WORD)
  offset 32  GroupMask[GroupCount]     16 bytes each: Mask (8), Group (2), 6 reserved

llama.cpp numbers the processors of one group (bit N of --cpu-mask is processor N), so only group 0 counts: a computer
with more than 64 logical processors pins on the first group's.
"""
from __future__ import annotations

import ctypes
import struct
from typing import Callable, List, Optional

from ...domain.hardware.cpu_topology import CpuTopology, LogicalCpu

RELATION_PROCESSOR_CORE = 0
HEADER, CLASS_AT, COUNT_AT, MASKS_AT, MASK_SIZE = 8, 9, 30, 32, 16
FIRST_GROUP = 0
ERROR_INSUFFICIENT_BUFFER = 122


def parse_cores(data: bytes) -> CpuTopology:
    """The logical processors of the processor-core records in `data` (what GetLogicalProcessorInformationEx wrote):
    each record's group 0 mask gives its logical processors, the record's position its core, its efficiency class its
    rank. A record that is cut short ends the list."""
    cpus: List[LogicalCpu] = []
    at, core = 0, 0
    while at + HEADER <= len(data):
        relation, size = struct.unpack_from("<II", data, at)
        if size < HEADER or at + size > len(data):
            break
        if relation == RELATION_PROCESSOR_CORE and size >= MASKS_AT:
            rank = data[at + CLASS_AT]
            (groups,) = struct.unpack_from("<H", data, at + COUNT_AT)
            for g in range(groups):
                pos = at + MASKS_AT + g * MASK_SIZE
                if pos + MASK_SIZE > at + size:
                    break
                mask, group = struct.unpack_from("<QH", data, pos)
                if group == FIRST_GROUP:
                    cpus += [LogicalCpu(bit, core, rank) for bit in range(64) if mask >> bit & 1]
            core += 1
        at += size
    return CpuTopology(tuple(cpus))


def read_records() -> Optional[bytes]:
    """The raw records of GetLogicalProcessorInformationEx (None when the call fails)."""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)             # type: ignore[attr-defined]
        size = ctypes.c_uint32(0)
        k.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, None, ctypes.byref(size))
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not size.value:    # type: ignore[attr-defined]
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not k.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, buf, ctypes.byref(size)):
            return None
        return buf.raw[:size.value]
    except (AttributeError, OSError):
        return None


class WindowsCpuLayout:
    """CpuLayout over GetLogicalProcessorInformationEx. records: what reads the raw records (tests give bytes)."""

    def __init__(self, records: Callable[[], Optional[bytes]] = read_records) -> None:
        self.records = records

    def topology(self) -> CpuTopology:
        data = self.records()
        return parse_cores(data) if data else CpuTopology()
