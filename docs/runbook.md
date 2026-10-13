# Runbook: measure a printer's camera stream

Use this to answer "what does this printer's camera actually send, and does the relay keep
up?" Run it on a new printer model, after a firmware update, or when a viewer reports low
frame rate.

## Scope

One printer, one host that can reach it on TCP 322, one browser. About 10 minutes.
Read-only toward the printer: it opens a video session and nothing else.

## Preflight

- `BAMBU_HOSTNAME` and `BAMBU_ACCESS_CODE` exported in the shell
- `python3 -c "import av; print(av.__version__)"` prints 14 or newer
- `ffprobe -version` works (part of FFmpeg; `brew install ffmpeg` or `apt install ffmpeg`)
- port 8765 is free: `lsof -ti tcp:8765` prints nothing

## Procedure

1. Read the source directly, before the relay, so the two can be compared later:

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

2. Start the relay and give it 10 s:

   ```bash
   python3 server.py --bind 0.0.0.0 &
   sleep 10
   curl -s localhost:8765/stats
   ```

   Record `packets_per_s`, `kbps`, `gop_packets`, `fragments_per_s`, `codec`. Expect
   `packets_per_s` to match `r_frame_rate` from step 1, `reconnects` 0, `last_error` empty.

3. Capture 10 s through the relay and check the container:

   ```bash
   curl -s --max-time 10 localhost:8765/stream.mp4 -o sample.mp4
   ffprobe -v error -count_frames -show_entries stream=codec_name,width,height,nb_read_frames -of compact sample.mp4
   ```

   Expect the same codec and size as step 1 and `nb_read_frames` of at least 8 × fps,
   about 10 × fps in practice. The capture starts at the last keyframe, so a shortfall of
   up to one GOP is normal.

4. Open `http://<host>:8765/` in each browser you care about. Wait 15 s. Record the big
   number, `latency`, `dropped`, and `api`. Repeat on a phone over Wi-Fi if the LAN is in
   scope; iOS shows `ManagedMediaSource`.

5. Open the page in a second tab. `clients` must read 2 and the first tab's fps must not
   change. The relay serves every client from one printer session. If Bambu Studio is
   open on the camera at the same time, leave it open: the printer serves both.

6. Stop the relay: `kill %1` or `lsof -ti tcp:8765 | xargs kill`.

## Verification

The run is good when every line holds:

- step 1 codec is `h264`
- step 2 `packets_per_s` is within 10 % of step 1 `r_frame_rate` and `reconnects` is 0
- step 3 `nb_read_frames` is at least 8 × fps
- step 4 fps is within 10 % of the camera rate and latency is under 1.0 s on every browser
- step 5 shows no change with a second viewer

## Failure branches

| Step | Symptom | Do |
|---|---|---|
| 1 | `Connection refused` | the printer is off, not in LAN mode, or the host is wrong |
| 1 | `401 Unauthorized` | wrong access code |
| 1 | codec `hevc` | out of scope; note the firmware version and stop |
| 2 | `reconnects` climbing | run `python3 server.py -v` and read the libav error line |
| 3 | `ffprobe` says `moov atom not found` | the capture ended before `moov`; rerun with a longer `--max-time` |
| 4 | fps far below `server in` | browser-side; check `dropped`, close other tabs, try another browser |
| 4 | `codec … (unsupported!)` | that browser cannot play the profile; record it |
| 5 | first tab stalls when the second opens | host CPU or network; record `top` and the link speed |

## Done definition

A dated block appended to the README's measured line, or a new entry in your notes, with
the five recorded values from steps 1 to 4, the browser list, and the firmware version
from the printer's settings screen. Delete `sample.mp4`.

## Update this runbook when

- `server.py` changes a route, the `/stats` keys, or `frag_duration` (the fragments/s and
  frame-count expectations move with it)
- a new printer model needs a different port or URL path
- a browser's MSE support changes the expected `api` value
