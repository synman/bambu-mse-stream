# bambu-mse-stream

Low-CPU browser viewer for Bambu Lab printer cameras that speak RTSPS (X1, X1C, X1E, H2D,
H2S, P2S). One Python process relays the printer's H.264 as fragmented MP4 and a one-file
web page plays it through MediaSource Extensions. No decode, no encode, no ffmpeg process
at runtime, no WebRTC ports.

Measured on an H2D, 2026-10-12: 30 fps at 1680×1080, 0.6 s of buffer between the live
edge and the playhead, about 1.1 Mbps per viewer. The same picture as MJPEG at 20 fps is
about 24 Mbps.

The relay only reads the camera. It sends no command to the printer, runs beside Bambu
Studio or any other viewer, and is safe to start and stop during a print.

## Why

Bambu printers already encode H.264. Every MJPEG viewer decodes it and re-encodes each frame
to JPEG on the host, then ships 20 to 30 JPEGs a second to the browser. This relay copies the
H.264 packets into fMP4 fragments instead, so the host does no video work and the browser's
own decoder does the rest. The page also sidesteps the WebKit multipart-image leaks that
MJPEG `<img>` streams suffer in Safari.

Printers that stream JPEG over port 6000 (A1, A1 mini, P1P, P1S) are out of scope. They send
no H.264, so there is nothing to pass through.

## Prerequisites

- Python 3.10 or newer
- [PyAV](https://pyav.org/) 14 or newer (`pip install -r requirements.txt`); it bundles FFmpeg
- A printer in LAN mode, reachable on TCP port 322
- The printer's LAN access code (Settings → General → LAN Only Mode on the printer)
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

```bash
export BAMBU_HOSTNAME=192.168.1.50        # printer IP or hostname
export BAMBU_ACCESS_CODE=12345678         # LAN access code, never commit it
python3 server.py                         # http://localhost:8765/
python3 server.py --bind 0.0.0.0          # reachable from other devices on the LAN
python3 server.py --port 9000 -v          # other port, debug logging
```

Open the URL. The page starts the stream and shows an FPS meter in the top left.

| Route | Returns |
|---|---|
| `/` | the viewer page |
| `/stream.mp4` | live fragmented MP4, `video/mp4`, starts on a keyframe |
| `/stats` | JSON counters: `packets_per_s`, `kbps`, `gop_packets`, `fragments_per_s`, `clients`, `codec`, `width`, `height`, `reconnects`, `last_error` |

The relay has no authentication. Bind to `127.0.0.1` (the default) or put it behind a proxy
that has some.

## Project structure

| File | Role |
|---|---|
| `server.py` | RTSPS reader, fMP4 remux, HTTP fan-out, `/stats` |
| `index.html` | viewer page: MediaSource plumbing, live-edge control, FPS meter |
| `requirements.txt` | `av>=14.0.0` |
| `docs/operators-guide.md` | how it works, configuration, verification, failure branches |
| `docs/runbook.md` | measuring a printer's stream, step by step |

## How it works, in one paragraph

`server.py` opens `rtsps://bblp:<code>@<host>:322/streaming/live/1` with PyAV, demuxes the
H.264 packets, rebuilds their timestamps (the printer sends dts only, with a bad duration,
and jumps on RTCP resync), and muxes them into an MP4 with `empty_moov`, `frag_keyframe`
and 200 ms `frag_duration`. The muxer output is cut into boxes: `ftyp`+`moov` become the
init segment, each `moof`+`mdat` pair becomes a fragment. Every HTTP client gets the init
segment, the fragments since the last keyframe, then live fragments. `index.html` fetches
`/stream.mp4`, appends chunks to a `SourceBuffer`, trims old buffer, and when more than
1.5 s is buffered ahead seeks to 0.5 s behind the live edge. Changing `frag_duration` in
`server.py` moves the latency and fragments/s figures in this file and both docs.

## Contributing

Issues and pull requests are welcome. Run `python3 -m py_compile server.py` before sending
a change, and update `docs/` when behaviour or configuration changes. Licensed under
GPL-3.0, see `LICENSE`.
