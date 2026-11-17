This is crates.io `io-surface` 0.16.1, under its original MIT / Apache-2.0 licenses.
The only source changes gate the `cgl` dependency/import and `bind_to_gl_texture`
method to macOS. GPUI's iOS core-video dependency uses IOSurface, but CGL links
the macOS-only OpenGL framework. iOS rendering continues to use Metal.

Remove this patch when the upstream dependency handles that platform boundary.
