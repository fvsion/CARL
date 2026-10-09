"""A separate MTP drafter (Gemma 4: catalogue "draft", llama-server -md): the catalogue check,
the model record (its path and status; never a model of its own), launch-env (DRAFT, the
n-gram fallback), download / verify / delete of both files, router presets, fit and Auto-tune."""
from __future__ import annotations

import copy
import unittest
from typing import Dict, Optional

from support import GIB, MDIR, SHA_A, FakeShapes, World, catalog, entry, shape, with_window
from carl_core.domain import models as dm
from carl_core.domain import tuning as t
from carl_core.domain.autofit import candidate
from carl_core.domain.errors import ConfigError
from carl_core.domain.fit import Spec, need_bytes
from carl_core.domain.gguf import ModelShape
from carl_core.domain.records import parse_catalog
from carl_core.domain.router import Common, ModelPlan, Preset, plan_model, preset_ini
from carl_core.domain.settings import Config
from carl_core.domain.types import CatalogEntry, HfRef, ModelInfo, Settings

SHA_D = "d" * 64
DRAFT_BYTES = 60 * 2 ** 20
GEM_PATH, DRAFT_PATH = f"{MDIR}/Gem-Q4_0.gguf", f"{MDIR}/mtp-Gem-Q4_0.gguf"
QWEN_PATH = f"{MDIR}/Qwen-Q4.gguf"
# Gemma 4 has no MTP head in the model file (nextn 0) and sliding-window layers
GEM_SHAPE: ModelShape = with_window(shape(experts=0, nextn=0, kv_elems=8192), 512, 20480)


def draft_ref(file: str = "mtp-Gem-Q4_0.gguf") -> HfRef:
    return {"repo": "ggml-org/gem-GGUF", "revision": "1" * 40, "file": file, "sha256": SHA_D, "bytes": DRAFT_BYTES}


def gem(spec: str = "draft-mtp", n: int = 2) -> CatalogEntry:
    e = entry("gem", "Gem-Q4_0.gguf", 4 * GIB, arch="dense", rank=3)
    e["mtp"] = True
    e["draft"] = draft_ref()
    e["tune"] = {**e.get("tune", {}), "spec": spec, "spec_n": n}
    return e


QWEN = entry("qwen", "Qwen-Q4.gguf", 10 * GIB, rank=1)
BOTH = {GEM_PATH: 4 * GIB, DRAFT_PATH: DRAFT_BYTES}


def world(files: Optional[Dict[str, int]] = None, spec: str = "draft-mtp", config: object = None,
          download_size: int = 0) -> World:
    shapes = FakeShapes({GEM_PATH: GEM_SHAPE, QWEN_PATH: shape(nextn=0)})
    return World(catalog(gem(spec), QWEN, default="qwen"), files=BOTH if files is None else files, shapes=shapes,
                 config=config, download_size=download_size)


def model(w: World, name: str = "gem") -> ModelInfo:
    m = w.carl.find(name, w.carl.all_models(Config()))
    assert m is not None
    return m


