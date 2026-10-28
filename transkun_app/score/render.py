"""MusicXML → SVG / PDF（Verovio + cairosvg + pikepdf）。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

ASSET_DIR = Path(__file__).resolve().parent / 'assets'
BUNDLED_FONT = ASSET_DIR / 'NotoSansCJKsc-Regular.otf'

PAGE_SIZES = {
    'A4': (2100, 2970),
    'Letter': (2160, 2790),
}


def _cache_dir() -> Path:
    base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    path = base / 'transkun' / 'score'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _font_family() -> str:
    if sys.platform.startswith('darwin'):
        return 'PingFang SC'
    if BUNDLED_FONT.exists():
        return 'Noto Sans CJK SC'
    return 'DejaVu Serif'


def _prepare_fontconfig() -> Optional[Path]:
    """给 cairosvg/fontconfig 一份包含随包字体的配置。"""
    if not BUNDLED_FONT.exists() or sys.platform.startswith('darwin'):
        return None
    try:
        cache = _cache_dir()
        conf = cache / 'fonts.conf'
        conf.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            '<fontconfig>\n'
            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
            f'  <dir>{ASSET_DIR}</dir>\n'
            f'  <cachedir>{cache / "fontconfig"}</cachedir>\n'
            '</fontconfig>\n', encoding='utf-8')
    except OSError:
        return None
    os.environ.setdefault('FONTCONFIG_FILE', str(conf))
    return conf


def render_svg(musicxml: Path, out_base: Path, *, page_size='A4',
               scale=40) -> tuple[list, list, int]:
    """返回 (SVG 路径列表, 警告列表, 页数)。"""
    import verovio

    warnings = []
    _prepare_fontconfig()
    width, height = PAGE_SIZES.get(page_size, PAGE_SIZES['A4'])
    toolkit = verovio.toolkit()
    toolkit.loadData(Path(musicxml).read_text(encoding='utf-8'))
    toolkit.setOptions({
        'pageWidth': width,
        'pageHeight': height,
        'pageMarginTop': 150,
        'pageMarginBottom': 150,
        'pageMarginLeft': 150,
        'pageMarginRight': 150,
        'scale': scale,
        'adjustPageHeight': False,
        'footer': 'auto',
        'header': 'auto',
        'breaks': 'auto',
        'svgCss': f'text, tspan {{ font-family: "{_font_family()}"; }}',
        'smuflTextFont': 'embedded',
    })
    pages = int(toolkit.getPageCount())
    if pages <= 0:
        raise RuntimeError('Verovio 没有生成任何页面')
    svg_paths = []
    for page in range(1, pages + 1):
        svg = toolkit.renderToSVG(page)
        path = out_base.parent / f'{out_base.name}.page{page}.svg'
        path.write_text(svg, encoding='utf-8')
        svg_paths.append(path)
    if pages > 1:
        warnings.append(f'共 {pages} 页')
    return svg_paths, warnings, pages


def render_pdf(svg_paths: list, pdf_path: Path) -> tuple[Optional[Path], list]:
    """SVG 逐页转 PDF 后合并；依赖缺失时返回 None 而不是让整个任务失败。"""
    warnings = []
    try:
        import cairosvg
        import pikepdf
    except Exception as exc:  # noqa: BLE001
        return None, [f'PDF 渲染依赖缺失（{exc}）；已保留 SVG/MusicXML。']

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='transkun-score-pdf-') as temp:
        page_pdfs = []
        for index, svg_path in enumerate(svg_paths, 1):
            page_pdf = Path(temp) / f'page{index}.pdf'
            try:
                cairosvg.svg2pdf(bytestring=svg_path.read_bytes(),
                                 write_to=str(page_pdf))
            except Exception as exc:  # noqa: BLE001
                return None, [f'第 {index} 页 PDF 转换失败（{exc}）；已保留 SVG/MusicXML。']
            page_pdfs.append(page_pdf)
        try:
            merged = pikepdf.Pdf.new()
            for page_pdf in page_pdfs:
                with pikepdf.open(str(page_pdf)) as source:
                    merged.pages.extend(source.pages)
            merged.save(str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            return None, [f'PDF 合并失败（{exc}）；已保留 SVG/MusicXML。']
    return pdf_path, warnings
