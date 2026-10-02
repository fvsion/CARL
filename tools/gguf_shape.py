"""GGUF header reading and memory maths shared by tools/llama-monitor.py and
tools/llama-fit.py. Standard library only.

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.
"""
import json, os, struct, subprocess, urllib.request

GGUF_TYPES = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              19: "IQ2_XXS", 20: "IQ2_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
              28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
# bytes per element of KV cache types (block formats hold 32 values)
KV_BPE = {"f32": 4, "f16": 2, "bf16": 2, "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32,
          "q5_0": 22 / 32, "q5_1": 24 / 32, "iq4_nl": 18 / 32}
GIB = 2 ** 30


def parse_meta(head):
    """Scalar metadata from the first bytes of a GGUF file (arrays skipped)."""
    out = {}
    if head[:4] != b"GGUF":
        return out
    o = 4
    def rd(fmt):
        nonlocal o
        v = struct.unpack_from(fmt, head, o); o += struct.calcsize(fmt); return v[0]
    def rstr():
        nonlocal o
        n = rd("<Q"); s = head[o:o + n]; o += n; return s
    try:
        rd("<I"); rd("<Q"); n = rd("<Q")
        for _ in range(n):
            key = rstr().decode(errors="replace"); t = rd("<I")
            if t == 8:
                v = rstr()
                if key == "tokenizer.chat_template":
                    out["_has_reasoning_effort"] = b"reasoning_effort" in v
                    out["_has_enable_thinking"] = b"enable_thinking" in v
                else:
                    out[key] = v.decode(errors="replace")
            elif t == 9:
                at = rd("<I"); cnt = rd("<Q")
                if at == 8:
                    for _ in range(cnt): rstr()
                else:
                    o += struct.calcsize(GGUF_TYPES[at]) * cnt
            else:
                out[key] = rd(GGUF_TYPES[t])
    except (struct.error, KeyError):
        pass
    return out


def local_meta(path):
    with open(path, "rb") as f:
        return parse_meta(f.read(64 * 1024 * 1024))


def remote_meta(repo, rev, file, cache=os.path.expanduser("~/models/.gguf-shapes.json")):
    """Header of a not-yet-downloaded file (first 24 MB via an HTTP range), cached."""
    key = f"{repo}@{rev}/{file}"
    try:
        db = json.load(open(cache))
    except (OSError, ValueError):
        db = {}
    if key not in db:
        url = f"https://huggingface.co/{repo}/resolve/{rev}/{file}"
        req = urllib.request.Request(url, headers={"Range": "bytes=0-25165823"})
        with urllib.request.urlopen(req, timeout=60) as r:
            db[key] = parse_meta(r.read())
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(db, open(cache, "w"))
    return db[key]


def model_shape(meta):
    a = meta.get("general.architecture", "")
    g = lambda key, d=0: meta.get(f"{a}.{key}", d)
    blocks, nextn = g("block_count"), g("nextn_predict_layers")
    main = blocks - nextn
    interval = g("full_attention_interval", 1) or 1
    attn = main // interval
    rec = main - attn if interval > 1 else 0
    kvh, kl, vl = g("attention.head_count_kv"), g("attention.key_length"), g("attention.value_length")
    if not kl:
        kl = vl = g("embedding_length") // max(g("attention.head_count", 1), 1)
    inner, rank, state = g("ssm.inner_size"), g("ssm.time_step_rank"), g("ssm.state_size")
    conv, groups = g("ssm.conv_kernel"), g("ssm.group_count")
    rs_layer = 0
    if rec and rank and state:
        rs_layer = rank * state * (inner // rank) * 4 + max(conv - 1, 0) * (inner + 2 * groups * state) * 4
    return {
        "arch": a, "blocks": blocks, "nextn": nextn, "attn_layers": attn, "rec_layers": rec,
        "kv_elems_per_token": attn * kvh * (kl + vl), "kv_elems_per_token_mtp": nextn * kvh * (kl + vl),
        "rs_bytes": rec * rs_layer, "experts": g("expert_count"), "experts_used": g("expert_used_count"),
        "ctx_train": g("context_length"),
        "ftype": FILE_TYPES.get(meta.get("general.file_type"), str(meta.get("general.file_type", "?"))),
        "kvh": kvh, "kl": kl, "vl": vl,
        "effort_levels": bool(meta.get("_has_reasoning_effort")), "thinking_switch": bool(meta.get("_has_enable_thinking")),
    }


def kv_bytes_per_token(shape, ktype="q4_0", vtype=None):
    vtype = vtype or ktype
    half = shape["kv_elems_per_token"] / 2
    return half * KV_BPE.get(ktype, 2) + half * KV_BPE.get(vtype, 2)


# compute buffers, MTP draft context and allocator slack: a conservative allowance
OVERHEAD = 1.0 * GIB


def gpu_limit():
    """(bytes, how) the GPU may use. An iogpu.wired_limit_mb override wins;
    otherwise Metal's recommendedMaxWorkingSetSize (via a cached Swift probe),
    else an estimate (2/3 of RAM up to 32 GB, 3/4 above)."""
    def sysctl(name):
        try:
            return int(subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip() or 0)
        except ValueError:
            return 0
    mem = sysctl("hw.memsize")
    wired = sysctl("iogpu.wired_limit_mb")
    if wired:
        return wired * 2**20, f"iogpu.wired_limit_mb={wired}"
    cache = os.path.expanduser("~/models/.metal-limit")
    try:
        cmem, lim = open(cache).read().split()
        if int(cmem) == mem:
            return int(lim), "Metal recommendedMaxWorkingSetSize"
    except (OSError, ValueError):
        pass
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metal-limit.swift")
    try:
        out = subprocess.run(["swift", probe], capture_output=True, text=True, timeout=120).stdout.strip()
        lim = int(out)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        open(cache, "w").write(f"{mem} {lim}\n")
        return lim, "Metal recommendedMaxWorkingSetSize"
    except Exception:
        frac = 2 / 3 if mem <= 32 * GIB else 3 / 4
        return int(mem * frac), f"estimate ({frac:.0%} of RAM; Swift unavailable)"
