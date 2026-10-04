"""Hugging Face specs, launch-env output safety, memory fit and the GGUF header parser."""
from __future__ import annotations

import struct
import unittest
from typing import Dict, List, Tuple, cast

from support import GIB, shape
from carl_core.domain import fit, hf
from carl_core.domain.errors import ConfigError
from carl_core.domain.gguf import kv_bytes_per_token, model_shape, parse_meta
from carl_core.domain.launch import launch_env, shell_lines
from carl_core.domain.settings import Config
from carl_core.domain.types import ModelInfo, SettingSource, Settings


class HfSpecTest(unittest.TestCase):
    def test_forms(self) -> None:
        self.assertEqual(hf.parse_hf("hf:Qwen/Qwen3-GGUF/Qwen3-Q4.gguf"), ("Qwen/Qwen3-GGUF", "Qwen3-Q4.gguf", "main"))
        self.assertEqual(hf.parse_hf("o/r/sub/dir/f.gguf"), ("o/r", "sub/dir/f.gguf", "main"))
        self.assertEqual(hf.parse_hf("hf:o/r"), ("o/r", None, "main"))
        self.assertEqual(hf.parse_hf("https://huggingface.co/o/r/resolve/abc123/My%20File.gguf?download=true"),
                         ("o/r", "My File.gguf", "abc123"))
        self.assertEqual(hf.parse_hf("https://huggingface.co/o/r/blob/main/f.gguf"), ("o/r", "f.gguf", "main"))

    def test_rejects(self) -> None:
        for spec in ("hf:o/r/x.bin", "justaname", "hf:o/r/../../etc/passwd.gguf", "hf:../r/f.gguf",
                     "https://huggingface.co/o/r/resolve/main/..%2F..%2Fx.gguf", "hf:o/r//f.gguf", "o/r;rm"):
            with self.subTest(spec=spec), self.assertRaises(ConfigError):
                hf.parse_hf(spec)

    def test_local_file_name_is_a_base_name(self) -> None:
        self.assertEqual(hf.local_file_name("sub/dir/f.gguf"), "f.gguf")
        for bad in ("../f.gguf", "/abs/f.gguf", "f.bin", "a\\b.gguf", "a/./b.gguf", ""):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                hf.local_file_name(bad)

    def test_urls_quote_names(self) -> None:
        self.assertEqual(hf.download_url({"repo": "o/r", "revision": "refs/pr/1", "file": "d/My F.gguf"}),
                         "https://huggingface.co/o/r/resolve/refs%2Fpr%2F1/d/My%20F.gguf")
        self.assertEqual(hf.tree_api_path("o/r", "main"), "models/o/r/tree/main?recursive=true")
        with self.assertRaises(ConfigError):
            hf.tree_api_path("o/r?x=1", "main")

    def test_gguf_files_from_tree(self) -> None:
        tree = [{"type": "file", "path": "a.gguf", "size": 5, "lfs": {"oid": "f" * 64}},
                {"type": "file", "path": "b-00002-of-00003.gguf", "size": 5},
                {"type": "file", "path": "mmproj-F16.gguf", "size": 5},
                {"type": "file", "path": "README.md", "size": 5},
                {"type": "directory", "path": "d"},
                {"type": "file", "path": "d/c.gguf", "size": "big"}, "junk"]
        self.assertEqual(hf.gguf_files(tree), [("a.gguf", 5, "f" * 64), ("d/c.gguf", 0, "")])
        with self.assertRaises(ConfigError):
            hf.gguf_files({"error": "x"})

    def test_names_and_parts(self) -> None:
        self.assertEqual(hf.model_name("Qwen3.6 35B (Q4).gguf"), "qwen3.6-35b-q4-")
        self.assertTrue(hf.is_extra_part("x-00012-of-00020.gguf"))
        self.assertFalse(hf.is_extra_part("x-00001-of-00020.gguf"))
        self.assertEqual(hf.commit_sha({"sha": "abc"}, "main"), "abc")
        self.assertEqual(hf.commit_sha([], "main"), "main")

    def test_validate_ref(self) -> None:
        with self.assertRaisesRegex(ConfigError, "not a SHA-256"):
            hf.validate_ref({"sha256": "zz"}, "m")
        with self.assertRaisesRegex(ConfigError, "hf.bytes"):
            hf.validate_ref({"bytes": -1}, "m")


