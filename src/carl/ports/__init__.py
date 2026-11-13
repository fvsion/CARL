"""Ports: what the domain and the application services need from the outside world (plan 4, R2).

One module per context; every Protocol is also here, so `from carl.ports import ModelFolder` keeps working.
Adapters in carl.adapters implement them, and the tests have one fake per port. A port declares
every attribute its callers read (no getattr with a default). A contract test checks that
every adapter and every fake satisfies its Protocol.

  storage       JsonDocument, LegacyEnv, RuntimeStore, KeyStore, ConfDirMigration, TemplateStore,
                VersionSource, ClientFiles, PackageWriter
  cache         DiskCacheFolder, CacheWatch, ClientConfigStore
  models        ModelFolder, ShapeReader
  hardware      GpuLimit, HostMemory, GpuInventory, OwnServerVram, HostInfo, CpuLayout
  process       ProcessTable, ProcessControl, ServerRunner, KeepAwake
  network       PortProbe, NetworkInterfaces
  llama         LlamaCpp, LlamaReleaseInstaller, Dependencies, MemoryDryRun, ServerFit
  engine        EngineAdapter
  hub           HubClient, Downloader
  secrets       SecretStore
  http          LlamaServerApi, ServerEndpoint
  terminal      Terminal
  system_stats  SystemStats, ServerWatch, ServerFiles, TcpTable
  desktop       Desktop
  tune          TuneServer, Progress
  ui            Console
  clock         Clock
  plugin        Plugin, PluginHost, PluginComputer, SessionFolders (the plugin seam, WP; the values a plugin and the
                host exchange are in the module)
"""
from __future__ import annotations

from .cache import CacheWatch, ClientConfigStore, DiskCacheFolder
from .clock import Clock
from .desktop import Desktop
from .engine import EngineAdapter
from .hardware import CpuLayout, GpuInventory, GpuLimit, HostInfo, HostMemory, OwnServerVram
from .http import LlamaServerApi, ServerEndpoint
from .hub import Downloader, HubClient
from .llama import Dependencies, LlamaCpp, LlamaReleaseInstaller, MemoryDryRun, ServerFit
from .models import ModelFolder, ShapeReader
from .network import NetworkInterfaces, PortProbe
from .plugin import Plugin, PluginComputer, PluginHost, SessionFolders
from .process import KeepAwake, ProcessControl, ProcessTable, ServerRunner
from .secrets import SecretStore
from .storage import (ClientFiles, ConfDirMigration, JsonDocument, KeyStore, LegacyEnv, PackageEntry, PackageFile,
                      PackageWriter, RuntimeStore, TemplateStore, VersionSource)
from .system_stats import ServerFiles, ServerWatch, SystemStats, TcpTable
from .terminal import Terminal
from .tune import Progress, TuneServer
from .ui import Console

__all__ = [
    "CacheWatch", "ClientConfigStore", "ClientFiles", "Clock", "ConfDirMigration", "Console", "CpuLayout", "Dependencies",
    "Desktop",
    "DiskCacheFolder", "Downloader", "EngineAdapter", "GpuInventory",
    "GpuLimit", "HostInfo", "HostMemory", "HubClient", "JsonDocument", "KeepAwake", "KeyStore", "LegacyEnv",
    "LlamaCpp", "LlamaReleaseInstaller", "LlamaServerApi", "MemoryDryRun", "ModelFolder", "NetworkInterfaces",
    "OwnServerVram",
    "PackageEntry", "PackageFile", "PackageWriter", "Plugin", "PluginComputer", "PluginHost", "PortProbe",
    "ProcessControl", "ProcessTable", "Progress",
    "RuntimeStore", "SecretStore", "ServerEndpoint", "ServerFiles", "ServerFit", "ServerRunner", "ServerWatch",
    "SessionFolders",
    "ShapeReader", "SystemStats",
    "TcpTable", "TemplateStore", "Terminal", "TuneServer", "VersionSource",
]
