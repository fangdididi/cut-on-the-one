---
name: cut-on-the-one
description: 让用代码渲染的视频（Remotion、HyperFrames、Motion Canvas、ffmpeg 幻灯片或任何按帧计时的时间线）严格卡在音乐上。适用于带配乐的宣传片、发布片、短视频和讲解片：找出速度和第一个强拍，保证每个镜头的阅读时间，把剪辑点对齐到小节和乐句，限制重音次数，让嗖声提前起、在剪辑点达到峰值，人声出现时压低音乐，在乐句结尾收尾，并在渲染前审查整条时间线。
---

# cut-on-the-one（卡在第一拍）

## 这是什么

一套给“用代码画出来的视频”用的卡点纪律。Remotion、HyperFrames 这类引擎可以把任何元素放到任何一帧，正因为太自由，配乐视频才容易跑拍：写成“上一段结束 + 45 帧”的剪辑点会一点点滑离节拍；需要两秒才读得完的标题只给了一拍；每个转场都砸一下，结果哪个都不显得重；音乐在乐句中间戛然而止；嗖声从剪辑点才开始响，听起来总是慢半拍。

这个技能先把配乐变成一张网格，再把画面放上去。网格就是契约：每个剪辑点都由绝对时间算出（`round((lead + 拍序号 × 每拍秒数) × fps)`），从不靠累加时长，所以一支 90 秒、123 BPM、24 fps 的片子到最后一帧依然在拍上。

辅助脚本 `scripts/grid.py`（Python 3，只用标准库）负责算数：从 WAV 估计速度和强拍，把镜头清单排到网格上，审查方案（包括你手改过的），为你的引擎导出帧号，并生成一条可以直接听的节拍校验音轨。

## 什么时候用

- 有背景音乐的产品宣传片、发布视频、社交短片、预告片或动态文字片。
- 一支已经做好的代码渲染视频，和音乐“对不上”。
- 要给成片换音乐，所有剪辑点都得跟着挪。
- 希望音效落在剪辑点上，而不是落在附近。

不适合：以人声为主、由说话节奏决定剪辑的视频（那种按句子剪，本技能只帮你处理背景乐和压低）；以及画面本身自带节奏、必须保留的实拍素材剪辑。

## 卡点规则

1. **先有网格，再放画面。** 写第一个 `<Sequence>` 之前，先确定 BPM、拍号，以及第 1 小节第 1 拍在哪。
2. **阅读时间优先于节奏。** 有字的镜头至少停留 `0.4 秒 + 英文单词数 ÷ 3`，或 `0.4 秒 + 中文字数 ÷ 6`。音乐逼着镜头变短时，删字，不删阅读时间。
3. **镜头长度用音乐单位。** 普通镜头 2、4、6、8 拍；开场、高潮和行动号召用整小节；单拍闪切要成串出现，别在安静段落里孤零零来一下。
4. **大动作落在强拍，段落从小节开始，故事在乐句处转折**（通常每 4 或 8 小节，常常就是能量抬升的地方）。
5. **重音要省着用。** 每 30 秒最多约三次大重音，两小节之内不能有两次。其余用普通硬切，或在镜头内部随拍做小动作。
6. **声音在剪辑点达到峰值。** 嗖声提前约 0.18 秒开始，让最响的那一刻正好是剪辑帧；上升音效提前一小节开始，在它要引出的剪辑点结束；撞击声直接放在剪辑帧。
7. **人声优先。** 说话时音乐压低约 10 dB，淡入淡出要短；不要在说话时砸大重音。
8. **在乐句结尾收。** 最后一个镜头撑到乐句结束，音乐在最后一小节里淡出，而不是一刀切断。
9. **长镜头要有内部运动。** 同一画面超过四小节，就在某个强拍上给它一个动作，或拆成两个镜头。
10. **渲染前先听。** 把节拍音轨叠在歌上听十秒，网格对不对一听便知。

## 工作流程

### 1. 做歌曲卡

如果有 WAV 音频（其他格式先转：`ffmpeg -i song.mp3 -ac 1 -ar 22050 -sample_fmt s16 song.wav`）：

```bash
python3 scripts/grid.py detect song.wav -o song.json
```

会得到 `bpm`、`offset`（到第一个强拍的秒数）、`confidence`（速度可信度）、`downbeat_confidence`（强拍可信度）、每小节的 `bar_energy`、`likely_lift_bar`（相邻 4 小节块之间能量上升最大的位置），以及最多八个带“小节.拍”位置的强击点 `hits`。如果曲子元数据里已知速度，加 `--bpm 122`，只搜索相位。

还没有音频？手写 `song.json`，填 `bpm`、`offset`、`meter`、`duration` 即可。

然后自己补上 `sections`（前奏、铺垫、抬升、尾声，含起始小节和长度）。检测器不会给段落命名，只会指出最可能的抬升点。

### 2. 用耳朵确认网格

先做一个空方案（一个镜头就够），再生成试听音轨：

```bash
python3 scripts/grid.py click plan.json check.wav --mix song.wav
```

高音咔嗒是每小节，低音咔嗒是每拍，低沉的长音是每个剪辑点。如果咔嗒和鼓点有“双响”，调 `offset`；如果越往后越分开，调 `bpm`；如果高音咔嗒落在第 3 拍，把 `offset` 挪两拍。`confidence` 低于 0.5 时，直接手动设置。

### 3. 写镜头清单

`shots.json` 按顺序列出镜头，每个镜头：

