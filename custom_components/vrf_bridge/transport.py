"""VRF 传输层：串口（USB / RS232）与局域网 TCP / UDP。

所有传输实现都运行在总线工作线程内，因此可以使用阻塞式 API；
``read`` 在超时后返回空字节，不会长时间占用线程。
"""

from __future__ import annotations

import logging
import socket
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any

from .const import (
    SERIAL_READ_TIMEOUT,
    SERIAL_RTS_LEVEL,
    SERIAL_STOPBITS,
    SERIAL_WRITE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

CONNECTION_SERIAL = "serial"
CONNECTION_TCP = "tcp"
CONNECTION_UDP = "udp"


class VrfTransport(ABC):
    """一条 VRF 通讯链路的抽象。"""

    def __init__(self) -> None:
        """初始化传输对象。"""

        self._handle: Any = None

    @abstractmethod
    def open(self) -> None:
        """建立连接（已连接时直接返回）。"""

    @abstractmethod
    def close(self) -> None:
        """断开连接。"""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """发送数据。"""

    @abstractmethod
    def read(self, size: int = 64) -> bytes:
        """读取数据；超时返回空字节。"""

    def reset_input(self) -> None:  # noqa: B027 - 并非所有传输都需要清空接收缓存
        """丢弃接收缓冲区中的残留数据。"""

    @property
    def is_open(self) -> bool:
        """返回连接是否可用。"""

        return self._handle is not None


class SerialTransport(VrfTransport):
    """本地串口：USB-RS485 转换器或 RS232 端口。"""

    def __init__(
        self,
        port: str,
        baud_rate: int,
        stopbits: int = SERIAL_STOPBITS,
        rts_level: bool = SERIAL_RTS_LEVEL,
    ) -> None:
        """记录串口参数。"""

        super().__init__()
        self.port = port
        self.baud_rate = baud_rate
        self.stopbits = stopbits
        self.rts_level = rts_level

    def open(self) -> None:
        """打开串口。"""

        if self._handle is not None:
            return
        import serial

        stopbits = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }.get(self.stopbits, serial.STOPBITS_ONE)
        self._handle = serial.Serial(
            self.port,
            self.baud_rate,
            timeout=SERIAL_READ_TIMEOUT,
            write_timeout=SERIAL_WRITE_TIMEOUT,
            stopbits=stopbits,
        )
        # 部分 232/485 转换器以 RTS 作为发送使能，需常高。
        if self.rts_level:
            with suppress(Exception):
                self._handle.rts = True
        _LOGGER.debug("串口已打开 %s（%d bps）", self.port, self.baud_rate)

    def close(self) -> None:
        """关闭串口。"""

        if self._handle is not None:
            with suppress(Exception):  # 关闭失败无需处理
                self._handle.close()
            self._handle = None

    def write(self, data: bytes) -> None:
        """发送数据。"""

        if self._handle is None:
            raise ConnectionError("串口未连接")
        self._handle.write(data)
        self._handle.flush()

    def read(self, size: int = 64) -> bytes:
        """读取数据。"""

        if self._handle is None:
            raise ConnectionError("串口未连接")
        return self._handle.read(size)

    def reset_input(self) -> None:
        """清空串口接收缓存。"""

        if self._handle is not None:
            with suppress(Exception):  # 部分驱动不支持清空缓存
                self._handle.reset_input_buffer()


class TcpTransport(VrfTransport):
    """局域网 TCP 透传（串口转网络模块）。"""

    def __init__(self, host: str, port: int, timeout: float = 2.0) -> None:
        """记录连接参数。"""

        super().__init__()
        self.host = host
        self.port = port
        self.timeout = timeout

    def open(self) -> None:
        """连接目标主机。"""

        if self._handle is not None:
            return
        self._handle = socket.create_connection((self.host, self.port), self.timeout)
        self._handle.settimeout(SERIAL_READ_TIMEOUT)
        _LOGGER.debug("TCP 已连接 %s:%s", self.host, self.port)

    def close(self) -> None:
        """关闭连接。"""

        if self._handle is not None:
            with suppress(Exception):  # 关闭失败无需处理
                self._handle.close()
            self._handle = None

    def write(self, data: bytes) -> None:
        """发送数据。"""

        if self._handle is None:
            raise ConnectionError("TCP 未连接")
        self._handle.sendall(data)

    def read(self, size: int = 64) -> bytes:
        """读取数据。"""

        if self._handle is None:
            raise ConnectionError("TCP 未连接")
        try:
            return self._handle.recv(size)
        except TimeoutError:
            return b""
        except OSError:
            raise ConnectionError("TCP 连接已断开") from None


class UdpTransport(VrfTransport):
    """局域网 UDP 透传（串口转网络模块）。"""

    def __init__(self, host: str, port: int, local_port: int = 0) -> None:
        """记录连接参数。"""

        super().__init__()
        self.host = host
        self.port = port
        self.local_port = local_port

    def open(self) -> None:
        """绑定本地端口。"""

        if self._handle is not None:
            return
        self._handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._handle.bind(("0.0.0.0", self.local_port))
        self._handle.settimeout(SERIAL_READ_TIMEOUT)
        _LOGGER.debug("UDP 已绑定 %s:%s", self.host, self.port)

    def close(self) -> None:
        """关闭套接字。"""

        if self._handle is not None:
            with suppress(Exception):  # 关闭失败无需处理
                self._handle.close()
            self._handle = None

    def write(self, data: bytes) -> None:
        """发送数据。"""

        if self._handle is None:
            raise ConnectionError("UDP 未打开")
        self._handle.sendto(data, (self.host, self.port))

    def read(self, size: int = 64) -> bytes:
        """接收数据。"""

        if self._handle is None:
            raise ConnectionError("UDP 未打开")
        try:
            data, _address = self._handle.recvfrom(size)
        except TimeoutError:
            return b""
        except OSError:
            raise ConnectionError("UDP 接收失败") from None
        return data

    def reset_input(self) -> None:
        """丢弃已排队的报文。"""

        if self._handle is None:
            return
        try:
            self._handle.setblocking(False)
            while True:
                try:
                    self._handle.recvfrom(1024)
                except (BlockingIOError, OSError):
                    break
        finally:
            self._handle.setblocking(True)
            self._handle.settimeout(SERIAL_READ_TIMEOUT)
