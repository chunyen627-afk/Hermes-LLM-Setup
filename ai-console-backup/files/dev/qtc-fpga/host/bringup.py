"""First check after programming the VCU118, before any pool connection.

  1. find the miner's UART (pings every CP210x port, or the one given)
  2. ping: protocol version and current mining clock
  3. status sanity: temperature, VCCINT, flags
  4. switch the mining clock (default 300 MHz) and mine a random local job; every candidate is
     re-hashed with the Python reference (known-answer check), hashrate must be ~clock/2
Prints PASS/FAIL per step; exit code 0 only when all pass.

  python bringup.py --fpga auto [--freq 300] [--seconds 30]
  python bringup.py --fpga sim                 (exercise the script without a board)
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]
import qtc_link as L                         # noqa: E402
from fpga_port import FpgaSim, SerialPort    # noqa: E402
from freq_sweep import run_step              # noqa: E402

TEMP_OK = (5.0, 80.0)       # C, at idle
VCCINT_OK = (0.80, 0.90)    # V, VCU118 VCCINT is 0.85 V nominal


def ask(link, frame, want, timeout=2.0):
    dec = L.Decoder()
    link.write(frame)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        for t, p in dec.feed(link.read(0.2)):
            if t == want:
                return p
    return None


def find_port(baud):
    from serial.tools import list_ports
    # VCU118 CP2105: the Standard port reaches the FPGA (measured 10/27), Enhanced the system controller.
    ports = sorted((p for p in list_ports.comports() if p.vid == 0x10C4),
                   key=lambda p: 'Standard' not in p.description)
    for p in ports:
        try:
            link = SerialPort(p.device, baud)
        except Exception as e:               # port busy or vanished
            print(f'  {p.device}: {e}')
            continue
        if ask(link, L.ping_frame(), 'P'):
            print(f'  {p.device}: miner answers ({p.description})')
            return link
        print(f'  {p.device}: no answer')
        link.close()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fpga', default='auto', help="'auto', a port (COM5) or 'sim'")
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--freq', type=float, default=300.0)
    ap.add_argument('--freq-table', choices=sorted(L.FREQ_TABLES), default='vcu118', help="the bitstream's clock table")
    ap.add_argument('--seconds', type=float, default=30.0)
    ap.add_argument('--rate', type=float, default=50.0, help='candidates per second to verify')
    a = ap.parse_args()

    if a.fpga == 'sim':
        link = FpgaSim(status_s=0.5)
    elif a.fpga == 'auto':
        print('1. looking for the miner UART')
        link = find_port(a.baud)
        if link is None:
            print('FAIL: no CP210x port answers the ping (bitstream loaded? USB on the UART connector?)')
            return 1
    else:
        link = SerialPort(a.fpga, a.baud)
    ok = True
    try:
        link.write(L.run_frame(False))
        p = ask(link, L.ping_frame(), 'P')
        if not p or p[:3] != b'QTC':
            print('FAIL 2. ping: no answer')
            return 1
        print(f'PASS 2. ping: {p[:3].decode()} v{p[3] & 15}, {L.pong_pipes(p)} pipeline(s), '
              f'clock {int.from_bytes(p[4:6], "big")} MHz')

        st = L.parse_status(ask(link, L.query_frame(), 'S'))
        good = TEMP_OK[0] <= st.temp_c <= TEMP_OK[1] and VCCINT_OK[0] <= st.vccint_v <= VCCINT_OK[1] \
            and not st.over_temp and not st.freq_fail
        ok &= good
        print(f'{"PASS" if good else "FAIL"} 3. status: {st.temp_c:.1f} C, VCCINT {st.vccint_v:.3f} V, '
              f'{st.freq_mhz:.1f} MHz, flags {st.flags:#x}, UART CRC errors {st.crc_errors}')
        if st.has_pwr:
            print(f'     power: {st.pwr_text()}, INA226 bus {st.ina_bus_v:.3f} V')
        if not good:
            return 1

        freqs = L.FREQ_TABLES[a.freq_table]
        idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - a.freq))
        mhz = freqs[idx]
        expected_hs = 2000 if a.fpga == 'sim' else mhz * 1e6 / 2 * L.pong_pipes(p)
        r = run_step(link, idx, a.seconds, a.rate, expected_hs, freqs)
        if r.get('error'):
            print(f'FAIL 4. mining at {mhz} MHz: {r["error"]}')
            return 1
        rate_ok = a.fpga == 'sim' or 0.97 <= r['hs'] / expected_hs <= 1.01
        good = r['verified'] > 0 and not r['wrong'] and not r['missing'] and not r['over_temp'] and rate_ok
        ok &= good
        print(f'{"PASS" if good else "FAIL"} 4. mining at {mhz} MHz: {r["hs"] / 1e6:.3f} MH/s '
              f'(expect {expected_hs / 1e6:.1f}), candidates verified {r["verified"]} / expected {r["expected"]}, '
              f'wrong {r["wrong"]}, max {r["temp_max"]:.1f} C')
    finally:
        link.write(L.run_frame(False))
        link.close()
    print('BRINGUP PASS' if ok else 'BRINGUP FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