| 字段 | 含义 |
|---|---|
| `id` | 在代码里使用的名字 |
| `text` | 屏幕上出现的全部文字（决定阅读时间）；`lang`：`en` 或 `zh` |
| `role` | `open` 开场、`section` 段落、`cta` 行动号召、`flash` 闪切，或默认的 `beat` |
| `weight` | `big`、`medium`、`small`（默认） |
| `in` | 进入方式：`cut` 硬切、`whoosh` 嗖声、`swell` 上升音效、`impact` 撞击 |
| `min_s` / `beats` | 可选：最短秒数，或固定拍数 |

有旁白时加上 `"voice": [{"start": 秒, "end": 秒}, ...]`。

### 4. 排到网格上

```bash
python3 scripts/grid.py plan song.json shots.json -o plan.json            # 在下一个乐句结尾收
python3 scripts/grid.py plan song.json shots.json --bars 16 -o plan.json  # 恰好 16 小节
python3 scripts/grid.py plan song.json shots.json --from-bar 9 --lead 0.5 -o plan.json
```

规划器会按阅读时间、以音乐单位给每个镜头定长，把大重音和段落镜头推到强拍上，在乐句结尾收尾（或填满指定小节数，多出来的拍优先给字多的镜头），把超额的大重音降级，摆好音效让它们在剪辑点达到峰值，生成压低列表，并标出音乐淡出的起点。`--from-bar` 让片子从歌曲后面的某个小节开始；`--lead` 在第一个强拍前加一段画面。务必看输出里的 `notes`：出现 `OVER_TARGET` 表示镜头塞不下，必须删掉或缩短一些。

### 5. 审查

```bash
python3 scripts/grid.py audit plan.json
```

每次手动修改后都再跑一遍。退出码为 1 表示有错误。

| 代码 | 级别 | 含义 |
|---|---|---|
| `OFF_GRID` | 错误 | 剪辑点离最近的拍超过一帧（有意的反拍剪辑请标 `"offbeat": true`） |
| `SHORT_READ` | 错误 | 镜头时长不够读完上面的字 |
| `GAP_OR_OVERLAP` | 错误 | 镜头之间有空隙或重叠 |
| `SFX_LATE` | 错误 | 音效峰值不在它的剪辑点上 |
| `PAST_SONG` | 错误 | 片长超过起点之后剩下的音乐 |
| `BIG_OFF_BAR` | 警告 | 大重音落在弱拍 |
| `ACCENT_FLOOD` / `ACCENT_CROWD` | 警告 | 大重音太多，或两个挨得太近 |
| `HIT_UNDER_VOICE` | 警告 | 大重音撞上人声 |
| `STALL` | 警告 | 同一画面停留超过四小节 |
| `ENDS_MID_PHRASE` | 警告 | 片子停在乐句中间 |
| `LOW_CONFIDENCE` | 警告 | 速度估计不可靠 |

### 6. 导出到你的引擎

```bash
python3 scripts/grid.py export plan.json --to remotion -o src/timeline.ts
python3 scripts/grid.py export plan.json --to csv     -o cuts.csv
python3 scripts/grid.py export plan.json --to labels  -o cuts.txt   # Audacity / 音频软件标签轨
python3 scripts/grid.py export plan.json --to srt     -o cuts.srt   # 任何播放器都能看到的标记
```

在 Remotion 里，把 `SHOTS` 映射成 `<Sequence from={s.from} durationInFrames={s.durationInFrames}>`，用 `<Audio startFrom={AUDIO_START_FRAMES}>` 起音乐，从 `MUSIC_FADE_FROM` 到 `TOTAL_FRAMES` 淡出音量，镜头内部需要打在拍上的动作用 `beatFrame(k)`。HyperFrames 或 Motion Canvas 里直接用 CSV 里的秒数（`start_s`、`end_s`）。

### 7. 检查成片

渲染后，眼睛盯着剪辑点完整听一遍。觉得哪里慢，多半是转场动画的“视觉峰值”比剪辑点晚了几帧：缩短它的缓入，或者让它更早开始，使最有冲击力的那一帧正好是剪辑帧。

## 示例

歌曲：122 BPM，第一个强拍在 0.42 秒，4/4 拍。一个发布说明工具的九个镜头，无旁白。

```text
cold-open  1.1  帧   0-59   开场，整小节
pain       2.1  帧  59-118  "Release notes nobody reads" 需要 1.73 秒，给 4 拍
logo       3.1  帧 118-177  大重音，上升音效提前一小节开始，在剪辑点结束
feature-1  4.1  帧 177-266  嗖声比剪辑点早 5 帧开始
feature-2  5.3  帧 266-325
flash-a/b  6.3  各一拍
metric     7.1  大重音 + 撞击，距 logo 重音 4 小节
cta        9.1  多撑 8 拍，让片子在第三个乐句结尾收
```

共 12 小节、23.6 秒、两次大重音，审查零问题。完整文件见 `examples/`。

## 小贴士

- 先选音乐，再画分镜。把做好的画面硬套到新歌上，永远比反过来更费劲。
- 用 `likely_lift_bar` 和 `hits` 决定 logo 或关键数字落在哪。高光时刻放在能量抬升处，而不是第 1 小节。
- 24 fps 的网格舍入误差比 30 或 60 fps 大，快歌优先用 30 或 60。
- 从同一条时间线再渲染一版无音乐的，社交平台常常要重新配乐。
- 镜头内部随拍的小动作（数字跳动、卡片轻推）往往比多剪几刀更好看。
- 客户换了音乐：保留 `shots.json`，重新跑 `detect` 和 `plan` 并导出。只要场景代码从 `timeline.ts` 读帧号，场景本身一行都不用改。
