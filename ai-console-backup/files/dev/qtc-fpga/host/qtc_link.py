"""UART link protocol between the PC relay and the VCU118 miner (docs/uart_protocol.md).

Frame: A5 | TYPE | LEN | PAYLOAD[LEN] | CRC8(poly 0x07, init 0, over TYPE, LEN, PAYLOAD).
Multi-byte fields are big-endian.
"""
from dataclasses import dataclass

SYNC = 0xA5
# Mining clock table of the VCU118 bitstream (rtl/gen/mmcm_freq_vcu118.svh), index -> MHz.
FREQ_VCU118 = [240.0, 266.7, 282.4, 300.0, 309.7, 320.0, 331.0, 342.9, 355.6, 369.2, 384.0, 400.0]
# KV260 bitstream (rtl/gen/mmcm_freq_kv260.svh, 100 MHz PS clock in).
FREQ_KV260 = [200.0, 218.2, 240.0, 252.6, 266.7, 282.4, 300.0, 320.0]
FREQ_TABLES = {'vcu118': FREQ_VCU118, 'kv260': FREQ_KV260}   # the U250 uses the VCU118 table


def crc8(data, crc=0):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame(ftype, payload=b''):
    body = bytes([ord(ftype), len(payload)]) + payload
    return bytes([SYNC]) + body + bytes([crc8(body)])


def job_frame(job_id, target64, cnt_start, base):
    payload = bytes([job_id]) + target64.to_bytes(8, 'big') + cnt_start.to_bytes(8, 'big')
    payload += b''.join(v.to_bytes(8, 'big') for v in base)
    assert len(payload) == 113
    return frame('J', payload)


def run_frame(on):
    return frame('R', bytes([1 if on else 0]))


def freq_frame(idx):
    return frame('F', bytes([idx]))


def query_frame():
    return frame('Q')


def ping_frame():
    return frame('P')


def pong_pipes(p):
    """Permutation pipelines from a pong payload ('QTC', version, MHz): the version byte's high nibble is
    pipelines - 1 (VCU118 1, U250 2), or F for the folded core (one permutation per 8 clocks = 1/8 of a
    pipeline, KV260) or E (one per 4 clocks, ZCU102); the hash rate is pipelines * clock / 2."""
    return {0xF: 0.125, 0xE: 0.25}.get(p[3] >> 4, (p[3] >> 4) + 1)


def vid_frame(code):
    """VCCINT regulator output voltage as a VR12 VID code (vid_volts); the FPGA refuses codes outside its range."""
    return frame('V', bytes([code]))


def clear_faults_frame():
    return frame('K')


def vid_volts(code):
    """VR12 VID (MAX20751 VOUT_COMMAND / READ_VOUT): 0.25 V + (code - 1) * 5 mV, 0 = off."""
    return 0.25 + (code - 1) * 0.005 if code else 0.0


def volts_vid(v):
    return round((v - 0.25) / 0.005) + 1