class LaunchEnvTest(unittest.TestCase):
    def test_shell_lines_rejects_unsafe_values(self) -> None:
        self.assertEqual(shell_lines({"A": "x y", "B": True, "C": False, "D": 1.5}), "A=x y\nB=1\nC=0\nD=1.5")
        for value in ("$(rm -rf ~)", "a;b", "`id`", "a\nB=1", "q'x", 'q"x', "a|b", "a&b", "a>b", "*"):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, "unsafe characters"):
                shell_lines({"ALIAS": value})
        with self.assertRaisesRegex(ConfigError, "not a variable name"):
            shell_lines({"PATH;X": "1"})
        with self.assertRaises(ConfigError):
            shell_lines({"EXTRA_ARGS": ["--a"]})

    def test_launch_env_lines(self) -> None:
        m = cast(ModelInfo, {"name": "small", "path": "/m/Small.gguf"})
        vals: Settings = {"kv": "q4_0", "ctx": 98304, "slots": "auto", "spec": "ngram-mod", "spec_n": 2, "temp": 1.0,
                          "top_p": 0.95, "top_k": 20, "min_p": 0, "presence": 0, "repeat": 1.0, "alias": "small"}
        src: Dict[str, SettingSource] = {"ctx": "auto-tune", "kv": "catalogue", "spec": "config", "slots": "default"}
        cfg = Config(llama={"net": "local", "cache_ram": "auto", "host": "", "think_toggle": False, "model": "x",
                            "extra_args": ["--no-mmap", "-fa", "on"]})
        env = launch_env(m, vals, src, cfg)
        self.assertEqual(list(env)[:3], ["MODEL", "MODEL_NAME", "ALIAS"])
        self.assertEqual(env["NET"], "local")
        self.assertNotIn("CACHE_RAM", env)                   # auto: the launcher sizes it
        self.assertNotIn("HOST", env)
        self.assertEqual(env["THINK_TOGGLE"], False)
        self.assertEqual(env["EXTRA_ARGS"], "--no-mmap -fa on")
        self.assertEqual(env["CARL_SOURCES"], "ctx:auto-tune kv:catalogue spec:config slots:default")
        self.assertNotIn("SWA_MODE", env)
        self.assertEqual(launch_env(m, vals, src, cfg, swa=True)["SWA_MODE"], "auto")  # sliding-window layers
        self.assertEqual(launch_env(m, vals, src, Config(cache={"swa": "window"}), swa=True)["SWA_MODE"], "window")
        self.assertIn("SPEC_N=2", shell_lines(env).splitlines())


