# cut-on-the-one（卡在第一拍）

[English](README.md) | **中文**

一个让代码渲染视频严格卡在音乐上的智能体技能。每个剪辑点都落在拍上，每行标题都停留到读得完，大重音省着用，音效在剪辑点达到峰值，片子在乐句结尾收，而不是停在小节中间。

适用于 Claude Code、Codex、Cursor 以及任何会读取 `SKILL.md` 的智能体。辅助脚本是纯 Python 3，没有任何依赖。

## 为什么需要它

用代码写视频（Remotion、HyperFrames、Motion Canvas、ffmpeg）时，时长通常写成帧数再一路累加。配乐剪辑就是这样出问题的：

- 时长累加加上四舍五入，剪辑点慢慢跑出拍子；
- 一句话的镜头只给了一拍，因为“这样更有冲击力”；
- 每个转场都砸一下，真正的高潮反而没力气了；
- 嗖声从剪辑点才开始，听起来总是慢；
- 音乐停在乐句中间；
- 最重要的重音正好砸在一句旁白上。

cut-on-the-one 把工作顺序摆正：先建节拍网格，再往上放画面，渲染前用机器审查和节拍音轨各检查一遍。

## 功能

- **速度与强拍检测**：从 WAV 读出 BPM、第一个强拍、可信度、每小节能量、最可能的能量抬升小节，以及带“小节.拍”位置的最强击点。
- **网格规划**：按阅读时间（英文单词或中文字数）以音乐单位给镜头定长，大动作和新段落放在强拍，在乐句结尾收或填满指定小节数；每一帧都由绝对时间算出，不会漂移。
- **重音预算**：每 30 秒约三次大重音，两小节内不重复；超出的自动降级并留下说明。
- **音效摆放**：嗖声提前起、在剪辑点到峰值；上升音效提前一小节开始；撞击声放在剪辑帧。
- **人声压低**列表，以及跨最后一小节的音乐淡出。
- **审查**：12 种问题代码（跑拍、读不完、空隙、音效迟到、超出音乐长度、弱拍重音、重音过多、重音撞人声、长时间不动、停在乐句中间、速度不可靠），有错误时退出码非零，可以放进 CI。
- **导出**：Remotion `timeline.ts`、CSV、Audacity/音频软件标签轨、SRT 标记。
- **节拍音轨**：可把歌曲混在下面，直接听网格对不对。

## 安装

把仓库克隆到智能体的技能目录：

```bash
# Claude Code
git clone https://github.com/fangdididi/cut-on-the-one ~/.claude/skills/cut-on-the-one

# Codex
git clone https://github.com/fangdididi/cut-on-the-one ~/.codex/skills/cut-on-the-one

# Cursor（项目规则 / 技能目录）
git clone https://github.com/fangdididi/cut-on-the-one .cursor/skills/cut-on-the-one
```

其他智能体：让它读取 `SKILL.zh-CN.md`（中文）或 `SKILL.md`（英文）。

环境要求：Python 3.8+。`ffmpeg` 可选，只用来把 MP3/AAC 转成 WAV。

## 使用

直接跟智能体说：

- “把这个 Remotion 宣传片卡到 `music/launch.mp3` 上，logo 要落在音乐爆发的地方。”
- “我们换了配乐，把每个场景按新歌重新卡一遍。”
- “审查一下 `plan.json`，结尾感觉被切断了。”

也可以自己运行脚本：

```bash
ffmpeg -i song.mp3 -ac 1 -ar 22050 -sample_fmt s16 song.wav
python3 scripts/grid.py detect song.wav -o song.json
python3 scripts/grid.py plan song.json shots.json -o plan.json
python3 scripts/grid.py click plan.json check.wav --mix song.wav   # 用耳朵听
python3 scripts/grid.py audit plan.json
python3 scripts/grid.py export plan.json --to remotion -o src/timeline.ts
```

## 示例

`examples/shots.json` 描述了一个发布说明工具的九个镜头，配一首 122 BPM 的曲子。规划结果是 12 小节（23.6 秒）：logo 落在第 3 小节，上升音效提前一小节开始；两个功能镜头带嗖声进入，嗖声提前 5 帧；一串两拍闪切引出第 7 小节的关键数字；行动号召一直撑到第三个乐句结束。审查结果为零问题。

在 Remotion 里使用导出文件：

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

## 文件结构

```text
cut-on-the-one/
├── SKILL.md              # 技能说明（英文）
├── SKILL.zh-CN.md        # 技能说明（中文）
├── README.md
├── README.zh-CN.md
├── LICENSE
├── scripts/
│   └── grid.py           # detect / plan / audit / export / click
├── examples/
│   ├── song.json         # 歌曲卡：速度、第一个强拍、段落
│   ├── shots.json        # 镜头清单：文字、角色、权重、进入方式
│   ├── plan.json         # 规划好的时间线（帧）
│   └── timeline.ts       # Remotion 导出
└── tests/
    └── test_grid.py      # 70 个测试：python3 -m unittest discover -s tests
```

## 许可证

MIT © fangdididi
