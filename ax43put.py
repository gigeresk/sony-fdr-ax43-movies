#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fred Tombs
"""ax43put - add any video to a Sony FDR-AX43 SD card so the camera plays it.

    python ax43put.py MOVIE.mkv [MORE.mp4 ...] --card F:\\         (Windows)
    python3 ax43put.py MOVIE.mkv --card /mnt/f                   (Linux / WSL)

Each video is converted to the camera's own "Dual Video REC" MP4 format
(1280x720 23.976p H.264 Main@4.0 + AAC-LC 48 kHz stereo, letterboxed as needed)
and written as the next free MP_ROOT/100ANV01/MAH#####.MP4 + .THM.

Then on the camera: MENU -> Setup -> Media Settings -> Repair Img. DB F. -> Yes.
The movie appears in playback after switching the movie format to MP4.

Safety: the card is only ever *added to*. Nothing on the card is modified,
renamed or deleted. New files are written under a temporary name, verified by
SHA-256, then renamed; an existing name is never overwritten.

Requires: Python 3.8+, ffmpeg and ffprobe on PATH. No other packages.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime

FAT32_MAX = 4 * 1024 ** 3 - 1
MAC_EPOCH_OFFSET = 2082844800

# H.264 settings the AX43's decoder accepts (matches the camera's own stream:
# POC type 0, no weighted prediction, chroma QP offset 0, HRD + AUD, <=1 reorder).
X264_PARAMS = ("nal-hrd=vbr:aud=1:weightp=0:b-pyramid=none:b-adapt=0:psy=0:"
               "chroma-qp-offset=0:open-gop=0")

# ---------------------------------------------------------------------------
# Static bytes copied from camera-made clips (no serial numbers / personal data)
# ---------------------------------------------------------------------------
FTYP = bytes.fromhex(
    "0000001c667479704d534e56015a00704d534e566d70343269736f6d"
)  # 28 bytes
PROF = bytes.fromhex(
    "000000947575696450524f4621d24fcebb88695cfac9c740000000000000000300000014465052460000000000000000"
    "000000000000002c4150524600000000000000026d703461000002290000000000000080000000800000bb8000000002"
    "0000003456505246000000000000000161766331014d00280002000200000bb800003e800017f9dc0017f9dc050002d0"
    "00010001"
)  # 148 bytes
USMT_TRAK = bytes.fromhex(
    "000000347575696455534d5421d24fcebb88695cfac9c7400000001c4d544454000100120000000a55c4000000000001"
    "00000000"
)  # 52 bytes
USMT_MOOV = bytes.fromhex(
    "0000002e7575696455534d5421d24fcebb88695cfac9c740000000164d5444540001000c0000000bd5c40000ff10"
)  # 46 bytes
MVHD_TAIL = bytes.fromhex(
    "000100000100000000000000000000000001000000000000000000000000000000010000000000000000000000000000"
    "4000000000000000000000000000000000000000000000000000000000000003"
)  # 80 bytes
TKHD_V_TAIL = bytes.fromhex(
    "000000000000000000000000000000000001000000000000000000000000000000010000000000000000000000000000"
    "400000000500000002d00000"
)  # 60 bytes
TKHD_A_TAIL = bytes.fromhex(
    "000000000000000000000000010000000001000000000000000000000000000000010000000000000000000000000000"
    "400000000000000000000000"
)  # 60 bytes
HDLR_V = bytes.fromhex(
    "0000003468646c72000000000000000076696465000000000000000000000000566964656f204d656469612048616e64"
    "6c657200"
)  # 52 bytes
HDLR_A = bytes.fromhex(
    "0000003468646c720000000000000000736f756e000000000000000000000000536f756e64204d656469612048616e64"
    "6c657200"
)  # 52 bytes
VMHD = bytes.fromhex(
    "00000014766d6864000000010000000000000000"
)  # 20 bytes
SMHD = bytes.fromhex(
    "00000010736d68640000000000000000"
)  # 16 bytes
DINF = bytes.fromhex(
    "0000002464696e660000001c6472656600000000000000010000000c75726c2000000001"
)  # 36 bytes
AVC1_HDR = bytes.fromhex(
    "000000000000000100000000000000000000000000000000050002d000480000004800000000000000010a4156432043"
    "6f64696e670000000000000000000000000000000000000000000018ffff"
)  # 78 bytes
STSD_A = bytes.fromhex(
    "0000005b7374736400000000000000010000004b6d703461000000000000000100000000000000000002001000000000"
    "bb8000000000002765736473000000000319000000041140150006000001f4000001f40005021190060102"
)  # 91 bytes
THM_EXIF = bytes.fromhex(
    "45786966000049492a00080000000a000e01020020000000860000000f01020005000000a6000000100102000a000000"
    "ac0000001201030001000000010000001a01050001000000b60000001b01050001000000be0000002801030001000000"
    "020000003201020014000000c60000001302030001000000020000006987040001000000da0000000000000020202020"
    "20202020202020202020202020202020202020202020202020202000534f4e5900004644522d4158343341005e010000"
    "010000005e01000001000000323032363a31313a31332032323a33383a3230000b000090070004000000303233300390"
    "0200140000006401000004900200140000007801000001910700040000000102030002910500010000008c0100008692"
    "0700400000009401000000a00700040000003031303001a00300010000000100000002a0040001000000a000000003a0"
    "0400010000007800000005a0040001000000d401000000000000323032363a31313a31332032323a33383a3230003230"
    "32363a31313a31332032323a33383a323000020000000100000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000020001000200"
    "0400000054484d00020007000400000030313030000000000000"
)  # 506 bytes
THM_DQT = bytes.fromhex(
    "000202020202020404040404040404040806040406080a08060606080a0e0a080606080a0e0e0c0a080a0c0e100c0a0a"
    "0c10100e0e0e101210101214141418181c01040404040404060404060c0604060c180c08080c181e180e0a0e181e1e1e"
    "1a12121a1e1e1e1e1e1c1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e"
)  # 130 bytes
THM_DHT = bytes.fromhex(
    "0000010501010101010100000000000000000102030405060708090a0b100002010303020403050504040000017d0102"
    "0300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a"
    "3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92"
    "939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4"
    "e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa0100030101010101010101010000000000000102030405060708090a0b110002"
    "0102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f01562"
    "72d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a"
    "737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5"
    "c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"
)  # 416 bytes


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
class AX43Error(Exception):
    """A problem worth showing to the user (bad input, card full, ffmpeg failed...)."""


class Cancelled(AX43Error):
    pass


def die(msg):
    raise AX43Error(msg)


# Tool locations; a GUI/bundle can point these at bundled ffmpeg/ffprobe.
FFMPEG = os.environ.get("AX43_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("AX43_FFPROBE", "ffprobe")
HIDE_CONSOLE = False  # GUI sets True so ffmpeg doesn't flash console windows on Windows


def _popen_kw():
    if HIDE_CONSOLE and os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL}
    return {}


def _resolve(cmd):
    exe = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}.get(cmd[0], cmd[0])
    return [exe] + list(cmd[1:])


def run(cmd, **kw):
    cmd = _resolve(cmd)
    try:
        return subprocess.run(cmd, check=True, **_popen_kw(), **kw)
    except FileNotFoundError:
        die(f"'{cmd[0]}' not found - install ffmpeg and put ffmpeg/ffprobe on PATH")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace")[-2000:] if e.stderr else ""
        die(f"{os.path.basename(cmd[0])} failed:\n{err}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# card inspection
# ---------------------------------------------------------------------------
def norm_card(card):
    # "F:" alone means "current dir on F:" on Windows; we want the drive root
    if re.fullmatch(r"[A-Za-z]:", card):
        card += "\\"
    return card


def looks_like_card(root):
    try:
        return (os.path.isdir(os.path.join(root, "AVF_INFO"))
                or os.path.isdir(os.path.join(root, "PRIVATE", "M4ROOT")))
    except OSError:
        return False


def find_cards():
    """Return roots of mounted volumes that look like a camera-formatted Sony card."""
    roots = []
    if os.name == "nt":
        import ctypes
        import string
        k32 = ctypes.windll.kernel32
        k32.SetErrorMode(0x0001 | 0x8000)  # no "insert a disk" popups for empty readers
        mask = k32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if letter in "AB" or not (mask >> i) & 1:
                continue
            root = f"{letter}:\\"
            if k32.GetDriveTypeW(root) in (2, 3):  # removable or fixed (card readers can be either)
                roots.append(root)
    else:
        import glob
        for pat in ("/mnt/*", "/media/*", "/media/*/*", "/run/media/*/*", "/Volumes/*"):
            roots += glob.glob(pat)
    return [r for r in roots if looks_like_card(r)]


def check_card(card):
    if not os.path.isdir(card):
        die(f"card path {card!r} does not exist")
    looks = [os.path.isdir(os.path.join(card, d)) for d in ("PRIVATE", "AVF_INFO", "MP_ROOT")]
    if not any(looks):
        die(f"{card!r} does not look like an AX43 card (no PRIVATE/, AVF_INFO/ or MP_ROOT/). "
            "Format the card in the camera and record one clip first.")
    folder = os.path.join(card, "MP_ROOT", "100ANV01")
    return folder


def next_number(card):
    used = 0
    root = os.path.join(card, "MP_ROOT")
    if os.path.isdir(root):
        for d in os.listdir(root):
            p = os.path.join(root, d)
            if os.path.isdir(p):
                for f in os.listdir(p):
                    m = re.fullmatch(r"MAH(\d{5})\.(MP4|THM)", f, re.I)
                    if m:
                        used = max(used, int(m.group(1)))
    return used + 1


# ---------------------------------------------------------------------------
# encoding (ffmpeg)
# ---------------------------------------------------------------------------
def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", path], capture_output=True).stdout
    info = json.loads(out)
    v = [s for s in info["streams"] if s["codec_type"] == "video"
         and s.get("disposition", {}).get("attached_pic", 0) == 0]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not v:
        die(f"{path}: no video stream")
    dur = float(info["format"].get("duration") or v[0].get("duration") or 0)
    return dur, bool(a)


def encode(src, dst, has_audio, bitrate_k, preset, duration=0.0, progress=None, cancel=None):
    """Encode src -> dst. With progress/cancel (GUI) ffmpeg reports via -progress;
    progress(fraction) is called, and cancel (threading.Event) aborts."""
    vf = ("scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
          "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24000/1001,format=yuv420p")
    quiet = progress is not None or cancel is not None
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    cmd += ["-nostats", "-progress", "pipe:1"] if quiet else ["-stats"]
    cmd += ["-y", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", preset, "-profile:v", "main", "-level", "4.0",
            "-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.2)}k",
            "-bufsize", f"{int(bitrate_k * 1.2)}k",
            "-g", "24", "-keyint_min", "24", "-sc_threshold", "0", "-bf", "1", "-refs", "2",
            "-x264-params", X264_PARAMS,
            "-color_primaries", "bt709", "-color_trc", "iec61966-2-4", "-colorspace", "bt709",
            "-color_range", "tv",
            "-af", "apad", "-shortest",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-aac_pns", "0", "-aac_is", "0",
            "-video_track_timescale", "24000", "-map_metadata", "-1", "-map_chapters", "-1",
            "-sn", "-dn", "-f", "mp4", dst]
    if not quiet:
        run(cmd)
        return
    cmd = _resolve(cmd)
    with tempfile.TemporaryFile() as errf:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, **_popen_kw())
        except FileNotFoundError:
            die(f"'{cmd[0]}' not found")
        try:
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise Cancelled("cancelled")
                k, _, v = line.decode(errors="replace").strip().partition("=")
                if k == "out_time_us" and duration > 0 and progress and v.isdigit():
                    progress(min(1.0, int(v) / 1e6 / duration))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode != 0:
            errf.seek(0)
            die("ffmpeg failed:\n" + errf.read().decode(errors="replace")[-2000:])


def thumb_rgb(src, at):
    vf = ("scale=160:90:force_original_aspect_ratio=decrease,"
          "pad=160:120:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    out = run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", src, "-frames:v", "1",
               "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    if len(out) != 160 * 120 * 3:
        out = run(["ffmpeg", "-v", "error", "-i", src, "-frames:v", "1", "-vf", vf,
                   "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    return out


# ---------------------------------------------------------------------------
# MP4 parsing (input from ffmpeg)
# ---------------------------------------------------------------------------
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta"}


def iter_boxes(buf, off, end):
    while off < end:
        size, typ = struct.unpack(">I4s", buf[off:off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        yield typ, off, hdr, size
        off += size


def find(buf, path, off=0, end=None):
    end = len(buf) if end is None else end
    head, _, rest = path.partition("/")
    out = []
    for typ, o, hdr, size in iter_boxes(buf, off, end):
        if typ == head.encode():
            out += find(buf, rest, o + hdr, o + size) if rest else [(o, hdr, size)]
    return out


def body(buf, path, off=0, end=None):
    o, h, s = find(buf, path, off, end)[0]
    return buf[o + h:o + s]


def read_moov(f):
    """Return the moov box bytes of an MP4 file (without reading mdat)."""
    f.seek(0, 2)
    end = f.tell()
    off = 0
    while off < end:
        f.seek(off)
        hdr = f.read(16)
        size, typ = struct.unpack(">I4s", hdr[:8])
        h = 8
        if size == 1:
            size = struct.unpack(">Q", hdr[8:16])[0]
            h = 16
        elif size == 0:
            size = end - off
        if typ == b"moov":
            f.seek(off)
            return f.read(size)
        off += size
    die("encoded file has no moov box")


def table(b, fmt_entry, n_off=4):
    n = struct.unpack(">I", b[n_off:n_off + 4])[0]
    sz = struct.calcsize(">" + fmt_entry)
    return [struct.unpack(">" + fmt_entry, b[n_off + 4 + i * sz:n_off + 4 + (i + 1) * sz]) for i in range(n)]


def tracks(moov):
    out = {}
    for o, h, s in find(moov, "moov/trak"):
        t0, t1 = o + h, o + s
        kind = body(moov, "mdia/hdlr", t0, t1)[8:12]
        md = body(moov, "mdia/mdhd", t0, t1)
        ts = struct.unpack(">I", md[12:16] if md[0] == 0 else md[20:24])[0]
        so, sh, ss = find(moov, "mdia/minf/stbl", t0, t1)[0]
        s0, s1 = so + sh, so + ss
        stsz = body(moov, "stsz", s0, s1)
        fixed, n = struct.unpack(">II", stsz[4:12])
        sizes = [fixed] * n if fixed else list(struct.unpack(">%dI" % n, stsz[12:12 + 4 * n]))
        if find(moov, "stco", s0, s1):
            chunks = [c[0] for c in table(body(moov, "stco", s0, s1), "I")]
        else:
            chunks = [c[0] for c in table(body(moov, "co64", s0, s1), "Q")]
        stsc = table(body(moov, "stsc", s0, s1), "III")
        offs, si = [], 0
        for ci, coff in enumerate(chunks, 1):
            per = [e[1] for e in stsc if e[0] <= ci][-1]
            p = coff
            for _ in range(per):
                offs.append(p)
                p += sizes[si]
                si += 1
        deltas = table(body(moov, "stts", s0, s1), "II")
        sync = [x[0] for x in table(body(moov, "stss", s0, s1), "I")] if find(moov, "stss", s0, s1) else None
        ctts = None
        if find(moov, "ctts", s0, s1):
            b = body(moov, "ctts", s0, s1)
            if b[0] != 0:
                die("unexpected negative composition offsets from ffmpeg")
            ctts = []
            for cnt, ofs in table(b, "II"):
                ctts += [ofs] * cnt
        stsd = body(moov, "stsd", s0, s1)
        out[kind] = dict(ts=ts, offs=offs, sizes=sizes, deltas=deltas, sync=sync, ctts=ctts, stsd=stsd)
    return out


def avcc_from_stsd(stsd):
    entry = stsd[8:]
    esz = struct.unpack(">I", entry[:4])[0]
    for typ, o, hdr, size in iter_boxes(entry, 8 + 78, esz):
        if typ == b"avcC":
            return entry[o:o + size]
    die("no avcC in encoded video")


X264_UUID = bytes.fromhex("dc45e9bde6d948b7962cd820d923eeef")


def strip_x264_sei(sample):
    out, p = [], 0
    while p < len(sample):
        n = int.from_bytes(sample[p:p + 4], "big")
        nal = sample[p + 4:p + 4 + n]
        p += 4 + n
        if nal and (nal[0] & 0x1F) == 6 and len(nal) > 18 and nal[1] == 5 and X264_UUID in nal[:24]:
            continue
        out.append(len(nal).to_bytes(4, "big") + nal)
    return b"".join(out)


# ---------------------------------------------------------------------------
# MP4 writing in the camera's layout (streams sample data; low memory)
# ---------------------------------------------------------------------------
def mkbox(typ, payload):
    return struct.pack(">I4s", len(payload) + 8, typ) + payload


def full(typ, version, flags, payload):
    return mkbox(typ, struct.pack(">I", (version << 24) | flags) + payload)


def remux(src_path, dst_path, when_unix):
    with open(src_path, "rb") as f:
        tr = tracks(read_moov(f))
        v, a = tr.get(b"vide"), tr.get(b"soun")
        if not v or not a:
            die("encoded file is missing audio or video")
        if v["ts"] != 24000 or any(d != 1001 for _, d in v["deltas"]):
            die("unexpected video timing from ffmpeg")
        if a["ts"] != 48000 or any(d != 1024 for _, d in a["deltas"][:-1]):
            die("unexpected audio timing from ffmpeg")

        nv = len(v["sizes"])
        vdur = nv * 1001
        vdur48 = vdur * 2
        na = min(len(a["sizes"]), vdur48 // 1024)
        vsizes = list(v["sizes"])
        asizes = a["sizes"][:na]
        # x264 puts its info SEI only in the first frame; strip it there
        f.seek(v["offs"][0])
        first = strip_x264_sei(f.read(vsizes[0]))
        vsizes[0] = len(first)
        cto = v["ctts"] or [1001] * nv
        sync = v["sync"] or list(range(1, nv + 1))

        # chunk plan: 12 video frames then the audio frames up to the same time
        plan, ai = [], 0
        for c in range(0, nv, 12):
            ve = min(c + 12, nv)
            upto = min(na, (ve * 1001 * 2) // 1024)
            plan.append((range(c, ve), range(ai, upto)))
            ai = upto

        head = FTYP + PROF
        pos = len(head) + 8
        vco, aco = [], []
        for vr, ar in plan:
            vco.append(pos)
            pos += sum(vsizes[i] for i in vr)
            if len(ar):
                aco.append(pos)
                pos += sum(asizes[i] for i in ar)
        mdat_size = pos - len(head)
        if mdat_size > 0xFFFFFFFF:
            die("movie too large for one file (over 4 GB) - lower --bitrate")

        # ---- moov
        mt = when_unix + MAC_EPOCH_OFFSET

        def tkhd(track_id, tail, dur):
            return full(b"tkhd", 0, 7, struct.pack(">IIIII", mt, mt, track_id, 0, dur) + tail)

        def mdhd(ts, dur):
            return full(b"mdhd", 0, 0, struct.pack(">IIIIHH", mt, mt, ts, dur, 0x55C4, 0))

        def edts(dur, media_time):
            return mkbox(b"edts", full(b"elst", 0, 0, struct.pack(">IIIi", 1, dur, media_time, 0x00010000)))

        def stsc(counts):
            ents, prev = [], None
            for i, c in enumerate(counts, 1):
                if c != prev:
                    ents.append(struct.pack(">III", i, c, 1))
                    prev = c
            return full(b"stsc", 0, 0, struct.pack(">I", len(ents)) + b"".join(ents))

        def u32(typ, vals):
            return full(typ, 0, 0, struct.pack(">I", len(vals)) + struct.pack(">%dI" % len(vals), *vals))

        runs = []
        for o in cto:
            if runs and runs[-1][1] == o:
                runs[-1][0] += 1
            else:
                runs.append([1, o])
        stsd_v = full(b"stsd", 0, 0, struct.pack(">I", 1) + mkbox(b"avc1", AVC1_HDR + avcc_from_stsd(v["stsd"])))
        v_stbl = mkbox(b"stbl", stsd_v
                       + full(b"stts", 0, 0, struct.pack(">III", 1, nv, 1001))
                       + full(b"ctts", 0, 0, struct.pack(">I", len(runs)) + b"".join(struct.pack(">II", c, o) for c, o in runs))
                       + stsc([len(vr) for vr, _ in plan])
                       + full(b"stsz", 0, 0, struct.pack(">II", 0, nv) + struct.pack(">%dI" % nv, *vsizes))
                       + u32(b"stco", vco)
                       + u32(b"stss", sync))
        a_stbl = mkbox(b"stbl", STSD_A
                       + full(b"stts", 0, 0, struct.pack(">III", 1, na, 1024))
                       + stsc([len(ar) for _, ar in plan if len(ar)])
                       + full(b"stsz", 0, 0, struct.pack(">II", 0, na) + struct.pack(">%dI" % na, *asizes))
                       + u32(b"stco", aco))
        adur = na * 1024
        moov = mkbox(b"moov",
                     full(b"mvhd", 0, 0, struct.pack(">IIII", mt, mt, 48000, vdur48) + MVHD_TAIL)
                     + mkbox(b"trak", tkhd(1, TKHD_V_TAIL, vdur48) + edts(vdur48, cto[0])
                             + mkbox(b"mdia", mdhd(24000, vdur) + HDLR_V + mkbox(b"minf", VMHD + DINF + v_stbl))
                             + USMT_TRAK)
                     + mkbox(b"trak", tkhd(2, TKHD_A_TAIL, adur) + edts(adur, 0)
                             + mkbox(b"mdia", mdhd(48000, adur) + HDLR_A + mkbox(b"minf", SMHD + DINF + a_stbl))
                             + USMT_TRAK)
                     + USMT_MOOV)
        if pos + len(moov) > FAT32_MAX:
            die("movie too large for FAT32 (4 GB) - lower --bitrate")

        # ---- write
        with open(dst_path, "xb") as out:
            out.write(head + struct.pack(">I4s", mdat_size, b"mdat"))
            for vr, ar in plan:
                for i in vr:
                    if i == 0:
                        out.write(first)
                    else:
                        f.seek(v["offs"][i])
                        out.write(f.read(vsizes[i]))
                for i in ar:
                    f.seek(a["offs"][i])
                    out.write(f.read(asizes[i]))
            out.write(moov)
            if out.tell() != pos + len(moov):
                die("internal error: size mismatch while writing")
    return nv, na


# ---------------------------------------------------------------------------
# THM: tiny baseline JPEG encoder (4:2:2, camera's tables) - no Pillow needed
# ---------------------------------------------------------------------------
ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48,
          41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15,
          23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]
_COS = [[(math.sqrt(0.5) if u == 0 else 1.0) * math.cos((2 * x + 1) * u * math.pi / 16) / 2
         for x in range(8)] for u in range(8)]


def _parse_dqt(b):
    q, p = {}, 0
    while p < len(b):
        tid = b[p] & 0x0F
        zz = b[p + 1:p + 65]
        nat = [0] * 64
        for i, k in enumerate(ZIGZAG):
            nat[k] = zz[i]
        q[tid] = nat
        p += 65
    return q


def _parse_dht(b):
    tabs, p = {}, 0
    while p < len(b):
        tc, th = b[p] >> 4, b[p] & 0x0F
        counts = b[p + 1:p + 17]
        n = sum(counts)
        syms = b[p + 17:p + 17 + n]
        code, k, codes = 0, 0, {}
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                codes[syms[k]] = (code, length)
                code += 1
                k += 1
            code <<= 1
        tabs[(tc, th)] = codes
        p += 17 + n
    return tabs


class _Bits:
    def __init__(self):
        self.out, self.acc, self.n = bytearray(), 0, 0

    def put(self, val, length):
        self.acc = (self.acc << length) | (val & ((1 << length) - 1))
        self.n += length
        while self.n >= 8:
            self.n -= 8
            byte = (self.acc >> self.n) & 0xFF
            self.out.append(byte)
            if byte == 0xFF:
                self.out.append(0)
        self.acc &= (1 << self.n) - 1

    def flush(self):
        if self.n:
            self.put((1 << (8 - self.n)) - 1, 8 - self.n)


def _fdct(block):
    tmp = [[sum(_COS[u][x] * block[y * 8 + x] for x in range(8)) for u in range(8)] for y in range(8)]
    return [sum(_COS[v][y] * tmp[y][u] for y in range(8)) for v in range(8) for u in range(8)]


def _enc_block(bits, block, qt, dc_tab, ac_tab, pred):
    coef = _fdct(block)
    q = [int(round(coef[i] / qt[i])) for i in range(64)]
    zz = [q[k] for k in ZIGZAG]

    def mag(x):
        s = abs(x).bit_length()
        return s, (x if x >= 0 else x + (1 << s) - 1)

    diff = zz[0] - pred
    s, m = mag(diff)
    c, l = dc_tab[s]
    bits.put(c, l)
    if s:
        bits.put(m, s)
    run_ = 0
    for i in range(1, 64):
        if zz[i] == 0:
            run_ += 1
            continue
        while run_ > 15:
            c, l = ac_tab[0xF0]
            bits.put(c, l)
            run_ -= 16
        s, m = mag(zz[i])
        c, l = ac_tab[(run_ << 4) | s]
        bits.put(c, l)
        bits.put(m, s)
        run_ = 0
    if run_:
        c, l = ac_tab[0x00]
        bits.put(c, l)
    return zz[0]


def make_thm(rgb, date_str):
    W, H = 160, 120
    qt = _parse_dqt(THM_DQT)
    ht = _parse_dht(THM_DHT)
    Y = [0.0] * (W * H)
    Cb = [0.0] * (W * H)
    Cr = [0.0] * (W * H)
    for i in range(W * H):
        r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        Y[i] = 0.299 * r + 0.587 * g + 0.114 * b - 128
        Cb[i] = -0.168736 * r - 0.331264 * g + 0.5 * b
        Cr[i] = 0.5 * r - 0.418688 * g - 0.081312 * b
    bits = _Bits()
    pred = [0, 0, 0]
    for my in range(0, H, 8):
        for mx in range(0, W, 16):
            for bx in (0, 8):
                blk = [Y[(my + y) * W + mx + bx + x] for y in range(8) for x in range(8)]
                pred[0] = _enc_block(bits, blk, qt[0], ht[(0, 0)], ht[(1, 0)], pred[0])
            for ci, plane in ((1, Cb), (2, Cr)):
                blk = [(plane[(my + y) * W + mx + 2 * x] + plane[(my + y) * W + mx + 2 * x + 1]) / 2
                       for y in range(8) for x in range(8)]
                pred[ci] = _enc_block(bits, blk, qt[1], ht[(0, 1)], ht[(1, 1)], pred[ci])
    bits.flush()

    def seg(marker, payload):
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    exif = re.sub(rb"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", date_str.encode(), THM_EXIF)
    sof = struct.pack(">BHHB", 8, H, W, 3) + bytes([1, 0x21, 0, 2, 0x11, 1, 3, 0x11, 1])
    sos = bytes([3, 1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0])
    return (b"\xFF\xD8" + seg(0xE1, exif) + seg(0xDB, THM_DQT) + seg(0xC4, THM_DHT)
            + seg(0xC0, sof) + seg(0xDA, sos) + bytes(bits.out) + b"\xFF\xD9")


# ---------------------------------------------------------------------------
# safe copy to card
# ---------------------------------------------------------------------------
def place(local, final, progress=None, cancel=None):
    """Copy local -> final on the card without ever overwriting anything.
    progress(fraction) covers copy (0-0.8) and read-back verification (0.8-1)."""
    if os.path.exists(final):
        die(f"refusing to overwrite existing {final}")
    tmp = os.path.join(os.path.dirname(final), f"_ax43put_{os.getpid()}.tmp")
    if os.path.exists(tmp):
        die(f"leftover temp file {tmp} from an interrupted run - check it and delete it by hand")
    total = max(1, os.path.getsize(local))
    try:
        h_local = hashlib.sha256()
        done = 0
        with open(local, "rb") as fi, open(tmp, "xb") as fo:
            for chunk in iter(lambda: fi.read(4 << 20), b""):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                fo.write(chunk)
                h_local.update(chunk)
                done += len(chunk)
                if progress:
                    progress(0.8 * done / total)
            fo.flush()
            os.fsync(fo.fileno())
        h_card = hashlib.sha256()
        done = 0
        with open(tmp, "rb") as fc:
            for chunk in iter(lambda: fc.read(4 << 20), b""):
                h_card.update(chunk)
                done += len(chunk)
                if progress:
                    progress(0.8 + 0.2 * done / total)
        if h_card.hexdigest() != h_local.hexdigest():
            die("verification failed: the copy on the card does not match (SHA-256)")
        if os.path.exists(final):
            die(f"{final} appeared during copy")
        os.rename(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)  # only ever our own temp file
        raise


# ---------------------------------------------------------------------------
# high-level API (used by the CLI below and by the GUI)
# ---------------------------------------------------------------------------
def add_videos(card, videos, bitrate=2500, preset="slow", dry_run=False, log=print,
               status=None, cancel=None, keep_going=False):
    """Convert and add each video to the card.

    status(index, stage, fraction) is called with stage in
    planned/encoding/building/copying/done/failed/cancelled.
    keep_going=True records per-file errors and continues with the next file.
    Returns a list of (video, 'MAH#####' or None, error message or None).
    """
    def st(i, stage, frac=0.0):
        if status:
            status(i, stage, frac)

    card = norm_card(card)
    folder = check_card(card)
    results = [[v, None, None] for v in videos]
    jobs = []
    num = next_number(card)
    for i, vpath in enumerate(videos):
        try:
            if not os.path.isfile(vpath):
                die(f"{os.path.basename(vpath)}: file not found")
            dur, has_audio = probe(vpath)
            est = int(dur * (bitrate * 1.1 + 140) * 1000 / 8) + 1_000_000
            if est > FAT32_MAX:
                die(f"{os.path.basename(vpath)}: ~{est / 1e9:.1f} GB at {bitrate} kbit/s is over the "
                    f"4 GB FAT32 limit; use bitrate {int(bitrate * FAT32_MAX / est * 0.95)}")
            jobs.append((i, vpath, dur, has_audio, est))
            log(f"  {os.path.basename(vpath)}  ({dur / 60:.1f} min{'' if has_audio else ', no audio'})"
                f"  ->  MAH{num:05d}.MP4 + .THM  (~{est / 1e6:.0f} MB)")
            num += 1
            st(i, "planned")
        except (AX43Error, OSError) as e:
            if not keep_going:
                raise
            results[i][2] = str(e)
            log(f"  SKIP {os.path.basename(vpath)}: {e}")
            st(i, "failed")

    free = shutil.disk_usage(card).free
    need = sum(j[4] for j in jobs)
    log(f"Card {card}: {free / 1e9:.2f} GB free, about {need / 1e9:.2f} GB needed")
    if need * 1.05 > free:
        die(f"not enough free space on the card ({free / 1e9:.2f} GB free, ~{need / 1e9:.2f} GB needed)")
    if dry_run:
        log("Dry run - nothing written.")
        return [tuple(r) for r in results]

    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ax43put_") as tmpd:
        for i, vpath, dur, has_audio, est in jobs:
            base = os.path.basename(vpath)
            enc = os.path.join(tmpd, "enc.mp4")
            mp4 = os.path.join(tmpd, "out.mp4")
            thm = os.path.join(tmpd, "out.thm")
            try:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                log(f"\n{base}: encoding ...")
                st(i, "encoding", 0.0)
                encode(vpath, enc, has_audio, bitrate, preset, duration=dur,
                       progress=(lambda f, i=i: st(i, "encoding", f)) if status else None,
                       cancel=cancel)
                st(i, "building", 0.0)
                now = int(time.time())
                nv, na = remux(enc, mp4, now)
                rgb = thumb_rgb(mp4, min(5.0, dur / 3) if dur else 0)
                with open(thm, "xb") as f:
                    f.write(make_thm(rgb, datetime.fromtimestamp(now).strftime("%Y:%m:%d %H:%M:%S")))
                os.remove(enc)
                size = os.path.getsize(mp4)
                if size * 1.02 > shutil.disk_usage(card).free:
                    die("card ran out of space")
                name = f"MAH{next_number(card):05d}"  # re-check right before writing
                log(f"{base}: copying to card as {name}.MP4 ({size / 1e6:.0f} MB) ...")
                st(i, "copying", 0.0)
                place(mp4, os.path.join(folder, name + ".MP4"),
                      progress=(lambda f, i=i: st(i, "copying", f)) if status else None, cancel=cancel)
                place(thm, os.path.join(folder, name + ".THM"))
                results[i][1] = name
                log(f"{base}: done -> {name}.MP4 (verified)")
                st(i, "done", 1.0)
            except Cancelled:
                st(i, "cancelled")
                raise
            except (AX43Error, OSError) as e:
                results[i][2] = str(e)
                st(i, "failed")
                if not keep_going:
                    raise
                log(f"{base}: FAILED - {e}")
            finally:
                for p_ in (enc, mp4, thm):
                    if os.path.exists(p_):
                        os.remove(p_)
    return [tuple(r) for r in results]


CAMERA_STEPS = ("Eject the card safely, put it in the camera, then:\n"
                "  MENU -> Setup -> Media Settings -> Repair Img. DB F. -> Yes\n"
                "  Playback -> switch the movie format to MP4.")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("videos", nargs="+", help="video file(s) to add")
    ap.add_argument("--card", help="SD card root, e.g. F:\\ or /mnt/f (default: auto-detect)")
    ap.add_argument("--bitrate", type=int, default=2500, help="video kbit/s (default 2500, like the camera)")
    ap.add_argument("--preset", default="slow", help="x264 preset (default slow; 'fast' encodes quicker)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, change nothing")
    args = ap.parse_args()
    try:
        card = args.card
        if not card:
            cards = find_cards()
            if len(cards) != 1:
                die("found no AX43 card - use --card" if not cards else
                    f"found several cards ({', '.join(cards)}) - pick one with --card")
            card = cards[0]
        res = add_videos(card, args.videos, args.bitrate, args.preset, args.dry_run)
    except AX43Error as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if not args.dry_run and any(r[1] for r in res):
        print("\nAll done. " + CAMERA_STEPS)


if __name__ == "__main__":
    main()
