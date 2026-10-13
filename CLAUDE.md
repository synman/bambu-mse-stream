# 📺 bambu-mse-stream — RTSPS to fMP4 relay

> forge ❯ bambu ❯ **bms** · application
>
> **Archetype:** Single-process relay + static viewer page
> **Language:** Python 3.10+ (PyAV) · vanilla JS
> **Interfaces:** HTTP (`/`, `/stream.mp4`, `/stats`) · RTSPS:322 client
> **Shared rules:** `~/ai/forge/bambu/.claude/rules/bambu-ecosystem.md`

## Build / Validate

- `python3 -m py_compile server.py`
- Live check: `docs/runbook.md` (ffprobe the source, start the relay, compare `/stats`)
- No test suite. The runbook is the verification.

## Architecture

- `server.py`: one PyAV reader thread demuxes H.264 over RTSPS, rebuilds timestamps,
  remuxes to fMP4 (`frag_keyframe` + 200 ms `frag_duration`), splits the muxer output into
  MP4 boxes, fans `moof`+`mdat` fragments out to HTTP clients. New clients get the init
  segment plus the fragments since the last keyframe.
- `index.html`: MediaSource player with live-edge seek, buffer trim, FPS HUD, `/stats` poll.
- Credentials: `BAMBU_HOSTNAME` and `BAMBU_ACCESS_CODE` from the environment only. The
  access code is masked in every log line and in `/stats`.
- Scope: RTSPS printers (X1, X1C, X1E, H2D, H2S, P2S). TCP:6000 JPEG printers (A1, A1 mini,
  P1P, P1S) are not served.
- Docs: `README.md`, `docs/operators-guide.md`, `docs/runbook.md`. Update them in the same
  change as any route, flag, env var or HUD change.

## Git Policy

Agent-managed. Full git lifecycle authorized: stage, commit, push.

## Credential Registry

No secrets in this repo. The printer access code lives in the operator's environment or
in the `bambu-mcp` secret store (`bambu-<name>_ip`, `bambu-<name>_access_code`). To run
from that store without printing the code:

```bash
cd ~/ai/forge/bambu/bambu-mcp && eval "$(.venv/bin/python3 -c 'import secrets_store as s; print("export BAMBU_HOSTNAME=%s BAMBU_ACCESS_CODE=%s" % (s.get("bambu-H2D_ip"), s.get("bambu-H2D_access_code")))')" && cd ../bambu-mse-stream && ../bambu-mcp/.venv/bin/python3 server.py --bind 0.0.0.0
```
