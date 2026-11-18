"""Mines a random local job at fixed clocks (no pool) and prints the die temperature over time, to find
a clock the board fan stays quiet at. Every candidate is re-hashed like freq_sweep.py.

  python temp_soak.py --fpga COM3 --freq 282.4 266.7 [--minutes 10] [--every 30]
"""
import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]
import qpow_ref as q          # noqa: E402
import qpow_job as qj         # noqa: E402
import qtc_link as L          # noqa: E402
from fpga_port import SerialPort   # noqa: E402


def soak(link, mhz_req, minutes, every, rate=20.0, ot_continue=False, abort_c=90.0, vout=None, reg_abort=None,
         freqs=L.FREQ_VCU118):
    idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - mhz_req))
    mhz = freqs[idx]
    dec = L.Decoder()
    header, nonce_hi, fixed = os.urandom(32), os.urandom(32), os.urandom(24)
    target64 = min(int((1 << 64) * rate / (mhz * 1e6 / 2)), (1 << 64) - 1)
    link.write(L.run_frame(False) + L.freq_frame(idx) + (L.vid_frame(L.volts_vid(vout)) if vout else b''))
    time.sleep(3)
    link.write(L.job_frame(1, target64, 0, qj.chip_base(header, nonce_hi, fixed)) + L.run_frame(True))
    t0 = time.monotonic()
    last_print, h_prev, ok, bad, st, st_at = t0, None, 0, 0, None, t0
    while time.monotonic() - t0 < minutes * 60:
        link.write(L.query_frame())
        for t, p in dec.feed(link.read(0.5)):
            if t == 'S':
                st, st_at = L.parse_status(p), time.monotonic()
                if h_prev is None:
                    h_prev = (st.hashes, time.monotonic())
            elif t == 'C':
                _, cnt = L.parse_candidate(p)
                h = q.nonce_hash(header, qj.nonce_be(nonce_hi, fixed, cnt))
                ok, bad = (ok + 1, bad) if qj.top64(h) <= target64 else (ok, bad + 1)
        now = time.monotonic()
        if now - st_at > 10:
            # 10/28: at 300 MHz the board dropped its configuration (VCCINT over-current) without any frame
            print(f'{time.strftime("%H:%M:%S")} NO STATUS for 10 s at t={(now - t0) / 60:.1f} min: FPGA lost '
                  f'(last {st.temp_c:.1f} C)' if st else 'NO STATUS', flush=True)
            st = None
            break
        if st and h_prev and now - last_print >= every:
            mhs = (st.hashes - h_prev[0]) / (now - h_prev[1]) / 1e6
            pwr = f' | {st.pwr_text()}' if st.has_pwr else ''
            print(f'{time.strftime("%H:%M:%S")} {mhz:.1f} MHz t={(now - t0) / 60:4.1f} min  {st.temp_c:5.1f} C  '
                  f'{st.vccint_v:.3f} V  {mhs:7.2f} MH/s  ok {ok} wrong {bad}  flags {st.flags:#x}{pwr}', flush=True)
            last_print, h_prev = now, (st.hashes, now)
        if st and st.temp_c >= abort_c:
            link.write(L.run_frame(False))
            print(f'{time.strftime("%H:%M:%S")} ABORT {st.temp_c:.1f} C >= {abort_c} C: stop sent, turn the fan on',
                  flush=True)
            break
        # 10/29: the VCCINT power stage reached 133 C (its OT warning) at 282.4 MHz with no airflow
        if st and reg_abort and st.has_pwr and st.reg_temp_c >= reg_abort:
            link.write(L.run_frame(False))
            print(f'{time.strftime("%H:%M:%S")} ABORT regulator {st.reg_temp_c:.0f} C >= {reg_abort} C: stop sent',
                  flush=True)
            break
        if st and st.over_temp and not ot_continue:
            print('FPGA thermal gate tripped', flush=True)
            break
    link.write(L.run_frame(False))
    return st, ok, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fpga', required=True)
    ap.add_argument('--freq', type=float, nargs='+', required=True, help='MHz, nearest table entries, run in order')
    ap.add_argument('--minutes', type=float, default=10.0, help='per clock')
    ap.add_argument('--every', type=float, default=30.0, help='seconds between lines')
    ap.add_argument('--ot-continue', action='store_true',
                    help="keep going when the FPGA's own thermal gate stops mining (to watch it cycle)")
    ap.add_argument('--abort', type=float, default=90.0, help='PC-side emergency stop, C')
    ap.add_argument('--reg-abort', type=float, help='stop when the VCCINT regulator reaches this, C')
    ap.add_argument('--freq-table', choices=sorted(L.FREQ_TABLES), default='vcu118', help="the bitstream's clock table")
    ap.add_argument('--vout', type=float, help='VCCINT setpoint in volts (FPGA accepts 0.720..0.850)')
    a = ap.parse_args()
    link = SerialPort(a.fpga, 115200)
    try:
        for f in a.freq:
            st, ok, bad = soak(link, f, a.minutes, a.every, ot_continue=a.ot_continue, abort_c=a.abort, vout=a.vout,
                            reg_abort=a.reg_abort, freqs=L.FREQ_TABLES[a.freq_table])
            print(f'DONE {f} MHz: final {st.temp_c:.1f} C, candidates ok {ok} wrong {bad}' if st else f'DONE {f}: no status',
                  flush=True)
    finally:
        link.write(L.run_frame(False))
        link.close()


if __name__ == '__main__':
    main()
