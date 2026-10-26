#!/usr/bin/env python3
"""检查 shell 脚本里两类「只在 macOS 上炸」的坑。两种写法在 Linux 上都好好的，
所以本地开发和 Linux 打包全测不出来，必须靠静态检查兜住。

1. `$VAR` 紧跟多字节字符
   macOS 自带 bash 3.2，它把多字节字符的**字节**也算作变量名的一部分：

       log "安装（$ver）…"        # 变量名被当成 `ver）` → unbound variable

   写成 `${ver}` 就好。（bash 5 会正确地在多字节字符前断开变量名。）

2. `${` 后面紧跟空白（含换行）

       log "……整理版 ${
         stat -f%z "$out"} 字节"

   这是 `bad substitution`，而且 `bash -n` 查不出来，只有真正执行到才报错。

用法: python packaging/check-shell.py [文件…]
缺省检查 gui/*.sh、packaging/*.sh 与 .github/workflows/*.yml。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `$name` 后面直接跟一个非 ASCII 字节（也就是 UTF-8 多字节字符的首字节）。
BAD_NAME = re.compile(rb'\$[A-Za-z_][A-Za-z0-9_]*(?=[\x80-\xff])')
# `${` 后面直接跟空白（含跨行）。
BAD_SUBST = re.compile(rb'\$\{[ \t\r\n]')


def is_comment(line: bytes) -> bool:
  return line.lstrip().startswith(b'#')


def targets(argv: list[str]) -> list[Path]:
  if argv:
    return [Path(a) for a in argv]
  files = sorted(ROOT.glob('gui/*.sh')) + sorted(ROOT.glob('packaging/*.sh'))
  return files + sorted(ROOT.glob('.github/workflows/*.yml'))


def check(path: Path) -> list[str]:
  data = path.read_bytes()
  lines = data.split(b'\n')
  problems: list[str] = []

  for lineno, line in enumerate(lines, 1):
    if is_comment(line):
      continue
    for match in BAD_NAME.finditer(line):
      name = match.group(0).decode('utf-8', 'replace')
      problems.append(f'{path}:{lineno}: `{name}` 后面紧跟多字节字符，'
                      f'请写成 `${{{name[1:]}}}`\n'
                      f'    {line.decode("utf-8", "replace").strip()}')

  for match in BAD_SUBST.finditer(data):
    lineno = data.count(b'\n', 0, match.start()) + 1
    if is_comment(lines[lineno - 1]):
      continue
    problems.append(f'{path}:{lineno}: `${{` 后面不能有空白（bash 会报 '
                    f'bad substitution）\n'
                    f'    {lines[lineno - 1].decode("utf-8", "replace").strip()}')

  return problems


def main() -> int:
  problems: list[str] = []
  for path in targets(sys.argv[1:]):
    if path.is_file():
      problems += check(path)
  if problems:
    print('\n'.join(problems))
    print(f'\n发现 {len(problems)} 处有问题的写法。', file=sys.stderr)
    return 1
  print('shell 写法检查通过（变量名 + ${} 展开）。')
  return 0


if __name__ == '__main__':
  sys.exit(main())
