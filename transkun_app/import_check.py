#!/usr/bin/env python3
"""依赖自检：把运行时需要的模块都 import 一遍。

安装器、build-runtime.sh、AppImage 构建都会跑它；缺任何一项都非零退出。
不加载模型，几秒钟结束。
"""

from __future__ import annotations

import importlib
import sys

MODULES = [
    'numpy', 'scipy', 'torch', 'torchaudio', 'moduleconf',
    'pretty_midi', 'mido', 'pydub', 'soxr', 'soundfile',
    'mir_eval', 'ncls',
    'transkun.ModelTransformer', 'transkun.Data', 'transkun.Util',
    'transkun_app', 'transkun_app.backend', 'transkun_app.midi_rearrange',
    'fastapi', 'uvicorn', 'multipart', 'starlette',
]


def main(argv=None) -> int:
    failed = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 汇总后一起报
            failed.append((name, exc))
            print(f'  [FAIL] {name}: {type(exc).__name__}: {exc}')
        else:
            print(f'  [OK]   {name}')

    try:
        from transkun_app import backend
        info = backend.model_info()
        if info['available']:
            size = info['weight_bytes'] or 0
            print(f'  [OK]   权重 {info["weight"]} '
                  f'({size / 2 ** 20:.0f} MiB)')
        else:
            failed.append(('权重', FileNotFoundError(info['weight'])))
            print(f'  [FAIL] 权重不存在: {info["weight"]}')
    except Exception as exc:  # noqa: BLE001
        failed.append(('transkun_app.backend', exc))
        print(f'  [FAIL] transkun_app.backend: {type(exc).__name__}: {exc}')

    if failed:
        print(f'\n{len(failed)} 项失败。', file=sys.stderr)
        return 1
    print('\n依赖自检通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
