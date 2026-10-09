# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-09

### Added

- `dsh-cf-tunnel` command: boots `dsh --profile web`, exposes it through a Cloudflare quick
  tunnel, and prints the public URL with a scannable terminal QR code.
- Startup preflight for both external tools: `cloudflared` and the `dsh` CLI are resolved before
  anything is started, and every missing one is reported with install instructions in a single
  exit. The program never installs or upgrades either tool.
- Platform-aware cloudflared install guidance (apt repository or the latest GitHub release asset),
  and a non-zero exit when the binary is missing.
- Loopback auth shim: the token URL answers with the application and its session cookie
  directly, and every forwarded request carries that cookie, so clients that cannot keep a
  cookie across dsh's `303` redirect still work.
- Generated `dsh --patch` overlay that marks the tunneled page host-owned, which is what makes
  dsh's Settings and Models pages load remotely.
- Three terminal QR styles (`background`, `half`, `plain`) with automatic selection, plus a
  PNG fallback.
- Verification that the public URL really answers before the banner is printed.
- Cooperative shutdown: `Ctrl-C` stops cloudflared and the dsh Web UI together.

[0.1.0]: https://github.com/riteme/dsh_cf_tunnel/releases/tag/dsh-cf-tunnel-v0.1.0
