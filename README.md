# bambu-mse-stream

One small daemon for every Bambu Lab printer camera: a live fragmented-MP4 stream that any
browser plays through MediaSource, plus a JPEG snapshot route, for any number of printers.
No ffmpeg process at runtime, no WebRTC ports, one Python file and one HTML file.

| Camera family | Printers | What the relay does |
|---|---|---|
| `rtsps` | X1, X1C, X1E, H2D, H2S, P2S | copies the printer's H.264 into fMP4; no decode, no encode |
| `jpeg` | A1, A1 mini, P1P, P1S | encodes each JPEG still (about one every 2 s) as one intra H.264 frame |

Both families come out as the same `video/mp4` stream and play in the same `<video>` page.
The family is detected per printer: port 322 open means `rtsps`, otherwise `jpeg`.

Measured 2026-10-13 on this ecosystem, Chromium:

| Printer | fps | size | buffer ahead | per viewer | host work |
|---|---|---|---|---|---|
| H2D (`rtsps`) | 30.0 | 1680×1080 | 0.6 s | 1.1 Mbps | none, remux only |
| A1 (`jpeg`) | 0.5 | 1920×1080 | one frame, 2 to 3 s | 0.7 Mbps | one libx264 intra encode per still, about 1 % of a core |

The same H2D picture as MJPEG at 20 fps is about 24 Mbps.

## Why

Bambu's RTSPS printers already encode H.264. Every MJPEG viewer decodes it and re-encodes
each frame to JPEG on the host, then ships 20 to 30 JPEGs a second to the browser. This
relay copies the packets instead, so the host does no video work and the browser's own
decoder does the rest. The A1/P1 family only sends stills, so for them the relay pays one
cheap encode per still to keep a single browser path. The `<video>` page also sidesteps
the WebKit multipart-image leaks that MJPEG `<img>` streams suffer in Safari.

The relay reads the camera and nothing else. It sends no command to the printer, runs
beside Bambu Studio or any other viewer, and is safe to start and stop during a print.

## Prerequisites

- Python 3.10 or newer
- [PyAV](https://pyav.org/) 14 or newer (`pip install -r requirements.txt`); it bundles
  FFmpeg with libx264
- Printers in LAN mode, reachable on TCP 322 (`rtsps`) or TCP 6000 (`jpeg`)
- Each printer's LAN access code (on the printer: Settings → General → LAN Only Mode)
- A browser with MediaSource: Chrome, Edge, Firefox, Safari on macOS, Safari on iOS 17.1+.
  iPhone Safari before 17.1 has no MediaSource; the page shows `api none` and plays nothing
- `ffprobe` (from FFmpeg) only if you run `docs/runbook.md`; the relay itself does not use it

## Setup

```bash
git clone https://github.com/synman/bambu-mse-stream.git
cd bambu-mse-stream
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Usage

One printer:

```bash
export BAMBU_HOSTNAME=192.168.1.50        # printer IP or hostname
export BAMBU_ACCESS_CODE=12345678         # LAN access code, never commit it
python3 server.py                         # http://localhost:8765/
```

Several printers:

```bash
export BAMBU_PRINTERS='[{"name":"H2D","host":"192.168.1.50","access_code":"12345678"},
                        {"name":"A1","host":"192.168.1.51","access_code":"87654321"}]'
python3 server.py --bind 0.0.0.0          # reachable from other devices on the LAN
```

Add `"camera":"rtsps"` or `"camera":"jpeg"` to an entry (or set `BAMBU_CAMERA` for the
single-printer form) to skip detection. `BAMBU_NAME` names the single printer; `--port`
moves the listener; `-v` logs every request.

Open the URL. The page lists the printers in a picker (top right), starts the stream and
shows an FPS meter (top left). Every route takes `?printer=<name>`; without it the first
configured printer is used.

| Route | Returns |
|---|---|
| `/` | the viewer page |
| `/printers` | JSON list of `{name, camera}` |
| `/stream.mp4` | live fragmented MP4, `video/mp4`, starts on a keyframe |
| `/snapshot.jpg` | one JPEG: the latest still (`jpeg`) or the latest frame decoded on demand (`rtsps`) |
| `/stats` | JSON counters: `camera`, `codec`, `width`, `height`, `packets_per_s`, `kbps`, `gop_packets`, `fragments_per_s`, `clients`, `reconnects`, `last_error`; rates average over 10 s |

The relay has no authentication. Bind to `127.0.0.1` (the default) or put it behind a proxy
that has some.

## Project structure

| File | Role |
|---|---|
| `server.py` | per-printer camera reader (RTSPS passthrough or JPEG→H.264), fMP4 remux, HTTP fan-out, snapshots, `/stats` |
| `index.html` | viewer page: printer picker, MediaSource plumbing, live-edge control, FPS meter |
| `requirements.txt` | `av>=14.0.0` |
| `docs/operators-guide.md` | how it works, configuration, verification, failure branches |
| `docs/runbook.md` | measuring a printer's stream, step by step |

## How it works, in one paragraph

Per printer, one reader thread. For `rtsps` it opens
`rtsps://bblp:<code>@<host>:322/streaming/live/1` with PyAV, demuxes the H.264 packets,
rebuilds their timestamps (the printer sends dts only, with a bad duration, and jumps on
RTCP resync), and muxes them into an MP4 with `empty_moov`, `frag_keyframe` and 200 ms
`frag_duration`. For `jpeg` it logs in on TLS:6000, reads each still, decodes it, encodes it
once with libx264 (`ultrafast`, intra-only) and muxes it through a one-packet container
that is closed at once, so the still leaves the moment it is encoded (the mp4 muxer only
flushes a fragment when the next packet arrives, which would hold each still for the two to
three seconds until the following one); the fragment's `tfdt` and sequence number are then
stamped with the still's real timestamp. In both cases the muxer output is cut into boxes:
`ftyp`+`moov` become the init segment, each `moof`+`mdat` pair a fragment. Every HTTP client
gets the init segment, the fragments since the last keyframe, then live fragments; when the
printer session ends, every client's response is closed, so the browser reconnects and
takes the new session's init segment instead of parking on the old timeline. `index.html`
fetches `stream.mp4` (relative, so it works behind a proxy prefix such as `/webcam/`),
appends chunks to a `SourceBuffer`, trims old buffer, seeks back near the live edge when it
runs more than 1.5 s (`rtsps`) or 8 s (`jpeg`) ahead, and reconnects on its own when no
bytes arrive for 10 s (`rtsps`) or 20 s (`jpeg`).

## Contributing

Issues and pull requests are welcome. Run `python3 -m py_compile server.py` before sending
a change, and update `docs/` when behaviour or configuration changes. Licensed under
GPL-3.0, see `LICENSE`.
