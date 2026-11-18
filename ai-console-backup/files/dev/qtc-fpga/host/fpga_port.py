"""Byte transports to the miner: the real VCU118 over the CP2105 serial port, or FpgaSim, a
software model that speaks the same UART protocol (docs/uart_protocol.md) and hashes with the
Python reference. Both expose write(bytes) and read(timeout) -> bytes.
"""
import queue
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ref_model'))
import qpow_ref as q   # noqa: E402
import qtc_link as L   # noqa: E402

M64 = (1 << 64) - 1


class SerialPort:
    def __init__(self, port, baud=115200):
        import serial
        from serial.tools import list_ports
        self.ser = serial.Serial(port, baud, timeout=0.05)
        # FTDI ports (KV260, U250 FT4232H) poll; the CP210x boards (VCU118, ZCU102) keep the read they ran with
        self.poll = any(p.device == port and p.vid == 0x0403 for p in list_ports.comports())

    def write(self, data):
        self.ser.write(data)

    def read(self, timeout=0.05):
        if not self.poll:
            self.ser.timeout = timeout
            first = self.ser.read(1)
            return first + self.ser.read(self.ser.in_waiting) if first else b''
        # Poll instead of setting ser.timeout per call: on Windows that setter reapplies the whole port
        # setup (SetCommState), and an FT4232H then corrupts ~2 % of the bytes in flight
        # (11/04, KV260 R5 echo test: 1.8 % that way, 0 % with the timeout left alone).
        end = time.monotonic() + timeout
        while True:
            n = self.ser.in_waiting
            if n:
                return self.ser.read(n)
            if time.monotonic() >= end:
                return b''
            time.sleep(0.002)

    def close(self):
        self.ser.close()


def bswap32(v):
    return int.from_bytes(v.to_bytes(4, 'big'), 'little')


def p4_top64(base, cnt):
    """What the chip computes per nonce: bswap64(canonical P4 state[0])."""
    s = list(base)
    s[6] = (s[6] + bswap32(cnt >> 32)) % q.P
    s[7] = (s[7] + bswap32(cnt & 0xFFFF_FFFF)) % q.P
    s = q.permute(s)
    s[0] = (s[0] + 1) % q.P
    s[1] = (s[1] + 1) % q.P
    return int.from_bytes(q.permute(s)[0].to_bytes(8, 'little'), 'big')


