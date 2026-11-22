import os
import sys
import tempfile
from pathlib import Path

# Must run before `app` is imported anywhere: settings are read from env at import time.
os.environ["DEMO_MODE"] = "1"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["REFRESH_MINUTES"] = "999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
