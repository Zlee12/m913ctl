"""Drive the TUI in a pseudo-terminal and return what the screen shows.

Used by tests/test_tui.py (skipped when pyte isn't installed).
"""

import os
import pty
import select
import struct
import sys
import termios
import fcntl
import time

import pyte

KEYS = {"<up>": "\x1bOA", "<down>": "\x1bOB", "<right>": "\x1bOC", "<left>": "\x1bOD",
        "<enter>": "\r", "<esc>": "\x1b", "<tab>": "\t", "<del>": "\x1b[3~", "<bs>": "\x7f"}


class Term:
    def __init__(self, argv, cols=110, rows=32, env=None):
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        pid, fd = pty.fork()
        if pid == 0:
            e = dict(os.environ, TERM="xterm-256color", ESCDELAY="25", **(env or {}))
            os.execvpe(argv[0], argv, e)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.pump(1.0)

    def pump(self, t=0.4):
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                self.stream.feed(data)

    def send(self, *keys, wait=0.35):
        for k in keys:
            os.write(self.fd, KEYS.get(k, k).encode())
            self.pump(wait)

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def close(self):
        try:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


if __name__ == "__main__":
    t = Term([sys.executable, "-m", "m913ctl", "--sensor", "3335", "tui", sys.argv[1]])
    t.send(*sys.argv[2:])
    print(t.text())
    t.close()
