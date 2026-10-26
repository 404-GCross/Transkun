#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Adapted from the MT3 Transcriber project (Apache-2.0), 404-GCross.

"""把转谱结果做一遍规则化整理（后处理）。

清理模型输出里常见的三类毛刺，并把被打碎的重复起音接回去：

1. 删弱音：力度 ``<= velocity`` 的音符视为杂音整对删除；
2. 删极短音：真实音长（按下到抬起）短于 ``min_ms`` 的音符删除；
3. 连续消除：同一通道同一音高，与上一次起音的间隔 ``< repeat_ms``
   且力度 ``< 2 * velocity`` 的重复起音合并（并把前一个音的结束延长到
   后一个音的结束，避免断音）；
4. 琶音整合：同一通道内，起始时间落在同一组（与组内首个起音相距
   ``<= chord_ms``）的音符吸附到该组的首个起音上。

阈值一律用毫秒，内部按文件自身的 tempo map 换算成"秒"再比较，因此
不假设任何 ppq（Transkun 输出是 960，人类钢琴谱常见 384，也能直接跑）。

与 xRoye/MIDIRearrange（思路来源）的差异，也是本实现的修正之处：

* 逐音轨、逐通道处理：原版只读取 ``tracks[1]``，多乐器输出会漏掉其它轨；
* 保留 ``program_change`` / ``control_change`` / 弯音 / 轨名等非音符事件：
  原版会把它们全部丢掉，于是所有音轨都变成默认钢琴音色；
* 抬起的 ``note_off`` 使用音符自己的通道：原版硬编码为通道 0，鼓轨或
  非 0 通道的音符会挂住不放；
* 默认跳过打击乐通道（零基 channel 9）；
* 音长按真实起止计算：原版把 ``note_off`` 的 delta 当音长，中间夹了其它
  事件时判断会失准；
* 报告每一步删改了多少音符，便于判断阈值是否合适。

算法是有损的（会删音符、会把琶音拍平，成串的重复起音可能被合成一个长音），
所以调用方应当写到另一个文件，不要覆盖原始转谱结果。

命令行用法::

    python gui/midi_rearrange.py 转谱.mid [输出.mid] \
        [--velocity 30] [--min-ms 30] [--chord-ms 60] [--repeat-ms 120] [--drums]
    python gui/midi_rearrange.py --selftest   # 合成 MIDI 行为自检
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import mido


DEFAULTS = {
    'velocity': 30,   # 力度阈值：<= 该值的音符删除（0-127）
    'min_ms': 30,     # 最小时值：短于该值的音符删除
    'chord_ms': 60,   # 琶音整合窗口
    'repeat_ms': 120,  # 连续消除窗口
    'drums': False,   # 是否也整理打击乐通道
}

# 打击乐在 MIDI 里固定是通道 10（零基索引 9）。
DRUM_CHANNEL = 9


@dataclasses.dataclass
class Result:
  """整理结果统计。"""

  path: Path
  tracks: int
  notes_in: int
  notes_out: int
  dropped_velocity: int = 0
  dropped_short: int = 0
  merged_repeat: int = 0
  merged_chord: int = 0
  drums_kept: int = 0

  def summary(self) -> str:
    parts = [f'音符 {self.notes_in} → {self.notes_out}']
    if self.dropped_velocity:
      parts.append(f'删弱音 {self.dropped_velocity}')
    if self.dropped_short:
      parts.append(f'删极短音 {self.dropped_short}')
    if self.merged_repeat:
      parts.append(f'合并重复起音 {self.merged_repeat}')
    if self.merged_chord:
      parts.append(f'整合同刻音 {self.merged_chord}')
    if self.drums_kept:
      parts.append(f'鼓音符保留 {self.drums_kept}')
    return '，'.join(parts)

  def detail(self) -> str:
    return (f'音符 {self.notes_in} → {self.notes_out}｜删弱音 '
            f'{self.dropped_velocity}｜删极短音 {self.dropped_short}｜'
            f'合并重复 {self.merged_repeat}｜整合同刻 {self.merged_chord}｜'
            f'鼓保留 {self.drums_kept}')


@dataclasses.dataclass
class Note:
  """一个音符（绝对 tick）。``end`` 为 None 表示没有配到 note_off。"""

  channel: int
  pitch: int
  velocity: int
  start: int
  end: int | None


class _TempoMap:
  """把绝对 tick 换算成秒，用于让毫秒阈值与 tempo 无关。"""

  def __init__(self, mid: mido.MidiFile):
    self.ppq = mid.ticks_per_beat or 480
    changes = []
    for track in mid.tracks:
      tick = 0
      for msg in track:
        tick += msg.time
        if msg.type == 'set_tempo':
          changes.append((tick, msg.tempo))
    changes.sort(key=lambda item: item[0])

    merged = []
    for tick, tempo in changes:
      if merged and merged[-1][0] == tick:
        merged[-1] = (tick, tempo)  # 同一 tick 只认最后一次
      else:
        merged.append((tick, tempo))
    if not merged or merged[0][0] != 0:
      merged.insert(0, (0, 500000))  # mido 默认 120 BPM

    # points: (起点的绝对 tick, 该 tick 处的秒数, 从这里开始的 tempo)
    self.points = []
    seconds = 0.0
    previous = 0
    tempo = merged[0][1]
    for tick, new_tempo in merged:
      if tick > previous:
        seconds += mido.tick2second(tick - previous, self.ppq, tempo)
      previous = tick
      tempo = new_tempo
      self.points.append((tick, seconds, tempo))

  def seconds(self, tick: int) -> float:
    index = 0
    for i, (point_tick, _, _) in enumerate(self.points):
      if point_tick <= tick:
        index = i
      else:
        break
    point_tick, seconds, tempo = self.points[index]
    if tick <= point_tick:
      return seconds
    return seconds + mido.tick2second(tick - point_tick, self.ppq, tempo)


def _absolute_events(track: mido.MidiTrack):
  """把 delta 时间展开成 (绝对 tick, 消息)。"""
  tick = 0
  for msg in track:
    tick += msg.time
    yield tick, msg


def _split_notes(events):
  """把事件流拆成音符（已配对）与非音符事件。"""
  pending: dict[tuple[int, int], list[Note]] = {}
  notes: list[Note] = []
  others: list[tuple[int, mido.Message]] = []

  for tick, msg in events:
    if msg.type == 'note_on' and msg.velocity > 0:
      pending.setdefault((msg.channel, msg.note), []).append(
          Note(msg.channel, msg.note, msg.velocity, tick, None))
    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
      queue = pending.get((msg.channel, msg.note))
      if queue:
        # 同音高重叠时按先按下先抬起配对（FIFO）。
        note = queue.pop(0)
        note.end = tick
        notes.append(note)
      else:
        others.append((tick, msg))  # 孤立的 note_off，原样保留
    elif msg.type == 'end_of_track':
      continue  # mido 会在写出时补
    else:
      others.append((tick, msg))

  for queue in pending.values():
    notes.extend(queue)  # 没配到 note_off 的，保持"只有按下"
  return notes, others


def _collapse_group(group, stats):
  """同一时刻的同音高重复起音合并成一个，取最长的结束时间与最大力度。"""
  if not group:
    return []
  best: dict[int, Note] = {}
  for note in group:
    existing = best.get(note.pitch)
    if existing is None:
      best[note.pitch] = note
      continue
    stats['merged_chord'] += 1
    if note.end is not None and (existing.end is None or note.end > existing.end):
      existing.end = note.end
    if note.velocity > existing.velocity:
      existing.velocity = note.velocity
  return list(best.values())


def _clean_notes(notes, *, tempo_map, velocity, min_seconds, chord_seconds,
                 repeat_seconds, stats):
  """按 力度 → 极短 → 连续消除 → 琶音整合 的顺序整理音符。"""
  seconds = tempo_map.seconds

  # 1) 力度 2) 真实音长
  kept = []
  for note in notes:
    if note.end is None:  # 没有抬起的音符不参与清理，原样保留
      kept.append(note)
      continue
    if note.velocity <= velocity:
      stats['dropped_velocity'] += 1
      continue
    if seconds(note.end) - seconds(note.start) < min_seconds:
      stats['dropped_short'] += 1
      continue
    kept.append(note)

  # 3) 连续消除：间隔从"上一次起音"算起（含被合并掉的那些），成串地合并。
  groups: dict[tuple[int, int], list[Note]] = {}
  for note in kept:
    groups.setdefault((note.channel, note.pitch), []).append(note)

  merged: list[Note] = []
  for items in groups.values():
    items.sort(key=lambda item: (item.start, item.end or item.start))
    previous: Note | None = None
    last_start: int | None = None
    for note in items:
      if (previous is not None and note.end is not None
          and previous.end is not None and last_start is not None
          and seconds(note.start) - seconds(last_start) < repeat_seconds
          and note.velocity < 2 * velocity):
        previous.end = max(previous.end, note.end)
        last_start = note.start
        stats['merged_repeat'] += 1
        continue
      merged.append(note)
      previous = note
      last_start = note.start

  # 4) 琶音整合：只在同一通道内吸附，绝不跨乐器。
  by_channel: dict[int, list[Note]] = {}
  for note in merged:
    by_channel.setdefault(note.channel, []).append(note)

  final: list[Note] = []
  for items in by_channel.values():
    items.sort(key=lambda item: (item.start, item.pitch))
    group: list[Note] = []
    group_start: Note | None = None
    for note in items:
      if (group_start is None
          or seconds(note.start) - seconds(group_start.start) > chord_seconds):
        final.extend(_collapse_group(group, stats))
        group = []
        group_start = note
      else:
        if note.start != group_start.start:
          note.start = group_start.start
          stats['merged_chord'] += 1
      group.append(note)
    final.extend(_collapse_group(group, stats))

  # 吸附后可能变成零长度（甚至负的），至少留一个 tick。
  for note in final:
    if note.end is not None and note.end <= note.start:
      note.end = note.start + 1
  return final


def _rebuild_track(others, notes) -> mido.MidiTrack:
  """按绝对 tick 重组一条轨：非音符事件原样，音符重新生成。"""
  items = []
  for tick, msg in others:
    items.append((tick, 0, msg.copy(time=0)))
  for note in notes:
    items.append((note.start, 2, mido.Message(
        'note_on', channel=note.channel, note=note.pitch,
        velocity=note.velocity, time=0)))
    if note.end is not None:
      items.append((note.end, 1, mido.Message(
          'note_off', channel=note.channel, note=note.pitch,
          velocity=0, time=0)))
  # 同一 tick 上先放控制器/元事件，再放 note_off，最后 note_on。
  items.sort(key=lambda item: (item[0], item[1]))

  track = mido.MidiTrack()
  previous = 0
  for tick, _, msg in items:
    msg.time = tick - previous
    previous = tick
    track.append(msg)
  return track


def rearrange_midi(mid, *, velocity=DEFAULTS['velocity'],
                   min_ms=DEFAULTS['min_ms'], chord_ms=DEFAULTS['chord_ms'],
                   repeat_ms=DEFAULTS['repeat_ms'], drums=DEFAULTS['drums']):
  """整理一个 mido.MidiFile，返回 (新的 MidiFile, 统计字典)。"""
  tempo_map = _TempoMap(mid)
  stats = {'notes_in': 0, 'notes_out': 0, 'dropped_velocity': 0,
           'dropped_short': 0, 'merged_repeat': 0, 'merged_chord': 0,
           'drums_kept': 0}

  out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
  for track in mid.tracks:
    notes, others = _split_notes(_absolute_events(track))
    if not notes:
      out.tracks.append(_rebuild_track(others, []))
      continue

    processable = [n for n in notes if drums or n.channel != DRUM_CHANNEL]
    skipped = [n for n in notes if not drums and n.channel == DRUM_CHANNEL]
    stats['drums_kept'] += len(skipped)

    cleaned = _clean_notes(
        processable, tempo_map=tempo_map, velocity=velocity,
        min_seconds=min_ms / 1000.0, chord_seconds=chord_ms / 1000.0,
        repeat_seconds=repeat_ms / 1000.0, stats=stats)
    stats['notes_in'] += len(processable)
    stats['notes_out'] += len(cleaned)

    out.tracks.append(_rebuild_track(others, cleaned + skipped))
  return out, stats


def default_output_path(src) -> Path:
  """``song.mid`` -> ``song.rearranged.mid``；重复整理时覆盖同一份。"""
  src = Path(src)
  stem = src.stem
  if stem.endswith('.rearranged'):
    stem = stem[: -len('.rearranged')]
  return src.with_name(stem + '.rearranged.mid')


def rearrange_file(src, dst=None, **kwargs) -> Result:
  """整理 ``src`` 并写出 MIDI（默认另存 ``*.rearranged.mid``）。"""
  src = Path(src)
  if not src.exists():
    raise FileNotFoundError(f'找不到 MIDI 文件: {src}')
  mid = mido.MidiFile(str(src))
  out, stats = rearrange_midi(mid, **kwargs)
  dst = Path(dst) if dst else default_output_path(src)
  dst.parent.mkdir(parents=True, exist_ok=True)
  out.save(str(dst))
  return Result(path=dst, tracks=len(out.tracks), **stats)


def selftest() -> int:
  """合成 MIDI 的行为自检：不需要 Transkun 音频与模型。"""
  import tempfile

  ppq, bpm = 220, 120

  def ticks(ms):
    return round(ms * ppq * bpm / 60000)

  def build():
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage('track_name', name='conductor', time=0))
    conductor.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    mid.tracks.append(conductor)

    lead = mido.MidiTrack()
    lead.append(mido.MetaMessage('track_name', name='lead', time=0))
    lead.append(mido.Message('program_change', channel=0, program=24, time=0))
    events = [
        ('on', 60, 20, 0), ('off', 60, 0, 500),      # 弱音：应删
        ('on', 62, 80, 1000), ('off', 62, 0, 1010),  # 极短：应删
        ('on', 64, 80, 1100), ('off', 64, 0, 1500),  # 保留
        ('on', 67, 80, 1140), ('off', 67, 0, 1540),  # 琶音：起点吸附到 1100
        ('on', 69, 80, 2000), ('off', 69, 0, 2200),  # 保留，结束被重复音延长
        ('on', 69, 40, 2080), ('off', 69, 0, 2280),
        ('on', 71, 80, 2400), ('off', 71, 0, 2600),
        ('on', 71, 70, 2480), ('off', 71, 0, 2680),  # 力度 >= 2×阈值：不合并
    ]
    previous = 0
    for kind, pitch, velocity, at_ms in sorted(
        events, key=lambda item: (item[3], item[0] != 'off')):
      at = ticks(at_ms)
      lead.append(mido.Message(
          'note_on' if kind == 'on' else 'note_off', channel=0, note=pitch,
          velocity=velocity, time=at - previous))
      previous = at
    mid.tracks.append(lead)

    drums = mido.MidiTrack()  # 打击乐：默认原样保留
    drums.append(mido.Message('note_on', channel=DRUM_CHANNEL, note=36,
                              velocity=10, time=ticks(500)))
    drums.append(mido.Message('note_off', channel=DRUM_CHANNEL, note=36,
                              velocity=0, time=ticks(100)))
    mid.tracks.append(drums)

    other = mido.MidiTrack()  # 另一个乐器：只删弱音，通道不能被改掉
    other.append(mido.Message('program_change', channel=1, program=32, time=0))
    other.append(mido.Message('note_on', channel=1, note=52, velocity=80,
                              time=ticks(1000)))
    other.append(mido.Message('note_off', channel=1, note=52, velocity=0,
                              time=ticks(300)))
    other.append(mido.Message('note_on', channel=1, note=53, velocity=10, time=0))
    other.append(mido.Message('note_off', channel=1, note=53, velocity=0,
                              time=ticks(100)))
    mid.tracks.append(other)
    return mid

  def read(path):
    mid = mido.MidiFile(str(path))
    tracks = []
    for track in mid.tracks:
      tick = 0
      notes, programs, opened = [], [], {}
      for msg in track:
        tick += msg.time
        if msg.type == 'program_change':
          programs.append((msg.channel, msg.program))
        elif msg.type == 'note_on' and msg.velocity > 0:
          opened.setdefault((msg.channel, msg.note), []).append(
              (tick, msg.velocity, msg.channel))
        elif msg.type == 'note_off' or (msg.type == 'note_on'
                                        and msg.velocity == 0):
          queue = opened.get((msg.channel, msg.note))
          if queue:
            start, velocity, channel = queue.pop(0)
            notes.append((channel, msg.note, start, tick, velocity, msg.channel))
      tracks.append((notes, programs))
    return tracks

  checks = []
  with tempfile.TemporaryDirectory(prefix='transkun_rearrange_selftest_') as work:
    src = Path(work) / 'song.mid'
    build().save(str(src))
    result = rearrange_file(src)
    checks += [
        ('默认输出名 song.rearranged.mid',
         Path(result.path).name == 'song.rearranged.mid', Path(result.path).name),
        ('notes_in=10', result.notes_in == 10, result.notes_in),
        ('notes_out=6', result.notes_out == 6, result.notes_out),
        ('删弱音 2', result.dropped_velocity == 2, result.dropped_velocity),
        ('删极短音 1', result.dropped_short == 1, result.dropped_short),
        ('合并重复起音 1', result.merged_repeat == 1, result.merged_repeat),
        ('整合同刻音 1', result.merged_chord == 1, result.merged_chord),
        ('跳过鼓音符 1', result.drums_kept == 1, result.drums_kept),
    ]

    tracks = read(result.path)
    lead_notes, lead_programs = tracks[1]
    lead_notes.sort(key=lambda note: (note[2], note[1]))
    checks.append(('轨1 音符', lead_notes == [
        (0, 64, ticks(1100), ticks(1500), 80, 0),
        (0, 67, ticks(1100), ticks(1540), 80, 0),
        (0, 69, ticks(2000), ticks(2280), 80, 0),
        (0, 71, ticks(2400), ticks(2600), 80, 0),
        (0, 71, ticks(2480), ticks(2680), 70, 0)], lead_notes))
    checks.append(('保留换乐器事件', (0, 24) in lead_programs, lead_programs))
    checks.append(('note_off 不改通道', all(n[0] == n[5] for n in lead_notes),
                   lead_notes))
    checks.append(('鼓轨原样保留',
                   tracks[2][0] == [(DRUM_CHANNEL, 36, ticks(500), ticks(600), 10,
                                     DRUM_CHANNEL)], tracks[2][0]))
    checks.append(('轨3 只删弱音',
                   tracks[3][0] == [(1, 52, ticks(1000), ticks(1300), 80, 1)],
                   tracks[3][0]))
    checks.append(('轨3 换乐器保留', (1, 32) in tracks[3][1], tracks[3][1]))

    out = mido.MidiFile(str(result.path))
    checks.append(('指挥轨速度保留',
                   any(m.type == 'set_tempo' for m in out.tracks[0]), None))
    checks.append(('ppq 不变', out.ticks_per_beat == ppq, out.ticks_per_beat))

    again = rearrange_file(result.path)
    checks.append(('重复整理覆盖同名', Path(again.path) == Path(result.path),
                   again.path))
    checks.append(('幂等：第二次不改动',
                   (again.dropped_velocity, again.dropped_short,
                    again.merged_repeat, again.merged_chord) == (0, 0, 0, 0),
                   again.detail()))

    drums_on = Path(work) / 'drums.mid'
    rearrange_midi(build(), drums=True)[0].save(str(drums_on))
    checks.append(('可选整理鼓轨', read(drums_on)[2][0] == [], read(drums_on)[2][0]))

  failed = [name for name, ok, _ in checks if not ok]
  for name, ok, got in checks:
    print(('  [OK]   ' if ok else '  [FAIL] ') + name
          + ('' if ok else f'  got={got}'))
  print()
  print(result.detail())
  if failed:
    print(f'\n{len(failed)} 项失败: {failed}')
    return 1
  print(f'\n自检通过（{len(checks)} 项）')
  return 0


def main(argv=None):
  parser = argparse.ArgumentParser(
      description='整理转谱得到的 MIDI（删弱音/极短音、合并琶音与重复起音）')
  parser.add_argument('input', nargs='?', help='输入 MIDI（例如转谱输出）')
  parser.add_argument('output', nargs='?',
                      help='输出 MIDI（默认 <输入>.rearranged.mid）')
  parser.add_argument('--velocity', type=int, default=DEFAULTS['velocity'],
                      help=f'力度阈值，<= 该值的音符删除（默认 {DEFAULTS["velocity"]}）')
  parser.add_argument('--min-ms', type=int, default=DEFAULTS['min_ms'],
                      help=f'最小时值（毫秒，默认 {DEFAULTS["min_ms"]}）')
  parser.add_argument('--chord-ms', type=int, default=DEFAULTS['chord_ms'],
                      help=f'琶音整合窗口（毫秒，默认 {DEFAULTS["chord_ms"]}）')
  parser.add_argument('--repeat-ms', type=int, default=DEFAULTS['repeat_ms'],
                      help=f'连续消除窗口（毫秒，默认 {DEFAULTS["repeat_ms"]}）')
  parser.add_argument('--drums', action='store_true',
                      help='连打击乐通道一起整理（默认跳过）')
  parser.add_argument('--selftest', action='store_true',
                      help='跑一遍合成 MIDI 自检（不需要 Transkun）')
  args = parser.parse_args(argv)

  if args.selftest:
    return selftest()
  if not args.input:
    parser.error('需要输入 MIDI 文件（或用 --selftest 自检）')

  try:
    result = rearrange_file(
        args.input, args.output, velocity=args.velocity, min_ms=args.min_ms,
        chord_ms=args.chord_ms, repeat_ms=args.repeat_ms, drums=args.drums)
  except Exception as exc:  # noqa: BLE001 - 直接展示给命令行用户
    print(f'整理失败: {type(exc).__name__}: {exc}', file=sys.stderr)
    return 1
  print(f'整理完成: {result.path}')
  print(result.detail())
  return 0


if __name__ == '__main__':
  sys.exit(main())
