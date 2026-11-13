"""CARL's default values, each fact once (rule R14). Pure.

Every other module reads these names; none repeats the number. The bash launcher of a353e86 (host/common.sh,
host/serve-llama.sh) held the same values: a unit test checks that they agree.
"""
from __future__ import annotations

from typing import Dict

# The server's port when none is set (PORT, --port): a port no common software uses (user, 2026-10-10: "8080 is
# too common ... a 9000 level port that nothing else generally uses"; not 9931, llama.cpp's own coming default, so a
# plain llama-server does not take CARL's port). host/common.sh CARL_DEFAULT_PORT and the client's setup say the same.
DEFAULT_PORT = 9275
# The context of each slot in tokens (96K): llama.ctx's default, and the smallest window people can work with.
DEFAULT_CTX = 98304
# -ub, the physical batch: 512 measured best (90.5 against 88.6 and 86.1 tok/s for 1024 and 2048). The default of the
# schema's -ub keys, the smallest -ub (the setup every size has to keep), and a Mac's -ub before Auto-tune (the M3 Pro, A3B IQ3, 2 x 96K: 2048 read 1% faster than 512, under tune's margin).
UBATCH = 512
# A PC's -ub before Auto-tune (decisions A44 and A45). Measured on a PC with an RTX 2070 SUPER (llama.cpp b11738; W6.10): with
# the MoE experts in the RAM the A3B IQ3 reads prompts at 386 / 635 / 978 / 1267 tok/s at -ub 512 / 1024 / 2048 / 4096,
# for one layer more in the RAM at 2048 and the same decode speed; Auto-tune found 2048 best for the 0.6B and the E4B.
UBATCH_PC = 2048
# The batch sizes Auto-tune measures the prompt read at, on every OS (A45); the model's record keeps the choice.
UB_CANDIDATES = (512, 1024, 2048, 4096)
DRAFT_MARGIN = 1.03                        # a mode must win by 3% (simpler is steadier): the measurement's noise
# Load mode none (the weights read into the RAM, not mapped) needs the free RAM to cover the server's RAM and this much
# more (llama.load_reserve_gib's default, A44: "at least 8GB by default").
LOAD_RESERVE_GIB = 8.0
# n-gram speculation (--spec-ngram-mod-n-match, -min, -max). 27B IQ3: 32 / 16 / 96 (llama.cpp's own: 24 / 48 / 64).
NGRAM: Dict[str, int] = {"match": 32, "min": 16, "max": 96}
# The VM network's host address (the host-only network vmnet8): the address --vm listens on (VM_HOST).
VM_ADDR = "192.168.42.1"
# The sampling defaults: temperature 1.0, top-p 0.95, top-k 20, no min-p, no presence or repeat penalty.
TEMP = 1.0
TOP_P = 0.95
TOP_K = 20
MIN_P = 0
PRESENCE = 0
REPEAT = 1.0
# The dashboard's detail levels (D): simple = the state, the next action, one sentence per number; full = the figures too.
DETAILS = ("simple", "full")
