"""打谱版的配置与结果对象。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

MODES = ('block', 'faithful')
MODE_LABELS = {
    'block': '缩编版（和弦块 + 琶音记号）',
    'faithful': '保留版（不删内声部）',
}

KEYS = (
    'auto',
    'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#',
    'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb',
    'Am', 'Em', 'Bm', 'F#m', 'C#m', 'G#m', 'D#m', 'A#m',
    'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm',
)

TIME_SIGNATURES = ('auto', '4/4', '3/4', '2/4', '6/8', '3/8')


@dataclass
class ScoreOptions:
    """打谱参数。

    ``key`` / ``time_signature`` / ``bpm`` 的 ``auto`` 表示自动估计；
    ``bpm=None`` 与 ``auto`` 等价。
    """

    mode: str = 'block'
    key: str = 'auto'
    time_signature: str = 'auto'
    bpm: Optional[float] = None
    split: int = 60
    max_span: int = 19
    max_notes: int = 5
    arpeggio_ms: float = 70.0
    max_chord_beats: float = 4.0
    include_pedal: bool = False
    pdf: bool = True
    svg: bool = True
    score_midi: bool = True
    title: str = ''
    composer: str = ''
    page_size: str = 'A4'  # A4 / Letter

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f'未知打谱模式: {self.mode}')
        if self.key not in KEYS:
            raise ValueError(f'未知调性: {self.key}')
        if self.time_signature not in TIME_SIGNATURES:
            raise ValueError(f'未知拍号: {self.time_signature}')
        self.split = max(0, min(127, int(self.split)))
        self.max_span = max(1, int(self.max_span))
        self.max_notes = max(1, int(self.max_notes))
        self.arpeggio_ms = max(0.0, float(self.arpeggio_ms))
        self.max_chord_beats = max(0.25, float(self.max_chord_beats))
        if self.bpm is not None:
            self.bpm = max(20.0, min(300.0, float(self.bpm)))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoreResult:
    """生成结果；路径不存在表示该格式没有生成。"""

    base: Path
    musicxml: Optional[Path] = None
    pdf: Optional[Path] = None
    midi: Optional[Path] = None
    svg_pages: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def detail(self) -> str:
        parts = []
        if self.stats:
            parts.append(
                f"小节 {self.stats.get('measures', '?')}，"
                f"和弦 {self.stats.get('chords', '?')}，"
                f"音符 {self.stats.get('notes_in', '?')} → "
                f"{self.stats.get('notes_out', '?')}")
            if self.stats.get('arpeggios'):
                parts.append(f"琶音 {self.stats['arpeggios']}")
            if self.stats.get('dropped'):
                parts.append(f"删内声部 {self.stats['dropped']}")
        if self.metadata:
            key = self.metadata.get('key')
            if isinstance(key, dict):
                key = key.get('name')
            parts.append(f"调性 {key or '?'}，"
                         f"拍号 {self.metadata.get('time_signature', '?')}，"
                         f"BPM {self.metadata.get('bpm', '?')}")
        return '｜'.join(parts)
