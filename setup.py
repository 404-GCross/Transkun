import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

requirements = [
"ncls",
"pretty_midi",
"scipy",
"torchaudio",
"torch",
"mir_eval",
"pydub",
"seaborn",
"matplotlib",
"tensorboard",
"tqdm",
"torch_optimizer",
"sox",
"soxr",
"moduleconf"
]

# The desktop/WebUI front end (transkun_app) is optional: `pip install .[app]`.
# It keeps the plain `pip install .` (upstream CLI) lean.
app_requirements = [
"fastapi",
"uvicorn",
"python-multipart",   # WebUI multipart uploads
"soundfile",          # wav/flac/ogg decoding without ffmpeg
"mido",               # MIDI post-processing / WebUI preview
]

setuptools.setup(
    name="transkun",
    version="0.2.0",
    author="Yujia Yan; 404-GCross fork",
    description='Transkun: automatic piano transcription (CLI, WebUI, desktop app)',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/404-GCross/Transkun",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    packages=setuptools.find_packages(),
    package_data={
        'transkun.pretrained': ['*.pt', '*.conf'],
        'transkun_app.webui': ['static/*'],
    },
    python_requires=">=3.10",
    entry_points={
        'console_scripts': [
            'transkun = transkun.transcribe:main',
            'transkunEval = transkun.computeMetrics:main',
            'transkun-app = transkun_app.cli:main',
        ]
    },
    install_requires=requirements,
    extras_require={
        'app': app_requirements,
        'dev': app_requirements + ['pytest', 'httpx'],
    },
)
