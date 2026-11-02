# 打谱版（Score Edition）

把 Transkun 的**演奏事件流**降维成一份可读、可弹的钢琴缩编谱；演奏版 MIDI
单独保留，不参与任何改写。

## 目标与非目标

**目标**

- 输出可编辑的 MusicXML（主交付），以及可打印的 PDF / 预览 SVG。
- 保持演奏 MIDI 原样；打谱版另存 `<name>.score.*`。
- 规则可解释、参数可覆盖、低置信度可回退。
- 离线可用；不依赖云端服务。

**非目标**

- 不是逐音精确回放；要精确听原演奏请用 `<name>.mid`。
- 不做真实 tempo map、rubato、强弱/指法/踏板线的完整还原。
- 不训练新模型；AI 只作为可选分析后端（见下）。

## 流程

```
演奏事件（秒）+ 可选音频
  → 分析：BPM / 调性 / 拍号（auto 或用户指定）
  → 量化：每个音符只选 16 分或三连音网格（不混用起音/音尾）
  → 左右手：按起音组搜索可演奏的切分缝隙
  → 和弦化：同格起音合并、滚奏加琶音记号
  → 节奏整理：连续三连音聚成 3:2；孤立三连音回退 16 分
  → 可演奏性：跨度/音数清理（保留旋律与低音）
  → music21：MusicXML + score MIDI
  → Verovio：SVG；cairosvg + pypdf：PDF
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `mode` | `block` | `block` 缩编；`faithful` 不删内声部 |
| `tuplet_mode` | `auto` | `auto` 把连续三连音聚成 `3:2`；`none` 纯 16 分、不生成 tuplet |
| `key` | `auto` | music21 Krumhansl 估计；可指定 `D` / `Bm` / `Bb` |
| `time_signature` | `auto` | 自动暂时保守用 4/4；可指定 3/4、6/8 等 |
| `bpm` | 自动 | 全局网格误差最小的 BPM；可指定 |
| `split` | 60 | 左右手分界偏置，不是硬切 |
| `max_span` | 19 | 单手和弦最大跨度（半音） |
| `max_notes` | 5 | 和弦最多音数 |
| `arpeggio_ms` | 70 | 原始起音跨度达到该值则加琶音记号 |
| `pdf` | 开 | 渲染依赖缺失时保留 MusicXML/SVG |

## 三连音规则

0.3.0 曾把起音和音尾独立吸附到 1/4 与 1/3 网格，相减会得到 `1/12`、`5/12`
等混合时值，music21 只能写成 `12:11`、`6:5` 这类非法 tuplet，甚至把
`start`/`stop` 落在同一个音上。0.3.1 起保证合法；0.3.2 起按起音间隔
聚组，混合织体也能保留更多三连音：

- **每个音符只选一种网格**：16 分（1/4）或三连音（1/6），不再混用。
- 连续、起音间隔正确的 3 个三连音才聚成一组，统一挂 `Tuplet(3, 2)`；
  组内和弦时值统一到三连音单位（短音补到下一个起音），不再要求每个和弦
  时值完全相同。
- 组起点必须在 16 分网格上，组总时值必须是二进制时值，且**不跨小节**。
- 孤立或不足 3 个的三连音回退到标准 16 分时值；不会写单音符 tuplet。
- 左右手切分可能把一组三连音拆到两只手；被拆开的音同样回退到 16 分，
  不会写成跨声部的 tuplet。
- 非 tuplet 时值只允许 `1/16`、`1/8`、`3/16`、`1/4`、`3/8`、`1/2`、
  `3/4`、`1`、`1.5`、`2`、`3`、`4`、`6`、`8` 拍。
- `--tuplet-mode none` 直接关闭三连音，适合对 tuplet 敏感的 DAW。

如果三连音没有被识别，通常是 BPM 估计落在了等价的二进制速度上（例如
95 BPM 被估成 142 BPM）。用 `--bpm 95` 指定实际速度即可恢复三连音。

## 速度与播放

- MusicXML 视觉上仍用文本 `M.M. 95`（避免 Verovio/cairosvg 的 SMuFL
  节拍器字形缺字），同时在同一个 `<direction>` 里写
  `<sound tempo="95"/>`，MuseScore/DAW 会按 95 BPM 播放。
- score MIDI 写入 `set_tempo = 60e6 / bpm`，不再回落默认 120 BPM。
- 回归测试会检查：MusicXML 有 `<sound tempo>`、score MIDI 的
  `set_tempo` 匹配 BPM、64 拍测试曲的 score MIDI 长度与输入误差 < 2%。

## 三种模式（规划）

- `block`（已实现）：每手一个声部，和弦块 + 琶音记号；最适合“可弹的缩编谱”。
- `faithful`（已实现第一版）：不删内声部，保留全部音符；仍按和弦块记谱。
- `lead`（规划）：旋律 + 低音 + 和弦骨架，内声部进一步简化。

## AI 的定位

- **默认不用 AI**：分析/量化/记谱全部是确定性代码，可复现、离线。
- **可选 AI 顾问**：云端 LLM 只输出结构化参数（拍号/调性/左右手提示/难度），
  由确定性引擎执行；不直接生成 MusicXML。
- **可选本地模型**：beat/downbeat、声部分离；需先做许可证、权重、离线评测。
- 云端上传只发摘要/局部片段，不发整首音频；用户可关闭。

## 代码落点

```
transkun_app/score/
  model.py       ScoreOptions / ScoreResult
  analysis.py    MIDI/事件解析、调性、BPM、量化
  layout.py      左右手、和弦、可演奏性
  rhythm.py      时值归一化、三连音 3:2 聚组
  notation.py    music21 → MusicXML / score MIDI（含悬空 beam 清理）
  validate.py    MusicXML 三连音校验（测试 / 自检）
  render.py      Verovio SVG → PDF（cairosvg + pypdf）
  pipeline.py    编排
  cli.py         transkun-score
  selftest.py    合成音符自检
