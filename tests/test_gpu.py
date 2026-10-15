"""A discrete GPU on Windows and Linux (tools/carl_core/domain/gpu.py): the parse of the GPU tools' output, the
llama.cpp build for a GPU, the release files of that build, and what a model can use. Made-up tool output in the
formats that the tools print (llama.cpp b11429, nvidia-smi 616, amdgpu sysfs)."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from carl_core.domain import gpu as dg
from carl_core.domain.autofit import Budget

MIB, GIB = 2 ** 20, 2 ** 30
LIST_CUDA = ("load_backend: loaded CUDA backend from C:\\llama\\ggml-cuda.dll\n"
             "Available devices:\n  CUDA0: NVIDIA GeForce RTX 3060 (12287 MiB, 11200 MiB free)\n")
LIST_TWO = ("Available devices:\n  Vulkan0: AMD Radeon RX 7800 XT (16368 MiB, 15000 MiB free)\n"
            "  Vulkan1: AMD Radeon(TM) Graphics (512 MiB, 400 MiB free)\n")
SMI = "0, NVIDIA GeForce RTX 3060, 12288, 9000, 616.56, 8.6\n"
SMI_HEADER = "| NVIDIA-SMI 616.56                 KMD Version: 616.56        CUDA UMD Version: 13.4     |"
# made-up asset names in the pattern of a llama.cpp release
ASSETS = ["cudart-llama-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip",
          "cudart-llama-b9999-bin-ubuntu-cuda-13.4-x64.tar.gz", "llama-b9999-bin-macos-arm64.tar.gz",
          "llama-b9999-bin-ubuntu-x64.tar.gz", "llama-b9999-bin-ubuntu-cuda-13.4-x64.tar.gz",
          "llama-b9999-bin-ubuntu-vulkan-x64.tar.gz", "llama-b9999-bin-ubuntu-rocm-10.0-x64.tar.gz",
          "llama-b9999-bin-win-cpu-x64.zip", "llama-b9999-bin-win-cuda-12.4-x64.zip",
          "llama-b9999-bin-win-cuda-13.4-x64.zip", "llama-b9999-bin-win-cuda-13.4-arm64.zip",
          "cudart-llama-bin-win-cuda-13.4-arm64.zip", "llama-b9999-bin-win-vulkan-x64.zip",
          "llama-b9999-bin-win-rocm-10.0-x64.zip", "llama-b9999-bin-win-sycl-x64.zip"]


class ParseTest(unittest.TestCase):
    def test_list_devices(self) -> None:
        (g,) = dg.parse_list_devices(LIST_CUDA)
        self.assertEqual((g.backend, g.index, g.name, g.vendor), ("CUDA", 0, "NVIDIA GeForce RTX 3060", "nvidia"))
        self.assertEqual((g.total, g.free), (12287 * MIB, 11200 * MIB))
        two = dg.parse_list_devices(LIST_TWO)
        self.assertEqual([(x.backend, x.index, x.vendor) for x in two], [("Vulkan", 0, "amd"), ("Vulkan", 1, "amd")])
        self.assertEqual(dg.main_gpu(two), two[0])                  # the most VRAM, not the integrated GPU
        self.assertEqual(dg.parse_list_devices("Available devices:\n  (none)\n"), [])

    def test_nvidia_smi(self) -> None:
        (g,) = dg.parse_nvidia_smi(SMI)
        self.assertEqual((g.total, g.free, g.compute_cap, g.driver), (12288 * MIB, 9000 * MIB, (8, 6), "616.56"))
        self.assertEqual(dg.parse_nvidia_smi("No devices were found\n"), [])
        self.assertEqual(dg.cuda_version(SMI_HEADER), (13, 4))
        self.assertEqual(dg.cuda_version("| NVIDIA-SMI 550.54   Driver Version: 550.54   CUDA Version: 12.4 |"),
                         (12, 4))
        self.assertIsNone(dg.cuda_version(""))

    def test_sysfs_and_windows(self) -> None:
        g = dg.parse_sysfs_vram("17163091968\n", "1073741824\n")
        assert g is not None
        self.assertEqual((g.vendor, g.total - g.free), ("amd", GIB))
        self.assertIsNone(dg.parse_sysfs_vram("x", "0"))
        w = dg.windows_adapter(8 * GIB, 3 * GIB, "AMD Radeon RX 6600", "pci\\ven_1002&dev_73ff")
        self.assertEqual((w.vendor, w.free), ("amd", 5 * GIB))
        self.assertIsNone(dg.windows_adapter(8 * GIB, None, "x", "pci\\ven_8086&dev_1").free)

    def test_vendor(self) -> None:
        for text, want in (("pci\\ven_10de&dev_1ec7", "nvidia"), ("Radeon RX 7900 XTX", "amd"),
                           ("Intel(R) Arc(TM) A770", "intel"), ("Parsec Virtual Display Adapter", "")):
            with self.subTest(text=text):
                self.assertEqual(dg.vendor_of(text), want)


class BuildTest(unittest.TestCase):
    def gpu(self, vendor: str, cc: tuple[int, int] | None = None) -> dg.Gpu:
        return dg.Gpu("a GPU", 8 * GIB, 6 * GIB, vendor, compute_cap=cc)

    def test_nvidia(self) -> None:
        self.assertEqual(dg.choose_build("Windows", self.gpu("nvidia", (7, 5)), (13, 4)).backend, "cuda-13")
        self.assertEqual(dg.choose_build("Windows", self.gpu("nvidia", (6, 1)), (13, 4)).backend, "cuda-12")
        self.assertEqual(dg.choose_build("Linux", self.gpu("nvidia", (8, 9)), (12, 8)).backend, "cuda-12")
        self.assertEqual(dg.choose_build("Windows", self.gpu("nvidia", (8, 6)), (12, 2)).backend, "vulkan")

    def test_amd_others_and_none(self) -> None:
        self.assertEqual(dg.choose_build("Windows", self.gpu("amd")).backend, "vulkan")
        self.assertEqual(dg.choose_build("Windows", self.gpu("amd"), prefer_rocm=True).backend, "rocm")
        self.assertEqual(dg.choose_build("Linux", self.gpu("intel")).backend, "vulkan")
        self.assertEqual(dg.choose_build("Windows", None).backend, "cpu")
        self.assertEqual(dg.choose_build("Darwin", None).backend, "metal")

    def test_release_files(self) -> None:
        self.assertEqual(dg.release_assets(ASSETS, "Windows", "AMD64", "cuda-13"),
                         ["llama-b9999-bin-win-cuda-13.4-x64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip"])
        self.assertEqual(dg.release_assets(ASSETS, "Windows", "AMD64", "cuda-12"),
                         ["llama-b9999-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"])
        self.assertEqual(dg.release_assets(ASSETS, "Windows", "ARM64", "cuda-13"),
                         ["llama-b9999-bin-win-cuda-13.4-arm64.zip", "cudart-llama-bin-win-cuda-13.4-arm64.zip"])
        self.assertEqual(dg.release_assets(ASSETS, "Windows", "AMD64", "rocm"), ["llama-b9999-bin-win-rocm-10.0-x64.zip"])
        self.assertEqual(dg.release_assets(ASSETS, "Windows", "AMD64", "vulkan"), ["llama-b9999-bin-win-vulkan-x64.zip"])
        self.assertEqual(dg.release_assets(ASSETS, "Linux", "x86_64", "cpu"), ["llama-b9999-bin-ubuntu-x64.tar.gz"])
        self.assertEqual(dg.release_assets(ASSETS, "Linux", "x86_64", "cuda-13"),
                         ["llama-b9999-bin-ubuntu-cuda-13.4-x64.tar.gz",
                          "cudart-llama-b9999-bin-ubuntu-cuda-13.4-x64.tar.gz"])
        self.assertEqual(dg.release_assets(ASSETS, "Linux", "x86_64", "cuda-12"), [])     # this release has none
        self.assertEqual(dg.release_assets(ASSETS, "Darwin", "arm64", "metal"), [])


class LimitTest(unittest.TestCase):
    def test_free_vram_less_the_kept_part(self) -> None:
        g = dg.Gpu("x", 8 * GIB, 4 * GIB)
        self.assertEqual(dg.vram_limit(g), 4 * GIB - dg.VRAM_KEEP)
        self.assertEqual(dg.vram_limit(dg.Gpu("x", 8 * GIB)), 8 * GIB - dg.VRAM_KEEP)   # free not known
        self.assertEqual(dg.vram_limit(dg.Gpu("x", GIB, 0)), 0)

    def test_a_discrete_budget_is_not_capped_by_the_ram(self) -> None:
        """A 16 GB PC with a 16 GB GPU: the model is in the VRAM, so the RAM less the reserve does not cap it."""
        mac = Budget(15 * GIB, 16 * GIB, 6 * GIB)
        pc = Budget(15 * GIB, 16 * GIB, 6 * GIB, unified=False)
        self.assertEqual((mac.allowed, pc.allowed), (10 * GIB, 15 * GIB))
        self.assertIn("free GPU memory", pc.describe())
        self.assertIn("apart from the RAM", pc.explain())
        self.assertIn("macOS", mac.describe())


if __name__ == "__main__":
    unittest.main()
