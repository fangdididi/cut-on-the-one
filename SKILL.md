---
name: cut-on-the-one
description: Lock a code-rendered video (Remotion, HyperFrames, Motion Canvas, an ffmpeg slideshow, or any frame-based timeline) to its music. Use when a promo, launch film, reel or explainer has a soundtrack and the cuts must land on the beat: find the tempo and the first downbeat, give every shot enough reading time, snap cuts to bars and phrases, ration the big hits, pre-roll whooshes so they peak on the cut, duck music under voice, end on a phrase, and audit the timeline before rendering.
---

# cut-on-the-one

## What this is

A timing discipline for videos that are drawn by code. Engines like Remotion or HyperFrames make it easy to place any element on any frame, which is exactly why music-driven edits drift: a cut written as "previous end + 45 frames" slowly slides off the beat, a headline that needs two seconds gets one beat, every transition gets a slam so none of them feels big, the music stops in the middle of a phrase, and a whoosh that *starts* on the cut sounds late.

This skill turns the soundtrack into a grid first and only then places pictures on it. The grid is the contract: every cut is computed from absolute time (`round((lead + beat × beat_seconds) × fps)`), never by adding durations, so a 90-second film at 123 BPM and 24 fps is still on the beat at the last frame.

The helper `scripts/grid.py` (Python 3, standard library only) does the arithmetic: it estimates tempo and downbeat from a WAV, plans a shot list onto the grid, audits a plan (including one you edited by hand), exports frames for your engine, and renders a click track you can listen to.

## When to use it

- A product promo, launch video, social reel, trailer or kinetic-type piece with a music bed.
- An existing code-rendered video "feels off" against its music.
- You are swapping the music under a finished timeline and need every cut moved.
- You want sound effects that land on the cuts instead of near them.

Skip it for talk-led videos where the voice sets the pace (cut on sentences there; this skill only helps with the music bed and ducking) and for footage edits where the picture has its own rhythm you must keep.

## The craft rules

1. **Grid before pictures.** Know BPM, meter, and where bar 1 beat 1 is before writing a single `<Sequence>`.
2. **Reading time beats rhythm.** A shot with text stays up at least `0.4 s + words ÷ 3` (English) or `0.4 s + characters ÷ 6` (Chinese). If the music forces a shorter shot, cut words, not reading time.
3. **Cut lengths in musical units.** Ordinary shots last 2, 4, 6 or 8 beats. Openers, big moments and the call to action last whole bars. Single-beat flashes come in short runs, never alone in a calm passage.
4. **Big moments start on a downbeat; sections start on a bar; the story turns on a phrase** (usually every 4 or 8 bars, often where the energy lifts).
5. **Ration the slams.** At most about three big hits per 30 seconds, never two inside two bars. Everything else is a plain cut or a small move inside the shot on a beat.
6. **Sound peaks on the cut.** A whoosh starts ~0.18 s early so its loudest point is the cut frame. A riser starts one bar before the cut it resolves into. An impact sits on the cut frame.
7. **Voice wins.** Duck music about 10 dB under speech with short ramps, and never put a big hit under a spoken word.
8. **End on a phrase.** Hold the last shot until the phrase finishes, and fade the music across the final bar instead of chopping it.
9. **Long holds need inner motion.** If one picture stays more than four bars, give it a move on a downbeat or split it.
10. **Listen before rendering.** A click track laid over the song tells you in ten seconds whether the grid is right.

## Workflow

### 1. Build the song card

If you have the audio as WAV (convert others: `ffmpeg -i song.mp3 -ac 1 -ar 22050 -sample_fmt s16 song.wav`):

```bash
python3 scripts/grid.py detect song.wav -o song.json
```

You get `bpm`, `offset` (seconds to the first downbeat), `confidence`, `downbeat_confidence`, per-bar `bar_energy`, a `likely_lift_bar` (biggest energy rise between 4-bar blocks), and up to eight strong `hits` with their bar.beat position. If the tempo is known from the track's metadata, pass `--bpm 122` so only the phase is searched.

No audio yet? Write `song.json` by hand with `bpm`, `offset`, `meter`, `duration`.

Then add `sections` yourself (intro, build, lift, outro, with starting bar and length). The detector does not name sections; it only points at the likely lift.

### 2. Confirm the grid by ear

Make an empty plan (one shot is enough), then render the audition track:

```bash
python3 scripts/grid.py click plan.json check.wav --mix song.wav
```

High click on each bar, low click on each beat, deep tone on each cut. If the clicks flam against the drums, fix `offset`; if they drift apart over time, fix `bpm`; if the high click sits on beat 3, move `offset` by two beats. Treat `confidence` under 0.5 as "set it by hand".

### 3. Write the shot list

`shots.json` lists shots in order. Per shot:

