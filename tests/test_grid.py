import copy
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import grid  # noqa: E402

SONG = {"bpm": 120.0, "offset": 0.5, "meter": 4, "duration": 60.0, "confidence": 0.9}


def analyse(buf, sr=22050, **kw):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.wav")
        grid.write_wav(p, buf, sr)
        mono, rate = grid.read_wav_mono(p)
    onset, env, power = grid.onset_envelope(mono, rate)
    return grid.estimate_grid(onset, power=power, **kw), env, onset


def drum_loop(bpm, offset, seconds, sr=22050):
    """kick on 1 and 3, snare on 2 and 4, quiet hats on eighths"""
    import random
    rnd = random.Random(3)
    buf = [rnd.uniform(-0.01, 0.01) for _ in range(int(seconds * sr))]
    beat = 60.0 / bpm
    k = 0
    while offset + k * beat / 2 < seconds:
        t = offset + k * beat / 2
        i0 = int(t * sr)
        on_beat = k % 2 == 0
        beat_idx = k // 2
        if on_beat and beat_idx % 2 == 0:
            amp, f, dec = (0.9 if beat_idx % 4 == 0 else 0.7), 60, 0.02
        elif on_beat:
            amp, f, dec = 0.5, 200, 0.012
        else:
            amp, f, dec = 0.12, 3000, 0.004
        for j in range(int(0.06 * sr)):
            if i0 + j < len(buf):
                buf[i0 + j] += amp * math.sin(2 * math.pi * f * j / sr) * math.exp(-j / (dec * sr))
        k += 1
    return buf


def shots(*items):
    return {"shots": [dict(id="s%d" % i, **it) for i, it in enumerate(items)]}


