# 第三方组件

Transkun 本仓库代码为 MIT（见 `LICENSE`）。分发包还包含以下组件，按各自许可证分发：

| 组件 | 用途 | 许可证 |
|---|---|---|
| [Transkun](https://github.com/Yujia-Yan/Transkun) | 钢琴转谱模型与权重 | MIT |
| [PyTorch](https://pytorch.org/) | 推理运行时 | BSD-3-Clause |
| [music21](https://web.mit.edu/music21/) | 乐谱分析、MusicXML | BSD-3-Clause |
| [Verovio](https://www.verovio.org/) | MusicXML → SVG | LGPL-3.0 |
| [CairoSVG](https://cairosvg.org/) | SVG → PDF | LGPL-3.0 |
| [pypdf](https://pypdf.readthedocs.io/) | PDF 合并 | BSD-3-Clause |
| [Noto Sans CJK SC](https://github.com/notofonts/noto-cjk) | 乐谱中文标题 | SIL OFL-1.1（`transkun_app/score/assets/OFL.txt`） |
| [FastAPI](https://fastapi.tiangolo.com/) / [Uvicorn](https://www.uvicorn.org/) | WebUI | MIT / BSD-3-Clause |
| [librosa](https://librosa.org/)（可选） | 后续节拍分析 | ISC |

Verovio、CairoSVG 为 LGPL：发行包中以独立 Python 包/动态库形式使用，未修改其源码；
如需替换版本，可重新安装对应 Python 包。Noto 字体的 OFL 文本随字体放在
`transkun_app/score/assets/OFL.txt`。
