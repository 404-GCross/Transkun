#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Adapted from the MT3 Transcriber project (Apache-2.0), 404-GCross.

"""Make the standalone Python's Tk use an Xft-capable Tcl/Tk.

The Tk bundled with the uv / python-build-standalone interpreter is built
without Xft/fontconfig.  As a result Tk cannot see system fonts, so CJK (and
other fontconfig) text renders blank -- only the core X fonts are available.
See https://github.com/astral-sh/python-build-standalone/issues/740.

The replacement libraries come from either:

* a Tcl/Tk prefix we build ourselves (``--source DIR``), which is the reliable
  path: uv's standalone Python now bundles Tcl/Tk **9.0** (``libtcl9.0.so`` and
  the upstream Tk-9 name ``libtcl9tk9.0.so``), which most systems do not ship;
  or
* the system libraries, matching the bundled major.minor (``--source`` omitted).

Two strategies:

* ``--mode symlink`` (default, for writable installs): replace the bundled
  ``libtcl*`` / ``libtk*`` with symlinks to the source libraries and relax the
  exact Tcl version check in ``init.tcl``.
* ``--mode copy`` (for read-only bundles such as an AppImage): copy the source
  libraries and their dependencies into the bundle instead, so it stays
  self-contained and does not need Tcl/Tk on the target system.

Usage::

    python3 fix_tk_system.py --python /path/to/venv/bin/python
    python3 fix_tk_system.py --python ... --source /path/to/tcltk --mode copy
    python3 fix_tk_system.py --python ... --reverse
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM_LIB_DIRS = (
    Path('/usr/lib64'),                 # Fedora / RHEL
    Path('/usr/lib/x86_64-linux-gnu'),  # Debian / Ubuntu
    Path('/usr/lib/aarch64-linux-gnu'),
    Path('/usr/lib'),
)

# 由目标系统的 glibc / 动态加载器提供，不能、也不必打进包里。
GLIBC_PREFIXES = (
    'libc.so', 'libm.so', 'libdl.so', 'libpthread.so', 'librt.so',
    'libresolv.so', 'libutil.so', 'libnsl.so', 'ld-linux',
)

INSTALL_HINTS = {
    'dnf': 'sudo dnf install tcl tk',
    'apt': 'sudo apt install libtcl9.0 libtk9.0',
    'pacman': 'sudo pacman -S tcl tk',
    'zypper': 'sudo zypper install tcl tk',
}


def map_tk_name(name):
  """Alternative names a distributed Tk 9 library might use.

  Tk 9 ships its shared library as ``libtcl9tk9.0.so`` (it encodes the Tcl
  major version).  Some distributions keep that name (Fedora), others rename it
  to ``libtk9.0.so``.  Returns a list of candidate file names to try, the real
  name first.
  """
  candidates = [name]
  m = re.match(r'libtcl(\d+)tk(\d+\.\d+)(\.so.*)?$', name)
  if m:
    candidates.append(f'libtk{m.group(2)}.so')
  return candidates


def python_base_prefix(python):
  try:
    out = subprocess.check_output(
        [str(python), '-c', 'import sys; print(sys.base_prefix)'],
        text=True, stderr=subprocess.DEVNULL).strip()
  except (OSError, subprocess.CalledProcessError):
    return None
  return Path(out) if out else None


def _lib_stem(name):
  """``libtcl9tk9.0.so`` -> ``libtcl9tk9.0`` (the part before ``.so``)."""
  index = name.find('.so')
  return name[:index] if index != -1 else name


def find_system_lib(name):
  """Locate a system library matching ``name`` in the same major.minor series.

  Restricting to the same series (e.g. ``libtk8.6``) avoids pairing a Tk 8.6
  tkinter with a Tk 9.0 shared library.  Both the exact name (Fedora keeps
  ``libtcl9tk9.0.so``) and the renamed variant (``libtk9.0.so``) are tried.
  """
  for candidate_name in map_tk_name(name):
    stem = _lib_stem(candidate_name)
    for directory in SYSTEM_LIB_DIRS:
      if not directory.is_dir():
        continue
      exact = directory / candidate_name
      if exact.exists():
        return exact
      matches = sorted(directory.glob(stem + '*.so*'))
      if matches:
        return matches[0]
  return None


def find_source_lib(name, source):
  """Locate ``name`` inside a Tcl/Tk prefix we built (``source/lib``)."""
  lib_dir = source / 'lib'
  for candidate_name in map_tk_name(name):
    exact = lib_dir / candidate_name
    if exact.exists():
      return exact
    matches = sorted(lib_dir.glob(_lib_stem(candidate_name) + '*.so*'))
    if matches:
      return matches[0]
  return None


def copy_dependencies(library, dest_dir, log):
  """Copy the non-glibc shared libraries ``library`` needs into ``dest_dir``."""
  try:
    output = subprocess.check_output(
        ['ldd', str(library)], text=True, stderr=subprocess.DEVNULL)
  except (OSError, subprocess.CalledProcessError):
    return
  for line in output.splitlines():
    line = line.strip()
    if '=>' not in line:
      continue
    path = line.split('=>', 1)[1].strip().split(' ')[0]
    if not path or not path.startswith('/'):
      continue
    name = Path(path).name
    if name.startswith(GLIBC_PREFIXES):
      continue
    target = dest_dir / name
    if target.exists():
      continue
    try:
      shutil.copy2(path, target)
      log(f'[tk]   复制依赖 {name}')
    except OSError as exc:
      log(f'[tk]   依赖复制失败 {name}: {exc}')


def swap_library(target, system, mode, log):
  """Replace ``target`` with the system library (symlink or copy)."""
  if mode == 'copy':
    shutil.copy2(system, target)
    log(f'[tk] {target.name} <- {system}')
    return
  backup = target.with_name(target.name + '.uv_backup')
  if not backup.exists():
    shutil.move(str(target), str(backup))
  elif target.exists() or target.is_symlink():
    target.unlink()
  target.symlink_to(system)
  log(f'[tk] {target.name} -> {system}')


def relax_tcl_version(lib_dir, log):
  # The system Tcl may be a different patch release than the bundled scripts;
  # drop the strict `package require -exact Tcl X.Y.Z` check so it still loads.
  changed = 0
  for tcl_dir in lib_dir.glob('tcl[0-9]*'):
    init = tcl_dir / 'init.tcl'
    if not init.exists():
      continue
    text = init.read_text(encoding='utf-8', errors='replace')
    patched = re.sub(
        r'^(\s*)(package\s+require\s+-exact\s+Tcl\s+[\d.]+)(.*)$',
        r'\1# \2\3 # patched by transkun', text, flags=re.M)
    if patched != text:
      backup = init.with_name('init.tcl.uv_backup')
      if not backup.exists():
        shutil.copy2(init, backup)
      init.write_text(patched, encoding='utf-8')
      log(f'[tk] 已放宽版本校验: {init}')
      changed += 1
  return changed


def process(python, reverse=False, mode='symlink', source=None, deps_dir=None,
            log=print):
  base = python_base_prefix(python)
  if base is None or not (base / 'lib').is_dir():
    log('[tk] 无法确定 Python 安装目录，跳过。')
    return 0
  lib_dir = base / 'lib'
  deps_target = Path(deps_dir) if deps_dir else lib_dir
  changed = 0
  for target in (sorted(lib_dir.glob('libtcl*.so*'))
                 + sorted(lib_dir.glob('libtk*.so*'))):
    if target.name.endswith('.uv_backup'):
      continue
    backup = target.with_name(target.name + '.uv_backup')
    if reverse:
      if backup.exists():
        if target.exists() or target.is_symlink():
          target.unlink()
        shutil.move(str(backup), str(target))
        log(f'[tk] 还原 {target.name}')
        changed += 1
      continue
    if source is not None:
      replacement = find_source_lib(target.name, Path(source))
    else:
      replacement = find_system_lib(target.name)
    if replacement is None:
      log(f'[tk] 未找到可替换库，保留自带版本: {target.name}')
      continue
    swap_library(target, replacement, mode, log)
    if mode == 'copy':
      deps_target.mkdir(parents=True, exist_ok=True)
      copy_dependencies(replacement, deps_target, log)
    changed += 1
  changed += relax_tcl_version(lib_dir, log)
  return changed


def install_hint():
  for manager, command in INSTALL_HINTS.items():
    if shutil.which(manager):
      return command
  return 'install Tcl/Tk 9.0'


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--python', default=sys.executable,
                      help='interpreter whose bundled Tk should be swapped')
  parser.add_argument('--source', metavar='DIR',
                      help='Tcl/Tk prefix to copy from (its lib/ dir is used); '
                           'omit to use the system libraries')
  parser.add_argument('--deps-dir', metavar='DIR',
                      help='where to copy shared-library dependencies in copy '
                           'mode (default: the interpreter lib directory)')
  parser.add_argument('--mode', choices=('symlink', 'copy'), default='symlink',
                      help='symlink to the libraries, or copy them into the '
                           'bundle')
  parser.add_argument('--reverse', action='store_true',
                      help='restore the original bundled libraries')
  parser.add_argument('--quiet', action='store_true')
  args = parser.parse_args(argv)
  log = (lambda *_: None) if args.quiet else print
  changed = process(Path(args.python), reverse=args.reverse, mode=args.mode,
                    source=args.source, deps_dir=args.deps_dir, log=log)
  if changed == 0 and not args.reverse:
    log('[tk] 未找到可替换的 Tcl/Tk 库；中文可能显示为空白。')
    if args.source:
      log('[tk] 请确认 --source 指向的 Tcl/Tk 前缀包含 lib/libtcl9.0.so。')
    else:
      log('[tk] 请安装后重试: ' + install_hint())
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
