"""同步 PTY 回环：用模拟器充当真实的 485 总线。

模拟器与总线线程分处两端：总线写入从设备，模拟器在主设备侧解析并回送响应。
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from contextlib import suppress

from .simulator.base import VrfSimulator


class Loopback:
    """把一个模拟器接到伪终端上，形成可运行的 485 总线。"""

    def __init__(self, simulator_factory: Callable[[int], VrfSimulator]) -> None:
        """创建伪终端与模拟器。"""

        self.master_fd, self.slave_fd = os.openpty()
        os.set_blocking(self.master_fd, False)
        self.slave_path = os.ttyname(self.slave_fd)
        self.simulator = simulator_factory(self.master_fd)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动回环线程。"""

        self._thread = threading.Thread(
            target=self._run, name="vrf-loopback", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """停止回环并释放伪终端。"""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        for fd in (self.master_fd, self.slave_fd):
            with suppress(OSError):
                os.close(fd)

    def _run(self) -> None:
        """读取主机发出的报文并回送模拟器响应。"""

        while not self._stop.is_set():
            try:
                chunk = os.read(self.master_fd, 4096)
            except BlockingIOError:
                self._stop.wait(0.005)
                continue
            except OSError:
                break
            if not chunk:
                continue
            self.simulator.feed_bytes(chunk)
            while True:
                try:
                    response = self.simulator.response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    os.write(self.master_fd, response)
                except OSError:
                    return
