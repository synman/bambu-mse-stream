#!/usr/bin/env python3
"""Bambu Lab RTSPS → fragmented MP4 relay for browser MSE playback.

One PyAV reader demuxes the printer's H.264 over RTSPS (X1, H2D, P2S) and remuxes it
to fragmented MP4 with no decode and no encode. Browsers play it through MediaSource.
Nothing is transcoded, so a NAS-class host serves the camera at full rate.

Routes:
  /            viewer page with an FPS meter (index.html next to this file)
  /stream.mp4  live fMP4: init segment, then fragments from the last keyframe on
  /stats       JSON: packets/s, kbps, GOP length, fragments/s, clients, codec, errors

Configuration (environment):
  BAMBU_HOSTNAME     printer hostname or IP (required)
  BAMBU_ACCESS_CODE  the printer's LAN access code (required)

Run:
  python3 server.py [--port 8765] [--bind 127.0.0.1]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av

log = logging.getLogger("bambu-mse-stream")
HERE = Path(__file__).resolve().parent

# rtsp over TLS, the printer's self-signed certificate is not verified
AV_OPTIONS = {"rtsp_transport": "tcp", "tls_verify": "0", "allowed_media_types": "video"}
# 200 ms fragments keep MSE latency low; frag_keyframe alone would hold a whole GOP
MUX_OPTIONS = {"movflags": "frag_keyframe+empty_moov+default_base_moof", "frag_duration": "200000"}
# The printer's RTP packets carry dts only (no pts), a garbage duration, and the dts
# jumps on RTCP resync. The mp4 muxer rejects all three, so timestamps are rebuilt:
# monotonic from 0, each step clamped to [1, 1 s] at 90 kHz.
TB_HZ = 90000


def _codec_string(ex: bytes) -> str:
    """RFC 6381 avc1.PPCCLL from the stream's extradata, avcC or Annex B SPS."""
    if len(ex) >= 4 and ex[0] == 1:  # avcC: [1, profile, compat, level, ...]
        return "avc1.%02X%02X%02X" % (ex[1], ex[2], ex[3])
    i = 0
    while i < len(ex) - 4:  # Annex B: 00 00 01 <nal>, SPS is nal_type 7
        if ex[i:i + 3] == b"\x00\x00\x01":
            nal = ex[i + 3]
            if nal & 0x1F == 7 and i + 7 <= len(ex):
                return "avc1.%02X%02X%02X" % (ex[i + 4], ex[i + 5], ex[i + 6])
            i += 3
        else:
            i += 1
    return ""


class _BoxSplitter:
    """Collect muxer writes and cut them into complete MP4 boxes."""

    def __init__(self, on_box):
        self._buf = bytearray()
        self._on_box = on_box

    def write(self, data: bytes) -> int:
        self._buf += data
        while len(self._buf) >= 8:
            size, kind = struct.unpack_from(">I4s", self._buf, 0)
            if size == 1:  # 64-bit size
                if len(self._buf) < 16:
                    break
                size = struct.unpack_from(">Q", self._buf, 8)[0]
            if size < 8 or len(self._buf) < size:
                break
            self._on_box(kind.decode("latin1"), bytes(self._buf[:size]))
            del self._buf[:size]
        return len(data)

    def flush(self) -> None:
        pass


