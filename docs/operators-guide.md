# Operators guide

Scope: running and verifying `server.py` on a workstation, a NAS, or a container, for one
printer. Source of truth is `server.py` and `index.html`; this guide describes what they do
and how to prove it.

## What runs

One process, three threads:

| Thread | Does |
|---|---|
| `rtsps-reader` | opens the printer's RTSPS stream, demuxes H.264, remuxes to fMP4, reconnects after 2 s on any error |
| HTTP server | `ThreadingHTTPServer`, one thread per client, serves `/`, `/stream.mp4`, `/stats` |
| per-client | writes the init segment, the keyframe backlog, then live fragments from a queue of 400 |

The reader runs whether or not anyone is watching. A client that falls 400 fragments behind
(about 80 s) has fragments dropped; the browser resyncs on the next keyframe.

## Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `BAMBU_HOSTNAME` | env | required | printer IP or DNS name |
| `BAMBU_ACCESS_CODE` | env | required | LAN access code; the process never logs it |
| `--port` | flag | `8765` | HTTP listen port |
| `--bind` | flag | `127.0.0.1` | `0.0.0.0` exposes the stream to the LAN with no auth |
| `-v` | flag | off | debug logging, includes one line per HTTP request |
| `MUX_OPTIONS` | `server.py` | 200 ms fragments | `frag_duration` is in microseconds |
| `TB_HZ` | `server.py` | `90000` | H.264 RTP clock, do not change |

## Start

```bash
export BAMBU_HOSTNAME=... BAMBU_ACCESS_CODE=...
python3 server.py --bind 0.0.0.0
```

Expected first log line, with the access code masked:

```
INFO viewer: http://0.0.0.0:8765/  source rtsps://bblp:****@<host>:322/streaming/live/1
```

## Verify

1. `curl -s localhost:8765/stats` within 10 s of start. Expect `packets_per_s` near the
   camera's rate (30 on an H2D), `kbps` around 1000, `reconnects` 0, `last_error` empty,
   `codec` such as `avc1.64C029`.
2. `curl -s --max-time 5 localhost:8765/stream.mp4 -o sample.mp4` then
   `ffprobe sample.mp4`. Expect `h264`, the printer's resolution, and a frame count near
   5 × fps. The file must begin with `ftyp` then `moov`.
3. Open `/` in a browser. The FPS number turns green at 15 fps or more. `latency` should
   settle between 0.3 and 1.0 s. `dropped` should stay near zero.

## The HUD

| Field | Source | Healthy |
|---|---|---|
| big number | frames presented per second, `requestVideoFrameCallback` over a 2 s window; reads 0 while the tab is hidden, since browsers stop presenting frames there | the camera's rate |
| decoded, dropped | `getVideoPlaybackQuality()` | dropped flat |
| latency | buffered end minus playhead; the README's 0.6 s is this figure, not glass-to-glass | 0.3 to 1.0 s |
| rx | bytes the page received, as kbps | matches `server in` |
| codec, size | `/stats` and `videoWidth` | `avc1.…`, printer resolution |
| server in | `/stats` packets/s and kbps | camera rate |
| gop | frames between keyframes | 30 on an H2D |
| frags | fragments per second | about 5 |
| clients | open `/stream.mp4` connections | the number of tabs |
| api | `MediaSource` or `ManagedMediaSource` (iOS) | either |

When latency passes 1.5 s the page seeks to 0.5 s behind the live edge. The `reconnect`
button drops the connection and starts over; use it after a printer reboot.

## Failure branches

| Symptom | Where to look | Likely cause and fix |
|---|---|---|
| `set BAMBU_HOSTNAME and BAMBU_ACCESS_CODE` at start | shell | export both variables |
| `/stats` shows `last_error` with `Connection refused` or timeout | printer | wrong host, printer off, or not in LAN mode; port 322 must answer |
| `last_error` mentions `401` or `Unauthorized` | printer | wrong access code |
| `reconnects` climbs every few seconds, `packets` stays 0 | printer, network | the reader connects then fails; run with `-v` and read the libav message |
| `/stream.mp4` answers `503 no stream yet` | reader | no init segment within 20 s; check `/stats` |
| page says `video error` or `append failed` | browser console | codec unsupported (`codec` row says so) or a corrupt fragment; reconnect once, then read the server log |
| fps green, latency climbs past 1.5 s and jumps back | network | the page is keeping up by seeking; expected on a slow link |
| fps red, `server in` healthy | browser | the tab is throttled or the decoder is overloaded; check `dropped` |
| fps red, `server in` 0 | printer | the camera stopped; the reader reconnects on its own after 2 s |
| `Address already in use` | host | another instance holds the port: `lsof -ti tcp:8765 \| xargs kill` |

The printer accepts more than one RTSPS client at once, so this relay can run beside Bambu
Studio or another viewer, and during a print. It opens a video session and sends nothing
else to the printer.

## Stop

Ctrl-C in the foreground, or `lsof -ti tcp:8765 | xargs kill`. The printer sees a closed
socket and nothing else; the print is never touched.

## Update this guide when

- a route, flag or environment variable is added or renamed in `server.py`
- `MUX_OPTIONS` changes (fragment size changes latency and the fragments/s figure)
- the HUD gains or loses a row in `index.html`
- the measured numbers in the README are re-taken on new firmware
