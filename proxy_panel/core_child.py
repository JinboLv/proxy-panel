"""Guardian: tie a core to its owner's process (not its spawning thread)."""
import os
import select
import signal
import subprocess
import sys


def main():
    owner_fd = int(sys.argv[1])
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP]:
        signal.signal(sig, stop)
    if select.select([owner_fd], [], [], 0)[0]:
        return
    child = subprocess.Popen(sys.argv[2:])
    try:
        while child.poll() is None and not stopping:
            if select.select([owner_fd], [], [], .1)[0]:
                break
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
        os.close(owner_fd)


if __name__ == '__main__':
    main()