class CatalogueTest(unittest.TestCase):
    def test_a_draft_is_checked_like_hf(self) -> None:
        parse_catalog(catalog(gem()), "c")
        for bad, msg in ((dict(draft_ref(), file="../../x.gguf"), "not a file name"),
                         ({k: v for k, v in draft_ref().items() if k != "bytes"}, "draft needs repo, file and bytes"),
                         (dict(draft_ref(), bytes=-1), "draft.bytes must be a byte count"),
                         (dict(draft_ref(), repo=5), "draft.repo must be a string"),
                         (dict(draft_ref(), file="mtp.bin"), "not a GGUF file"),
                         (dict(draft_ref(), file="Gem-Q4_0.gguf"), "names the model file itself"),
                         ("x", "draft must be an object")):
            e = gem()
            e["draft"] = bad        # type: ignore[typeddict-item]
            with self.assertRaisesRegex(ConfigError, msg):
                parse_catalog(catalog(e), "c")

    def test_the_real_catalogue_names_the_gemma_drafters(self) -> None:
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host", "catalog.json")
        with open(path, encoding="utf-8") as f:
            cat = parse_catalog(json.load(f), path)
        drafts = {m.get("name"): m for m in cat.get("models", []) if m.get("draft")}
        self.assertEqual(sorted(drafts), ["gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b", "gemma-4-e4b"])
        for name, m in drafts.items():
            d = m.get("draft") or {}
            self.assertTrue(d.get("file", "").startswith("mtp-gemma-4-"), name)
            self.assertEqual(d.get("repo"), (m.get("hf") or {}).get("repo"))
            self.assertEqual(len(d.get("sha256", "")), 64)
            self.assertTrue(m.get("mtp"))
            self.assertIn("draft-mtp", str((m.get("tune") or {}).get("spec")))
        self.assertEqual(((drafts["gemma-4-e4b"].get("tune") or {}).get("spec"),
                          (drafts["gemma-4-e4b"].get("draft") or {}).get("bytes")), ("draft-mtp,ngram-mod", 59678240))


class ModelRecordTest(unittest.TestCase):
    def test_path_and_status_and_never_a_model_of_its_own(self) -> None:
        w = world()
        ms = w.carl.all_models(Config())
        self.assertEqual(sorted(m.get("name", "") for m in ms), ["gem", "qwen"])   # the mtp-*.gguf is not listed
        m = model(w)
        self.assertEqual((m.get("draft_path"), m.get("draft_status")), (DRAFT_PATH, "downloaded"))
        self.assertTrue(dm.has_drafter(m))
        self.assertEqual(dm.draft_bytes(m), DRAFT_BYTES)
        self.assertEqual(dm.draft_bytes(model(w, "qwen")), 0)
        self.assertNotIn("draft_path", model(w, "qwen"))

    def test_missing_and_partial_drafter(self) -> None:
        self.assertEqual(model(world({GEM_PATH: 4 * GIB})).get("draft_status"), "missing")
        part = world({GEM_PATH: 4 * GIB, DRAFT_PATH: 5, DRAFT_PATH + ".aria2": 1})
        self.assertEqual(model(part).get("draft_status"), "partial")
        self.assertFalse(dm.has_drafter(model(part)))
        self.assertEqual(model(part).get("status"), "downloaded")          # the model itself still starts

    def test_mtp_source_and_fallback(self) -> None:
        m = model(world())
        self.assertEqual(dm.mtp_source(m, GEM_SHAPE), "drafter")
        self.assertEqual(dm.mtp_source(m, shape(nextn=1)), "head")          # a head in the file wins
        lone = model(world({GEM_PATH: 4 * GIB}))
        self.assertEqual(dm.mtp_source(lone, GEM_SHAPE), "none")
        self.assertEqual(dm.mtp_fallback(m, "draft-mtp", "drafter"), ("draft-mtp", None))
        self.assertEqual(dm.mtp_fallback(m, "ngram-mod", "none"), ("ngram-mod", None))
        spec, note = dm.mtp_fallback(lone, "draft-mtp,ngram-mod", "none")
        self.assertEqual(spec, "ngram-mod")
        self.assertIn("drafter is not downloaded (./carl.sh download gem downloads it)", str(note))
        _, note = dm.mtp_fallback({"name": "q"}, "draft-mtp", "none")
        self.assertIn("its file has no MTP head", str(note))