class Detect(unittest.TestCase):
    def check(self, bpm, offset, sr=22050, tol_bpm=0.3, tol_off=0.02):
        g, _, _ = analyse(grid.synth_clicks(bpm, offset, 20, sr=sr, noise=0.02), sr)
        self.assertAlmostEqual(g["bpm"], bpm, delta=tol_bpm)
        self.assertAlmostEqual(g["offset"], offset, delta=tol_off)
        return g

    def test_120(self):
        self.check(120, 0.5)

    def test_95_not_doubled(self):
        self.check(95, 0.31)

    def test_140(self):
        self.check(140, 1.2)

    def test_slow_78_offset_zero(self):
        self.check(78, 0.0)

    def test_fast_174_not_halved(self):
        self.check(174, 0.4)

    def test_44100_rate(self):
        self.check(128, 0.77, sr=44100)

    def test_16000_rate(self):
        self.check(100, 0.05, sr=16000)

    def test_confidence_high_on_clean_clicks(self):
        g = self.check(120, 0.5)
        self.assertGreater(g["confidence"], 0.8)

    def test_downbeat_is_accented_beat(self):
        # offset 0.5 + one beat of pickup that is not accented: downbeat must stay at the loud click
        g = self.check(110, 0.6)
        self.assertGreater(g["downbeat_confidence"], 0.3)

    def test_drum_loop_tempo(self):
        g, _, _ = analyse(drum_loop(124, 0.25, 20))
        self.assertAlmostEqual(g["bpm"], 124, delta=0.5)

    def test_drum_loop_offset(self):
        g, _, _ = analyse(drum_loop(124, 0.25, 20))
        self.assertAlmostEqual(g["offset"], 0.25, delta=0.03)

    def test_known_bpm_only_searches_phase(self):
        g, _, _ = analyse(grid.synth_clicks(90, 0.33, 15, noise=0.02), bpm_min=90, bpm_max=90)
        self.assertAlmostEqual(g["bpm"], 90, delta=0.2)
        self.assertAlmostEqual(g["offset"], 0.33, delta=0.02)

    def test_noise_gives_low_confidence(self):
        import random
        rnd = random.Random(1)
        g, _, _ = analyse([rnd.uniform(-0.3, 0.3) for _ in range(22050 * 10)])
        self.assertLess(g["confidence"], 0.6)

    def test_bar_energy_and_lift(self):
        quiet = grid.synth_clicks(120, 0.0, 8, noise=0.0)
        loud = [3 * x for x in grid.synth_clicks(120, 0.0, 8, noise=0.0)]
        loud = [max(-1, min(1, x)) for x in loud]
        g, env, _ = analyse(quiet + loud)
        e = grid.bar_energy(env, g, 4, 16.0)
        self.assertEqual(len(e), 8)
        self.assertEqual(grid.suggest_drop(e), 5)

    def test_hits_are_bar_spaced(self):
        g, _, onset = analyse(grid.synth_clicks(120, 0.5, 12, noise=0.01))
        hits = grid.strong_hits(onset, g, 4)
        ts = [h["t"] for h in hits]
        self.assertTrue(all(b - a >= 2.0 - 1e-6 for a, b in zip(ts, ts[1:])))

    def test_cli_detect_writes_song_json(self):
        with tempfile.TemporaryDirectory() as d:
            wav, out = os.path.join(d, "a.wav"), os.path.join(d, "song.json")
            grid.write_wav(wav, grid.synth_clicks(120, 0.5, 10, noise=0.02), 22050)
            grid.main(["detect", wav, "-o", out])
            song = json.load(open(out))
            for key in ("bpm", "offset", "bar_energy", "hits", "confidence", "duration"):
                self.assertIn(key, song)

    def test_rejects_24bit(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.wav")
            with wave.open(p, "wb") as w:
                w.setnchannels(1); w.setsampwidth(3); w.setframerate(8000); w.writeframes(b"\0" * 300)
            with self.assertRaises(SystemExit):
                grid.read_wav_mono(p)


class ReadTime(unittest.TestCase):
    def test_empty_text_is_zero(self):
        self.assertEqual(grid.read_seconds({"text": ""}), 0.0)

    def test_english_words(self):
        self.assertAlmostEqual(grid.read_seconds({"text": "one two three four five six"}), 0.4 + 2.0)

    def test_chinese_characters_skip_punctuation(self):
        self.assertAlmostEqual(grid.read_seconds({"text": "一键生成，发布说明", "lang": "zh"}), 0.4 + 8 / 6.0, places=3)

    def test_min_s_wins(self):
        self.assertEqual(grid.read_seconds({"text": "hi", "min_s": 3}), 3.0)

    def test_explicit_words(self):
        self.assertAlmostEqual(grid.read_seconds({"words": 3}), 1.4)


class Plan(unittest.TestCase):
    def test_every_cut_on_grid(self):
        p = grid.plan(SONG, shots({"text": "a b c d e f g"}, {"text": "x"}, {"role": "flash"}, {"text": "y z"}))
        ctx = {"fps": 30, "lead": 0.0, "beat_s": 0.5}
        for s in p["shots"][1:]:
            self.assertEqual(s["from"], grid.frame_of(s["start_beat"], ctx))

    def test_no_drift_at_awkward_tempo(self):
        song = dict(SONG, bpm=123.0)
        sp = shots(*[{"text": "w"} for _ in range(40)])
        p = grid.plan(song, sp, fps=24)
        last = p["shots"][-1]
        exact = last["start_beat"] * 60.0 / 123.0 * 24
        self.assertLessEqual(abs(last["from"] - exact), 0.5)

    def test_contiguous(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b"}, {"role": "flash"}))
        end = 0
        for s in p["shots"]:
            self.assertEqual(s["from"], end)
            end += s["duration"]
        self.assertEqual(end, p["total_frames"])

    def test_reading_time_respected(self):
        long = " ".join(["word"] * 12)
        p = grid.plan(SONG, shots({"text": long}))
        s = p["shots"][0]
        self.assertGreaterEqual(s["duration"] / 30.0, s["read_s"])

    def test_big_starts_on_downbeat(self):
        p = grid.plan(SONG, shots({"role": "flash"}, {"weight": "big", "text": "Boom"}))
        self.assertEqual(p["shots"][1]["start_beat"] % 4, 0)

    def test_section_starts_on_downbeat(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"role": "flash"}, {"role": "section"}))
        self.assertTrue(p["shots"][2]["pos"].endswith(".1"))

    def test_ends_on_phrase(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b"}))
        self.assertEqual(p["total_beats"] % 16, 0)

    def test_phrase_bars_option(self):
        p = grid.plan(SONG, shots({"text": "a"}), phrase_bars=2)
        self.assertEqual(p["total_beats"], 8)

    def test_target_bars_fills_exactly(self):
        p = grid.plan(SONG, shots({"text": "a b c"}, {"text": "d"}, {"role": "cta", "text": "go"}), target_bars=6)
        self.assertEqual(p["total_beats"], 24)
        self.assertEqual(sum(s["beats"] for s in p["shots"]), 24)

    def test_target_too_small_is_reported_not_squeezed(self):
        sp = shots(*[{"text": " ".join(["w"] * 8)} for _ in range(6)])
        p = grid.plan(SONG, sp, target_bars=2)
        self.assertTrue(any(n.startswith("OVER_TARGET") for n in p["notes"]))
        for s in p["shots"]:
            self.assertGreaterEqual(s["duration"] / 30.0 + 1e-6, s["read_s"])

    def test_flash_can_be_one_beat(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"role": "flash"}, {"text": "b"}))
        self.assertEqual(p["shots"][1]["beats"], 1)

    def test_explicit_beats(self):
        p = grid.plan(SONG, shots({"text": "a", "beats": 6}, {"text": "b"}))
        self.assertEqual(p["shots"][0]["beats"], 6)

    def test_big_budget_downgrades_middle(self):
        sp = shots(*[{"weight": "big", "text": "x"} for _ in range(6)])
        p = grid.plan(SONG, sp)
        bigs = [s for s in p["shots"] if s["weight"] == "big"]
        budget = max(1, round(3 * p["duration_s"] / 30.0))
        self.assertLessEqual(len(bigs), budget)
        self.assertEqual(p["shots"][0]["weight"], "big")
        self.assertEqual(p["shots"][-1]["weight"], "big")

    def test_budget_of_one_keeps_only_first(self):
        sp = shots(*[{"weight": "big", "text": "x"} for _ in range(3)])
        p = grid.plan(SONG, sp, phrase_bars=1)
        self.assertEqual([s["weight"] for s in p["shots"]], ["big", "medium", "medium"])

    def test_whoosh_peaks_on_cut(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b", "in": "whoosh"}))
        x = p["sfx"][0]
        self.assertEqual(x["peak"], p["shots"][1]["from"])
        self.assertEqual(x["peak"] - x["start"], round(0.18 * 30))

    def test_swell_starts_one_bar_early(self):
        p = grid.plan(SONG, shots({"role": "open"}, {"role": "section", "in": "swell"}))
        x = p["sfx"][0]
        self.assertEqual(x["peak"] - x["start"], 60)

    def test_first_shot_has_no_preroll_sfx(self):
        p = grid.plan(SONG, shots({"text": "a", "in": "whoosh"}))
        self.assertEqual(p["sfx"], [])

    def test_lead_shifts_frames(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b"}), lead=0.5)
        self.assertEqual(p["shots"][1]["from"], int(round((0.5 + p["shots"][1]["start_beat"] * 0.5) * 30)))
        self.assertAlmostEqual(p["audio_start_s"], 0.0)

    def test_from_bar_moves_audio_start(self):
        p = grid.plan(SONG, shots({"text": "a"}), from_bar=9)
        self.assertAlmostEqual(p["audio_start_s"], 0.5 + 8 * 2.0)

    def test_lead_longer_than_pickup_notes(self):
        p = grid.plan(SONG, shots({"text": "a"}), lead=2.0)
        self.assertEqual(p["audio_start_s"], 0.0)
        self.assertTrue(p["notes"])

    def test_voice_ducking(self):
        sp = shots({"text": "a"})
        sp["voice"] = [{"start": 1.0, "end": 2.0}]
        p = grid.plan(SONG, sp)
        self.assertEqual(p["duck"], [{"from": 24, "to": 69, "gain_db": -10}])

    def test_fade_starts_on_last_bar(self):
        p = grid.plan(SONG, shots({"text": "a"}))
        self.assertEqual(p["music"]["end"] - p["music"]["fade_from"], 60)

    def test_example_plan_audits_clean(self):
        with open(os.path.join(ROOT, "examples", "song.json")) as a, open(os.path.join(ROOT, "examples", "shots.json")) as b:
            song, sp = json.load(a), json.load(b)
        p = grid.plan(song, sp)
        self.assertFalse([f for f in grid.audit(p) if f["level"] == "ERROR"])


class Audit(unittest.TestCase):
    def base(self):
        return grid.plan(SONG, shots({"role": "open"}, {"text": "a b c"}, {"weight": "big", "text": "x"}, {"text": "d"}))

    def codes(self, p):
        return {f["code"] for f in grid.audit(p)}

    def test_clean(self):
        self.assertEqual(self.codes(self.base()), set())

    def test_off_grid(self):
        p = self.base()
        p["shots"][1]["duration"] += 7
        p["shots"][2]["from"] += 7
        p["shots"][2]["duration"] -= 7
        self.assertIn("OFF_GRID", self.codes(p))

    def test_one_frame_tolerance(self):
        p = self.base()
        p["shots"][1]["duration"] += 1
        p["shots"][2]["from"] += 1
        p["shots"][2]["duration"] -= 1
        self.assertNotIn("OFF_GRID", self.codes(p))

    def test_offbeat_flag_allows(self):
        p = self.base()
        p["shots"][1]["duration"] += 7
        p["shots"][2]["from"] += 7
        p["shots"][2]["duration"] -= 7
        p["shots"][2]["offbeat"] = True
        self.assertNotIn("OFF_GRID", self.codes(p))

    def test_short_read(self):
        p = self.base()
        p["shots"][1]["read_s"] = 99
        self.assertIn("SHORT_READ", self.codes(p))

    def test_gap(self):
        p = self.base()
        p["shots"][2]["from"] += 30
        self.assertIn("GAP_OR_OVERLAP", self.codes(p))

    def test_big_off_bar(self):
        p = self.base()
        s1, s2 = p["shots"][1], p["shots"][2]
        s1["duration"] -= 15
        s2["from"] -= 15
        s2["duration"] += 15
        self.assertIn("BIG_OFF_BAR", self.codes(p))

    def test_accent_flood_and_crowd(self):
        p = self.base()
        for s in p["shots"]:
            s["weight"] = "big"
        c = self.codes(p)
        self.assertIn("ACCENT_FLOOD", c)
        self.assertIn("ACCENT_CROWD", c)

    def test_ends_mid_phrase(self):
        p = self.base()
        p["total_beats"] -= 2
        self.assertIn("ENDS_MID_PHRASE", self.codes(p))

    def test_hit_under_voice(self):
        p = self.base()
        t = p["shots"][2]["from"] / 30.0
        p["voice"] = [{"start": t - 0.5, "end": t + 0.5}]
        self.assertIn("HIT_UNDER_VOICE", self.codes(p))

    def test_stall(self):
        p = self.base()
        last = p["shots"][-1]
        last["duration"] += 400
        p["total_frames"] += 400
        self.assertIn("STALL", self.codes(p))

    def test_low_confidence(self):
        p = self.base()
        p["song"]["confidence"] = 0.3
        self.assertIn("LOW_CONFIDENCE", self.codes(p))

    def test_past_song(self):
        p = grid.plan(dict(SONG, duration=5.0), shots({"text": "a"}, {"text": "b"}))
        self.assertIn("PAST_SONG", self.codes(p))

    def test_sfx_late(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b", "in": "whoosh"}))
        p["sfx"][0]["peak"] += 4
        self.assertIn("SFX_LATE", self.codes(p))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, "g.json"), os.path.join(d, "b.json")
            p = self.base()
            json.dump(p, open(good, "w"))
            q = copy.deepcopy(p)
            q["shots"][1]["read_s"] = 99
            json.dump(q, open(bad, "w"))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(grid.main(["audit", good]), 0)
                self.assertEqual(grid.main(["audit", bad, "--json"]), 1)


class Export(unittest.TestCase):
    def setUp(self):
        self.p = grid.plan(SONG, shots({"text": "a b"}, {"text": "c, \"quoted\"", "in": "whoosh"}))

    def test_remotion(self):
        ts = grid.export(self.p, "remotion")
        self.assertIn("export const SHOTS", ts)
        self.assertIn("durationInFrames", ts)
        self.assertIn("beatFrame", ts)

    def test_csv_quotes(self):
        rows = grid.export(self.p, "csv").strip().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn('""quoted""', rows[2])

    def test_labels(self):
        lines = grid.export(self.p, "labels").strip().splitlines()
        self.assertEqual(len(lines[0].split("\t")), 3)

    def test_srt(self):
        self.assertIn("00:00:00,000 -->", grid.export(self.p, "srt"))

    def test_unknown(self):
        with self.assertRaises(SystemExit):
            grid.export(self.p, "nope")


class Click(unittest.TestCase):
    def test_length_and_peak(self):
        p = grid.plan(SONG, shots({"text": "a"}, {"text": "b"}))
        buf = grid.click_track(p, 8000)
        self.assertEqual(len(buf), int(math.ceil(p["total_frames"] / 30.0 * 8000)))
        self.assertLessEqual(max(abs(x) for x in buf), 0.99)

    def test_clicks_land_on_beats(self):
        p = grid.plan(SONG, shots({"text": "a"}))
        buf = grid.click_track(p, 8000)
        i = int(round(0.5 * 8000))
        self.assertGreater(max(abs(x) for x in buf[i:i + 40]), 0.1)
        j = int(round(0.25 * 8000))
        self.assertLess(max(abs(x) for x in buf[j:j + 40]), 0.05)

    def test_detect_reads_back_click_track(self):
        p = grid.plan(dict(SONG, offset=0.0), shots(*[{"text": "a"} for _ in range(6)]))
        g, _, _ = analyse(grid.click_track(p, 22050))
        self.assertAlmostEqual(g["bpm"], 120, delta=0.3)

    def test_cli_mix(self):
        with tempfile.TemporaryDirectory() as d:
            song_wav, plan_json, out = (os.path.join(d, n) for n in ("s.wav", "p.json", "o.wav"))
            grid.write_wav(song_wav, grid.synth_clicks(120, 0.5, 12), 16000)
            json.dump(grid.plan(SONG, shots({"text": "a"})), open(plan_json, "w"))
            with redirect_stdout(io.StringIO()):
                grid.main(["click", plan_json, out, "--mix", song_wav])
            self.assertTrue(os.path.getsize(out) > 1000)


class Cli(unittest.TestCase):
    def test_end_to_end_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            plan_json = os.path.join(d, "plan.json")
            script = os.path.join(ROOT, "scripts", "grid.py")
            ex = os.path.join(ROOT, "examples")
            subprocess.check_call([sys.executable, script, "plan", os.path.join(ex, "song.json"), os.path.join(ex, "shots.json"), "-o", plan_json])
            r = subprocess.run([sys.executable, script, "audit", plan_json], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            r = subprocess.run([sys.executable, script, "export", plan_json, "--to", "csv"], capture_output=True, text=True)
            self.assertIn("cold-open", r.stdout)


if __name__ == "__main__":
    unittest.main()
