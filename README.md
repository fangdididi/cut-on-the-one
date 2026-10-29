# cut-on-the-one

**English** | [中文](README.zh-CN.md)

An agent skill that locks code-rendered videos to their music. Every cut lands on the beat, every headline stays up long enough to read, big hits are rationed, sound effects peak on the cut, and the film ends on a phrase instead of mid-bar.

Works with Claude Code, Codex, Cursor and any agent that reads `SKILL.md`. The helper script is plain Python 3 with no dependencies.

## Why

When a video is written in code (Remotion, HyperFrames, Motion Canvas, ffmpeg), timings are usually typed as frame counts and added up. That is how music edits go wrong:

- cuts drift off the beat because durations are accumulated and rounded,
- a shot with a sentence on it gets one beat because it "felt punchy",
- every transition slams, so the real climax has nothing left,
- whooshes start on the cut and sound late,
- the music stops in the middle of a phrase,
- the hero hit lands right on top of a spoken word.

cut-on-the-one fixes the order of work: build the beat grid first, place pictures on it second, and check the result with a machine audit and a click track before you render.

## Features

- **Tempo and downbeat detection** from a WAV file: BPM, first downbeat, confidence scores, energy per bar, the likely lift bar, and the strongest hits with bar.beat positions.
- **Grid planner**: sizes each shot by reading time (English words or Chinese characters) in musical units, puts big moments and new sections on downbeats, ends on a phrase or fills an exact bar count, and computes every frame from absolute time so nothing drifts.
- **Accent budget**: about three big hits per 30 seconds, never two within two bars; extras are demoted with a note.
- **Sound placement**: whooshes pre-rolled to peak on the cut, risers that start one bar early, impacts on the cut frame.
- **Voice ducking** list and a music fade across the final bar.
- **Audit** with 12 finding codes (off-grid cut, too short to read, gap, late SFX, past the song, weak-beat hit, too many hits, hit under voice, stall, mid-phrase ending, low confidence) and a non-zero exit code for errors, so it can run in CI.
- **Exports** to a Remotion `timeline.ts`, CSV, an Audacity/DAW label track, and SRT markers.
- **Click track** renderer that can mix the song underneath, so you can hear whether the grid is right.

## Install

Clone the repo into your agent's skills folder:

```bash
# Claude Code
git clone https://github.com/fangdididi/cut-on-the-one ~/.claude/skills/cut-on-the-one

# Codex
git clone https://github.com/fangdididi/cut-on-the-one ~/.codex/skills/cut-on-the-one

# Cursor (project rules / skills folder)
git clone https://github.com/fangdididi/cut-on-the-one .cursor/skills/cut-on-the-one
```

Any other agent: point it at `SKILL.md` (or `SKILL.zh-CN.md` for Chinese).

Requirements: Python 3.8+. `ffmpeg` is optional, only to convert MP3/AAC to WAV.

## Usage

Talk to your agent normally:

- "Cut this Remotion promo to `music/launch.mp3` and make sure the logo lands on the drop."
- "We swapped the music. Re-time every scene to the new track."
- "Audit `plan.json`; the ending feels chopped."

Or run the helper yourself:

```bash
ffmpeg -i song.mp3 -ac 1 -ar 22050 -sample_fmt s16 song.wav
python3 scripts/grid.py detect song.wav -o song.json
python3 scripts/grid.py plan song.json shots.json -o plan.json
python3 scripts/grid.py click plan.json check.wav --mix song.wav   # listen
python3 scripts/grid.py audit plan.json
python3 scripts/grid.py export plan.json --to remotion -o src/timeline.ts
```

## Example

`examples/shots.json` describes nine shots for a release-notes tool on a 122 BPM track. The planner returns twelve bars (23.6 s): the logo lands on bar 3 with a riser that starts a bar earlier, two features arrive with whooshes that start five frames early, a two-beat flash run leads into the key number on bar 7, and the call to action holds until the third phrase ends. The audit reports zero findings.

Using the export in Remotion:

```tsx
import {Audio, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {SHOTS, AUDIO_START_FRAMES, MUSIC_FADE_FROM, TOTAL_FRAMES} from './timeline';

export const Film = () => {
  const frame = useCurrentFrame();
  const volume = interpolate(frame, [MUSIC_FADE_FROM, TOTAL_FRAMES], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <>
      <Audio src={staticFile('launch.wav')} startFrom={AUDIO_START_FRAMES} volume={volume} />
      {SHOTS.map((s) => (
        <Sequence key={s.id} from={s.from} durationInFrames={s.durationInFrames}>
          <Scene id={s.id} />
        </Sequence>
      ))}
    </>
  );
};
```

## File structure

```text
cut-on-the-one/
├── SKILL.md              # the skill (English)
├── SKILL.zh-CN.md        # the skill (Chinese)
├── README.md
├── README.zh-CN.md
├── LICENSE
├── scripts/
│   └── grid.py           # detect / plan / audit / export / click
├── examples/
│   ├── song.json         # song card: tempo, first downbeat, sections
│   ├── shots.json        # shot list with text, role, weight, entrance
│   ├── plan.json         # planned timeline (frames)
│   └── timeline.ts       # Remotion export
└── tests/
    └── test_grid.py      # 70 tests: python3 -m unittest discover -s tests
```

## License

MIT © fangdididi