class FpgaSim:
    """Behavioural model of qpow_sys: frame parser, job registers, nonce counter, candidate
    FIFO (16 deep, sticky overflow), status/ping replies plus a status frame every status_s,
    60 s host watchdog, over-temperature gate with hysteresis (stop >= 85 C, resume <= 75 C). fault_rate corrupts that fraction of results to exercise the relay's checks."""

    FIFO_DEPTH = 16

    TEMP_STOP, TEMP_RESUME = 47052, 45761      # 85 C / 75 C, as qpow_sys

    def __init__(self, freq_idx=8, temp_c=45.0, fault_rate=0.0, seed=1, watchdog_s=60.0, status_s=1.0,
                 fault_above_mhz=None):
        self.rx = bytearray()
        self.out = queue.Queue()
        self.lock = threading.Lock()
        self.base, self.target64, self.cnt, self.job = [0] * 12, 0, 0, 0
        self.run_req = self.ot = self.wd = self.overflow = False
        self.hashes = self.crc_errors = 0
        self.fifo = []
        self.freq_idx, self.temp_c, self.fault_rate = freq_idx, temp_c, fault_rate
        self.reg_c = 55.0                          # VCCINT regulator temperature, C
        self.fault_above_mhz = fault_above_mhz     # model timing errors above this clock
        self.rng = random.Random(seed)
        self.watchdog_s, self.last_frame = watchdog_s, time.monotonic()
        self.status_s, self.last_status = status_s, time.monotonic()
        self.stop_flag = False
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    # ---- transport ----
    def write(self, data):
        with self.lock:
            self.rx += data
            self._parse()

    def read(self, timeout=0.05):
        try:
            chunks = [self.out.get(timeout=timeout)]
        except queue.Empty:
            return b''
        while not self.out.empty():
            chunks.append(self.out.get_nowait())
        return b''.join(chunks)

    def close(self):
        self.stop_flag = True
        self.thread.join(timeout=2)

    # ---- device ----
    @property
    def temp_raw(self):
        return int(round((self.temp_c + 279.42657680) * 65536 / 507.5921310)) & 0xFFFF

    def _parse(self):
        while True:
            i = self.rx.find(L.SYNC)
            if i < 0:
                self.rx.clear()
                return
            del self.rx[:i]
            if len(self.rx) < 3 or len(self.rx) < 4 + self.rx[2]:
                return
            n = self.rx[2]
            body, crc = bytes(self.rx[1:3 + n]), self.rx[3 + n]
            del self.rx[:4 + n]
            if L.crc8(body) != crc:
                self.crc_errors += 1
                continue
            self._frame(chr(body[0]), body[2:])

    def _frame(self, t, p):
        self.last_frame = time.monotonic()
        if t == 'J' and len(p) == 113:
            self.job = p[0]
            self.target64 = int.from_bytes(p[1:9], 'big')
            self.cnt = int.from_bytes(p[9:17], 'big')
            self.base = [int.from_bytes(p[17 + 8 * i:25 + 8 * i], 'big') for i in range(12)]
            self.overflow = False
        elif t == 'R' and len(p) == 1:
            self.run_req, self.wd = bool(p[0] & 1), False
        elif t == 'F' and len(p) == 1:
            if p[0] < len(L.FREQ_VCU118):
                self.freq_idx = p[0]
        elif t == 'Q' and not p:
            self.out.put(L.frame('S', self._status()))
        elif t == 'V' and len(p) == 1:
            if 0x5F <= p[0] <= 0x79:                              # same range check as the FPGA
                self.vid, self.vid_wr = p[0], (self.vid_wr + 1) & 15
            else:
                self.vid_rej = (self.vid_rej + 1) & 15
        elif t == 'P' and not p:
            self.out.put(L.frame('P', b'QTC\x04' + int(self.mhz).to_bytes(2, 'big')))

    def _status(self):
        flags = self.running | self.overflow << 1 | self.ot << 2 | self.wd << 3
        return (self.hashes.to_bytes(8, 'big') + self.cnt.to_bytes(8, 'big') + bytes([self.job, flags])
                + self.temp_raw.to_bytes(2, 'big') + (18500).to_bytes(2, 'big') + self.crc_errors.to_bytes(2, 'big')
                + int(round(self.mhz * 10)).to_bytes(2, 'big') + self._pwr())

    vid, vid_wr, vid_rej, pwr_rounds = 0x79, 0, 0, 0

    def _pwr(self):
        # Status v4 power fields as on the VCU118 (measured 10/28: 20 A idle, ~85 A mining at 282.4 MHz)
        amps = 20 + (0.23 * self.mhz if self.running else 0)
        iout = (0b11111 << 11) | int(round(amps * 2))            # LINEAR11, exponent -1
        rtemp = (0b11111 << 11) | int(round(2 * self.reg_c))      # LINEAR11, exponent -1
        self.pwr_rounds = (self.pwr_rounds + 1) & 0xFF
        words = (iout, rtemp, 0, int(round(amps * 0.55 / 2.5e-3)) & 0xFFFF, int(0.853 / 1.25e-3), self.vid)
        return (b''.join(x.to_bytes(2, 'big') for x in words)
                + bytes([0x22, self.vid - 1, 0, 0, self.pwr_rounds, 0, self.vid_wr << 4 | self.vid_rej]))

    @property
    def mhz(self):
        return L.FREQ_VCU118[self.freq_idx]

    @property
    def running(self):
        return self.run_req and not self.ot and not self.wd

    def _worker(self):
        while not self.stop_flag:
            with self.lock:
                if time.monotonic() - self.last_frame > self.watchdog_s:
                    self.wd = True
                if self.temp_raw >= self.TEMP_STOP or self.temp_raw < 30914:
                    self.ot = True
                elif self.temp_raw <= self.TEMP_RESUME:
                    self.ot = False
                if self.status_s and time.monotonic() - self.last_status >= self.status_s:
                    self.last_status = time.monotonic()
                    self.out.put(L.frame('S', self._status()))
                if not self.running:
                    pending = None
                else:
                    pending = (list(self.base), self.target64, self.cnt, self.job)
                    self.cnt = (self.cnt + 64) & M64
            if pending is None:
                time.sleep(0.01)
                continue
            base, tgt, cnt0, job = pending
            hits = []
            for k in range(64):
                cnt = (cnt0 + k) & M64
                top = p4_top64(base, cnt)
                rate = self.fault_rate
                if self.fault_above_mhz is not None and self.mhz > self.fault_above_mhz:
                    rate = max(rate, 0.01 * (self.mhz - self.fault_above_mhz))
                if rate and self.rng.random() < rate:
                    top = 0                    # a wrong "hit" the relay must catch
                if top <= tgt:
                    hits.append(cnt)
            time.sleep(0.002)                   # let the relay's event loop run (GIL)
            with self.lock:
                self.hashes += 64
                for cnt in hits:
                    if len(self.fifo) < self.FIFO_DEPTH:
                        self.fifo.append((job, cnt))
                    else:
                        self.overflow = True
                while self.fifo:                # UART is far faster than this model
                    j, c = self.fifo.pop(0)
                    self.out.put(L.frame('C', bytes([j]) + c.to_bytes(8, 'big')))
