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
  → 量化：16 分 + 三连音网格（music21 兼容）
  → 左右手：按起音组搜索可演奏的切分缝隙
  → 和弦化：同格起音合并、滚奏加琶音记号
  → 可演奏性：跨度/音数清理（保留旋律与低音）
  → music21：MusicXML + score MIDI
  → Verovio：SVG；cairosvg + pypdf：PDF
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `mode` | `block` | `block` 缩编；`faithful` 不删内声部 |
| `key` | `auto` | music21 Krumhansl 估计；可指定 `D` / `Bm` / `Bb` |
| `time_signature` | `auto` | 自动暂时保守用 4/4；可指定 3/4、6/8 等 |
| `bpm` | 自动 | 全局网格误差最小的 BPM；可指定 |
| `split` | 60 | 左右手分界偏置，不是硬切 |
| `max_span` | 19 | 单手和弦最大跨度（半音） |
| `max_notes` | 5 | 和弦最多音数 |
| `arpeggio_ms` | 70 | 原始起音跨度达到该值则加琶音记号 |
| `pdf` | 开 | 渲染依赖缺失时保留 MusicXML/SVG |

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
  notation.py    music21 → MusicXML / score MIDI
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
transkun-score --selftest
```

WebUI：上传页勾选“生成打谱版”，任务完成后下载 MusicXML / PDF / score MIDI。
GUI：勾选“转谱后生成打谱版”，完成后打开 MusicXML / PDF。

## 依赖与打包

- `music21`（BSD-3）、`verovio`（LGPL-3）、`cairosvg`（LGPL-3）、
  `pypdf`（MPL-2.0）。
- PDF 需要 `libcairo2`（Debian）/ `cairo`（Fedora）；AppImage 目标机没有
  cairo 时只保留 MusicXML/SVG。
- 中文标题：随包 `NotoSansCJKsc-Regular.otf`（OFL-1.1），Verovio SVG 用该
  字体渲染；MusicXML 里的标题始终是完整 UTF-8。

## 测试

- 单元：三连音量化、跨 C4 的左右手、宽和弦、琶音、MusicXML round-trip。
- 端到端：合成事件 → MusicXML/PDF/score MIDI（`--selftest`）。
- CI：pytest、`transkun-app --score-selftest`、runtime/AppImage/macOS 冒烟。

## 路线

- **0.3.0**：规则版（当前实现），MusicXML + PDF，WebUI/GUI 接入。
- **0.3.x**：自动拍号/下拍改进、琶音跨网格合并、faithful 多声部。
- **0.4**：可选 beat/downbeat 小模型；云端 LLM 作为“参数顾问”的 A/B 实验。
