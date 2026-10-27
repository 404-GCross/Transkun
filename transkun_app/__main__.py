"""``python -m transkun_app`` 等价于 ``transkun-app``。"""

import sys

from transkun_app.cli import main

sys.exit(main())
