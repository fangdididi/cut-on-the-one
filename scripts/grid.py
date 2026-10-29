#!/usr/bin/env python3
"""cut-on-the-one: lock a code-rendered video's edit to its music.

Standard library only. Subcommands:
  detect  SONG.wav            -> song.json  (tempo, first downbeat, bar energy, strong hits)
  plan    SONG.json SHOTS.json -> plan.json (every cut on the grid, frames from absolute time)
  audit   PLAN.json           -> findings, exit 1 when any ERROR
  export  PLAN.json --to remotion|csv|labels|srt
  click   PLAN.json OUT.wav [--mix SONG.wav]  -> audition track (clicks + cut tones)
"""
import argparse
import array
import json
import math
import random
import sys
import wave

ENV_RATE = 100  # onset envelope frames per second (10 ms hop)
_HOP_BIAS = 0.0  # seconds; envelope frame i covers [i, i+1) hops
READ_RATE = {"en": 3.0, "zh": 6.0}  # words/s (en) or characters/s (zh) a viewer can read on screen
SETTLE_S = 0.4  # time for the eye to land before reading starts
BIG_PER_30S = 3
SEVERITY = {
    "OFF_GRID": "ERROR", "SHORT_READ": "ERROR", "PAST_SONG": "ERROR", "SFX_LATE": "ERROR",
    "ACCENT_FLOOD": "WARN", "ACCENT_CROWD": "WARN", "BIG_OFF_BAR": "WARN",
    "ENDS_MID_PHRASE": "WARN", "HIT_UNDER_VOICE": "WARN", "STALL": "WARN",
    "LOW_CONFIDENCE": "WARN", "GAP_OR_OVERLAP": "ERROR",
}