```

## 入口

```bash
# 转谱 + 打谱
transkun-app song.mp3 -o song.mid --score
transkun-app song.mp3 --score --score-time-signature 3/4 --score-key D

# 只对已有 MIDI 生成
transkun-score song.mid -o song.score --pdf
transkun-score song.mid -o song.score --tuplet-mode none   # 纯 16 分
transkun-score song.mid -o song.score --bpm 95             # 指定速度以保留三连音
transkun-score --selftest
```

WebUI：上传页勾选“生成打谱版”，任务完成后下载 MusicXML / PDF / score MIDI。
GUI：勾选“转谱后生成打谱版”，完成后打开 MusicXML / PDF。

## 依赖与打包

- `music21`（BSD-3）、`verovio`（LGPL-3）、`cairosvg`（LGPL-3）、
  `pypdf`（BSD-3-Clause）。
- PDF 需要 `libcairo2`（Debian）/ `cairo`（Fedora）；AppImage 目标机没有
  cairo 时只保留 MusicXML/SVG。
- 中文标题：随包 `NotoSansCJKsc-Regular.otf`（OFL-1.1），Verovio SVG 用该
  字体渲染；MusicXML 里的标题始终是完整 UTF-8。

## 测试

- 单元：三连音量化、跨 C4 的左右手、宽和弦、琶音、MusicXML round-trip。
- 三连音 XML：按 `(staff, voice, number)` 检查组大小必须为 3、比例只能是
  `3:2`、组不跨小节、组跨度与 `actual-notes:normal-notes` 匹配。
- 随机混合网格：120 个随机音符必须能写出 MusicXML，不能出现无法记谱的时值。
- 混合织体：同一起音上不同时值的和弦仍应聚成三连音，组内时值统一。
- 速度：MusicXML `<sound tempo>` 与 score MIDI `set_tempo` 必须匹配 BPM，
  score MIDI 长度与输入误差 < 2%。
- 端到端：合成事件 → MusicXML/PDF/score MIDI（`--selftest`）。
- CI：pytest、`transkun-app --score-selftest`、runtime/AppImage/macOS 冒烟。

## 路线

- **0.3.0**：规则版，MusicXML + PDF，WebUI/GUI 接入。
- **0.3.1**：修复非法 tuplet（单音符组、`12:11`/`6:5`、跨小节），
  增加 `--tuplet-mode auto|none`，并清理悬空 beam。
- **0.3.2**（当前）：修复 tempo 只写文本导致 score MIDI/MuseScore 按
  120 BPM 播放；三连音改为按起音间隔聚组，混合织体保留更多三连音。
- **0.3.x**：自动拍号/下拍改进、琶音跨网格合并、faithful 多声部。
- **0.4**：可选 beat/downbeat 小模型；云端 LLM 作为“参数顾问”的 A/B 实验。
