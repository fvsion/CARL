// Prints how many bytes the GPU may use on this Mac (Metal's
// recommendedMaxWorkingSetSize). Used by tools/gguf_shape.py (cached in
// ~/models/.metal-limit). Needs the Xcode command-line tools: swift metal-limit.swift
import Metal
if let d = MTLCreateSystemDefaultDevice() { print(d.recommendedMaxWorkingSetSize) }