# ---------------------------------------------------------------- audio io
def read_wav_mono(path, max_seconds=None):
    with wave.open(path, "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if max_seconds:
            n = min(n, int(max_seconds * sr))
        raw = w.readframes(n)
    if width == 2:
        data = array.array("h", raw)
        scale = 32768.0
    elif width == 1:
        data = array.array("h", [(b - 128) << 8 for b in raw])
        scale = 32768.0
    else:
        raise SystemExit("Only 8/16-bit PCM WAV is supported. Convert first: "
                         "ffmpeg -i song.mp3 -ac 1 -ar 22050 -sample_fmt s16 song.wav")
    if sys.byteorder == "big" and width == 2:
        data.byteswap()
    if ch > 1:
        mono = array.array("f", (sum(data[i:i + ch]) / (ch * scale) for i in range(0, len(data) - ch + 1, ch)))
    else:
        mono = array.array("f", (x / scale for x in data))
    return mono, sr


def write_wav(path, samples, sr):
    out = array.array("h", (max(-32767, min(32767, int(s * 32767))) for s in samples))
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(out.tobytes())


# ---------------------------------------------------------------- detect
def onset_envelope(mono, sr):
    env, power = [], []
    n = 0
    while True:
        i, j = int(n * sr / ENV_RATE), int((n + 1) * sr / ENV_RATE)  # exact 10 ms frames for any sample rate
        if j > len(mono) or j <= i:
            break
        seg = mono[i:j]
        pw = sum(x * x for x in seg) / (j - i)
        n += 1
        power.append(pw)
        env.append(math.log1p(1000.0 * pw))
    raw = [env[0] if env else 0.0] + [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    # remove slow swells so steady loud passages do not read as onsets
    out = []
    win = 8
    for i in range(len(raw)):
        lo, hi = max(0, i - win), min(len(raw), i + win + 1)
        local = sum(raw[lo:hi]) / (hi - lo)
        out.append(max(0.0, raw[i] - local))
    return out, env, power


def _sample(onset, t):
    i = int(round(t))
    if not 0 <= i < len(onset):
        return None
    return max(onset[max(0, i - 1):i + 2])  # tolerate one hop of jitter


def _comb(onset, period, phase):
    on, off, k = [], [], 0
    while True:
        t = phase + k * period
        a = _sample(onset, t)
        if a is None:
            break
        on.append(a)
        b = _sample(onset, t + period / 2.0)
        if b is not None:
            off.append(b)
        k += 1
    if len(on) < 4:
        return -1.0, 0.0, 0.0
    m_on = sum(on) / len(on)
    m_off = sum(off) / len(off) if off else 0.0
    return m_on - m_off, m_on, m_off


def _prior(bpm):
    # mild preference for the 80-160 band where most promo music lives
    return math.exp(-0.5 * (math.log2(bpm / 118.0) / 0.9) ** 2)


def _peak_near(onset, t, radius=3):
    lo, hi = max(0, int(round(t)) - radius), min(len(onset) - 1, int(round(t)) + radius)
    if lo > hi:
        return None, 0.0
    i = max(range(lo, hi + 1), key=lambda j: onset[j])
    if onset[i] <= 0:
        return None, 0.0
    a = onset[i - 1] if i > 0 else 0.0
    b = onset[i + 1] if i + 1 < len(onset) else 0.0
    den = a - 2 * onset[i] + b
    frac = 0.5 * (a - b) / den if den < 0 else 0.0
    return i + max(-0.5, min(0.5, frac)), onset[i]


def _fit_line(onset, period, phase):
    """weighted least squares of detected peak time against beat index -> (period, phase)"""
    ks, ts, ws = [], [], []
    k = 0
    while phase + k * period < len(onset):
        t, w = _peak_near(onset, phase + k * period)
        if t is not None:
            ks.append(k); ts.append(t); ws.append(w)
        k += 1
    if len(ks) < 4:
        return period, phase
    W = sum(ws)
    mk = sum(w * x for w, x in zip(ws, ks)) / W
    mt = sum(w * y for w, y in zip(ws, ts)) / W
    sxx = sum(w * (x - mk) ** 2 for w, x in zip(ws, ks))
    if sxx <= 0:
        return period, phase
    slope = sum(w * (x - mk) * (y - mt) for w, x, y in zip(ws, ks, ts)) / sxx
    if abs(slope - period) > 0.05 * period:
        return period, phase
    return slope, mt - slope * mk


def estimate_grid(onset, bpm_min=60.0, bpm_max=200.0, meter=4, power=None):
    best = None
    per_bpm = []
    bpm = bpm_min
    while bpm <= bpm_max + 1e-9:
        period = 60.0 * ENV_RATE / bpm
        top = None
        for phase in range(int(period) + 1):
            s, _, _ = _comb(onset, period, phase)
            s *= _prior(bpm)
            if top is None or s > top:
                top = s
            if best is None or s > best[0]:
                best = (s, bpm, phase)
        per_bpm.append(top)
        bpm += 0.5
    _, bpm0, ph0 = best
    ranked = sorted(per_bpm)
    typical = ranked[len(ranked) // 2]
    # how far the winner stands above a typical tempo guess; ~0 for noise, ~1 for a clear pulse
    standout = (best[0] - typical) / (abs(best[0]) + 1e-9) if len(ranked) > 2 else None
    # refine tempo and phase on a finer lattice around the winner
    best = None
    b = bpm0 - 0.5
    while b <= bpm0 + 0.5 + 1e-9:
        period = 60.0 * ENV_RATE / b
        p = ph0 - 2.0
        while p <= ph0 + 2.0 + 1e-9:
            if p >= 0:
                s, m_on, m_off = _comb(onset, period, p)
                if best is None or s > best[0]:
                    best = (s, b, p, m_on, m_off)
            p += 0.25
        b += 0.05
    s, bpm, phase, m_on, m_off = best
    period = 60.0 * ENV_RATE / bpm
    for _ in range(3):
        period, phase = _fit_line(onset, period, phase)
    while phase - period >= -0.5:
        phase -= period
    phase = max(phase, 0.0)
    bpm = 60.0 * ENV_RATE / period
    # which of the `meter` beat phases carries the accent -> downbeat (linear power keeps loudness differences)
    acc = power if power else onset
    sums = [0.0] * meter
    k = 0
    while True:
        t = phase + k * period
        a = _sample(acc, t)
        if a is None:
            break
        sums[k % meter] += a
        k += 1
    down = max(range(meter), key=lambda i: sums[i])
    first_down = phase + down * period
    while first_down - meter * period >= -0.5:
        first_down -= meter * period
    first_down = max(first_down, 0.0)
    conf = (m_on - m_off) / (m_on + m_off + 1e-9) if m_on > 0 else 0.0
    if standout is not None:
        conf = min(conf, standout)
    srt = sorted(sums)
    accent = (srt[-1] - srt[-2]) / (srt[-1] + 1e-9) if len(srt) > 1 else 0.0
    return {"bpm": round(bpm, 2), "offset": round(first_down / ENV_RATE + _HOP_BIAS, 3),
            "confidence": round(max(0.0, min(1.0, conf)), 2),
            "downbeat_confidence": round(max(0.0, min(1.0, accent)), 2)}


def bar_energy(env, grid, meter, duration):
    bar_s = meter * 60.0 / grid["bpm"]
    out, t = [], grid["offset"]
    while t + bar_s <= duration + 1e-6:
        lo, hi = int(t * ENV_RATE), int((t + bar_s) * ENV_RATE)
        seg = env[lo:hi]
        out.append(round(sum(seg) / len(seg), 3) if seg else 0.0)
        t += bar_s
    return out


def strong_hits(onset, grid, meter, limit=8):
    if not onset:
        return []
    med = sorted(onset)[len(onset) // 2]
    ranked = sorted(range(len(onset)), key=lambda i: -onset[i])
    beat_s = 60.0 / grid["bpm"]
    bar_s = meter * beat_s
    picked = []
    for i in ranked:
        if onset[i] <= max(3 * med, 1e-6):
            break
        t = i / ENV_RATE
        if any(abs(t - p) < bar_s for p in picked):
            continue
        picked.append(t)
        if len(picked) >= limit:
            break
    hits = []
    for t in sorted(picked):
        k = round((t - grid["offset"]) / beat_s)
        hits.append({"t": round(t, 3), "beat": k, "pos": bar_pos(k, meter)})
    return hits


def suggest_drop(energies, phrase_bars=4):
    best, where = 0.0, None
    for i in range(phrase_bars, len(energies) - phrase_bars + 1, phrase_bars):
        before = sum(energies[i - phrase_bars:i]) / phrase_bars
        after = sum(energies[i:i + phrase_bars]) / phrase_bars
        if after - before > best:
            best, where = after - before, i + 1
    return where


def cmd_detect(a):
    mono, sr = read_wav_mono(a.song, a.max_seconds)
    duration = len(mono) / float(sr)
    onset, env, power = onset_envelope(mono, sr)
    lo, hi = (a.bpm, a.bpm) if a.bpm else (a.min_bpm, a.max_bpm)
    grid = estimate_grid(onset, lo, hi, a.meter, power)
    energies = bar_energy(env, grid, a.meter, duration)
    song = {
        "source": a.song, "duration": round(duration, 3), "meter": a.meter,
        "bpm": grid["bpm"], "offset": grid["offset"],
        "confidence": grid["confidence"], "downbeat_confidence": grid["downbeat_confidence"],
        "bar_energy": energies, "likely_lift_bar": suggest_drop(energies),
        "hits": strong_hits(onset, grid, a.meter),
        "sections": [],
        "note": "Check by ear: run `grid.py click` with --mix before trusting this grid.",
    }
    dump(song, a.out)


# ---------------------------------------------------------------- plan
def bar_pos(k, meter):
    return "%d.%d" % (k // meter + 1, k % meter + 1)


def read_seconds(shot):
    lang = shot.get("lang", "en")
    text = shot.get("text", "")
    if "words" in shot:
        units = shot["words"]
    elif lang == "zh":
        units = sum(1 for c in text if not c.isspace() and c not in "，。！？、,.!?：:；;")
    else:
        units = len(text.split())
    if units == 0:
        base = 0.0
    else:
        base = SETTLE_S + units / READ_RATE.get(lang, 3.0)
    return round(max(base, float(shot.get("min_s", 0.0))), 3)


def frame_of(k, ctx):
    return int(round((ctx["lead"] + k * ctx["beat_s"]) * ctx["fps"]))


def plan(song, spec, fps=30, lead=0.0, from_bar=1, phrase_bars=4, target_bars=None):
    meter = int(song.get("meter", 4))
    beat_s = 60.0 / float(song["bpm"])
    ctx = {"fps": fps, "lead": lead, "beat_s": beat_s}
    audio_start = float(song["offset"]) + (from_bar - 1) * meter * beat_s - lead
    notes = []
    if audio_start < 0:
        notes.append("lead is longer than the music before the first downbeat; audio starts at 0 and frames stay on the grid")
        audio_start = 0.0
    shots_in = spec["shots"]
    out, k = [], 0
    for i, s in enumerate(shots_in):
        role = s.get("role", "beat")
        weight = s.get("weight", "small")
        need_s = read_seconds(s)
        on_bar = weight == "big" or role in ("open", "section", "cta")
        if on_bar and k % meter and out:
            pad = meter - k % meter
            out[-1]["beats"] += pad
            k += pad
        unit = meter if (weight == "big" or role in ("open", "cta")) else (1 if role == "flash" else 2)
        beats = max(unit, int(math.ceil(need_s / beat_s - 1e-9)))
        beats = int(math.ceil(beats / float(unit)) * unit)
        if "beats" in s:
            beats = int(s["beats"])
        out.append({"id": s["id"], "role": role, "weight": weight, "in": s.get("in", "cut"),
                    "text": s.get("text", ""), "lang": s.get("lang", "en"),
                    "read_s": need_s, "start_beat": k, "beats": beats})
        k += beats
    total = k
    phrase = phrase_bars * meter
    if target_bars:
        want = target_bars * meter
        if total > want:
            notes.append("OVER_TARGET: shots need %d bars but target is %d; cut a shot or shorten text, never squeeze reading time" % (int(math.ceil(total / float(meter))), target_bars))
        else:
            extra = want - total
            _spread(out, extra, meter)
            total = want
    elif total % phrase:
        extra = phrase - total % phrase
        out[-1]["beats"] += extra
        total += extra
        notes.append("held the last shot %d extra beats so the film ends on a phrase boundary" % extra)
    # restack start beats after padding
    k = 0
    for s in out:
        s["start_beat"] = k
        k += s["beats"]
    # accent budget
    dur_s = lead + total * beat_s
    budget = max(1, int(round(BIG_PER_30S * dur_s / 30.0)))
    bigs = [s for s in out if s["weight"] == "big"]
    if len(bigs) > budget:
        # keep the first big hit (and the last when the budget allows); demote the middle ones first
        order = bigs[1:-1] + ([bigs[-1]] if len(bigs) > 1 else [])
        if budget >= 2:
            order = bigs[1:-1]
        for s in order:
            if len([b for b in out if b["weight"] == "big"]) <= budget:
                break
            s["weight"] = "medium"
            notes.append("downgraded %s from big to medium to stay inside %d big hits" % (s["id"], budget))
    shots, sfx = [], []
    for i, s in enumerate(out):
        a, b = s["start_beat"], s["start_beat"] + s["beats"]
        f0 = 0 if i == 0 else frame_of(a, ctx)
        f1 = frame_of(b, ctx)
        row = dict(s)
        row.update({"from": f0, "duration": f1 - f0, "pos": bar_pos(a, meter),
                    "start_s": round(f0 / float(fps), 3), "end_s": round(f1 / float(fps), 3)})
        shots.append(row)
        cut = f0
        kind = s["in"]
        if i == 0 and kind in ("whoosh", "swell"):
            kind = "cut"
        if kind == "whoosh":
            lead_f = int(round(0.18 * fps))
            sfx.append({"shot": s["id"], "kind": "whoosh", "start": max(0, cut - lead_f), "peak": cut})
        elif kind == "swell":
            sfx.append({"shot": s["id"], "kind": "swell", "start": max(0, frame_of(a - meter, ctx)), "peak": cut})
        elif kind == "impact":
            sfx.append({"shot": s["id"], "kind": "impact", "start": cut, "peak": cut})
    end_f = shots[-1]["from"] + shots[-1]["duration"]
    duck = []
    for v in spec.get("voice", []):
        duck.append({"from": max(0, int(round((v["start"] - 0.2) * fps))),
                     "to": int(round((v["end"] + 0.3) * fps)), "gain_db": -10})
    fade_from = frame_of(total - meter, ctx)
    song_left = float(song.get("duration", 1e9)) - audio_start
    return {
        "song": {k2: song.get(k2) for k2 in ("source", "bpm", "offset", "meter", "duration", "confidence")},
        "fps": fps, "lead": lead, "from_bar": from_bar, "phrase_bars": phrase_bars,
        "beat_s": round(beat_s, 6), "audio_start_s": round(audio_start, 3),
        "total_beats": total, "total_frames": end_f, "duration_s": round(end_f / float(fps), 3),
        "song_seconds_available": round(song_left, 3),
        "shots": shots, "sfx": sfx, "duck": duck,
        "music": {"fade_from": fade_from, "end": end_f},
        "voice": spec.get("voice", []), "notes": notes,
    }


def _spread(out, extra, meter):
    # give spare beats where they help most: text-heavy shots first, then the closing shot
    order = sorted(range(len(out)), key=lambda i: (-(out[i]["read_s"] / (out[i]["beats"] or 1)), -i))
    while extra > 0:
        progressed = False
        for i in order:
            step = 2 if extra >= 2 else extra
            if out[i]["weight"] == "big" or out[i]["role"] in ("open", "cta"):
                if extra < meter:
                    continue
                step = meter
            out[i]["beats"] += step
            extra -= step
            progressed = True
            if extra <= 0:
                break
        if not progressed:
            out[-1]["beats"] += extra
            extra = 0


def cmd_plan(a):
    song, spec = load(a.song), load(a.shots)
    p = plan(song, spec, fps=a.fps, lead=a.lead, from_bar=a.from_bar,
             phrase_bars=a.phrase_bars, target_bars=a.bars)
    dump(p, a.out)


# ---------------------------------------------------------------- audit
def audit(p, max_hold_bars=4):
    f = []
    fps, meter = p["fps"], int(p["song"].get("meter") or 4)
    ctx = {"fps": fps, "lead": p.get("lead", 0.0), "beat_s": p["beat_s"]}
    grid = {}
    k = 0
    last = p["total_frames"] + fps
    while True:
        fr = frame_of(k, ctx)
        if fr > last:
            break
        grid[fr] = k
        k += 1

    def near(fr):
        for d in (0, -1, 1):
            if fr + d in grid:
                return grid[fr + d]
        return None

    def add(code, where, msg):
        f.append({"code": code, "level": SEVERITY[code], "where": where, "msg": msg})

    if (p["song"].get("confidence") or 1) < 0.5:
        add("LOW_CONFIDENCE", "song", "tempo confidence %.2f; confirm with the click track or set bpm/offset by hand" % p["song"]["confidence"])
    shots = p["shots"]
    exp = 0
    bigs = []
    for i, s in enumerate(shots):
        if s["from"] != exp:
            add("GAP_OR_OVERLAP", s["id"], "starts at frame %d but previous shot ends at %d" % (s["from"], exp))
        exp = s["from"] + s["duration"]
        kk = near(s["from"]) if i else 0
        if kk is None and not s.get("offbeat"):
            add("OFF_GRID", s["id"], "cut at frame %d is more than 1 frame from any beat" % s["from"])
        dur_s = s["duration"] / float(fps)
        if s.get("read_s", 0) > dur_s + 1e-6:
            add("SHORT_READ", s["id"], "on screen %.2fs but its text needs %.2fs" % (dur_s, s["read_s"]))
        if s.get("weight") == "big":
            bigs.append((s["from"], s["id"]))
            if kk is not None and kk % meter:
                add("BIG_OFF_BAR", s["id"], "big hit lands on beat %s, not a downbeat" % bar_pos(kk, meter))
        bar_s = meter * p["beat_s"]
        if dur_s > max_hold_bars * bar_s + 1e-6:
            add("STALL", s["id"], "holds %.1f bars with one picture; add an inner move on a downbeat or split it" % (dur_s / bar_s))
        for v in p.get("voice", []):
            t = s["from"] / float(fps)
            if s.get("weight") == "big" and v["start"] - 0.25 <= t <= v["end"] + 0.25:
                add("HIT_UNDER_VOICE", s["id"], "big hit at %.2fs collides with speech %.2f-%.2fs" % (t, v["start"], v["end"]))
    dur = p["total_frames"] / float(fps)
    budget = max(1, int(round(BIG_PER_30S * dur / 30.0)))
    if len(bigs) > budget:
        add("ACCENT_FLOOD", "film", "%d big hits in %.1fs; keep %d or fewer so each still lands" % (len(bigs), dur, budget))
    two_bars = 2 * meter * p["beat_s"] * fps
    for (fa, ia), (fb, ib) in zip(bigs, bigs[1:]):
        if fb - fa < two_bars - 1:
            add("ACCENT_CROWD", ib, "big hit only %.1f bars after %s" % ((fb - fa) / (meter * p["beat_s"] * fps), ia))
    if p["total_beats"] % (p.get("phrase_bars", 4) * meter):
        add("ENDS_MID_PHRASE", "film", "ends after %d beats, not on a %d-bar phrase" % (p["total_beats"], p.get("phrase_bars", 4)))
    avail = p.get("song_seconds_available")
    if avail is not None and dur - p.get("lead", 0) > avail + 0.05:
        add("PAST_SONG", "film", "film runs %.2fs but only %.2fs of music remain after the start point" % (dur, avail))
    starts = {s["id"]: s["from"] for s in shots}
    for x in p.get("sfx", []):
        if x["shot"] in starts and abs(x["peak"] - starts[x["shot"]]) > 1:
            add("SFX_LATE", x["shot"], "%s peaks at frame %d but the cut is at %d" % (x["kind"], x["peak"], starts[x["shot"]]))
    return f


def cmd_audit(a):
    p = load(a.plan)
    f = audit(p, a.max_hold_bars)
    if a.json:
        print(json.dumps(f, ensure_ascii=False, indent=1))
    else:
        for x in f:
            print("%-5s %-16s %-14s %s" % (x["level"], x["code"], x["where"], x["msg"]))
        print("%d finding(s): %d error, %d warn" % (len(f), sum(x["level"] == "ERROR" for x in f), sum(x["level"] == "WARN" for x in f)))
    return 1 if any(x["level"] == "ERROR" for x in f) else 0


# ---------------------------------------------------------------- export
def export(p, to):
    fps = p["fps"]
    if to == "remotion":
        lines = ["// generated by cut-on-the-one; edit the plan, not this file",
                 "export const FPS = %d;" % fps,
                 "export const BEAT_S = %r;" % p["beat_s"],
                 "export const LEAD_S = %r;" % p.get("lead", 0.0),
                 "export const AUDIO_START_FRAMES = %d;" % int(round(p["audio_start_s"] * fps)),
                 "export const TOTAL_FRAMES = %d;" % p["total_frames"],
                 "export const MUSIC_FADE_FROM = %d;" % p["music"]["fade_from"],
                 "// frame of beat k counted from the first downbeat, from absolute time (no drift)",
                 "export const beatFrame = (k: number) => Math.round((LEAD_S + k * BEAT_S) * FPS);",
                 "export const SHOTS = ["]
        for s in p["shots"]:
            lines.append("  { id: %s, from: %d, durationInFrames: %d, pos: %s, weight: %s }," % (
                json.dumps(s["id"]), s["from"], s["duration"], json.dumps(s["pos"]), json.dumps(s["weight"])))
        lines.append("] as const;")
        lines.append("export const SFX = %s as const;" % json.dumps(p["sfx"]))
        lines.append("export const DUCK = %s as const;" % json.dumps(p["duck"]))
        return "\n".join(lines) + "\n"
    if to == "csv":
        rows = ["id,pos,from_frame,duration_frames,start_s,end_s,weight,in,read_s,text"]
        for s in p["shots"]:
            txt = '"%s"' % s.get("text", "").replace('"', '""')
            rows.append("%s,%s,%d,%d,%.3f,%.3f,%s,%s,%.2f,%s" % (s["id"], s["pos"], s["from"], s["duration"], s["start_s"], s["end_s"], s["weight"], s["in"], s["read_s"], txt))
        return "\n".join(rows) + "\n"
    if to == "labels":  # Audacity / most DAWs import tab-separated label tracks
        return "".join("%.3f\t%.3f\t%s %s\n" % (s["start_s"], s["end_s"], s["pos"], s["id"]) for s in p["shots"])
    if to == "srt":
        def ts(x):
            ms = int(round(x * 1000))
            return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
        out = []
        for i, s in enumerate(p["shots"], 1):
            out.append("%d\n%s --> %s\n[%s] %s %s\n" % (i, ts(s["start_s"]), ts(s["end_s"]), s["pos"], s["id"], s.get("text", "")))
        return "\n".join(out)
    raise SystemExit("unknown export target: " + to)


def cmd_export(a):
    text = export(load(a.plan), a.to)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------- click
def tone(n, sr, freq, amp):
    return [amp * math.sin(2 * math.pi * freq * i / sr) * math.exp(-i / (0.012 * sr)) for i in range(n)]


def click_track(p, sr=22050, mix=None):
    fps, meter = p["fps"], int(p["song"].get("meter") or 4)
    total = int(math.ceil(p["total_frames"] / float(fps) * sr))
    buf = [0.0] * total
    ctx = {"fps": fps, "lead": p.get("lead", 0.0), "beat_s": p["beat_s"]}

    def put(t, samples):
        i0 = int(round(t * sr))
        for j, v in enumerate(samples):
            if 0 <= i0 + j < total:
                buf[i0 + j] += v

    hi, lo = tone(int(0.05 * sr), sr, 1760, 0.5), tone(int(0.05 * sr), sr, 880, 0.3)
    cut = tone(int(0.12 * sr), sr, 330, 0.6)
    for k in range(p["total_beats"] + 1):
        put(ctx["lead"] + k * ctx["beat_s"], hi if k % meter == 0 else lo)
    for s in p["shots"][1:]:
        put(s["from"] / float(fps), cut)
    if mix is not None:
        song, ssr = mix
        if ssr != sr:
            step = ssr / float(sr)
            song = [song[int(i * step)] for i in range(int(len(song) / step))]
        start = int(round(p["audio_start_s"] * sr))
        for i in range(total):
            j = start + i
            if j < len(song):
                buf[i] = 0.6 * buf[i] + 0.7 * song[j]
    peak = max(1e-9, max(abs(x) for x in buf))
    if peak > 0.98:
        buf = [x * 0.98 / peak for x in buf]
    return buf


def cmd_click(a):
    p = load(a.plan)
    mix = None
    sr = a.rate
    if a.mix:
        mono, msr = read_wav_mono(a.mix)
        mix = (mono, msr)
    write_wav(a.out, click_track(p, sr, mix), sr)
    print("wrote %s (%.2fs). Listen: the high click marks each bar, the low click each beat, the deep tone each cut. They should sit on the drums." % (a.out, p["total_frames"] / float(p["fps"])))


# ---------------------------------------------------------------- synth (for tests and demos)
def synth_clicks(bpm, offset, seconds, sr=22050, meter=4, noise=0.0, seed=7):
    rnd = random.Random(seed)
    buf = [rnd.uniform(-noise, noise) if noise else 0.0 for _ in range(int(seconds * sr))]
    beat = 60.0 / bpm
    k = 0
    while True:
        t = offset + k * beat
        if t >= seconds:
            break
        amp = 0.9 if k % meter == 0 else 0.45
        i0 = int(t * sr)
        for j in range(int(0.03 * sr)):
            if i0 + j < len(buf):
                buf[i0 + j] += amp * math.sin(2 * math.pi * 120 * j / sr) * math.exp(-j / (0.006 * sr))
        k += 1
    return buf


# ---------------------------------------------------------------- cli
def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump(obj, path):
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    if path:
        open(path, "w", encoding="utf-8").write(text + "\n")
    else:
        print(text)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grid.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="estimate tempo, first downbeat, bar energy, strong hits")
    d.add_argument("song")
    d.add_argument("-o", "--out")
    d.add_argument("--meter", type=int, default=4)
    d.add_argument("--bpm", type=float, help="known tempo; only phase is searched")
    d.add_argument("--min-bpm", type=float, default=60)
    d.add_argument("--max-bpm", type=float, default=200)
    d.add_argument("--max-seconds", type=float, default=120)
    d.set_defaults(fn=cmd_detect)
    pl = sub.add_parser("plan", help="snap a shot list to the beat grid")
    pl.add_argument("song")
    pl.add_argument("shots")
    pl.add_argument("-o", "--out")
    pl.add_argument("--fps", type=int, default=30)
    pl.add_argument("--lead", type=float, default=0.0, help="seconds of picture before the first downbeat")
    pl.add_argument("--from-bar", type=int, default=1, help="start the film at this bar of the song")
    pl.add_argument("--phrase-bars", type=int, default=4)
    pl.add_argument("--bars", type=int, help="fit the film to exactly this many bars")
    pl.set_defaults(fn=cmd_plan)
    au = sub.add_parser("audit", help="check a plan (also after hand edits)")
    au.add_argument("plan")
    au.add_argument("--json", action="store_true")
    au.add_argument("--max-hold-bars", type=float, default=4)
    au.set_defaults(fn=cmd_audit)
    ex = sub.add_parser("export", help="write timeline for an engine or editor")
    ex.add_argument("plan")
    ex.add_argument("--to", choices=["remotion", "csv", "labels", "srt"], default="remotion")
    ex.add_argument("-o", "--out")
    ex.set_defaults(fn=cmd_export)
    cl = sub.add_parser("click", help="render an audition WAV: beat clicks + cut tones (+ song)")
    cl.add_argument("plan")
    cl.add_argument("out")
    cl.add_argument("--mix", help="16-bit WAV of the song to lay under the clicks")
    cl.add_argument("--rate", type=int, default=22050)
    cl.set_defaults(fn=cmd_click)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
