import copy
import json
import os
import tempfile
import threading
from pathlib import Path


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'nodes': [], 'subscriptions': [], 'selected': None}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def transaction(self, fn):
        with self.lock:
            candidate = copy.deepcopy(self.data)
            result = fn(candidate)
            atomic_json(self.path, candidate)
            self.data = candidate
            return result