class LaunchEnvTest(unittest.TestCase):
    def test_drafter_downloaded(self) -> None:
        env, note = world().carl.launch_env("gem", use_config=True)
        self.assertIsNone(note)
        self.assertEqual((env["SPEC"], env["SPEC_N"], env["MTP_SOURCE"], env["DRAFT"]),
                         ("draft-mtp", 2, "drafter", DRAFT_PATH))

    def test_drafter_missing_falls_back_to_ngram_once(self) -> None:
        env, note = world({GEM_PATH: 4 * GIB}).carl.launch_env("gem", use_config=True)
        self.assertEqual((env["SPEC"], env["MTP_SOURCE"], "DRAFT" in env), ("ngram-mod", "none", False))
        self.assertEqual(str(note).count("n-gram only"), 1)

    def test_a_drafter_is_passed_for_an_ngram_spec_too(self) -> None:
        """The launcher passes -md only when SPEC uses MTP, also a SPEC from the environment (Auto-tune)."""
        env, _ = world(spec="ngram-mod").carl.launch_env("gem", use_config=True)
        self.assertEqual((env["SPEC"], env["DRAFT"]), ("ngram-mod", DRAFT_PATH))

    def test_a_file_without_an_mtp_head(self) -> None:
        w = world({**BOTH, QWEN_PATH: 10 * GIB}, config={"models": {"qwen": {"spec": "draft-mtp"}}})
        env, note = w.carl.launch_env("qwen", use_config=True)
        self.assertEqual((env["SPEC"], env["MTP_SOURCE"], "DRAFT" in env), ("ngram-mod", "none", False))
        self.assertIn("its file has no MTP head", str(note))

    def test_unreadable_header_changes_nothing(self) -> None:
        w = World(catalog(gem()), files=BOTH)
        env, _ = w.carl.launch_env("gem", use_config=True)
        self.assertEqual((env["SPEC"], "MTP_SOURCE" in env, "DRAFT" in env), ("draft-mtp", False, False))


class DownloadTest(unittest.TestCase):
    def test_downloads_the_model_then_its_drafter(self) -> None:
        w = world({}, download_size=0)
        sizes = {"Gem-Q4_0.gguf": 4 * GIB, "mtp-Gem-Q4_0.gguf": DRAFT_BYTES}

        def fetch(url: str, directory: str, file_name: str) -> int:
            w.downloader.calls.append((url, directory, file_name))
            w.folder.files[f"{directory}/{file_name}"] = sizes[file_name]
            return 0
        w.downloader.fetch = fetch                       # type: ignore[method-assign]
        w.folder.hashes[DRAFT_PATH] = SHA_D
        self.assertTrue(w.carl.download(model(w), MDIR))
        self.assertEqual([c[2] for c in w.downloader.calls], ["Gem-Q4_0.gguf", "mtp-Gem-Q4_0.gguf"])
        self.assertEqual(w.downloader.calls[1][0],
                         f"https://huggingface.co/ggml-org/gem-GGUF/resolve/{'1' * 40}/mtp-Gem-Q4_0.gguf")
        self.assertIn(f"  gem MTP drafter: the file is correct (SHA-256 {SHA_D}).", w.console.lines)

    def test_only_the_missing_drafter(self) -> None:
        w = world({GEM_PATH: 4 * GIB}, download_size=DRAFT_BYTES)
        w.folder.hashes[DRAFT_PATH] = SHA_D
        self.assertTrue(w.carl.download(model(w), MDIR))
        self.assertEqual([c[2] for c in w.downloader.calls], ["mtp-Gem-Q4_0.gguf"])
        self.assertNotIn(f"  gem: the file is correct (SHA-256 {SHA_A}).", w.console.lines)          # the 4 GB model is not hashed again

    def test_a_bad_drafter_is_moved_aside(self) -> None:
        w = world({GEM_PATH: 4 * GIB}, download_size=DRAFT_BYTES)
        w.folder.hashes[DRAFT_PATH] = "e" * 64
        self.assertFalse(w.carl.download(model(w), MDIR))
        self.assertIn(DRAFT_PATH + ".bad", w.folder.files)
        self.assertNotIn(GEM_PATH + ".bad", w.folder.files)

    def test_both_downloaded_verifies_both(self) -> None:
        w = world()
        w.folder.hashes[DRAFT_PATH] = SHA_D
        self.assertTrue(w.carl.download(model(w), MDIR))
        self.assertEqual(w.downloader.calls, [])
        self.assertIn(f"  gem: the file is correct (SHA-256 {SHA_A}).", w.console.lines)
        self.assertIn(f"  gem MTP drafter: the file is correct (SHA-256 {SHA_D}).", w.console.lines)

    def test_disk_space_for_the_drafter(self) -> None:
        w = world({GEM_PATH: 4 * GIB})
        w.folder.free = 1 * GIB
        self.assertFalse(w.carl.download(model(w), MDIR))
        self.assertIn("gem MTP drafter needs 63 MB and 5 GB more on the disk", w.console.errors[0])


