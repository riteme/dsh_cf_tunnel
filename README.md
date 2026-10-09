# dsh-cf-tunnel

Serve the [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) Web UI through a
Cloudflare quick tunnel, and print the public URL together with a scannable terminal QR code.

```console
$ dsh-cf-tunnel
11:48:19 | INFO    | using cloudflared: cloudflared version 2026.10.0 (built 2026-10-05-17:37 UTC)
11:48:19 | INFO    | auth shim listening on 127.0.0.1:39503 -> dsh on 127.0.0.1:45263
11:48:26 | INFO    | quick tunnel ready: https://tidy-river-jumps-quietly.trycloudflare.com
11:48:28 | INFO    | dsh Web UI ready on loopback port 45263
11:48:41 | INFO    | public URL answered with HTTP 200 after 4 attempt(s)
========================================================================
  Public URL : https://tidy-river-jumps-quietly.trycloudflare.com/?token=…
  Tunnel     : https://tidy-river-jumps-quietly.trycloudflare.com
  Local URL  : http://127.0.0.1:45263/?token=…
========================================================================
  Scan to open the dsh Web UI (background style):

  ██████████████████████████████
  ██ ▄▄▄▄▄ █▀▄█▀▄▀█ ▄▀▄ ▄▄▄▄▄ ██
  ██ █   █ █▀▄ ▄ ▀▄▀█▄█ █   █ ██
  …
```

Scan the code with a phone, or send someone the URL, and the dsh Web UI opens from anywhere.

## Requirements

- Python 3.9 or newer.
- The `dsh` CLI on `PATH` (or `--dsh-bin /path/to/dsh`). This package launches it, it does not ship it.
- `cloudflared` on `PATH` (or `--cloudflared-bin /path/to/cloudflared`). When it is missing the
  program prints how to install the latest release and exits with status 1.

## Install

```console
pip install dsh-cf-tunnel          # or: uv tool install dsh-cf-tunnel / pipx install dsh-cf-tunnel
```

## Usage

```console
dsh-cf-tunnel                      # pick a free port, open a tunnel, print the URL and QR code
dsh-cf-tunnel --port 8123          # use a fixed port
dsh-cf-tunnel --qr-style half      # compact QR code for a narrow terminal
dsh-cf-tunnel --qr-png ./qr.png    # also write the QR code to a PNG file
dsh-cf-tunnel --no-qr              # print the URL only
dsh-cf-tunnel --no-host-page       # leave dsh unpatched (see "Settings pages" below)
```

Press `Ctrl-C` to stop; the tunnel and the dsh Web UI are shut down with it.

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--port` | `0` | Loopback port for the dsh Web UI; `0` picks a free one |
| `--dsh-bin` | `dsh` | dsh executable to launch |
| `--cloudflared-bin` | `cloudflared` | cloudflared executable |
| `--ready-timeout` | `60` | Seconds to wait for the tunnel URL and then the dsh URL |
| `--public-timeout` | `120` | Seconds to wait until the public URL really answers; `0` skips the check |
| `--qr-style` | `auto` | `background`, `half`, `plain`, or `auto` (see below) |
| `--qr-border` | `2` | Quiet zone around the printed QR code, in modules |
| `--qr-png PATH` | – | Also write the QR code to a PNG file |
| `--no-qr` | – | Do not print a QR code |
| `--runtime-dir PATH` | temp dir | Where the generated dsh patch files are written |
| `--no-host-page` | – | Do not patch dsh with the host-owned page flag |
| `--shim-open-index` | – | Let the auth shim serve the index without a token (weaker) |
| `--no-auth-shim` | – | Connect the tunnel straight to dsh |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

## How it works

```
browser ──https──▶ Cloudflare edge ──tunnel──▶ cloudflared ──▶ auth shim ──▶ dsh --profile web
                                                              127.0.0.1       127.0.0.1
```

1. **cloudflared preflight.** The binary is resolved from `PATH`; when it is missing, the exact
   install commands for the running platform are printed (apt repository or the latest GitHub
   release asset) and the program exits non-zero.
2. **Quick tunnel.** `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<shim>` is started
   and its `https://<name>.trycloudflare.com` hostname is parsed from the log. A failed tunnel
   request is reported instead of being mistaken for a tunnel URL.