| field | meaning |
|---|---|
| `id` | name you will use in code |
| `text` | every word on screen (drives reading time); `lang`: `en` or `zh` |
| `role` | `open`, `section`, `cta`, `flash`, or plain `beat` (default) |
| `weight` | `big`, `medium`, `small` (default) |
| `in` | how it arrives: `cut`, `whoosh`, `swell`, `impact` |
| `min_s` / `beats` | optional floor in seconds, or a fixed length in beats |

Add `"voice": [{"start": s, "end": s}, ...]` if there is narration.

### 4. Plan onto the grid

```bash
python3 scripts/grid.py plan song.json shots.json -o plan.json            # end on the next phrase
python3 scripts/grid.py plan song.json shots.json --bars 16 -o plan.json  # exactly 16 bars
python3 scripts/grid.py plan song.json shots.json --from-bar 9 --lead 0.5 -o plan.json
```

The planner sizes each shot to its reading time in musical units, pushes big and section shots onto downbeats, ends on a phrase (or fills the exact bar count, giving spare beats to the text-heavy shots first), demotes extra big hits, places sound effects so they peak on the cut, builds the ducking list, and marks where the music fade begins. `--from-bar` starts the film later in the song; `--lead` adds picture before the first downbeat. Read `notes` in the output: `OVER_TARGET` means your shots do not fit and you must drop or shorten something.

### 5. Audit

```bash
python3 scripts/grid.py audit plan.json
```

Run it again after every hand edit. Exit code 1 means an error.

| code | level | what it means |
|---|---|---|
| `OFF_GRID` | error | a cut is more than one frame from any beat (mark deliberate off-beat cuts `"offbeat": true`) |
| `SHORT_READ` | error | a shot is shorter than its text needs |
| `GAP_OR_OVERLAP` | error | shots do not butt together |
| `SFX_LATE` | error | a sound effect's peak is not on its cut |
| `PAST_SONG` | error | the film runs longer than the music left after the start point |
| `BIG_OFF_BAR` | warn | a big hit lands on a weak beat |
| `ACCENT_FLOOD` / `ACCENT_CROWD` | warn | too many big hits, or two too close |
| `HIT_UNDER_VOICE` | warn | a big hit collides with speech |
| `STALL` | warn | one picture holds more than four bars |
| `ENDS_MID_PHRASE` | warn | the film stops inside a phrase |
| `LOW_CONFIDENCE` | warn | the tempo estimate is shaky |

### 6. Export to your engine

```bash
python3 scripts/grid.py export plan.json --to remotion -o src/timeline.ts
python3 scripts/grid.py export plan.json --to csv     -o cuts.csv
python3 scripts/grid.py export plan.json --to labels  -o cuts.txt   # Audacity / DAW label track
python3 scripts/grid.py export plan.json --to srt     -o cuts.srt   # markers you can see in any player
```

In Remotion, map `SHOTS` to `<Sequence from={s.from} durationInFrames={s.durationInFrames}>`, start the music with `<Audio startFrom={AUDIO_START_FRAMES}>`, fade its volume from `MUSIC_FADE_FROM` to `TOTAL_FRAMES`, and use `beatFrame(k)` for any motion inside a shot that should hit a beat. In HyperFrames or Motion Canvas use the same numbers as seconds (`start_s`, `end_s` in the CSV).

### 7. Check the render

Render, then listen once with your eyes on the cuts. If anything feels late, it is usually a transition animation whose *visual* peak is a few frames after the cut: shorten its ease-in or start it earlier so the strongest frame is the cut frame.

## Example

Song: 122 BPM, first downbeat at 0.42 s, 4/4. Nine shots for a release-notes tool, no narration.

```text
cold-open  1.1  frames   0-59   open, whole bar
pain       2.1  frames  59-118  "Release notes nobody reads" needs 1.73 s, gets 4 beats
logo       3.1  frames 118-177  big, riser starts a bar early and resolves on the cut
feature-1  4.1  frames 177-266  whoosh starts 5 frames before the cut
feature-2  5.3  frames 266-325
flash-a/b  6.3  one beat each
metric     7.1  big + impact, 4 bars after the logo hit
cta        9.1  held 8 extra beats so the film ends with the third phrase
```

Twelve bars, 23.6 s, two big hits, zero audit findings. Full files are in `examples/`.

## Tips

- Pick the music before you storyboard. Cutting a finished picture to a new song always costs more than the reverse.
- Use `likely_lift_bar` and `hits` to decide where the logo or the key number lands. Put the hero moment on the lift, not on bar 1.
- 24 fps grids round more than 30 or 60 fps grids. Prefer 30 or 60 for fast tempos.
- Keep a no-music render from the same timeline; people often re-score social cuts.
- Small beat-synced moves inside a shot (a counter ticking, a card nudging) feel better than more cuts.
- If the client changes the music, keep `shots.json`, re-run `detect` and `plan`, and re-export. Nothing in your scene code needs to change if it reads frames from `timeline.ts`.