class VerifyDeleteTest(unittest.TestCase):
    def test_verify_checks_both(self) -> None:
        w = world()
        w.folder.hashes[DRAFT_PATH] = SHA_D
        self.assertTrue(w.carl.verify(model(w)))
        w.folder.hashes[DRAFT_PATH] = "e" * 64
        self.assertFalse(w.carl.verify(model(w)))
        self.assertIn("gem MTP drafter: bad file. The SHA-256 is", w.console.errors[-1])

    def test_a_missing_drafter_is_reported_not_an_error(self) -> None:
        w = world({GEM_PATH: 4 * GIB})
        self.assertTrue(w.carl.verify(model(w)))
        self.assertIn("the MTP drafter is not downloaded, so a start uses n-gram speculation only", w.console.lines[-1])

    def test_delete_removes_both(self) -> None:
        w = world({**BOTH, DRAFT_PATH + ".bad": 1})
        w.carl.delete(model(w))
        self.assertEqual(sorted(w.folder.removed), sorted([GEM_PATH, DRAFT_PATH, DRAFT_PATH + ".bad"]))

    def test_delete_refuses_a_drafter_path_that_is_not_gguf(self) -> None:
        m = model(world())
        m["draft_path"] = "/etc/passwd"
        w = world()
        with self.assertRaisesRegex(ConfigError, "CARL does not delete"):
            w.carl.delete(m)
        self.assertEqual(w.folder.removed, [])


class RouterTest(unittest.TestCase):
    COMMON = Common(batch=2048, ubatch=512, ckpt=8, ckpt_step=4096, cache_ram=None)
    VALS: Settings = {"ctx": 98304, "slots": "1", "kv": "q4_0", "spec": "draft-mtp", "spec_n": 2, "temp": 1.0,
                      "top_p": 0.95, "top_k": 64, "min_p": 0, "presence": 0, "repeat": 1.0}

    def test_the_section_names_the_drafter(self) -> None:
        p, why = plan_model("gem", GEM_PATH, self.VALS, GEM_SHAPE, 4 * GIB, 24 * GIB, 32 * GIB, 6 * GIB,
                            self.COMMON, None, DRAFT_PATH)
        assert p is not None, why
        ini = preset_ini(Preset(models=[p]), self.COMMON)
        self.assertIn(f"spec-type = draft-mtp\nspec-draft-n-max = 2\nspec-draft-model = {DRAFT_PATH}\n", ini)
        with self.assertRaises(ConfigError):
            plan_model("gem", GEM_PATH, self.VALS, GEM_SHAPE, GIB, 24 * GIB, 32 * GIB, 6 * GIB, self.COMMON, None,
                       "/x\n[y].gguf")

    @staticmethod
    def gem_plan(files: Dict[str, int]) -> ModelPlan:
        preset, _ = world(files).carl.router_preset(Config(), "/tmpl")
        return next(p for p in preset.models if p.name == "gem")

    def test_app_presets(self) -> None:
        p = self.gem_plan(BOTH)
        self.assertEqual((p.spec, p.draft), ("draft-mtp", DRAFT_PATH))
        # its weights count, and its draft context: two more compute buffers (Phase 23.4.4)
        mtp = Spec("draft-mtp", p.spec_n, DRAFT_BYTES)
        self.assertAlmostEqual(p.need, need_bytes(GEM_SHAPE, 4 * GIB, p.ctx, p.slots, p.kv, p.swa, mtp))
        self.assertGreater(p.need - self.gem_plan({GEM_PATH: 4 * GIB}).need, DRAFT_BYTES)

    def test_app_presets_without_the_drafter(self) -> None:
        p = self.gem_plan({GEM_PATH: 4 * GIB})
        self.assertEqual((p.spec, p.draft), ("ngram-mod", None))
        self.assertNotIn("spec-draft-model", preset_ini(Preset(models=[p]), self.COMMON))


