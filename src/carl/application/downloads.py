"""Downloads, checks and deletes of model files: pinned catalogue files and any GGUF from Hugging Face (resumable,
SHA-256 checked), each model's drafters (one folder for each kind) and its vision projector beside it (WCATX, A74), and
the records in models.json.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from ..domain.catalog import inventory as dm, layout
from ..domain.catalog.drafts import KIND_WORDS, MTP_FILE, VISION_WORDS, drafts_of, vision_of
from ..domain.catalog.hf import (commit_sha, download_url, gguf_files, local_file_name, model_name, parse_hf,
                                 revision_api_path, tree_api_path, validate_repo)
from ..domain.catalog.hf_token import DENIED_EXIT, denied_text
from ..domain.shared.errors import ConfigError, HubDenied
from ..domain.shared.types import HfFileList, HfRef, JsonValue, ModelInfo
from ..domain.shared.units import file_size
from ..ports import Clock, Console, Downloader, HubClient, ModelFolder
from .config import ConfigService
from .hub_token import HubToken
from .inventory import Inventory

DOWNLOAD_HEADROOM = 5e9                    # free disk space to keep beyond the file


class Downloads:
    """The model files: list on Hugging Face, download, verify, delete. Contract C-109."""

    def __init__(self, config: ConfigService, inventory: Inventory, files: ModelFolder, hub: HubClient,
                 downloader: Downloader, clock: Clock, console: Console, token: Optional[HubToken] = None) -> None:
        self.token = token                  # the user's Hugging Face token (None: no token seam, as a test's Carl)
        self.config = config
        self.inventory = inventory
        self.files = files
        self.hub = hub
        self.downloader = downloader
        self.clock = clock
        self.console = console

    def _has_token(self) -> bool:
        return bool(self.token and self.token.current())

    def _denied(self, repo: str, code: int) -> ConfigError:
        """The error for a 401 or 403 of Hugging Face: the repo is gated, and how to give CARL the token."""
        return ConfigError(denied_text(repo, code, self._has_token(), self.config.words.command))

    def _hub_get(self, repo: str, api_path: str) -> JsonValue:
        try:
            return self.hub.get(api_path)
        except HubDenied as e:
            raise self._denied(repo, e.code) from None

    def hf_files(self, repo: str, revision: str = "main") -> HfFileList:
        """[(file, bytes, sha256)] of the GGUF files in a Hugging Face repo (first parts only). The call carries the
        user's token: a gated repo lists other SHA-256s without it than with it (decision A75)."""
        repo = validate_repo(repo)
        return gguf_files(self._hub_get(repo, tree_api_path(repo, revision)))

    def _listed_sha(self, ref: HfRef) -> Optional[str]:
        """The SHA-256 that Hugging Face lists for a pinned file when the list is read again with the token; None
        when there is no token, the list cannot be read or the file is not in it."""
        if not self._has_token() or not ref.get("repo") or not ref.get("file"):
            return None
        try:
            found = [sha for f, _, sha in self.hf_files(ref.get("repo", ""), ref.get("revision") or "main")
                     if f == ref.get("file")]
        except ConfigError:
            return None
        return found[0] if found and found[0] else None

    def _check_sha(self, label: str, path: str, want: str, ref: Optional[HfRef] = None,
                   name: str = "") -> bool:
        """A file against its pinned SHA-256 (progress and the result on the console). A file that does not match
        is not called bad before the list is read again with the user's token (ref: the file's record; a gated repo's
        list answers other SHA-256s without the token than with it): when that list holds the file's SHA-256, the file
        is right, and a custom model's record (name) takes the SHA-256 of the list."""
        self.console.info(f"  {label}: CARL checks the SHA-256 of {file_size(self.files.size(path))}...")
        got = self.files.sha256(path)
        if got != want and ref is not None and self._listed_sha(ref) == got:
            self.console.info(f"  {label}: the list that Hugging Face gives with your token has this SHA-256, not "
                              f"the one that was recorded ({want}). The file is correct.")
            self._repin(name, got)
            want = got
        if got != want:
            self.console.error(f"  {label}: bad file. The SHA-256 is {got}, not {want}.")
            return False
        self.console.info(f"  {label}: the file is correct (SHA-256 {got}).")
        return True

    def verify(self, m: ModelInfo) -> bool:
        """Check a downloaded file against its pinned SHA-256, and its MTP drafter's when it has one
        (a drafter that is not downloaded is reported, not an error: starts use n-gram)."""
        return self._verify_model(m) and self._verify_draft(m) and self._verify_beside(m)

    def _verify_model(self, m: ModelInfo) -> bool:
        name, path = m.get("name", ""), m.get("path", "")
        ref: HfRef = m.get("hf") or {}
        want = ref.get("sha256")
        if m.get("status") != "downloaded" and not (path and self.files.exists(path)):
            self.console.error(f"  {name}: not downloaded.")
            return False
        if not want:
            self.console.info(f"  {name}: not checked. CARL does not know the SHA-256 of a local file.")
            return True
        if not self._check_sha(name, path, want, ref, name):
            return False
        if m.get("custom"):                   # catalogue models are pinned in the repo already
            db = self.inventory.load_local()
            db["models"].setdefault(name, {})["verified"] = self.clock.today()
            self.inventory.save_local(db)
        return True

    def _verify_draft(self, m: ModelInfo) -> bool:
        draft = m.get("draft")
        dpath = m.get("draft_path", "")
        if not draft or not dpath:
            return True
        name = m.get("name", "")
        if dm.status_of(dpath, draft.get("bytes"), self.files) != "downloaded":
            self.console.info(f"  {name}: the MTP drafter is not downloaded, so a start uses n-gram speculation only. "
                              f"To download it: {self.config.words.command} download {name}")
            return True
        want = draft.get("sha256")
        return self._check_sha(f"{name} MTP drafter", dpath, want) if want else True

    def _verify_beside(self, m: ModelInfo) -> bool:
        """The SHA-256 of each downloaded drafter of another kind (DFlash, DSpark, EAGLE-3) and of the vision projector;
        one that is not downloaded is reported (the projector, which is off by default, is not)."""
        name, ok = m.get("name", ""), True
        for d in dm.file_drafts(m):
            path = str(d.get("path", ""))
            label = f"{name} {KIND_WORDS.get(str(d.get('kind')), 'drafter')}"
            if not path or dm.status_of(path, d.get("bytes"), self.files) != "downloaded":
                self.console.info(f"  {label}: not downloaded, so a start does not use it. To download it: "
                                  f"{self.config.words.command} download {name}")
            elif d.get("sha256"):
                ok = self._check_sha(label, path, str(d["sha256"])) and ok
        vision, vpath = vision_of(m), str(m.get("vision_path", ""))
        if vision and vpath and dm.status_of(vpath, vision.get("bytes"), self.files) == "downloaded" \
                and vision.get("sha256"):
            ok = self._check_sha(f"{name} {VISION_WORDS}", vpath, str(vision["sha256"])) and ok
        return ok

    def _fetch(self, label: str, ref: HfRef, folder: str) -> Optional[str]:
        """Download one pinned file into a folder (the model's own, made here; resumable) and check its size; its
        path, or None after an error (reported)."""
        size = ref.get("bytes", 0)
        file = local_file_name(ref.get("file", ""))
        path = os.path.join(folder, file)
        self.files.make_dir(folder)
        free = self.files.free_bytes(folder) or 0
        have = self.files.size(path) if self.files.exists(path) else 0
        if free < size + DOWNLOAD_HEADROOM - have:
            self.console.error(f"error: {label} needs {file_size(size)} and 5 GB more on the disk, but only "
                               f"{file_size(free)} is free.")
            return None
        self.console.info(f"{label}: CARL downloads {file_size(size)} to {path}.")
        rc = self.downloader.fetch(download_url(ref), folder, file)
        if rc == DENIED_EXIT:
            text = denied_text(ref.get("repo", ""), rc, self._has_token(), self.config.words.command)
            self.console.error(f"error: {text}")
            return None
        if rc != 0 or not self.files.exists(path):
            self.console.error(f"error: the download failed (exit {rc}). Run the command again to continue it.")
            return None
        if size and self.files.size(path) != size:
            self.console.error(f"error: {path} does not have the correct size.")
            return None
        return path

    def _repin(self, name: str, sha: str) -> None:
        """A model added from Hugging Face (source hf in models.json) takes the SHA-256 that the list gives with the
        token; a catalogue model's record is the catalogue's and is left as it is."""
        if not name:
            return
        db = self.inventory.load_local()
        entry = db["models"].get(name) or {}
        hf = entry.get("hf")
        if entry.get("source") == "hf" and isinstance(hf, dict) and hf.get("sha256") != sha:
            hf["sha256"] = sha
            self.inventory.save_local(db)

    def _bad_checksum(self, path: str) -> None:
        self.files.rename(path, path + dm.BAD_SUFFIX)
        self.console.error(f"error: the SHA-256 is not correct. CARL renamed the file to {path}{dm.BAD_SUFFIX}.")

    def _fetch_checked(self, label: str, ref: HfRef, path: str) -> bool:
        """Download one pinned file beside a model into path's folder, unless it is there, and check its SHA-256."""
        if dm.status_of(path, ref.get("bytes"), self.files) == "downloaded":
            self.console.info(f"The {label} is already downloaded: {path}")
            return True
        got = self._fetch(label, ref, os.path.dirname(path))
        if got is None:
            return False
        want = ref.get("sha256")
        if want and not self._check_sha(label, got, want):
            self._bad_checksum(got)
            return False
        return True

    def _beside(self, m: ModelInfo, path: str, models_dir: str, vision: bool) -> List[Tuple[str, HfRef, str]]:
        """(label, pinned file, where it goes) of each file beside the model that the catalogue names and is not
        downloaded: the drafters of every kind other than MTP's (each in the folder of its kind) and, when vision is
        wanted, the projector (vision/)."""
        name, out = m.get("name", ""), []
        for d in drafts_of(m):
            kind = str(d.get("kind", MTP_FILE))
            if kind == MTP_FILE:
                continue                                        # the MTP drafter has its own place in download()
            p = layout.locate_beside(path, local_file_name(d.get("file", "")), self.files, models_dir,
                                     layout.DRAFTER_KINDS[kind][0])
            out.append((f"{name} {KIND_WORDS[kind]}", d, p))
        v = vision_of(m)
        if vision and v:
            out.append((f"{name} {VISION_WORDS}",
                        v, layout.locate_beside(path, local_file_name(v.get("file", "")), self.files, models_dir,
                                                layout.VISION)))
        return [x for x in out if dm.status_of(x[2], x[1].get("bytes"), self.files) != "downloaded"]

    def download_drafter(self, m: ModelInfo) -> bool:
        """Download a model's recorded MTP drafter next to its file (resumable, SHA-256 checked)."""
        draft: HfRef = m.get("draft") or {}
        if not draft:
            return True
        dpath = m.get("draft_path") or os.path.join(os.path.dirname(m.get("path", "")),
                                                      local_file_name(draft.get("file", "")))
        folder = os.path.dirname(dpath)
        if dm.status_of(dpath, draft.get("bytes"), self.files) == "downloaded":
            self.console.info(f"The MTP drafter of {m.get('name')} is already downloaded: {dpath}")
            return True
        dlabel = f"{m.get('name')} MTP drafter"
        got = self._fetch(dlabel, draft, folder)
        if got is None:
            return False
        want = draft.get("sha256")
        if want and not self._check_sha(dlabel, got, want):
            self._bad_checksum(got)
            return False
        return True

    def download(self, m: ModelInfo, models_dir: str, vision: Optional[bool] = None) -> bool:
        """Download a pinned file into its quant folder in the model's own folder in the models folder (resumable),
        then verify it; then its drafters, each into the folder of its kind (mtp/, dflash/, dspark/, eagle3/), and
        its vision projector into vision/, when the catalogue names them (WCATX). The projector comes when vision is
        wanted: vision True, or None and llama.vision is on (the projector costs about 1 GB, so it is off by default).
        A file in an older place (layout.py) is used where it is. When only a drafter or the projector is missing,
        only that is fetched (carl verify NAME checks the model)."""
        hf: HfRef = m.get("hf") or {}
        name, size = m.get("name", ""), hf.get("bytes", 0)
        path = layout.locate(models_dir, local_file_name(hf.get("file", "")), self.files)
        draft: HfRef = m.get("draft") or {}
        dpath = layout.locate_beside(path, local_file_name(draft.get("file", "")), self.files, models_dir) \
            if draft else ""
        need_draft = bool(draft) and dm.status_of(dpath, draft.get("bytes"), self.files) != "downloaded"
        if vision is None:
            vision = bool(self.config.load_config().llama.get("vision"))
        more = self._beside(m, path, models_dir, vision)
        done: ModelInfo = {**m, "path": path, "status": "downloaded"}
        if draft:
            done["draft_path"] = dpath
        if dm.status_of(path, size, self.files) == "downloaded":
            self.console.info(f"{name} is already downloaded: {path}")
            if not need_draft and not more:
                return self.verify(done)
        else:
            got = self._fetch(name, hf, os.path.dirname(path))
            if got is None:
                return False
            if not self._verify_model(done):                    # its drafters come next
                self._bad_checksum(path)
                return False
        if need_draft and not self.download_drafter({**m, "path": path, "draft_path": dpath}):
            return False
        ok = True
        for label, ref, where in more:
            ok = self._fetch_checked(label, ref, where) and ok
        return ok

    def download_hf(self, spec: str, name: Optional[str] = None) -> bool:
        """Any GGUF from Hugging Face: pin the revision, size and sha256, record it in
        models.json, download. A repo without a file lists its GGUF files instead."""
        repo, file, rev = parse_hf(spec)
        if not file:
            self.console.info(f"The GGUF files of {repo}. To download one: {self.config.words.command} download "
                              f"hf:{repo}/FILE.gguf")
            for f, b, _ in self.hf_files(repo, rev):
                self.console.info(f"  {f:60} {file_size(b):>8}")
            return False
        pinned = commit_sha(self._hub_get(repo, revision_api_path(repo, rev)), rev)
        match = [x for x in self.hf_files(repo, pinned) if x[0] == file]
        if not match:
            raise ConfigError(f"{repo} has no file {file}. To see its files: {self.config.words.command} download "
                              f"hf:{repo}")
        _, size, sha = match[0]
        name = name or model_name(file)
        mdir = self.config.models_dir(self.config.load_config())
        ref: HfRef = {"repo": repo, "revision": pinned, "file": file, "sha256": sha, "bytes": size}
        db = self.inventory.load_local()
        entry = db["models"].setdefault(name, {})
        entry.update({"source": "hf", "hf": ref, "path": layout.locate(mdir, local_file_name(file), self.files),
                      "added": self.clock.today()})
        self.inventory.save_local(db)
        if not self.download({"name": name, "hf": ref}, mdir):
            return False
        inv = self.inventory
        m = inv.find(name, inv.all_models(self.config.load_config()))
        if m and m.get("draft_offer"):                    # a Gemma 4 file: its drafter too, as for the catalogue
            return self.download_drafter(inv.add_drafter(m))
        return True

    def _shared_beside(self, m: ModelInfo) -> List[str]:
        """The drafters and the vision projector of M that another downloaded model still uses: quants of one model
        name one file in the model's folder, and a delete of one quant keeps it for the others."""
        out: List[str] = []
        for o in self.inventory.all_models(self.config.load_config()):
            if o.get("name") == m.get("name") or o.get("status") != "downloaded":
                continue
            out += [str(x.get("path", "")) for x in o.get("drafts", []) if x.get("path")]
            out += [str(o.get(k, "")) for k in ("draft_path", "vision_path") if o.get(k)]
        return out

    def delete(self, m: ModelInfo) -> None:
        """Delete a model's file and its drafters' and vision projector's (and their part/bad copies), and forget its
        verification. A drafter or projector that another downloaded model uses too stays. The folders they leave empty go too (the quant folder, mtp/, dflash/, vision/, the model's own
        folder); a folder with anything else in it (another quant, a lora/ folder) stays, and a file in an older
        place leaves no folder to remove."""
        beside = [str(x.get("path", "")) for x in m.get("drafts", []) if x.get("path")]
        paths = [m.get("path", "")] + [p for p in dict.fromkeys(
            ([m.get("draft_path", "")] if m.get("draft_path") else []) + beside
            + ([m.get("vision_path", "")] if m.get("vision_path") else []))]
        for path in paths:
            if not path.endswith(".gguf"):
                raise ConfigError(f"{m.get('name')}: CARL does not delete {path!r}, because it is not a .gguf file")
        shared = self._shared_beside(m)
        paths = [paths[0]] + [p for p in paths[1:] if not any(layout.same_file(p, s) for s in shared)]
        for path in paths:
            for p in (path, path + dm.PART_SUFFIX, path + dm.BAD_SUFFIX):
                self.files.remove(p)
        mdir = self.config.models_dir(self.config.load_config())
        for folder in sorted({f for path in paths for f in layout.tidy_folders(path, mdir)}, key=len, reverse=True):
            self.files.remove_empty_dir(folder)                 # the innermost first: a folder is empty only then
        db = self.inventory.load_local()
        name = m.get("name", "")
        if m.get("custom"):
            db["models"].pop(name, None)
        elif name in db["models"]:
            db["models"][name].pop("verified", None)
        self.inventory.save_local(db)