class FitTest(unittest.TestCase):
    S = shape(kv_elems=8192, rs_bytes=GIB // 4)         # 4.5 KiB/token at q4_0

    def test_need_and_max_ctx(self) -> None:
        per_tok = kv_bytes_per_token(self.S, "q4_0")
        self.assertEqual(per_tok, 4608)
        self.assertEqual(fit.need_bytes(self.S, 10 * GIB, 65536, 2), 10 * GIB + per_tok * 65536 * 2 + GIB // 2 + GIB)
        room = 16 * GIB - 10 * GIB - GIB // 4 - GIB
        self.assertEqual(fit.max_ctx(self.S, 10 * GIB, 16 * GIB), min(int(room // per_tok) // 4096 * 4096, 262144))
        self.assertEqual(fit.max_ctx(self.S, 20 * GIB, 16 * GIB), 0)
        self.assertEqual(fit.max_ctx(shape(kv_elems=0), GIB, 16 * GIB), 262144)

    def test_slots_cache_reserve_and_limit(self) -> None:
        self.assertEqual(fit.plan_slots("auto", True), 2)
        self.assertEqual(fit.plan_slots("auto", False), 1)
        self.assertEqual(fit.plan_slots("3", False), 3)
        with self.assertRaises(ConfigError):
            fit.plan_slots("many", True)
        self.assertEqual(fit.reserve_bytes(None, True), 10 * GIB)
        self.assertEqual(fit.reserve_bytes(None, False), 6 * GIB)
        self.assertEqual(fit.reserve_bytes(2.5, True), 2.5 * GIB)
        self.assertEqual(fit.prompt_cache_mib(36 * GIB, 20 * GIB, 6 * GIB), 8192)
        self.assertEqual(fit.prompt_cache_mib(36 * GIB, 28 * GIB, 6 * GIB) % 256, 0)
        self.assertEqual(fit.prompt_cache_mib(24 * GIB, 30 * GIB, 6 * GIB), 1024)
        self.assertEqual(fit.estimated_limit(24 * GIB), (16 * GIB, 2 / 3))
        self.assertEqual(fit.estimated_limit(64 * GIB), (48 * GIB, 3 / 4))
        self.assertEqual(fit.estimated_limit(32 * GIB), (24 * GIB, 3 / 4))      # 32 GB: like a real M2 Max (25.0 GiB)

    def test_offline_default_or_small(self) -> None:
        self.assertEqual(fit.offline_default("big", "small", 30 * GIB, 24 * GIB), "small")
        self.assertEqual(fit.offline_default("big", "small", 20 * GIB, 24 * GIB), "big")
        self.assertEqual(fit.offline_default("big", "small", 23.5 * GIB, 24 * GIB), "small")   # + 1 GiB buffers
        self.assertEqual(fit.offline_default("big", "small", None, 1), "big")
        self.assertEqual(fit.offline_default("big", None, 30 * GIB, 24 * GIB), "big")

    def test_start_check(self) -> None:
        """Over the limit = refused: the check says what it needs and the largest window that fits."""
        ok = fit.check_start(self.S, 10 * GIB, 65536, 2, "q4_0", 16 * GIB)
        self.assertTrue(ok.fits)
        self.assertEqual(ok.need, fit.need_bytes(self.S, 10 * GIB, 65536, 2))
        big = fit.check_start(self.S, 10 * GIB, 262144, 4, "q8_0", 16 * GIB)
        self.assertFalse(big.fits)
        self.assertEqual(big.largest, fit.max_ctx(self.S, 10 * GIB, 16 * GIB, 4, "q8_0"))
        self.assertEqual(big.setup(), "--ctx 256K x 4 slots (q8_0 KV)")
        self.assertEqual(fit.check_start(self.S, 20 * GIB, 4096, 0, "q4_0", 16 * GIB).largest, 0)
        self.assertEqual(fit.check_start(self.S, GIB, 4096, 0, "q4_0", 16 * GIB).slots, 1)
        self.assertEqual(fit.window_label(98304), "96K")
        self.assertEqual(fit.window_label(0), "–")


def gguf_header(kvs: List[Tuple[str, int, bytes]]) -> bytes:
    def s(x: bytes) -> bytes:
        return struct.pack("<Q", len(x)) + x
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs))
    for key, t, payload in kvs:
        out += s(key.encode()) + struct.pack("<I", t) + payload
    return out


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


class GgufTest(unittest.TestCase):
    def test_parse_and_shape(self) -> None:
        head = gguf_header([
            ("general.architecture", 8, struct.pack("<Q", 7) + b"qwen35m"),
            ("general.file_type", 4, u32(23)),
            ("qwen35m.block_count", 4, u32(41)), ("qwen35m.nextn_predict_layers", 4, u32(1)),
            ("qwen35m.full_attention_interval", 4, u32(4)), ("qwen35m.attention.head_count_kv", 4, u32(2)),
            ("qwen35m.attention.key_length", 4, u32(256)), ("qwen35m.attention.value_length", 4, u32(256)),
            ("qwen35m.expert_count", 4, u32(256)), ("qwen35m.context_length", 4, u32(262144)),
            ("tokenizer.ggml.tokens", 9, u32(8) + struct.pack("<Q", 2) + struct.pack("<Q", 1) + b"a"
             + struct.pack("<Q", 1) + b"b"),
            ("tokenizer.chat_template", 8, struct.pack("<Q", 21) + b"{{ enable_thinking }}"),
        ])
        meta = parse_meta(head)
        self.assertEqual(meta["general.architecture"], "qwen35m")
        self.assertIs(meta["_has_enable_thinking"], True)
        self.assertNotIn("tokenizer.ggml.tokens", meta)
        shp = model_shape(meta)
        self.assertEqual((shp["attn_layers"], shp["rec_layers"], shp["ftype"]), (10, 30, "IQ3_XXS"))
        self.assertEqual(shp["kv_elems_per_token"], 10 * 2 * 512)
        self.assertTrue(shp["thinking_switch"])
        self.assertEqual(shp.get("swa_window"), 0)
        gemma = {"general.architecture": "gemma4", "gemma4.block_count": 6, "gemma4.attention.head_count_kv": 2,
                 "gemma4.attention.key_length": 512, "gemma4.attention.value_length": 512,
                 "gemma4.attention.key_length_swa": 256, "gemma4.attention.value_length_swa": 256,
                 "gemma4.attention.sliding_window": 512, "gemma4.attention.shared_kv_layers": 2,
                 "gemma4.attention.sliding_window_pattern": "110110"}
        g = model_shape(gemma)                       # own KV: the first 4 layers, 3 of them sliding-window
        self.assertEqual((g["swa_window"], g["kv_elems_per_token"], g["kv_elems_per_token_swa"]), (512, 2048, 3072))
        no_pattern = model_shape({k: v for k, v in gemma.items() if not k.endswith("pattern")})
        self.assertEqual((no_pattern["kv_elems_per_token"], no_pattern["kv_elems_per_token_swa"]), (6 * 2048, 0))

    def test_truncated_or_foreign_headers(self) -> None:
        self.assertEqual(parse_meta(b"NOPE"), {})
        head = gguf_header([("a.x", 4, struct.pack("<I", 1)), ("a.y", 4, struct.pack("<I", 2))])
        self.assertEqual(parse_meta(head[:-2]), {"a.x": 1})
        self.assertEqual(model_shape({})["ftype"], "?")


if __name__ == "__main__":
    unittest.main()


class SlidingWindowFitTest(unittest.TestCase):
    """fit.py with sliding-window layers: the window only, or every layer at full length (--swa-full)."""
    SWA = {**shape(kv_elems=2048, rs_bytes=0), "swa_window": 512, "kv_elems_per_token_swa": 8192}

    def test_need_with_and_without_the_full_cache(self) -> None:
        from carl_core.domain.fit import need_bytes
        full = need_bytes(self.SWA, GIB, 98304, 2, "q4_0", swa_full=True)
        window = need_bytes(self.SWA, GIB, 98304, 2, "q4_0", swa_full=False)
        self.assertAlmostEqual(full - window, 8192 * 18 / 32 * (98304 - 1024) * 2)
        self.assertEqual(need_bytes(shape(), GIB, 98304, 2), need_bytes(shape(), GIB, 98304, 2, swa_full=False))

    def test_auto_takes_the_full_cache_when_it_fits_and_prefers_a_second_slot(self) -> None:
        from carl_core.domain.fit import need_bytes, swa_plan
        roomy, tight = 32 * GIB, need_bytes(self.SWA, GIB, 98304, 2, "q4_0", swa_full=False) + 1
        self.assertEqual(swa_plan("auto", self.SWA, GIB, 98304, "auto", "q4_0", roomy), (2, True))
        self.assertEqual(swa_plan("auto", self.SWA, GIB, 98304, "auto", "q4_0", tight), (2, False))
        self.assertEqual(swa_plan("window", self.SWA, GIB, 98304, "auto", "q4_0", roomy), (2, False))
        self.assertEqual(swa_plan("full", self.SWA, GIB, 98304, "auto", "q4_0", tight), (1, True))
        self.assertEqual(swa_plan("auto", shape(), GIB, 98304, "auto", "q4_0", roomy), (2, None))