class FitTest(unittest.TestCase):
    def test_auto_fit_counts_the_drafter(self) -> None:
        """The drafter's weights count while MTP runs: they are in the candidate's speculation, not its weights
        (Auto fit drops them with MTP when MTP does not fit)."""
        m = model(world())
        c = candidate(m, GEM_SHAPE)
        self.assertEqual((c.weights, c.spec.mtp, c.spec.draft_bytes), (4 * GIB, True, DRAFT_BYTES))
        self.assertEqual(c.spec.without_mtp().draft_bytes, 0)
        plain = copy.deepcopy(m)
        plain.pop("draft")
        self.assertEqual((candidate(plain, GEM_SHAPE).weights, candidate(plain, GEM_SHAPE).spec.mtp), (4 * GIB, False))


class TuneModesTest(unittest.TestCase):
    def test_a_drafter_gets_mtp_at_one_to_four_drafts(self) -> None:
        modes = t.speculation_modes(False, False, drafter=True)
        self.assertEqual(modes[:2], [("none", 1), ("ngram-mod", 2)])
        self.assertEqual(modes[2:], [(s, n) for n in (1, 2, 3, 4) for s in ("draft-mtp", "draft-mtp,ngram-mod")])
        self.assertEqual(t.speculation_modes(False, True, drafter=True)[-1], ("draft-mtp,ngram-mod", 2))
        self.assertNotIn(("ngram-mod", 1), modes)                  # n-gram alone at n=1 only without MTP

    def test_qwen_modes_unchanged(self) -> None:
        self.assertEqual(t.speculation_modes(True, False)[-1], ("draft-mtp,ngram-mod", 2))
        self.assertEqual(t.speculation_modes(True, True)[-1], ("draft-mtp,ngram-mod", 1))
        self.assertEqual(t.speculation_modes(False, False), [("none", 1), ("ngram-mod", 2), ("ngram-mod", 1)])

    def test_plan_with_a_drafter(self) -> None:
        plan = t.TunePlan(shape=GEM_SHAPE, weights=4 * GIB + DRAFT_BYTES, limit=24 * GIB, base_ctx=98304,
                          depth="default", date="2026-10-04", machine="m", llama_cpp="v", edit_source="", drafter=True)
        self.assertEqual(len(plan.modes()), 10)
        self.assertEqual(plan.steps(), 1 + 10 + 2 + 1)
        no_drafter = t.TunePlan(shape=GEM_SHAPE, weights=4 * GIB, limit=24 * GIB, base_ctx=98304, depth="default",
                                date="d", machine="m", llama_cpp="v", edit_source="")
        self.assertEqual(no_drafter.modes(), [("none", 1), ("ngram-mod", 2), ("ngram-mod", 1)])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Phase 21.1: custom Gemma 4 models
FINE_PATH = f"{MDIR}/Fine-12B-Q4_K_M.gguf"
G12_SHAPE: ModelShape = {**with_window(shape(experts=0, nextn=0, kv_elems=4608), 1024, 92160), "arch": "gemma4",
                         "blocks": 48}


def gemma12_world(files: Optional[Dict[str, int]] = None, local: object = None) -> World:
    g = gem("draft-mtp,ngram-mod")
    g["name"] = "gemma-4-12b"
    shapes = FakeShapes({FINE_PATH: G12_SHAPE, GEM_PATH: G12_SHAPE})
    return World(catalog(g, QWEN, default="qwen"), files={FINE_PATH: 7 * GIB} if files is None else files,
                 shapes=shapes, local=local, download_size=DRAFT_BYTES)


