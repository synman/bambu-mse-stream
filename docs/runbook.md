# Runbook: measure a printer's camera stream

Use this to answer "what does this printer's camera actually send, and does the relay keep
up?" Run it on a new printer model, after a firmware update, or when a viewer reports low
frame rate.

## Scope

One printer, one host that can reach it on TCP 322 or 6000, one browser. About 10 minutes.
Read-only toward the printer: it opens a video session and nothing else.

## Preflight

- the printer's host and access code exported as `BAMBU_HOSTNAME` and `BAMBU_ACCESS_CODE`
  (the relay can also run from `BAMBU_PRINTERS`; step 1 needs the two variables)
- `python3 -c "import av; print(av.__version__)"` prints 14 or newer
- `ffprobe -version` works (part of FFmpeg; `brew install ffmpeg` or `apt install ffmpeg`)
- port 8765 is free: `lsof -ti tcp:8765` prints nothing
- know the family: `nc -z -w 3 "$BAMBU_HOSTNAME" 322 && echo rtsps || echo jpeg`

## Procedure

1. Read the source directly, before the relay, so the two can be compared later.

   `rtsps` family:

   ```bash
   ffprobe -v error -rtsp_transport tcp -select_streams v:0 \
     -show_entries stream=codec_name,profile,width,height,r_frame_rate,pix_fmt -of json \
     "rtsps://bblp:${BAMBU_ACCESS_CODE}@${BAMBU_HOSTNAME}:322/streaming/live/1"
   ```

   Record `codec_name`, `profile`, `width`, `height`, `r_frame_rate`. Expect `h264`; if it
   is `hevc`, stop: browsers will not play it through this relay without changes.
   This one command puts the access code on `ffprobe`'s command line, so it is visible in
   the process list and shell history on this host while it runs. The relay never does
   that; it masks the code everywhere. Run the probe from a script, or with
   `HISTCONTROL=ignorespace` and a leading space, if that matters on your host.

   `jpeg` family: there is no codec to read. Start the relay (step 2) and take the frame
   rate from `packets_per_s` and the size from `width` × `height`; the relay reads them from
   the stills. Expect 0.4 to 0.5 and 1920×1080 on an A1.

2. Start the relay and give it 15 s:

   ```bash
   python3 server.py --bind 0.0.0.0 &
   sleep 15
   curl -s localhost:8765/printers
   curl -s localhost:8765/stats
   ```

   Record `camera`, `packets_per_s`, `kbps`, `gop_packets`, `fragments_per_s`, `codec`.
   On `rtsps` expect `packets_per_s` to match `r_frame_rate` from step 1. On `jpeg` expect
   `gop_packets` 1 and `fragments_per_s` equal to `packets_per_s`. Expect `reconnects` 0
   and `last_error` empty on both.

3. Take a snapshot and a 10 s capture through the relay and check both:

   ```bash
   curl -s localhost:8765/snapshot.jpg -o snap.jpg && file snap.jpg
   curl -s --max-time 10 localhost:8765/stream.mp4 -o sample.mp4
   ffprobe -v error -count_frames -show_entries stream=codec_name,width,height,nb_read_frames -of compact sample.mp4
   ```

   Expect a JPEG of the printer's resolution, and in the capture the same codec and size as
   the source, with `nb_read_frames` of at least 8 × fps (about 10 × fps in practice; on
   `jpeg` that is 4 or 5 frames). The capture starts at the last keyframe, so a shortfall
   of up to one GOP is normal.

4. Open `http://<host>:8765/` in each browser you care about. Wait 15 s on `rtsps`, 30 s on
   `jpeg`. Record the big number, `latency`, `dropped`, and `api`. Repeat on a phone over
   Wi-Fi if the LAN is in scope; iOS shows `ManagedMediaSource`.

5. Open the page in a second tab. `clients` must read 2 and the first tab's fps must not
   change. The relay serves every client from one printer session. If Bambu Studio is
   open on the camera at the same time, leave it open: the printer serves both.

6. Stop the relay: `kill %1` or `lsof -ti tcp:8765 | xargs kill`.

## Verification

The run is good when every line holds:

- step 1 codec is `h264` (`rtsps`), or the family is `jpeg`
- step 2 `packets_per_s` is within 10 % of the camera rate and `reconnects` is 0
- step 3 returns a JPEG and the capture has at least 8 × fps frames
- step 4 fps is within 20 % of the camera rate on every browser, and latency is under
  1.0 s (`rtsps`) or under one frame interval plus 1 s (`jpeg`)
- step 5 shows no change with a second viewer

## Failure branches

| Step | Symptom | Do |
|---|---|---|
| 1 | `Connection refused` | the printer is off, not in LAN mode, or the host is wrong |
| 1 | `401 Unauthorized` | wrong access code |
| 1 | codec `hevc` | out of scope; note the firmware version and stop |
| 2 | `camera` stays empty | neither port answers; see step 1's first row |
| 2 | `reconnects` climbing | run `python3 server.py -v` and read the libav error line |
| 3 | `503 no frame yet` | no keyframe yet; wait a few seconds and retry |
| 3 | `ffprobe` says `moov atom not found` | the capture ended before `moov`; rerun with a longer `--max-time` |
| 4 | fps far below `server in` | browser-side; check `dropped`, close other tabs, try another browser |
| 4 | `codec … (unsupported!)` | that browser cannot play the profile; record it |
| 5 | first tab stalls when the second opens | host CPU or network; record `top` and the link speed |

## Done definition

A dated row added to the README's measured table, or a new entry in your notes, with the
recorded values from steps 1 to 4, the browser list, and the firmware version from the
printer's settings screen. Delete `snap.jpg` and `sample.mp4`.

## Update this runbook when

- `server.py` changes a route, the `/stats` keys, or the fragmenting options (the
  fragments/s and frame-count expectations move with them)
- a new printer model needs a different port, URL path or detection rule
- a browser's MSE support changes the expected `api` value