3. **Auth shim.** dsh authenticates a browser by trading the launch token for an `HttpOnly`,
   `SameSite=Strict`, authority-bound cookie during a `303` redirect. Clients that open the URL
   from another app, a WebView, or a browser extension routinely lose that cookie and land on
   `dsh web authentication required`. The shim answers the token URL with the application itself
   and attaches the session cookie to everything it forwards — static assets, `/api`, and the
   `/api/remote.mux` WebSocket upgrade.
4. **dsh with a generated patch.** `dsh --patch <generated> --profile web --port <port>
   --trusted-host <tunnel host>` is started. The tunnel hostname is not a loopback authority, so
   without `--trusted-host` every `/api` request would fail the browser-trust fence with 403; and
   because the browser side keeps its settings pages unavailable on a page that does not count as
   loopback, the generated patch publishes `globalThis.__DSH_TRANSPORT__ = {ownsHost: true}` — the
   same flag the desktop shell sets for the pages it serves itself.
5. **Banner.** Once the public URL answers, the URL is printed with a QR code and both children are
   supervised until `Ctrl-C` or until one of them exits.

### QR styles

Module colors are always drawn explicitly as black on white, so a dark terminal theme cannot
invert the code:

| `--qr-style` | Rendering | Width | Notes |
| --- | --- | --- | --- |
| `background` | Two spaces per module on an explicit black or white background | 2 cells/module | Default in a terminal when it fits; square modules, no font glyphs involved, no seams |
| `half` | Upper half block per top module, cell background per bottom module | 1 cell/module | Half the width; depends on the font drawing `▀` as a full half cell |
| `plain` | Half blocks without color | 1 cell/module | Used automatically when stdout is not a terminal |

`--qr-style auto` prefers `background`, falls back to `half` when the terminal is too narrow, and
uses `plain` when the output is redirected. A warning is printed when the code is wider than the
terminal; `--qr-png` is the fallback for scanners that cannot read terminal art at all.

## Settings pages

The Settings and Models pages in dsh are deliberately limited to loopback pages. A tunneled page is
never loopback, so without the generated patch those pages report:

```
Loading the provider directory failed: settings are unavailable in this browser
```

No request is made in that case, which is why the browser console and network panel stay clean. The
default patch lifts that limit for the tunneled page. Pass `--no-host-page` to keep dsh's original
behavior.

## Security

The printed URL contains the dsh launch token: **anyone who has the link can drive the agent on your
machine**, including running commands. The auth shim does not change that, and `--shim-open-index`
additionally treats the tunnel hostname itself as the secret. Stop the program (`Ctrl-C`) when you
are done, and prefer a short-lived session over a long-running one.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `dsh web authentication required` | A client that could not keep the session cookie. The shim is on by default; if you disabled it with `--no-auth-shim`, re-enable it. If the client also mangles the URL, add `--shim-open-index`. |
| `settings are unavailable in this browser` | The page is not host-owned. Do not pass `--no-host-page`, and make sure the generated patch was applied (`--log-level DEBUG` prints its path). |
| Public URL does not resolve yet | A fresh quick tunnel needs a moment to propagate; the program waits for the first answer before printing the banner. |
| QR code is unreadable | Try `--qr-style background`, lower `--qr-border`, or write `--qr-png ./qr.png`. |
| cloudflared download fails | Export `HTTPS_PROXY`/`HTTP_PROXY`, or install cloudflared from the apt repository printed in the message. |

## Development

```console
uv venv --python 3.12 .venv
uv pip install -e '.[dev]'
uv run pytest                      # unit tests
DSH_CF_TUNNEL_E2E=1 uv run pytest -m e2e   # boots a real tunnel, needs dsh + cloudflared
uv build                           # wheel and sdist in dist/
```

Releases are cut by hand with `uv build` + `uv publish`; the full checklist (PyPI tokens, version
bump, TestPyPI rehearsal) is in [RELEASING.md](RELEASING.md), and notable changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
