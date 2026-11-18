"""Steps the VCCINT setpoint down (5 mV by default) at a fixed clock while mining a random local job (no
pool), to find the voltage where the core current drops to a target. Every candidate is re-hashed like
temp_soak.py; the first wrong one (or a lost FPGA) ends the sweep. The setpoint goes back to 0.850 V
at the end either way.

  python vout_sweep.py --fpga COM3 --freq 300 [--target-a 80] [--step-min 3] [--floor 0.72] [--probe 2]

Current is estimated from the INA226 shunt (AMPS_PER_MV, AMPS_AT_0MV): the MAX20751's own READ_IOUT drifts low as the
regulator warms up (10/28), so it is only printed for reference.
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

DEFAULT_V = 0.850
# 10/28 21:24-21:26, regulator still cool after a pool outage (MAX20751 reading trusted): idle 17.0 A at
# 8.0 mV, mining 86.0 A at 46.275 mV -> straight line through both
AMPS_PER_MV = (86.0 - 17.0) / (46.275 - 8.0)
AMPS_AT_0MV = 17.0 - 8.0 * AMPS_PER_MV


def run_step(link, dec, job, minutes, abort_c, reg_abort):
    """Mines for `minutes`; returns averages over the second half and the candidate counts."""
    header, nonce_hi, fixed, target64 = job
    t0 = st_at = time.monotonic()
    ok = bad = 0
    st = h0 = None
    late = []
    while time.monotonic() - t0 < minutes * 60:
        link.write(L.query_frame())
        for t, p in dec.feed(link.read(0.5)):
            if t == 'S':
                st, st_at = L.parse_status(p), time.monotonic()
                if st_at - t0 >= minutes * 30:
                    if h0 is None:
                        h0 = (st.hashes, st_at)
                    late.append(st)
            elif t == 'C':
                _, cnt = L.parse_candidate(p)
                h = q.nonce_hash(header, qj.nonce_be(nonce_hi, fixed, cnt))
                ok, bad = (ok + 1, bad) if qj.top64(h) <= target64 else (ok, bad + 1)
        if time.monotonic() - st_at > 10:
            return None, ok, bad, 'FPGA lost (no status for 10 s)'
        if bad:
            return st, ok, bad, 'wrong candidate'
        if st and st.temp_c >= abort_c:
            return st, ok, bad, f'die {st.temp_c:.1f} C >= {abort_c} C'
        if st and st.over_temp:
            return st, ok, bad, 'FPGA thermal gate'
        if st and st.reg_temp_c >= reg_abort:
            return st, ok, bad, f'regulator {st.reg_temp_c:.0f} C >= {reg_abort} C'
    n = len(late)
    if not n:
        return st, ok, bad, 'no status in the second half of the step'
    avg = dict(ina_mv=sum(s.ina_shunt_mv for s in late) / n, die_c=sum(s.temp_c for s in late) / n,
               reg_c=sum(s.reg_temp_c for s in late) / n, reg_max=max(s.reg_temp_c for s in late),
               status_temp=late[-1].status_temp, iout=sum(s.iout_a for s in late) / n,
               mhs=(late[-1].hashes - h0[0]) / (st_at - h0[1]) / 1e6 if st_at > h0[1] else 0.0)
    avg['amps'] = AMPS_AT_0MV + avg['ina_mv'] * AMPS_PER_MV
    return st, ok, bad, avg


def set_vout(link, dec, v, wait_s=20.0):
    """Sends the setpoint and waits until the FPGA reports it read back from the regulator (an I2C
    round takes a few seconds), or counts it as refused."""
    code = L.volts_vid(v)
    link.write(L.vid_frame(code))
    t0, rej0 = time.monotonic(), None
    while time.monotonic() - t0 < wait_s:
        link.write(L.query_frame())
        for t, p in dec.feed(link.read(0.5)):
            if t == 'S':
                st = L.parse_status(p)
                rej0 = st.pwr_rej if rej0 is None else rej0
                if st.vout_cmd == code:
                    return True
                if st.pwr_rej != rej0:
                    return False
        time.sleep(0.5)
    return False


def sweep(link, mhz_req, target_a, step_min, floor_v, probe, abort_c, reg_abort=125.0, step_v=0.005, rate=20.0,
          freqs=L.FREQ_VCU118):
    idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - mhz_req))
    mhz = freqs[idx]
    dec = L.Decoder()
    header, nonce_hi, fixed = os.urandom(32), os.urandom(32), os.urandom(24)
    target64 = min(int((1 << 64) * rate / (mhz * 1e6 / 2)), (1 << 64) - 1)
    link.write(L.run_frame(False) + L.freq_frame(idx))
    time.sleep(3)
    if not set_vout(link, dec, DEFAULT_V):
        print('FPGA did not take 0.850 V: no voltage control in this bitstream?', flush=True)
        return []
    # clear latched regulator faults so the step lines show what this run trips
    link.write(L.clear_faults_frame() + L.job_frame(1, target64, 0, qj.chip_base(header, nonce_hi, fixed))
               + L.run_frame(True))
    rows, v, below = [], DEFAULT_V, 0
    while True:
        st, ok, bad, res = run_step(link, dec, (header, nonce_hi, fixed, target64), step_min, abort_c, reg_abort)
        if not isinstance(res, dict):
            print(f'{time.strftime("%H:%M:%S")} {mhz:.1f} MHz {v:.3f} V: STOP, {res} (ok {ok} wrong {bad})', flush=True)
            rows.append(dict(v=v, fail=res))
            break
        res.update(v=v, ok=ok)
        rows.append(res)
        print(f'{time.strftime("%H:%M:%S")} {mhz:.1f} MHz {v:.3f} V  ~{res["amps"]:5.1f} A (INA226 {res["ina_mv"]:.3f} mV, '
              f'MAX {res["iout"]:.1f} A)  die {res["die_c"]:.1f} C  regulator {res["reg_c"]:.0f} C (max {res["reg_max"]:.0f}, status {res["status_temp"]:#04x})  '
              f'{res["mhs"]:.2f} MH/s  ok {ok} wrong 0', flush=True)
        below = below + 1 if res['amps'] <= target_a else 0
        if below > probe or v - step_v < floor_v - 1e-9:
            break
        v = round(v - step_v, 3)
        if not set_vout(link, dec, v):
            print(f'FPGA refused {v:.3f} V (outside its range): stop', flush=True)
            break
    link.write(L.run_frame(False))
    set_vout(link, dec, DEFAULT_V)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fpga', required=True)
    ap.add_argument('--freq', type=float, nargs='+', required=True, help='MHz, nearest table entries, run in order')
    ap.add_argument('--target-a', type=float, default=80.0, help='VCCINT current to get under (INA226 estimate)')
    ap.add_argument('--step-min', type=float, default=3.0, help='minutes per voltage step')
    ap.add_argument('--floor', type=float, default=0.72, help='lowest setpoint to try, V')
    ap.add_argument('--probe', type=int, default=2, help='extra steps below the target, for the error margin')
    ap.add_argument('--step-mv', type=float, default=5.0, help='setpoint step, mV (the VID resolution is 5 mV)')
    ap.add_argument('--abort', type=float, default=85.0, help='PC-side emergency stop, die C')
    ap.add_argument('--reg-abort', type=float, default=125.0, help='stop when the VCCINT regulator reaches this, C')
    a = ap.parse_args()
    link = SerialPort(a.fpga, 115200)
    try:
        for f in a.freq:
            rows = sweep(link, f, a.target_a, a.step_min, a.floor, a.probe, a.abort, a.reg_abort,
                         a.step_mv / 1000)
            good = [r for r in rows if 'fail' not in r]
            fit = [r for r in good if r['amps'] <= a.target_a]
            # recommend the highest voltage under the target that still has `probe` error-free steps below it
            pick = next((r for r in fit if sum(g['v'] < r['v'] for g in good) >= a.probe), None)
            fails = [r for r in rows if 'fail' in r]
            print(f'DONE {f} MHz: ' + (f'use {pick["v"]:.3f} V (~{pick["amps"]:.1f} A)' if pick else 'no setpoint under the target with margin')
                  + (f'; failed at {fails[0]["v"]:.3f} V ({fails[0]["fail"]})' if fails else ''), flush=True)
    finally:
        link.write(L.run_frame(False))
        link.close()


if __name__ == '__main__':
    main()
