# Operators guide

Scope: running and verifying `server.py` on a workstation, a NAS, or a container, for one
or more printers. Source of truth is `server.py` and `index.html`; this guide describes what
they do and how to prove it.

## What runs

One process. Per printer, one reader thread; plus the HTTP server and one thread per client.

| Thread | Does |
|---|---|
| `camera-<name>` | detects the camera family (unless configured), reads the camera, remuxes to fMP4, reconnects after 2 s on any error. On any error or new session it forgets the init segment, backlogs and last still, and closes every client's response: the browser reconnects and takes the new session's timeline instead of parking on the old one. Until a session is up, `/stream.mp4` and `/snapshot.jpg` answer 503 and `/stats` keeps the last known `camera`, so a printer that is off never reads as the other family |
| HTTP server | `ThreadingHTTPServer`, serves `/`, `/printers`, `/stream.mp4`, `/snapshot.jpg`, `/stats` |
| per-client | writes the init segment, the keyframe backlog, then live fragments from a queue of 400 |

Readers run whether or not anyone is watching. A client that falls 400 fragments behind
(about 80 s on `rtsps`, hours on `jpeg`) has fragments dropped; the browser resyncs on the
next keyframe.

### The two camera families

| | `rtsps` | `jpeg` |
|---|---|---|
| Printers | X1, X1C, X1E, H2D, H2S, P2S | A1, A1 mini, P1P, P1S |
| Source | H.264 over RTSPS, port 322 | JPEG stills over TLS, port 6000, about 0.5 fps |
| Relay work | copy packets, rebuild timestamps | decode still, encode one intra H.264 frame (libx264 `ultrafast`, crf 22) |
| Fragmenting | `frag_keyframe` + 200 ms `frag_duration` | one container per still, closed at once, so each still leaves as soon as it is encoded (the muxer alone would hold it until the next one) |
| Snapshot | the last GOP is decoded on demand | the last still is returned as-is |
| Codec string seen | `avc1.64C029` (High 4.1) | `avc1.42C028` (Constrained Baseline 4.0) |
| Detection | port 322 answers | port 322 refused |

Detection runs once per connection attempt, so a printer that was off at start is picked up
correctly when it appears.

## Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `BAMBU_PRINTERS` | env | — | JSON list of `{"name","host","access_code","camera"?}`; wins over the single-printer variables |
| `BAMBU_HOSTNAME` | env | required without `BAMBU_PRINTERS` | printer IP or DNS name |
| `BAMBU_ACCESS_CODE` | env | required without `BAMBU_PRINTERS` | LAN access code; masked in every log line and in `/stats` |
| `BAMBU_NAME` | env | `printer` | display name for the single-printer form |
| `BAMBU_CAMERA` | env | detect | `rtsps` or `jpeg` for the single-printer form |
| `--port` | flag | `8765` | HTTP listen port |
| `--bind` | flag | `127.0.0.1` | `0.0.0.0` exposes the streams to the LAN with no auth |
| `-v` | flag | off | debug logging, includes one line per HTTP request |
| `MUX_RTSPS`, `MUX_JPEG` | `server.py` | see above | `frag_duration` is in microseconds |
| `X264_JPEG` | `server.py` | `ultrafast`, crf 22, intra-only | encoder settings for the `jpeg` family |
| `RATE_WINDOW` | `server.py` | `10` s | window the `/stats` rates average over |
| `TB_HZ` | `server.py` | `90000` | H.264 RTP clock, do not change |

## Start

```bash
export BAMBU_PRINTERS='[{"name":"H2D","host":"...","access_code":"..."},{"name":"A1","host":"...","access_code":"..."}]'
python3 server.py --bind 0.0.0.0
```

Expected first lines:

```
INFO printer H2D: host <host> camera detect
INFO printer A1: host <host> camera detect
INFO viewer: http://0.0.0.0:8765/
```

`/printers` reports the detected family for each printer a few seconds later.

## Verify

1. `curl -s localhost:8765/printers`. Every printer is listed with `camera` set to `rtsps`
   or `jpeg`; an empty `camera` means its reader has not connected yet.
2. `curl -s 'localhost:8765/stats?printer=<name>'` after 15 s. Expect `packets_per_s` near
   the camera's rate (30 on an H2D, 0.4 to 0.5 on an A1), `reconnects` 0, `last_error`
   empty, `codec` such as `avc1.64C029` or `avc1.42C028`.
3. `curl -s 'localhost:8765/snapshot.jpg?printer=<name>' -o snap.jpg`. Expect HTTP 200 and
   a JPEG of the printer's resolution (`file snap.jpg`).