class CustomGemmaDrafterTest(unittest.TestCase):
    def test_the_size_class_picks_the_catalogue_drafter(self) -> None:
        from carl_core.domain.drafters import matching_drafter
        cat = parse_catalog(catalog(gem()), "c")                                # its Gemma entry is "gem"
        g = gem()
        g["name"] = "gemma-4-12b"
        cat2 = parse_catalog(catalog(g), "c")
        self.assertEqual(matching_drafter(G12_SHAPE, cat2), ("gemma-4-12b", draft_ref()))
        self.assertIsNone(matching_drafter(G12_SHAPE, cat))                    # no gemma-4-12b entry
        self.assertIsNone(matching_drafter({**G12_SHAPE, "arch": "qwen"}, cat2))
        self.assertIsNone(matching_drafter({**G12_SHAPE, "blocks": 35}, cat2))  # an unknown size (E2B)
        self.assertIsNone(matching_drafter(None, cat2))

    def test_the_real_catalogue_has_a_drafter_for_every_size(self) -> None:
        import json
        import os
        from carl_core.domain.drafters import GEMMA4_SIZES
        with open(os.path.join(os.path.dirname(__file__), "..", "host", "catalog.json"), encoding="utf-8") as f:
            names = {m["name"]: m for m in json.load(f)["models"]}
        for name in GEMMA4_SIZES.values():
            self.assertIn("draft", names[name], name)

    def test_a_custom_gemma_file_is_offered_the_drafter(self) -> None:
        w = gemma12_world()
        m = model(w, "fine-12b-q4_k_m")
        self.assertEqual((m.get("draft_for"), m.get("draft_offer")), ("gemma-4-12b", draft_ref()))
        self.assertNotIn("draft", m)                        # offered, not recorded yet

    def test_adding_records_it_and_the_download_fetches_only_the_drafter(self) -> None:
        w = gemma12_world()
        w.folder.hashes[f"{MDIR}/mtp-Gem-Q4_0.gguf"] = SHA_D
        m = w.carl.add_drafter(model(w, "fine-12b-q4_k_m"))
        self.assertEqual(m.get("draft_path"), f"{MDIR}/mtp-Gem-Q4_0.gguf")
        saved = w.local.doc["models"]["fine-12b-q4_k_m"]       # type: ignore[index]
        self.assertEqual((saved["draft"], saved["path"], saved["source"]), (draft_ref(), FINE_PATH, "file"))
        self.assertTrue(any("same size as gemma-4-12b" in x for x in w.console.lines))
        self.assertTrue(w.carl.download_drafter(m))
        self.assertEqual([c[2] for c in w.downloader.calls], ["mtp-Gem-Q4_0.gguf"])
        again = model(w, "fine-12b-q4_k_m")                    # recorded: start, fit and delete see it
        self.assertEqual((again.get("draft_status"), again.get("draft_path")), ("downloaded", f"{MDIR}/mtp-Gem-Q4_0.gguf"))
        self.assertNotIn("draft_offer", again)
        names = [x["name"] for x in w.carl.all_models(Config())]
        self.assertNotIn("mtp-gem-q4_0", names)               # the drafter is not a model of its own
        self.assertEqual(dm.mtp_source(again, G12_SHAPE), "drafter")

    def test_no_offer_for_other_models_or_a_recorded_drafter(self) -> None:
        w = gemma12_world({FINE_PATH: 7 * GIB, QWEN_PATH: 10 * GIB})
        self.assertNotIn("draft_offer", model(w, "qwen"))
        w2 = gemma12_world(local={"schema": 1, "models": {"fine": {"path": FINE_PATH, "source": "file",
                                                                    "draft": draft_ref()}}})
        self.assertNotIn("draft_offer", model(w2, "fine"))
