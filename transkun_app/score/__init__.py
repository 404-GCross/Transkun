"""打谱版：把演奏事件流降维成可读的钢琴缩编谱。"""

from transkun_app.score.model import ScoreOptions, ScoreResult
from transkun_app.score.pipeline import generate_score, generate_score_from_events

__all__ = [
    'ScoreOptions',
    'ScoreResult',
    'generate_score',
    'generate_score_from_events',
]
