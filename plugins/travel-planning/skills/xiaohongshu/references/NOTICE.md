# Third-party notice

## xpzouying/xiaohongshu-mcp

Source: <https://github.com/xpzouying/xiaohongshu-mcp>

Pinned release: `v2.5.0` at commit `6583124dfda92312b6bc19a042a6acfae63fe498`

Licensed under the Apache License 2.0; a copy is provided in `LICENSE.upstream`. The plugin distributes a local modification (`search-wait.patch`, `scripts/search_ready.go`) and its regression tests. It replaces whole-page stability waiting with bounded query-specific readiness checks. The full upstream source and browser extension are not vendored.

The installer verifies the pinned source archive and an isolated Go toolchain using the SHA256 values in `upstream.lock.json`, applies the local modification, and builds the server locally. The login binary remains an official, SHA256-verified release asset. Local build manifests record upstream commit, repair fingerprint, Go version and binary SHA256; they are not upstream release signatures. The original server release digests remain in the lock as upstream provenance, not as the digests of repaired builds.

This is an unofficial integration and does not claim that Xiaohongshu endorses or authorizes it.
