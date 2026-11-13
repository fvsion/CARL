// Prints how many bytes the GPU may use on this Mac (Metal's
// recommendedMaxWorkingSetSize). Run by MacGpuLimit in src/carl/adapters/macos/sysctl_host.py
// (cached in ~/models/.metal-limit). Needs the Xcode command-line tools: swift metal-limit.swift
import Metal
if let d = MTLCreateSystemDefaultDevice() { print(d.recommendedMaxWorkingSetSize) }
