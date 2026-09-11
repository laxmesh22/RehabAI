"""Live IMU transports. Missing hardware fails closed; it never becomes simulation."""
import socket
from edge.imu.packets import lost_packet, parse_line


class UdpImu:
    def __init__(self, host='127.0.0.1', port=8766, placement='arm', stale_s=0.5):
        self.placement = placement
        self.stale_s = stale_s
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        self.last = None
        self.last_time = None

    def read(self, timestamp):
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                return lost_packet('live', self.placement)
            try:
                self.last = parse_line(data, source='live', default_placement=self.placement)
                self.last_time = timestamp
            except (ValueError, KeyError, TypeError):
                continue
        if self.last is None or self.last_time is None or (timestamp - self.last_time) > self.stale_s:
            return lost_packet('live', self.placement)
        packet = dict(self.last)
        packet['timestamp'] = timestamp
        return packet

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class SerialImu:
    def __init__(self, port, baud=115200, placement='arm', stale_s=0.5):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError('pyserial is required for REHABAI_IMU_TRANSPORT=serial') from exc
        self.placement = placement
        self.stale_s = stale_s
        self.serial = serial.Serial(port, baud, timeout=0)
        self.last = None
        self.last_time = None
        self.buffer = ''

    def read(self, timestamp):
        try:
            waiting = self.serial.in_waiting
            if waiting:
                chunk = self.serial.read(waiting).decode('utf-8', errors='replace')
                self.buffer += chunk
        except OSError:
            return lost_packet('live', self.placement)
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            try:
                self.last = parse_line(line, source='live', default_placement=self.placement)
                self.last_time = timestamp
            except (ValueError, KeyError, TypeError):
                continue
        if self.last is None or self.last_time is None or (timestamp - self.last_time) > self.stale_s:
            return lost_packet('live', self.placement)
        packet = dict(self.last)
        packet['timestamp'] = timestamp
        return packet

    def close(self):
        try:
            self.serial.close()
        except OSError:
            pass