4. `curl -s --max-time 10 'localhost:8765/stream.mp4?printer=<name>' -o sample.mp4` then
   `ffprobe sample.mp4`. Expect `h264`, the printer's resolution, and a frame count near
   10 × fps. The file must begin with `ftyp` then `moov`.
5. Open `/` in a browser, pick each printer. The FPS number turns green at 80 % of the
   camera's own rate. `latency` should settle between 0.3 and 1.0 s on `rtsps` and show one
   frame interval, 2 to 3 s, on `jpeg`. `dropped` should stay near zero.

## The HUD

| Field | Source | Healthy |
|---|---|---|
| big number | frames presented per second, `requestVideoFrameCallback`, over 2 s (`rtsps`) or 10 s (`jpeg`); reads 0 while the tab is hidden, since browsers stop presenting frames there | ≥ 80 % of `server in` |
| source | `/stats` `camera` | `rtsps h264 passthrough` or `jpeg stills → h264` |
| decoded, dropped | `getVideoPlaybackQuality()` | dropped flat |
| latency | buffered end minus playhead; the README's figures are this, not glass-to-glass | 0.3 to 1.0 s `rtsps`; one frame interval `jpeg` |
| rx | bytes the page received, as kbps | matches `server in` |
| codec, size | `/stats` and `videoWidth` | `avc1.…`, printer resolution |
| server in | `/stats` packets/s and kbps, 10 s average | camera rate |
| gop | frames between keyframes | 30 on an H2D; 1 on `jpeg` |
| frags | fragments per second | about 5 on `rtsps`; the frame rate on `jpeg` |
| clients | open `/stream.mp4` connections for this printer | the number of tabs |
| api | `MediaSource` or `ManagedMediaSource` (iOS) | either |

When latency passes the family's limit (1.5 s `rtsps`, 8 s `jpeg`) the page seeks to just
behind the live edge. The `reconnect` button drops the connection and starts over; use it
after a printer reboot. The `snapshot` link opens `/snapshot.jpg` for the current printer.

## Failure branches

| Symptom | Where to look | Likely cause and fix |
|---|---|---|
| `set BAMBU_HOSTNAME and BAMBU_ACCESS_CODE, or BAMBU_PRINTERS` at start | shell | export the variables |
| `BAMBU_PRINTERS entries need name, host and access_code` | shell | fix the JSON |
| `/printers` shows `camera` empty for a long time | printer | neither 322 nor 6000 answers; wrong host, printer off, or not in LAN mode |
| `/stats` `last_error` with `Connection refused` or timeout | printer | same as above |
| `last_error` mentions `401` or `Unauthorized` (`rtsps`) | printer | wrong access code |
| `last_error` `camera closed the socket` right after connect (`jpeg`) | printer | wrong access code, or the printer rejected the login |
| `reconnects` climbs every few seconds, `packets` stays 0 | printer, network | the reader connects then fails; run with `-v` and read the libav message |
| `/stream.mp4` answers `503 no stream yet` | reader | no init segment within 20 s; check `/stats` |
| `/snapshot.jpg` answers `503 no frame yet` | reader | no keyframe received yet; wait a GOP (1 s `rtsps`) or one still (2 to 3 s `jpeg`) |
| page says `video error` or `append failed` | browser console | codec unsupported (`codec` row says so) or a corrupt fragment; reconnect once, then read the server log |
| fps green, latency climbs past the limit and jumps back | network | the page is keeping up by seeking; expected on a slow link |
| fps red, `server in` healthy | browser | the tab is throttled or the decoder is overloaded; check `dropped` |
| fps red, `server in` 0 | printer | the camera stopped; the reader reconnects on its own after 2 s, and the page reconnects itself when no bytes arrive for 10 s (`rtsps`) or 20 s (`jpeg`) |
| picture frozen, `server in` healthy, page says `streaming` | browser | a stall the page's byte watchdog has not yet hit (under 10 s / 20 s); if it lasts longer than that, read the browser console and the server log, since the watchdog should have fired |
| `Address already in use` | host | another instance holds the port: `lsof -ti tcp:8765 \| xargs kill` |

The printers accept more than one camera client at once, so this relay can run beside Bambu
Studio or another viewer, and during a print. It opens a video session and sends nothing
else to the printer.

## Stop

Ctrl-C in the foreground, or `lsof -ti tcp:8765 | xargs kill`. The printers see a closed
socket and nothing else; a running print is never touched.

## Update this guide when

- a route, flag or environment variable is added or renamed in `server.py`
- `MUX_RTSPS`, `MUX_JPEG` or `X264_JPEG` changes (fragment size and encoder settings change
  latency, bitrate and the fragments/s figure)
- the HUD gains or loses a row in `index.html`
- the measured numbers in the README are re-taken on new firmware or a new model
