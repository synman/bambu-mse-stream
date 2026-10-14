# 📺 bambu-mse-stream — every-model camera relay (fMP4 + snapshots)

> forge ❯ bambu ❯ **bms** · application
>
> **Archetype:** Single-process multi-printer relay + static viewer page
> **Language:** Python 3.10+ (PyAV) · vanilla JS
> **Interfaces:** HTTP (`/`, `/printers`, `/stream.mp4`, `/snapshot.jpg`, `/stats`) · RTSPS:322 and TLS:6000 clients
> **Shared rules:** `~/ai/forge/bambu/.claude/rules/bambu-ecosystem.md`

## Build / Validate

- `python3 -m py_compile server.py`
- Live check: `docs/runbook.md` (ffprobe the source, start the relay, compare `/stats`)
- No test suite. The runbook is the verification.

## Architecture

- `server.py`: one `Relay` per printer. Family detected per connection (port 322 open →
  `rtsps`, else `jpeg`). `rtsps`: demux H.264, rebuild timestamps, remux to fMP4
  (`frag_keyframe` + 200 ms `frag_duration`), keep the raw GOP for on-demand snapshot
  decode. `jpeg`: TLS:6000 login, decode each still, libx264 intra encode, mux with
  one closed-at-once container per still, keep the still for snapshots. Both split the muxer output into MP4
  boxes and fan `moof`+`mdat` fragments to HTTP clients; new clients get the init segment
  plus the fragments since the last keyframe. `/stats` rates average over 10 s.
- `index.html`: printer picker from `/printers`, MediaSource player with per-family
  live-edge limits (1.5 s / 8 s), buffer trim, FPS HUD, `/stats` poll, snapshot link.
- Credentials: `BAMBU_PRINTERS` JSON or `BAMBU_HOSTNAME`/`BAMBU_ACCESS_CODE` from the
  environment only. The access code is masked in every log line and in `/stats`.
- Scope: every Bambu model with a camera. `rtsps` = X1, X1C, X1E, H2D, H2S, P2S; `jpeg` =
  A1, A1 mini, P1P, P1S.
- Docs: `README.md`, `docs/operators-guide.md`, `docs/runbook.md`. Update them in the same
  change as any route, flag, env var or HUD change.

## Git Policy

Agent-managed. Full git lifecycle authorized: stage, commit, push.

## Credential Registry

No secrets in this repo. The printer access codes live in the operator's environment or
in the `bambu-mcp` secret store (`bambu-<name>_ip`, `bambu-<name>_access_code`). To run
both printers from that store without printing a code:

```bash
cd ~/ai/forge/bambu/bambu-mcp && eval "$(.venv/bin/python3 -c '
import json, secrets_store as s
ps=[{"name":n,"host":s.get(f"bambu-{n}_ip"),"access_code":s.get(f"bambu-{n}_access_code")} for n in ("H2D","A1")]
print("export BAMBU_PRINTERS=" + repr(json.dumps(ps)))')" && cd ../bambu-mse-stream && ../bambu-mcp/.venv/bin/python3 server.py --bind 0.0.0.0
```
