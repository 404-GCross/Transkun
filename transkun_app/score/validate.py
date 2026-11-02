"""MusicXML 三连音校验：按 ``(staff, voice, number)`` 统计 tuplet 组。

只用于测试与自检，不参与打谱流水线。``tuplet_details`` 返回每个组的比例、
起音数、总时值、书写类型、正常类型和起止小节；``tuplet_report`` 是它的
简化版，供只需要组大小/比例的调用方使用。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

TYPE_QUARTER_LENGTH = {
    'whole': 4.0,
    'half': 2.0,
    'quarter': 1.0,
    'eighth': 0.5,
    '16th': 0.25,
    '32nd': 0.125,
    '64th': 0.0625,
    '128th': 0.03125,
}


def tuplet_details(musicxml_path):
    """返回 ``(ratios, details, divisions)``。

    ``ratios`` 是 XML 里出现过的所有 ``actual-notes:normal-notes``；
    ``details`` 的每一项包含 ``ratio``、``count``（起音数）、``duration``
    （MusicXML divisions 总和）、``type``、``normal_type``、
    ``start_measure`` 和 ``stop_measure``；``divisions`` 是每四分音符的
    MusicXML divisions，用于校验跨度。
    """
    root = ET.parse(str(musicxml_path)).getroot()
    ratios = set()
    details = []
    active = {}
    divisions = None
    for measure in root.iter('measure'):
        measure_number = int(measure.get('number'))
        attributes = measure.find('attributes')
        if attributes is not None and divisions is None:
            value = attributes.findtext('divisions')
            if value:
                divisions = int(value)
        for note in measure.findall('note'):
            staff = note.findtext('staff', '1')
            voice = note.findtext('voice', '1')
            tm = note.find('time-modification')
            ratio = None
            if tm is not None:
                ratio = (int(tm.findtext('actual-notes')),
                         int(tm.findtext('normal-notes')))
                ratios.add(ratio)
            # <chord/> 音只是同一个起音上的另一个音高，不是新的节奏事件。
            if note.find('chord') is not None:
                continue
            tuplets = []
            notations = note.find('notations')
            if notations is not None:
                for item in notations.findall('tuplet'):
                    tuplets.append((item.get('type'), item.get('number')))
            touched = set()
            opened_here = set()
            duration = int(note.findtext('duration', '0'))
            for kind, number in tuplets:
                key = (staff, voice, number)
                touched.add(key)
                if kind == 'start':
                    if key in active:
                        details.append(active[key])
                        del active[key]
                    active[key] = {
                        'ratio': ratio,
                        'count': 1,
                        'duration': duration,
                        'type': note.findtext('type'),
                        'normal_type': (tm.findtext('normal-type')
                                        if tm is not None else None),
                        'start_measure': measure_number,
                        'stop_measure': measure_number,
                    }
                    opened_here.add(key)
                elif kind == 'continue' and key in active:
                    active[key]['count'] += 1
                    active[key]['duration'] += duration
                    active[key]['stop_measure'] = measure_number
                elif kind == 'stop' and key in active:
                    # 同一个音上既有 start 又有 stop：这是非法的单音符组，
                    # 只能算 1 个起音，不能把 stop 再算一次。
                    if key not in opened_here:
                        active[key]['count'] += 1
                        active[key]['duration'] += duration
                    active[key]['stop_measure'] = measure_number
                    details.append(active[key])
                    del active[key]
            # MusicXML 允许省略 continue；活动组里的普通音也要计数。
            for key, state in list(active.items()):
                if key not in touched:
                    state['count'] += 1
                    state['duration'] += duration
                    state['stop_measure'] = measure_number
    return ratios, details, divisions


def tuplet_report(musicxml_path):
    """返回 ``(ratios, [(ratio, size), ...])`` 的简化报告。"""
    ratios, details, _ = tuplet_details(musicxml_path)
    return ratios, [(item['ratio'], item['count']) for item in details]


def tuplet_span_ok(detail, divisions) -> bool:
    """组总时值是否等于 ``normal-notes × normal-type``。"""
    normal_type = detail.get('normal_type')
    ratio = detail.get('ratio')
    if divisions is None or normal_type not in TYPE_QUARTER_LENGTH or not ratio:
        return False
    expected = ratio[1] * TYPE_QUARTER_LENGTH[normal_type] * divisions
    return abs(detail.get('duration', 0) - expected) <= 1
