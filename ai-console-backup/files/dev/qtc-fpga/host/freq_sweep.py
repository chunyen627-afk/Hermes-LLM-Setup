"""Finds the highest mining clock the board computes correctly at (no pool involved).

For each frequency in the table the FPGA mines a random local job with a loose candidate threshold;
every candidate is re-hashed here. Reported per step: hashrate from the hash counter, candidates
verified vs. expected (missing ones mean lost results), wrong candidates (hardware errors), and
temperature. Stops climbing at the first step with errors or when the FPGA's thermal gate trips.

  python freq_sweep.py --fpga COM7 [--start 300] [--seconds 60] [--rate 50]
  python freq_sweep.py --fpga sim --sim-fault-above 360      (exercise the logic without a board)
"""
import argparse
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]
import qpow_ref as q          # noqa: E402
import qpow_job as qj         # noqa: E402
import qtc_link as L          # noqa: E402
from fpga_port import FpgaSim, SerialPort   # noqa: E402


def run_step(link, idx, seconds, rate, expected_hs, freqs=L.FREQ_VCU118):
    dec = L.Decoder()
    header, nonce_hi, fixed = os.urandom(32), os.urandom(32), os.urandom(24)
    target64 = min(int((1 << 64) * rate / expected_hs), (1 << 64) - 1)
    link.write(L.run_frame(False) + L.freq_frame(idx))
    t_end = time.monotonic() + 20
    st = None
    while time.monotonic() < t_end:                 # wait for the new clock to be up
        link.write(L.query_frame())
        for t, p in dec.feed(link.read(0.3)):
            if t == 'S':
                st = L.parse_status(p)
        if st and not st.freq_busy and abs(st.freq_mhz - freqs[idx]) < 0.2:
            break
    else:
        return dict(idx=idx, mhz=freqs[idx], error='clock did not switch')
    if st.freq_fail:
        return dict(idx=idx, mhz=freqs[idx], error='MMCM lock failure')
    link.write(L.job_frame(1, target64, 0, qj.chip_base(header, nonce_hi, fixed)) + L.run_frame(True))
    t0, h0, ok, bad, temps, last = time.monotonic(), None, 0, 0, [], None
    while time.monotonic() - t0 < seconds:
        link.write(L.query_frame())
        for t, p in dec.feed(link.read(0.5)):
            if t == 'S':
                last = L.parse_status(p)
                temps.append(last.temp_c)
                if h0 is None:
                    h0 = (last.hashes, time.monotonic())
                if last.over_temp:
                    break
            elif t == 'C':
                jid, cnt = L.parse_candidate(p)
                h = q.nonce_hash(header, qj.nonce_be(nonce_hi, fixed, cnt))
                if qj.top64(h) <= target64:
                    ok += 1
                else:
                    bad += 1
        if last and last.over_temp:
            break
    link.write(L.run_frame(False))
    hashed = last.hashes - h0[0] if last and h0 else 0
    dt = time.monotonic() - h0[1] if h0 else 1
    expect = hashed * target64 / 2 ** 64
    # Candidates are Poisson: flag a shortfall beyond 4 sigma as lost results.
    short = expect - ok > 4 * math.sqrt(max(expect, 1))
    return dict(idx=idx, mhz=freqs[idx], hs=hashed / dt, verified=ok, expected=round(expect, 1),
                wrong=bad, missing=short, temp_max=max(temps) if temps else None,
                over_temp=bool(last and last.over_temp))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fpga', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--start', type=float, default=300.0, help='first MHz to test')
    ap.add_argument('--stop', type=float, default=400.0)
    ap.add_argument('--seconds', type=float, default=60.0, help='per step')
    ap.add_argument('--rate', type=float, default=50.0, help='target candidates per second')
    ap.add_argument('--freq-table', choices=sorted(L.FREQ_TABLES), default='vcu118', help="the bitstream's clock table")
    ap.add_argument('--sim-fault-above', type=float, help='FpgaSim: inject errors above this MHz')
    a = ap.parse_args()
    if a.fpga == 'sim':
        link = FpgaSim(fault_above_mhz=a.sim_fault_above, status_s=0.5)
    else:
        link = SerialPort(a.fpga, a.baud)
    results = []
    try:
        freqs = L.FREQ_TABLES[a.freq_table]
        for idx, mhz in enumerate(freqs):
            if mhz < a.start - 0.1 or mhz > a.stop + 0.1:
                continue
            expected_hs = 2000 if a.fpga == 'sim' else mhz * 1e6 / 2
            r = run_step(link, idx, a.seconds, a.rate, expected_hs, freqs)
            results.append(r)
            print(r, flush=True)
            if r.get('error') or r.get('wrong') or r.get('missing') or r.get('over_temp'):
                break
    finally:
        link.write(L.run_frame(False))
        link.close()
    good = [r for r in results if not (r.get('error') or r.get('wrong') or r.get('missing') or r.get('over_temp'))]
    if good:
        best = good[-1]
        print(f"highest clean step: {best['mhz']} MHz ({best['hs'] / 1e6:.2f} MH/s measured); "
              f"run 5-10% below it for long-term mining")
    else:
        print('no clean step')


if __name__ == '__main__':
    main()