@dataclass
class Status:
    hashes: int
    cnt_now: int
    job_now: int
    flags: int
    temp_raw: int
    vccint_raw: int
    crc_errors: int
    freq_mhz10: int = 0
    # Board power readings (VCU118 I2C: MAX20751 VCCINT regulator, INA226 monitor).
    # Status v3 (38 bytes) or v4 (45 bytes); pwr_ver says which, 0 = none.
    iout_raw: int = 0         # PMBus READ_IOUT, LINEAR11
    oc_limit_raw: int = 0     # v3 only: PMBus IOUT_OC_FAULT_LIMIT (the MAX20751 NACKs it: always 0)
    reg_temp_raw: int = 0     # v4: PMBus READ_TEMPERATURE_1, LINEAR11
    pmbus_status: int = 0     # PMBus STATUS_WORD
    ina_shunt_raw: int = 0    # INA226 shunt voltage, signed, 2.5 uV/LSB
    ina_bus_raw: int = 0      # INA226 bus voltage, 1.25 mV/LSB
    vout_cmd: int = 0         # v4: PMBus VOUT_COMMAND (VID code in the low byte)
    vout_mode: int = 0        # v4: PMBus VOUT_MODE (0x2x = VID)
    vout_vid: int = 0         # v4: PMBus READ_VOUT, VID code
    status_iout: int = 0      # v4: PMBus STATUS_IOUT
    status_temp: int = 0      # v4: PMBus STATUS_TEMPERATURE
    pwr_ok: int = 0           # error-free I2C polling rounds (8-bit counter; stops when stale)
    pwr_err: int = 0          # aborted I2C transactions (8-bit counter)
    pwr_wr: int = 0           # v4: voltage writes sent (4-bit counter)
    pwr_rej: int = 0          # v4: voltage requests refused by the FPGA's range checks (4-bit counter)
    pwr_ver: int = 0

    @property
    def freq_mhz(self):
        return self.freq_mhz10 / 10

    @property
    def freq_busy(self):
        return bool(self.flags & 16)

    @property
    def freq_fail(self):
        return bool(self.flags & 32)

    @property
    def running(self):
        return bool(self.flags & 1)

    @property
    def overflow(self):
        return bool(self.flags & 2)

    @property
    def over_temp(self):
        return bool(self.flags & 4)

    @property
    def watchdog(self):
        return bool(self.flags & 8)

    @property
    def temp_c(self):
        # UltraScale+ SYSMONE4, internal reference (UG580). Raw is the 16-bit left-aligned code.
        return self.temp_raw * 507.5921310 / 65536 - 279.42657680

    @property
    def vccint_v(self):
        return self.vccint_raw / 65536 * 3.0

    @property
    def iout_a(self):
        return linear11(self.iout_raw)

    @property
    def oc_limit_a(self):
        return linear11(self.oc_limit_raw)

    @property
    def has_pwr(self):
        return self.pwr_ver != 0

    @property
    def reg_temp_c(self):
        return linear11(self.reg_temp_raw)

    @property
    def vout_set_v(self):
        return vid_volts(self.vout_cmd & 0xFF)

    @property
    def vout_v(self):
        return vid_volts(self.vout_vid)

    def pwr_text(self):
        """One-line summary of the power readings for logs ('' when the bitstream has none)."""
        if self.pwr_ver == 4:
            return (f'VCCINT {self.iout_a:.1f} A, set {self.vout_set_v:.3f} V read {self.vout_v:.3f} V, '
                    f'regulator {self.reg_temp_c:.0f} C (INA226 {self.ina_shunt_mv:.3f} mV, '
                    f'pmbus status {self.pmbus_status:#06x} iout {self.status_iout:#04x} temp {self.status_temp:#04x}, '
                    f'i2c ok {self.pwr_ok} err {self.pwr_err}, vid wr {self.pwr_wr} rej {self.pwr_rej})')
        if self.pwr_ver == 3:
            return (f'VCCINT {self.iout_a:.1f} A (INA226 {self.ina_shunt_mv:.3f} mV, '
                    f'pmbus status {self.pmbus_status:#06x}, i2c ok {self.pwr_ok} err {self.pwr_err})')
        return ''

    @property
    def ina_shunt_mv(self):
        r = self.ina_shunt_raw
        return (r - 65536 if r & 0x8000 else r) * 2.5e-3

    @property
    def ina_bus_v(self):
        return self.ina_bus_raw * 1.25e-3


def linear11(w):
    """PMBus LINEAR11: 5-bit signed exponent over an 11-bit signed mantissa."""
    e = w >> 11
    m = w & 0x7FF
    return (m - 2048 if m & 0x400 else m) * 2.0 ** (e - 32 if e & 0x10 else e)


def parse_status(p):
    w = lambda i: int.from_bytes(p[i:i + 2], 'big')
    st = Status(int.from_bytes(p[0:8], 'big'), int.from_bytes(p[8:16], 'big'), p[16], p[17],
                w(18), w(20), w(22), w(24) if len(p) >= 26 else 0)
    if len(p) >= 45:
        (st.iout_raw, st.reg_temp_raw, st.pmbus_status, st.ina_shunt_raw, st.ina_bus_raw,
         st.vout_cmd) = (w(i) for i in range(26, 38, 2))
        st.vout_mode, st.vout_vid, st.status_iout, st.status_temp = p[38], p[39], p[40], p[41]
        st.pwr_ok, st.pwr_err, st.pwr_wr, st.pwr_rej, st.pwr_ver = p[42], p[43], p[44] >> 4, p[44] & 15, 4
    elif len(p) >= 38:
        st.iout_raw, st.oc_limit_raw, st.pmbus_status, st.ina_shunt_raw, st.ina_bus_raw = (w(i) for i in range(26, 36, 2))
        st.pwr_ok, st.pwr_err, st.pwr_ver = p[36], p[37], 3
    return st


class Decoder:
    """Incremental decoder for FPGA -> host bytes. feed() returns [(type, payload)];
    frames with a bad CRC are counted and dropped."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                self.buf.clear()
                return out
            del self.buf[:i]
            if len(self.buf) < 3:
                return out
            n = self.buf[2]
            if len(self.buf) < 4 + n:
                return out
            body = bytes(self.buf[1:3 + n])
            if crc8(body) == self.buf[3 + n]:
                out.append((chr(body[0]), body[2:]))
                del self.buf[:4 + n]
            else:
                self.bad += 1
                del self.buf[:1]           # resync on the next A5


def parse_candidate(p):
    return p[0], int.from_bytes(p[1:9], 'big')