class Relay:
    """One RTSPS reader; fans fMP4 fragments out to every HTTP client."""

    def __init__(self, url: str, redacted: str):
        self._url = url
        self._redacted = redacted
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []
        self._init: bytes = b""
        self._pending_moof: bytes | None = None
        # fragments since the last keyframe-led fragment, so a joiner starts decodable
        self._gop_frags: list[bytes] = []
        self._cur_key = False
        self._frag_emitted = False
        self.stats = {
            "codec": "", "width": 0, "height": 0, "packets": 0, "bytes": 0, "keyframes": 0,
            "fragments": 0, "gop_packets": 0, "clients": 0, "reconnects": 0, "started": time.time(),
            "last_error": "",
        }
        self._rate_prev = (time.time(), 0, 0, 0)
        self._since_key = 0
        threading.Thread(target=self._reader, daemon=True, name="rtsps-reader").start()

    # -- client side --------------------------------------------------------
    def subscribe(self) -> tuple[queue.Queue, bytes, list[bytes]]:
        q: queue.Queue = queue.Queue(maxsize=400)
        with self._lock:
            self._clients.append(q)
            self.stats["clients"] = len(self._clients)
            return q, self._init, list(self._gop_frags)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)
            self.stats["clients"] = len(self._clients)

    def rates(self) -> dict:
        now = time.time()
        t0, p0, b0, f0 = self._rate_prev
        dt = max(now - t0, 1e-6)
        s = self.stats
        out = {**s, "uptime_s": round(now - s["started"], 1),
               "packets_per_s": round((s["packets"] - p0) / dt, 1),
               "kbps": round((s["bytes"] - b0) * 8 / dt / 1000, 1),
               "fragments_per_s": round((s["fragments"] - f0) / dt, 1)}
        self._rate_prev = (now, s["packets"], s["bytes"], s["fragments"])
        return out

    # -- muxer output -------------------------------------------------------
    def _on_box(self, kind: str, box: bytes) -> None:
        if kind in ("ftyp", "moov"):
            self._init += box
            return
        if kind == "moof":
            self._pending_moof = box
            return
        if kind == "mdat" and self._pending_moof is not None:
            frag = self._pending_moof + box
            self._pending_moof = None
            self._frag_emitted = True
            self.stats["fragments"] += 1
            with self._lock:
                if self._cur_key:
                    self._gop_frags = [frag]
                else:
                    self._gop_frags.append(frag)
                for q in self._clients:
                    try:
                        q.put_nowait(frag)
                    except queue.Full:
                        pass  # slow client: drop, MSE resyncs on the next keyframe

    # -- reader -------------------------------------------------------------
    def _reader(self) -> None:
        while True:
            try:
                self._run_once()
            except Exception as e:  # noqa: BLE001
                msg = (type(e).__name__ + ": " + str(e)[:200]).replace(self._url, self._redacted)
                self.stats["last_error"] = msg
                log.warning("reader error: %s", msg)
            self.stats["reconnects"] += 1
            time.sleep(2)

    def _run_once(self) -> None:
        ic = av.open(self._url, options=AV_OPTIONS, timeout=15.0)
        try:
            vs = ic.streams.video[0]
            cc = vs.codec_context
            self.stats["codec"] = _codec_string(cc.extradata or b"")
            self.stats["width"], self.stats["height"] = cc.width, cc.height
            with self._lock:
                self._init = b""
                self._gop_frags = []
            splitter = _BoxSplitter(self._on_box)
            oc = av.open(splitter, mode="w", format="mp4", options=MUX_OPTIONS)
            try:
                os_ = oc.add_stream_from_template(vs)
                started = False
                prev_in = None
                out_ts = 0
                for pkt in ic.demux(vs):
                    if pkt.dts is None:
                        continue
                    if not started:
                        if not pkt.is_keyframe:
                            continue  # the init + first fragment must start on a keyframe
                        started = True
                        self._cur_key = True
                    if prev_in is not None:
                        step = pkt.dts - prev_in
                        out_ts += step if 0 < step <= TB_HZ else TB_HZ // 30
                    prev_in = pkt.dts
                    pkt.dts = pkt.pts = out_ts
                    pkt.duration = 0
                    self.stats["packets"] += 1
                    self.stats["bytes"] += pkt.size
                    if pkt.is_keyframe:
                        self.stats["keyframes"] += 1
                        if self._since_key:
                            self.stats["gop_packets"] = self._since_key
                        self._since_key = 0
                    self._since_key += 1
                    self._frag_emitted = False
                    pkt.stream = os_
                    oc.mux(pkt)
                    # the muxer flushes the previous fragment while writing the packet that
                    # opens the next one, so after a flush this packet leads the new fragment
                    if self._frag_emitted:
                        self._cur_key = bool(pkt.is_keyframe)
            finally:
                try:
                    oc.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            ic.close()


class Handler(BaseHTTPRequestHandler):
    relay: Relay = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):  # quiet
        log.debug(fmt, *args)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = (HERE / "index.html").read_bytes()
            self._send(200, "text/html; charset=utf-8", body)
        elif path == "/stats":
            self._send(200, "application/json", json.dumps(self.relay.rates()).encode())
        elif path == "/stream.mp4":
            self._stream()
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self) -> None:
        q, init, backlog = self.relay.subscribe()
        try:
            deadline = time.time() + 20
            while not init and time.time() < deadline:
                time.sleep(0.2)
                self.relay.unsubscribe(q)
                q, init, backlog = self.relay.subscribe()
            if not init:
                self._send(503, "text/plain", b"no stream yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(init)
            for frag in backlog:
                self.wfile.write(frag)
            self.wfile.flush()
            while True:
                frag = q.get(timeout=30)
                self.wfile.write(frag)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, queue.Empty, OSError):
            pass
        finally:
            self.relay.unsubscribe(q)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="127.0.0.1", help="0.0.0.0 to view from another device on the LAN")
    ap.add_argument("-v", action="store_true", help="debug logging")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.v else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    host = os.environ.get("BAMBU_HOSTNAME", "")
    code = os.environ.get("BAMBU_ACCESS_CODE", "")
    if not host or not code:
        sys.exit("set BAMBU_HOSTNAME and BAMBU_ACCESS_CODE in the environment")
    url = f"rtsps://bblp:{code}@{host}:322/streaming/live/1"
    redacted = f"rtsps://bblp:****@{host}:322/streaming/live/1"

    Handler.relay = Relay(url, redacted)
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.daemon_threads = True
    log.info("viewer: http://%s:%d/  source %s", "localhost" if args.bind == "127.0.0.1" else args.bind, args.port, redacted)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
