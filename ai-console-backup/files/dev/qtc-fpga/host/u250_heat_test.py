"""Guarded first run of a U250 without forced airflow (10/31): short local mining steps at the
lowest table clock (240 MHz), every candidate re-hashed, stopping the moment the die reaches
--stop-c. No pool. The board's VCCINT regulator temperature cannot be read (no I2C on the U250),
so the die limit is kept well under the FPGA's own 85 C gate and the steps are short.

  python u250_heat_test.py --fpga COM16 [--steps 5] [--seconds 6] [--stop-c 70] [--idle-s 60]
  python u250_heat_test.py --fpga sim --idle-s 5 --steps 2      (exercise the script without a board)

First watches the idle die for --idle-s seconds (clock lowered to 240 MHz, not mining), then mines in
steps; between steps it waits (up to 120 s) until the die is back under --stop-c - 10. Always ends with
mining stopped.
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]
import qtc_link as L                         # noqa: E402
from bringup import ask                      # noqa: E402
from fpga_port import FpgaSim, SerialPort    # noqa: E402
from freq_sweep import run_step              # noqa: E402


def status(link):
    p = ask(link, L.query_frame(), 'S')
    return L.parse_status(p) if p else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fpga', required=True)
    ap.add_argument('--steps', type=int, default=5)
    ap.add_argument('--seconds', type=float, default=6.0)
    ap.add_argument('--stop-c', type=float, default=70.0)
    ap.add_argument('--idle-s', type=float, default=60.0)
    a = ap.parse_args()
    link = FpgaSim(status_s=0.5) if a.fpga == 'sim' else SerialPort(a.fpga)
    t0 = time.monotonic()

    def log(msg):
        print(f'{time.monotonic() - t0:6.1f}s {msg}', flush=True)

    try:
        link.write(L.run_frame(False))
        p = ask(link, L.ping_frame(), 'P')
        if not p or p[:3] != b'QTC':
            log('no answer to ping (not programmed?)')
            return 1
        log(f'pong v{p[3] & 15}, {L.pong_pipes(p)} pipelines')
        link.write(L.freq_frame(0))                       # 240 MHz while idle
        end = time.monotonic() + a.idle_s
        while True:
            st = status(link)
            if st:
                log(f'idle {st.freq_mhz:.1f} MHz  die {st.temp_c:.1f} C  VCCINT {st.vccint_v:.3f} V  flags {st.flags:#x}')
                if st.temp_c >= a.stop_c:
                    log('idle die already at the limit: stopping here')
                    return 1
            if time.monotonic() >= end:
                break
            time.sleep(5)
        expected = L.FREQ_VCU118[0] * 1e6 / 2 * L.pong_pipes(p)
        for n in range(a.steps):
            cool_end = time.monotonic() + 120
            while (st := status(link)) and st.temp_c > a.stop_c - 10:
                log(f'waiting to cool: die {st.temp_c:.1f} C')
                if time.monotonic() > cool_end:
                    log('not cooling below the limit within 120 s: stopping here')
                    return 1
                time.sleep(5)
            r = run_step(link, 0, a.seconds, 50.0, expected)
            st = status(link)
            log(f'step {n + 1}: {r.get("hs", 0) / 1e6:.1f} MH/s ({r.get("hs", 0) / expected:.1%} of {expected / 1e6:.0f})  '
                f'verified {r.get("verified")}/{r.get("expected")}  wrong {r.get("wrong")}  '
                f'die max {r.get("temp_max") or 0:.1f} C, now {st.temp_c if st else 0:.1f} C  {r.get("error", "")}')
            if r.get('error') or r.get('wrong') or r.get('over_temp') or (r.get('temp_max') or 0) >= a.stop_c:
                log('stopping: error, wrong result or temperature limit')
                break
        for _ in range(6):                                # cool-down trace after the last step
            time.sleep(5)
            st = status(link)
            if st:
                log(f'after  die {st.temp_c:.1f} C  VCCINT {st.vccint_v:.3f} V')
        return 0
    finally:
        link.write(L.run_frame(False))
        link.close()


if __name__ == '__main__':
    sys.exit(main())
