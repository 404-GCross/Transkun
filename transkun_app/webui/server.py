"""WebUI 服务：FastAPI 应用 + 局域网令牌 + 上传/队列/下载接口。

由 ``transkun-app --web`` 启动。安全模型：

- **强制令牌**：连 127.0.0.1 也要令牌，否则任意网页都能用表单 POST 打本机
  端口（CSRF / DNS rebinding）。
- **只允许局域网**：明文 HTTP，没有 TLS；文档里明确不要做端口转发。
- 令牌只能上传音频、占用算力、下载自己任务的 MIDI，读不到服务器上的其它文件。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from transkun_app import __version__
from transkun_app.webui import jobs as jobs_module

APP_NAME = 'Transkun'
COOKIE_NAME = 'transkun_token'
COOKIE_MAX_AGE = 30 * 24 * 3600
STATIC_DIR = Path(__file__).resolve().parent / 'static'
MODEL_ID = '2.0'

#: multipart 的分隔符/字段开销，用来给 Content-Length 预检留余量。
UPLOAD_OVERHEAD = 8 * 1024 * 1024
TRUE_VALUES = ('1', 'true', 'yes', 'on')


# --------------------------------------------------------------------- 路径/令牌


def default_cache_dir() -> Path:
    """任务目录：必须是可写的位置（包内可能是只读卷）。"""
    env = os.environ.get('TRANSKUN_USER_CACHE')
    if env:
        return Path(env)
    if sys.platform.startswith('darwin'):
        return Path.home() / 'Library' / 'Caches' / 'Transkun'
    base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'transkun'


def token_path() -> Path:
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return base / 'transkun' / 'web_token'


def load_token(new=False) -> str:
    """读取（或首次生成）访问令牌；``new=True`` 时重新生成。"""
    path = token_path()
    if not new:
        try:
            token = path.read_text(encoding='utf-8').strip()
        except OSError:
            token = ''
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + '\n', encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def lan_addresses() -> list:
    """本机的局域网 IPv4 地址（尽力而为，不保证全）。"""
    found = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(('8.8.8.8', 80))
            found.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith('127.'):
                found.append(address)
    except OSError:
        pass
    return found


def read_version() -> str:
    root = Path(__file__).resolve().parent.parent.parent
    try:
        text = (root / 'VERSION').read_text(encoding='utf-8').strip()
        if text:
            return text
    except OSError:
        pass
    return __version__


def model_paths():
    """权重/配置路径（不导入 torch）。"""
    try:
        from importlib.resources import files
        base = files('transkun').joinpath('pretrained')
        return Path(str(base / '2.0.pt')), Path(str(base / '2.0.conf'))
    except Exception:  # pragma: no cover
        import pkg_resources
        base = Path(pkg_resources.resource_filename(
            'transkun', 'pretrained'))
        return base / '2.0.pt', base / '2.0.conf'


def model_available() -> bool:
    weight, conf = model_paths()
    return weight.exists() and conf.exists()


def probe_duration_minutes(path) -> Optional[float]:
    """只读容器头拿时长；失败返回 None（交给 worker 去报错）。"""
    try:
        import soundfile
        info = soundfile.info(str(path))
        if info.samplerate:
            return info.frames / float(info.samplerate) / 60.0
    except Exception:  # noqa: BLE001 - 格式不支持时换 ffprobe
        pass
    try:
        import subprocess
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1', str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        text = proc.stdout.decode('utf-8', 'replace').strip()
        if text:
            return float(text) / 60.0
    except Exception:  # noqa: BLE001 - 实在拿不到就不拦
        pass
    return None


def choose_port(host, port, tries=20) -> int:
    for candidate in range(port, port + tries):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, candidate))
            return candidate
        except OSError:
            continue
        finally:
            sock.close()
    raise SystemExit(
        '端口 {}-{} 都被占用了，用 --port 换一个。'.format(
            port, port + tries - 1))


def truthy(value) -> bool:
    return str(value or '').strip().lower() in TRUE_VALUES


# --------------------------------------------------------------------- 配置


@dataclass
class WebConfig:
    token: str
    jobs_dir: Path
    device: str = 'auto'
    device_info: str = ''
    threads: Optional[int] = None
    weight: Optional[str] = None
    conf: Optional[str] = None
    max_bytes: int = 1 << 30
    max_minutes: float = 30.0
    queue_limit: int = 8
    keep_hours: float = 24.0
    version: str = 'dev'
    rearrange_defaults: dict = field(default_factory=dict)


# --------------------------------------------------------------------- 应用


def create_app(config: WebConfig, manager=None) -> FastAPI:
    """构造应用；``manager`` 只在自检时注入（用假 runner）。"""
    if manager is None:
        manager = jobs_module.JobManager(
            jobs_dir=config.jobs_dir,
            runner=jobs_module.make_runner(config),
            queue_limit=config.queue_limit,
            keep_hours=config.keep_hours)

    app = FastAPI(
        title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.manager = manager
    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')

    # ------------------------------------------------------------------ 鉴权

    def check_token(request: Request) -> str:
        expected = config.token
        given = request.cookies.get(COOKIE_NAME) or ''
        header = request.headers.get('authorization') or ''
        if header.lower().startswith('bearer '):
            given = header[7:].strip()
        if not given or not secrets.compare_digest(given, expected):
            raise HTTPException(
                status_code=401,
                detail='需要访问令牌：请用启动时打印的带 ?t= 的链接打开。',
                headers={'WWW-Authenticate': 'Bearer'})
        return given

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # 跨站请求：浏览器一定会带 Origin / Sec-Fetch-Site，直接拒绝。
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '跨站请求被拒绝'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host', ''):
                return JSONResponse({'detail': '跨站请求被拒绝'}, status_code=403)
            # 1 GB 的文件没必要先收完再拒：Content-Length 超限直接 413。
            if request.method == 'POST' and request.url.path == '/api/jobs':
                length = request.headers.get('content-length') or ''
                if length.isdigit() and int(length) > config.max_bytes + UPLOAD_OVERHEAD:
                    return JSONResponse(
                        {'detail': '文件超过上限（{:.2f} GB）'.format(
                            config.max_bytes / 2 ** 30)},
                        status_code=413)
        response = await call_next(request)
        if request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'")
        return response

    # ------------------------------------------------------------------ 页面

    @app.get('/')
    def index(request: Request, t: Optional[str] = None):
        if t is not None:
            if not secrets.compare_digest(t, config.token):
                raise HTTPException(401, '访问令牌不正确。')
            response = RedirectResponse(url='/', status_code=303)
            response.set_cookie(
                COOKIE_NAME, config.token, max_age=COOKIE_MAX_AGE,
                httponly=True, samesite='strict', path='/')
            return response
        if not secrets.compare_digest(request.cookies.get(COOKIE_NAME) or '',
                                      config.token):
            return HTMLResponse(
                (STATIC_DIR / 'login.html').read_text(encoding='utf-8'),
                status_code=401)
        return FileResponse(STATIC_DIR / 'index.html', media_type='text/html')

    @app.get('/api/health')
    def health():
        """不需要令牌：给探活/监控用，不暴露任何服务端信息。"""
        return {'ok': True, 'app': 'transkun'}

    # ------------------------------------------------------------------ 信息

    @app.get('/api/info', dependencies=[Depends(check_token)])
    def info():
        weight, _ = model_paths()
        try:
            weight_bytes = weight.stat().st_size
        except OSError:
            weight_bytes = None
        return {
            'app': 'transkun',
            'version': config.version,
            'device': config.device_info or config.device,
            'models': {MODEL_ID: model_available()},
            'model': {
                'id': MODEL_ID,
                'name': 'Transkun 2.0',
                'upstream': '2.0.1',
                'weight_bytes': weight_bytes,
                'sample_rate': 44100,
            },
            'limits': {
                'max_bytes': config.max_bytes,
                'max_minutes': config.max_minutes,
                'queue_limit': config.queue_limit,
                'keep_hours': config.keep_hours,
            },
            'rearrange_defaults': config.rearrange_defaults,
        }

    # ------------------------------------------------------------------ 任务

    def rearrange_params(rearrange, velocity, min_ms, chord_ms, repeat_ms):
        if not truthy(rearrange):
            return None
        return {
            'velocity': max(0, min(127, int(velocity))),
            'min_ms': max(0, int(min_ms)),
            'chord_ms': max(0, int(chord_ms)),
            'repeat_ms': max(0, int(repeat_ms)),
            'drums': False,
        }

    @app.post('/api/jobs', status_code=202,
              dependencies=[Depends(check_token)])
    async def create_job(
        request: Request,
        file: UploadFile = File(...),
        model: str = Form(MODEL_ID),
        rearrange: str = Form('false'),
        velocity: int = Form(30),
        min_ms: int = Form(30),
        chord_ms: int = Form(60),
        repeat_ms: int = Form(120),
    ):
        if manager.queued_count() >= config.queue_limit:
            raise HTTPException(
                429, '队列已满（{} 个等待中），稍后再传。'.format(config.queue_limit))

        original = Path(file.filename or 'audio').name[:200]
        suffix = Path(original).suffix.lower()
        if suffix not in jobs_module.AUDIO_EXTENSIONS:
            raise HTTPException(415, '不支持的格式：{}。可用：{}'.format(
                suffix or '（无扩展名）',
                ' '.join(ext.lstrip('.') for ext in jobs_module.AUDIO_EXTENSIONS)))
        if model != MODEL_ID:
            raise HTTPException(400, '未知模型：{}'.format(model))
        if not model_available():
            raise HTTPException(
                409, '服务器上没有 Transkun 2.0 权重，请检查安装包/源码里的 '
                     'transkun/pretrained/2.0.pt。')

        job_id, directory = manager.new_directory()
        destination = directory / ('input' + suffix)
        written = 0
        try:
            with destination.open('wb') as out:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > config.max_bytes:
                        raise HTTPException(
                            413, '文件超过上限（{:.2f} GB）'.format(
                                config.max_bytes / 2 ** 30))
                    out.write(chunk)
            if written == 0:
                raise HTTPException(400, '空文件。')

            minutes = await asyncio.to_thread(probe_duration_minutes, destination)
            if minutes is not None and minutes > config.max_minutes:
                raise HTTPException(
                    413, '音频时长 {:.1f} 分钟，超过上限 {:g} 分钟。'.format(
                        minutes, config.max_minutes))

            job = jobs_module.Job(
                id=job_id, filename=original, model=MODEL_ID,
                directory=directory, input_path=destination,
                rearrange=rearrange_params(
                    rearrange, velocity, min_ms, chord_ms, repeat_ms))
            snapshot = manager.submit(job)
        except jobs_module.QueueFull as exc:
            manager.discard(job_id)
            raise HTTPException(429, str(exc)) from exc
        except BaseException:
            manager.discard(job_id)
            raise
        finally:
            await file.close()

        return {
            'id': snapshot['id'],
            'status': snapshot['status'],
            'queue_position': snapshot['queue_position'],
        }

    @app.get('/api/jobs', dependencies=[Depends(check_token)])
    def list_jobs():
        return {'jobs': manager.list()}

    @app.get('/api/jobs/{job_id}', dependencies=[Depends(check_token)])
    def get_job(job_id: str):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, '任务不存在（可能已被清理）。')
        return job

    @app.get('/api/jobs/{job_id}/events', dependencies=[Depends(check_token)])
    async def job_events(job_id: str):
        async def stream():
            last = None
            while True:
                job = manager.get(job_id)
                if job is None:
                    yield 'event: gone\ndata: {}\n\n'
                    return
                payload = json.dumps(job, ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield 'data: {}\n\n'.format(payload)
                if job['status'] in jobs_module.FINAL_STATUSES:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.post('/api/jobs/{job_id}/retry', status_code=202,
              dependencies=[Depends(check_token)])
    def retry_job(
        job_id: str,
        model: str = MODEL_ID,
        rearrange: str = 'false',
        velocity: int = 30,
        min_ms: int = 30,
        chord_ms: int = 60,
        repeat_ms: int = 120,
    ):
        """用同一个已上传的文件、按新参数再转一遍（不用重新上传）。"""
        if model != MODEL_ID:
            raise HTTPException(400, '未知模型：{}'.format(model))
        if not model_available():
            raise HTTPException(
                409, '服务器上没有 Transkun 2.0 权重。')
        if manager.queued_count() >= config.queue_limit:
            raise HTTPException(
                429, '队列已满（{} 个等待中），稍后再试。'.format(config.queue_limit))

        try:
            snapshot = manager.clone_for_retry(
                job_id, MODEL_ID,
                rearrange_params(rearrange, velocity, min_ms, chord_ms, repeat_ms))
        except KeyError as exc:
            raise HTTPException(404, '任务不存在（可能已被清理）。') from exc
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except jobs_module.QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        return {
            'id': snapshot['id'],
            'status': snapshot['status'],
            'queue_position': snapshot['queue_position'],
        }

    @app.post('/api/jobs/{job_id}/cancel', dependencies=[Depends(check_token)])
    def cancel_job(job_id: str):
        job = manager.cancel(job_id)
        if job is None:
            raise HTTPException(404, '任务不存在（可能已被清理）。')
        return job

    @app.delete('/api/jobs/{job_id}', status_code=204,
                dependencies=[Depends(check_token)])
    def delete_job(job_id: str):
        try:
            removed = manager.delete(job_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not removed:
            raise HTTPException(404, '任务不存在（可能已被清理）。')
        return None

    @app.get('/api/jobs/{job_id}/midi', dependencies=[Depends(check_token)])
    def download_midi(job_id: str, kind: str = 'raw'):
        if kind not in ('raw', 'rearranged'):
            raise HTTPException(400, 'kind 只能是 raw 或 rearranged。')
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, '任务不存在（可能已被清理）。')
        if not job[kind]:
            raise HTTPException(
                404, '还没有整理版 MIDI：转谱时没有勾选后处理。' if kind == 'rearranged'
                else '还没有生成 MIDI。')
        stored = manager.path(job_id, kind)
        if stored is None or not stored.exists():
            raise HTTPException(404, '文件已被清理。')
        stem = Path(job['filename']).stem or 'output'
        name = stem + ('.rearranged.mid' if kind == 'rearranged' else '.mid')
        return FileResponse(stored, media_type='audio/midi', filename=name)

    return app


# --------------------------------------------------------------------- 入口


def run_server(args) -> int:
    """``transkun-app --web`` 的入口。"""
    from transkun_app import backend

    device = backend.resolve_device(args.device)
    device_info = backend.device_summary(device)

    host = '0.0.0.0' if getattr(args, 'lan', False) else args.host
    token = args.token or load_token(new=args.new_token)
    jobs_dir = Path(
        os.environ.get('TRANSKUN_WEB_JOB_DIR') or (default_cache_dir() / 'web'))

    config = WebConfig(
        token=token,
        jobs_dir=jobs_dir,
        device=device,
        device_info=device_info,
        threads=getattr(args, 'threads', None),
        weight=getattr(args, 'weight', None),
        conf=getattr(args, 'conf', None),
        max_bytes=max(1, int(args.max_bytes)),
        max_minutes=max(0.1, float(args.max_minutes)),
        queue_limit=max(1, int(args.queue)),
        keep_hours=max(0.0, float(args.keep_hours)),
        version=read_version(),
        rearrange_defaults={
            'velocity': int(args.rearrange_velocity),
            'min_ms': int(args.rearrange_min_ms),
            'chord_ms': int(args.rearrange_chord_ms),
            'repeat_ms': int(args.rearrange_repeat_ms),
        },
    )

    app = create_app(config)
    port = choose_port(host, int(args.port))
    local_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host

    print()
    print('=' * 68, flush=True)
    print('  Transkun 转谱服务已启动（WebUI）', flush=True)
    print('  本机：    http://{}:{}/?t={}'.format(local_host, port, token),
          flush=True)
    if host in ('0.0.0.0', '::'):
        addresses = lan_addresses()
        if addresses:
            for address in addresses:
                print('  局域网：  http://{}:{}/?t={}'.format(address, port, token),
                      flush=True)
        else:
            print('  局域网：  没有找到非回环地址，检查网络连接。', flush=True)
    else:
        print('  局域网：  当前只监听本机；加 --lan 让局域网设备访问。', flush=True)
    print('  令牌：    {}'.format(token_path()), flush=True)
    print('  设备：    {}'.format(device_info), flush=True)
    print('  上限：    单文件 {:.2f} GB，时长 {:g} 分钟，队列 {}'.format(
        config.max_bytes / 2 ** 30, config.max_minutes, config.queue_limit),
        flush=True)
    print('=' * 68, flush=True)
    print('  Ctrl+C 停止。', flush=True)
    print()

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level='info', limit_concurrency=32)
    return 0
