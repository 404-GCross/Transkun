#!/usr/bin/env python3
"""Transkun Tk 桌面界面。

只在真正打开窗口时导入 tkinter，所以服务器/CLI/WebUI 不需要 Tk。

    transkun-app --gui
    transkun-app --gui song.mp3
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from transkun_app import APP_NAME, __version__

AUDIO_FILE_TYPES = [
    ('音频文件', '*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.aiff *.aif *.opus'),
    ('WAV', '*.wav'),
    ('MP3', '*.mp3'),
    ('所有文件', '*.*'),
]

if sys.platform.startswith('darwin'):
    DEVICE_CHOICES = [
        ('auto', '自动（CPU）'),
        ('cpu', 'CPU'),
    ]
else:
    DEVICE_CHOICES = [
        ('auto', '自动（优先 NVIDIA GPU）'),
        ('cuda', 'NVIDIA GPU'),
        ('cpu', 'CPU'),
    ]

SCORE_MODE_CHOICES = [
    ('block', '缩编版（和弦块 + 琶音记号）'),
    ('faithful', '保留版（不删内声部）'),
]

SCORE_KEY_CHOICES = [
    ('auto', '自动'),
    ('C', 'C'), ('G', 'G'), ('D', 'D'), ('A', 'A'), ('E', 'E'),
    ('B', 'B'), ('F#', 'F#'), ('F', 'F'), ('Bb', 'Bb'), ('Eb', 'Eb'),
    ('Ab', 'Ab'), ('Db', 'Db'),
    ('Am', 'Am'), ('Em', 'Em'), ('Bm', 'Bm'), ('F#m', 'F#m'),
    ('Dm', 'Dm'), ('Gm', 'Gm'), ('Cm', 'Cm'), ('Fm', 'Fm'),
]

SCORE_TIME_CHOICES = [
    ('auto', '自动'), ('4/4', '4/4'), ('3/4', '3/4'),
    ('2/4', '2/4'), ('6/8', '6/8'), ('3/8', '3/8'),
]

SCORE_HINT = '演奏 MIDI 保留；打谱版另存 .score.musicxml / .score.pdf。'

CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'transkun'
CONFIG_FILE = CONFIG_DIR / 'config.json'


class Cancelled(Exception):
    """用户点了取消。"""


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_config(values):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass


def open_path(path):
    """用系统默认程序打开文件/目录。"""
    path = str(path)
    if sys.platform.startswith('darwin'):
        subprocess.Popen(['open', path])
    elif os.name == 'nt':
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(['xdg-open', path])


def _venv_python_candidates():
    candidates = []
    env_dir = os.environ.get('TRANSKUN_VENV_DIR')
    if env_dir:
        candidates.append(Path(env_dir) / 'bin' / 'python')
    candidates.append(Path.home() / '.local/share/transkun/venv/bin/python')
    candidates.append(Path(__file__).resolve().parent.parent / '.venv/bin/python')
    return candidates


def ensure_supported_interpreter():
    """用系统 Python 启动、但没有 torch 时，切到随包/项目 venv。"""
    if os.environ.get('TRANSKUN_BOOTSTRAPPED') == '1':
        return
    import importlib.util

    if importlib.util.find_spec('torch') is not None:
        return

    current = Path(sys.executable).resolve()
    for candidate in _venv_python_candidates():
        if not candidate.exists():
            continue
        try:
            if candidate.resolve() == current:
                continue
        except OSError:
            continue
        os.environ['TRANSKUN_BOOTSTRAPPED'] = '1'
        print(f'[transkun] 使用虚拟环境解释器: {candidate}', file=sys.stderr)
        # 重新走 CLI 入口，保留 --gui/--web/音频参数（直接执行 gui.py 时补 --gui）。
        args = list(sys.argv[1:])
        if Path(sys.argv[0]).name in ('gui.py', 'transkun_app.gui'):
            args = ['--gui', *args]
        os.execv(str(candidate),
                 [str(candidate), '-m', 'transkun_app.cli', *args])


# 常见中文字体族，按优先级排列。
CJK_FONT_CANDIDATES = (
    'Noto Sans CJK SC', 'Noto Sans CJK', 'Noto Sans SC',
    'Source Han Sans SC', 'Source Han Sans CN',
    'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei', 'WQY Zen Hei',
    'Sarasa Gothic SC', 'Sarasa Term SC',
    'Droid Sans Fallback', 'AR PL UMing CN', 'AR PL UKai CN',
    'Microsoft YaHei', 'PingFang SC', 'Heiti SC', 'SimHei',
)


def configure_cjk_fonts(root):
    """把 Tk 默认字体指向已安装的中文字体族。"""
    import tkinter as tk
    import tkinter.font as tkfont

    families = set(tkfont.families(root))
    override = os.environ.get('TRANSKUN_UI_FONT')
    if override:
        chosen = override
    else:
        chosen = next((name for name in CJK_FONT_CANDIDATES if name in families), None)
        if chosen is None:
            for name in sorted(families):
                lowered = name.lower()
                if any(key in lowered for key in
                       ('cjk', 'wenquanyi', 'wqy', 'sarasa', 'source han',
                        'noto sans sc', 'yahei', 'heiti')):
                    chosen = name
                    break
    if chosen is None:
        return None

    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont',
                 'TkCaptionFont', 'TkSmallCaptionFont', 'TkIconFont',
                 'TkTooltipFont'):
        try:
            tkfont.nametofont(name).configure(family=chosen)
        except tk.TclError:
            pass
    try:
        import tkinter.ttk as ttk
        ttk.Style(root).configure('.', font='TkDefaultFont')
    except tk.TclError:
        pass
    return chosen


def check_fonts() -> int:
    """无窗口的 Tk 中文字体冒烟测试（CI/AppImage 用）。"""
    import importlib.util

    if importlib.util.find_spec('tkinter') is None:
        print('FAIL: tkinter is not available', file=sys.stderr)
        return 1
    import tkinter as tk
    import tkinter.font as tkfont

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f'FAIL: 无法打开 Tk（没有显示器/DISPLAY？）: {exc}', file=sys.stderr)
        return 1
    root.withdraw()
    chosen = configure_cjk_fonts(root)
    families = sorted(tkfont.families(root))
    default_font = tkfont.nametofont('TkDefaultFont')
    width = default_font.measure('中文测试')

    print(f'tk default family: {default_font.actual("family")}')
    print(f'cjk family chosen: {chosen}')
    print(f'measure("中文测试") = {width}')
    if chosen is None:
        print('FAIL: no CJK font family is visible to Tk', file=sys.stderr)
        print('first families: ' + ', '.join(families[:40]), file=sys.stderr)
        return 1
    if width <= 0:
        print('FAIL: CJK text renders with zero width', file=sys.stderr)
        return 1
    print('OK: CJK font available to Tk')
    return 0


class TranskunApp:
    """Tkinter 桌面 UI。"""

    def __init__(self, initial_file=None):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.queue = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.last_output = None
        self.last_score_musicxml = None
        self.last_score_pdf = None
        self.last_score_midi = None
        self.config = load_config()

        root = tk.Tk()
        self.root = root
        self.cjk_font = configure_cjk_fonts(root)
        root.title(f'{APP_NAME} · 钢琴转谱')
        root.minsize(680, 560)
        root.protocol('WM_DELETE_WINDOW', self.on_close)

        self.device_var = tk.StringVar(
            value=self.config.get('device', 'auto'))
        if self.device_var.get() not in [value for value, _ in DEVICE_CHOICES]:
            self.device_var.set('auto')
        self.threads_var = tk.StringVar(
            value=str(self.config.get('threads', '') or ''))
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value='就绪')

        self.score_enabled = tk.BooleanVar(
            value=bool(self.config.get('score_enabled', False)))
        self.score_mode = tk.StringVar(
            value=self.config.get('score_mode', 'block'))
        if self.score_mode.get() not in [v for v, _ in SCORE_MODE_CHOICES]:
            self.score_mode.set('block')
        self.score_key = tk.StringVar(
            value=self.config.get('score_key', 'auto'))
        if self.score_key.get() not in [v for v, _ in SCORE_KEY_CHOICES]:
            self.score_key.set('auto')
        self.score_time = tk.StringVar(
            value=self.config.get('score_time', 'auto'))
        if self.score_time.get() not in [v for v, _ in SCORE_TIME_CHOICES]:
            self.score_time.set('auto')
        self.score_bpm = tk.StringVar(
            value=str(self.config.get('score_bpm', '') or ''))
        self.score_pdf = tk.BooleanVar(
            value=bool(self.config.get('score_pdf', True)))

        main = ttk.Frame(root, padding=10)
        main.pack(fill='both', expand=True)
        main.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(main, text='音频文件：').grid(row=row, column=0, sticky='w')
        ttk.Entry(main, textvariable=self.input_var).grid(
            row=row, column=1, sticky='ew', padx=(0, 6))
        ttk.Button(main, text='浏览…', command=self.on_browse_input).grid(
            row=row, column=2, sticky='ew')
        row += 1

        ttk.Label(main, text='输出 MIDI：').grid(row=row, column=0, sticky='w')
        ttk.Entry(main, textvariable=self.output_var).grid(
            row=row, column=1, sticky='ew', padx=(0, 6))
        ttk.Button(main, text='另存为…', command=self.on_browse_output).grid(
            row=row, column=2, sticky='ew')
        row += 1

        options = ttk.Frame(main)
        options.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        ttk.Label(options, text='设备：').pack(side='left')
        ttk.Combobox(
            options, textvariable=self.device_var,
            values=[label for _, label in DEVICE_CHOICES], state='readonly',
            width=22).pack(side='left', padx=(0, 14))
        ttk.Label(options, text='CPU 线程：').pack(side='left')
        ttk.Spinbox(options, from_=1, to=64, textvariable=self.threads_var,
                    width=6).pack(side='left')
        ttk.Label(options, text='（留空 = 自动）').pack(side='left', padx=(4, 0))
        row += 1

        post = ttk.LabelFrame(main, text='打谱版（MusicXML / PDF，可选）')
        post.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        row += 1
        ttk.Checkbutton(post, text='转谱后生成打谱版',
                        variable=self.score_enabled).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 2))
        cell = ttk.Frame(post)
        cell.grid(row=1, column=0, sticky='w', padx=10, pady=(0, 2))
        ttk.Label(cell, text='模式：').pack(side='left')
        ttk.Combobox(cell, textvariable=self.score_mode, state='readonly',
                     values=[label for _, label in SCORE_MODE_CHOICES],
                     width=26).pack(side='left', padx=(4, 0))
        cell = ttk.Frame(post)
        cell.grid(row=1, column=1, sticky='w', padx=10, pady=(0, 2))
        ttk.Label(cell, text='调性：').pack(side='left')
        ttk.Combobox(cell, textvariable=self.score_key, state='readonly',
                     values=[label for _, label in SCORE_KEY_CHOICES],
                     width=8).pack(side='left', padx=(4, 0))
        cell = ttk.Frame(post)
        cell.grid(row=2, column=0, sticky='w', padx=10, pady=(0, 2))
        ttk.Label(cell, text='拍号：').pack(side='left')
        ttk.Combobox(cell, textvariable=self.score_time, state='readonly',
                     values=[label for _, label in SCORE_TIME_CHOICES],
                     width=8).pack(side='left', padx=(4, 0))
        cell = ttk.Frame(post)
        cell.grid(row=2, column=1, sticky='w', padx=10, pady=(0, 2))
        ttk.Label(cell, text='BPM：').pack(side='left')
        ttk.Entry(cell, textvariable=self.score_bpm, width=7).pack(
            side='left', padx=(4, 0))
        ttk.Label(cell, text='（留空 = 自动）').pack(side='left', padx=(4, 0))
        ttk.Checkbutton(post, text='生成 PDF', variable=self.score_pdf).grid(
            row=3, column=0, sticky='w', padx=10, pady=(0, 2))
        ttk.Label(post, text=SCORE_HINT).grid(
            row=4, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 6))

        buttons = ttk.Frame(main)
        buttons.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(12, 6))
        self.start_button = ttk.Button(
            buttons, text='开始转谱', command=self.on_start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(
            buttons, text='取消', command=self.on_cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=(8, 0))
        self.open_dir_button = ttk.Button(
            buttons, text='打开输出目录', command=self.on_open_dir,
            state='disabled')
        self.open_dir_button.pack(side='left', padx=(8, 0))
        self.open_midi_button = ttk.Button(
            buttons, text='打开 MIDI', command=self.on_open_midi,
            state='disabled')
        self.open_midi_button.pack(side='left', padx=(8, 0))
        self.open_musicxml_button = ttk.Button(
            buttons, text='打开 MusicXML', command=self.on_open_musicxml,
            state='disabled')
        self.open_musicxml_button.pack(side='left', padx=(8, 0))
        self.open_score_pdf_button = ttk.Button(
            buttons, text='打开 PDF', command=self.on_open_score_pdf,
            state='disabled')
        self.open_score_pdf_button.pack(side='left', padx=(8, 0))
        row += 1

        self.progress = ttk.Progressbar(main, mode='determinate', maximum=100)
        self.progress.grid(row=row, column=0, columnspan=3, sticky='ew')
        row += 1

        ttk.Label(main, textvariable=self.status_var, anchor='w').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=(4, 6))
        row += 1

        log_frame = ttk.LabelFrame(main, text='日志')
        log_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
        main.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=10, wrap='word', state='disabled')
        self.log_text.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(
            log_frame, orient='vertical', command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.input_var.trace_add('write', self.on_input_changed)
        if initial_file:
            path = Path(initial_file)
            if path.exists():
                self.input_var.set(str(path))
            else:
                self.log(f'忽略不存在的音频文件: {initial_file}')

        self.log(f'{APP_NAME} {__version__}（模型 2.0）')
        self.log('界面字体: ' + (self.cjk_font or '(未找到中文字体)'))
        if self.cjk_font is None:
            self.root.after(300, self.warn_missing_cjk_font)

        self.root.after(100, self.poll_queue)

    # ------------------------------------------------------------------ UI 事件

    def warn_missing_cjk_font(self):
        if sys.platform.startswith('darwin'):
            hint = ('  系统自带苹方/黑体，若缺失可在“系统设置 → 通用 → 语言与地区”'
                    '里重新安装中文语言支持。\n\n')
        else:
            hint = ('  Debian/Ubuntu: sudo apt install fonts-noto-cjk\n'
                    '  Fedora:        sudo dnf install google-noto-sans-cjk-fonts\n'
                    '  Arch:          sudo pacman -S noto-fonts-cjk\n\n')
        self.messagebox.showwarning(
            '缺少中文字体 / Missing CJK font',
            '未找到可用的中文字体，界面里中文可能显示为空白。\n\n'
            '请安装任一中文字体后重新打开：\n' + hint +
            'No CJK font found; Chinese labels may be blank.')

    def persist_config(self):
        try:
            threads = int(self.threads_var.get()) if self.threads_var.get().strip() else None
        except ValueError:
            threads = None
            self.threads_var.set('')
        values = {
            'device': self.device_var.get(),
            'threads': threads,
            'score_enabled': bool(self.score_enabled.get()),
            'score_mode': self.score_mode.get(),
            'score_key': self.score_key.get(),
            'score_time': self.score_time.get(),
            'score_bpm': self.score_bpm.get().strip(),
            'score_pdf': bool(self.score_pdf.get()),
        }
        save_config(values)

    def on_browse_input(self):
        path = self.filedialog.askopenfilename(
            title='选择钢琴音频', filetypes=AUDIO_FILE_TYPES)
        if path:
            self.input_var.set(path)

    def on_browse_output(self):
        initial = Path(self.output_var.get()).name if self.output_var.get() else ''
        path = self.filedialog.asksaveasfilename(
            title='保存 MIDI', defaultextension='.mid', initialfile=initial,
            filetypes=[('MIDI 文件', '*.mid *.midi'), ('所有文件', '*.*')])
        if path:
            self.output_var.set(path)

    def on_input_changed(self, *_args):
        audio = self.input_var.get().strip()
        if audio:
            self.output_var.set(str(Path(audio).with_suffix('.mid')))

    def on_open_dir(self):
        if self.last_output:
            open_path(Path(self.last_output).parent)

    def on_open_midi(self):
        if self.last_output and Path(self.last_output).exists():
            open_path(self.last_output)

    def on_open_musicxml(self):
        if self.last_score_musicxml and Path(self.last_score_musicxml).exists():
            open_path(self.last_score_musicxml)

    def on_open_score_pdf(self):
        if self.last_score_pdf and Path(self.last_score_pdf).exists():
            open_path(self.last_score_pdf)

    # ------------------------------------------------------------------ 转谱

    def on_start(self):
        if self.busy:
            return

        audio = self.input_var.get().strip()
        output = self.output_var.get().strip()
        if not audio or not Path(audio).exists():
            self.messagebox.showerror('错误', '请选择有效的音频文件。')
            return
        if not output:
            self.messagebox.showerror('错误', '请指定输出 MIDI 路径。')
            return
        if Path(output).exists() and not self.messagebox.askyesno(
                '覆盖确认', f'文件已存在，是否覆盖？\n{output}'):
            return

        self.persist_config()
        self.busy = True
        self.cancel_event.clear()
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.open_dir_button.configure(state='disabled')
        self.open_midi_button.configure(state='disabled')
        self.open_musicxml_button.configure(state='disabled')
        self.open_score_pdf_button.configure(state='disabled')
        self.set_progress(0, None)
        self.status_var.set('准备中…')
        self.log('=' * 60)
        self.log(f'输入: {audio}')
        self.log(f'输出: {output}')

        score_options = None
        if self.score_enabled.get():
            from transkun_app.score import ScoreOptions
            bpm = None
            if self.score_bpm.get().strip():
                try:
                    bpm = float(self.score_bpm.get())
                except ValueError:
                    self.score_bpm.set('')
            score_options = ScoreOptions(
                mode=dict((label, value) for value, label in
                          SCORE_MODE_CHOICES).get(self.score_mode.get(),
                                                  self.score_mode.get()),
                key=dict((label, value) for value, label in
                         SCORE_KEY_CHOICES).get(self.score_key.get(),
                                                self.score_key.get()),
                time_signature=dict((label, value) for value, label in
                                    SCORE_TIME_CHOICES).get(
                                        self.score_time.get(),
                                        self.score_time.get()),
                bpm=bpm, pdf=bool(self.score_pdf.get()))
            self.log('打谱版: 已启用（' + score_options.mode + '，调性 '
                     + score_options.key + '，拍号 '
                     + score_options.time_signature + '）')

        device = dict((label, value) for value, label in DEVICE_CHOICES).get(
            self.device_var.get(), self.device_var.get())
        threads = None
        if self.threads_var.get().strip():
            try:
                threads = max(1, int(self.threads_var.get()))
            except ValueError:
                self.threads_var.set('')

        threading.Thread(
            target=self.worker,
            args=(audio, output, device, threads, score_options),
            daemon=True).start()

    def on_cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.status_var.set('正在取消…')
            self.log('已请求取消：当前片段结束后停止。')

    # ------------------------------------------------------------------ 后台线程

    def worker(self, audio, output, device, threads, score_options=None):
        q = self.queue
        start = time.time()
        try:
            q.put(('status', '正在加载依赖与模型（首次较慢）…'))
            from transkun_app import backend

            transcriber = backend.get_transcriber(
                device=device, threads=threads)
            q.put(('log', f'推理设备: {backend.device_summary(transcriber.device)}'))

            def check_cancel():
                if self.cancel_event.is_set():
                    raise Cancelled()

            def progress_cb(done, total):
                check_cancel()
                q.put(('progress', done, total))

            q.put(('status', '正在转谱…'))
            events = transcriber.transcribe_file(
                audio, progress_cb=progress_cb, cancel_cb=check_cancel)

            q.put(('status', '正在写入 MIDI…'))
            backend.save_midi(events, output)
            notes = backend.note_count(events)
            pedals = backend.pedal_count(events)

            score_result = None
            if score_options is not None:
                q.put(('status', '正在生成打谱版…'))
                from transkun_app.score import generate_score_from_events
                base = Path(output).with_suffix('').with_name(
                    Path(output).stem + '.score')
                score_result = generate_score_from_events(
                    events, base, score_options)

            q.put(('done', output, notes, pedals,
                   time.time() - start, score_result))
        except Cancelled:
            q.put(('cancelled',))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            q.put(('error', f'{type(exc).__name__}: {exc}',
                   traceback.format_exc()))

    # ------------------------------------------------------------------ 队列轮询

    def poll_queue(self):
        try:
            while True:
                self.handle_message(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def handle_message(self, message):
        kind = message[0]
        if kind == 'status':
            self.status_var.set(message[1])
        elif kind == 'log':
            self.log(message[1])
        elif kind == 'progress':
            done, total = message[1], message[2]
            self.set_progress(done, total)
            if total:
                self.status_var.set(f'正在转谱… {done}/{total} 个片段')
            else:
                self.status_var.set(f'正在转谱… 已处理 {done} 个片段')
        elif kind == 'done':
            _, output, notes, pedals, elapsed, score_result = message
            self.finish()
            self.last_output = output
            self.open_dir_button.configure(state='normal')
            self.open_midi_button.configure(state='normal')
            self.status_var.set(f'完成：{notes} 个音符')
            self.log(f'完成：{notes} 个音符，{pedals} 段踏板，'
                     f'耗时 {elapsed:.1f} 秒')
            self.log(f'MIDI 已保存: {output}')
            extra = ''
            if score_result is not None:
                if score_result.musicxml:
                    self.last_score_musicxml = str(score_result.musicxml)
                    self.open_musicxml_button.configure(state='normal')
                    self.log(f'MusicXML: {score_result.musicxml}')
                if score_result.pdf:
                    self.last_score_pdf = str(score_result.pdf)
                    self.open_score_pdf_button.configure(state='normal')
                    self.log(f'PDF: {score_result.pdf}')
                if score_result.midi:
                    self.last_score_midi = str(score_result.midi)
                for warning in score_result.warnings:
                    self.log(f'打谱提示: {warning}')
                extra = (f'\n\n打谱版：\n{score_result.musicxml}\n'
                         f'{score_result.pdf or ""}\n'
                         f'{score_result.detail()}')
            self.messagebox.showinfo(
                '转谱完成',
                f'已生成 MIDI：\n{output}\n\n'
                f'音符：{notes}\n踏板区间：{pedals}\n'
                f'耗时：{elapsed:.1f} 秒{extra}')
        elif kind == 'cancelled':
            self.finish()
            self.status_var.set('已取消')
            self.log('已取消（未生成 MIDI）。')
        elif kind == 'error':
            _, summary, details = message
            self.finish()
            self.status_var.set('出错了')
            self.log(f'错误: {summary}')
            self.log(details)
            self.messagebox.showerror('转谱失败', summary)

    # ------------------------------------------------------------------ 小工具

    def finish(self):
        self.busy = False
        self.cancel_event.clear()
        self.start_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')
        self.progress.stop()
        self.progress.configure(mode='determinate')
        if self.last_score_musicxml:
            self.open_musicxml_button.configure(
                state='normal' if Path(self.last_score_musicxml).exists()
                else 'disabled')
        if self.last_score_pdf:
            self.open_score_pdf_button.configure(
                state='normal' if Path(self.last_score_pdf).exists()
                else 'disabled')

    def set_progress(self, done, total):
        if total:
            if str(self.progress.cget('mode')) != 'determinate':
                self.progress.stop()
                self.progress.configure(mode='determinate')
            self.progress.configure(maximum=total, value=min(done, total))
        else:
            if str(self.progress.cget('mode')) != 'indeterminate':
                self.progress.configure(mode='indeterminate')
                self.progress.start(12)

    def log(self, text):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def on_close(self):
        if self.busy and not self.messagebox.askyesno(
                '正在转谱', '任务还没结束，确定退出吗？'):
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run_gui(initial_file=None) -> int:
    ensure_supported_interpreter()
    import importlib.util

    if importlib.util.find_spec('tkinter') is None:
        print('未安装 Tkinter，无法启动图形界面。', file=sys.stderr)
        if sys.platform.startswith('darwin'):
            print('macOS: 请使用随包分发的 Transkun.app（内置 Tk）。',
                  file=sys.stderr)
        else:
            print('Debian/Ubuntu: sudo apt install python3-tk', file=sys.stderr)
            print('Fedora:        sudo dnf install python3-tkinter', file=sys.stderr)
        print('或使用命令行: transkun-app song.mp3 -o song.mid', file=sys.stderr)
        return 1
    try:
        TranskunApp(initial_file=initial_file).run()
    except Exception as exc:  # noqa: BLE001 - 没有显示器是最常见的失败
        text = str(exc).lower()
        if 'display' in text or 'screen' in text:
            print('无法打开图形界面：没有可用的显示器（DISPLAY）。', file=sys.stderr)
            print('服务器/SSH 环境请用：transkun-app --cli 音频 -o 输出.mid',
                  file=sys.stderr)
            print('或浏览器：transkun-app --web --lan', file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == '__main__':
    initial = None
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        initial = sys.argv[1]
    sys.exit(run_gui(initial))
