"""PC relay: Kryptex QTC stratum <-> VCU118 miner over USB-UART.

  python relay.py --pool kryptex-hk --fpga COM7        (real pool, real board: needs user approval)
  python relay.py --pool local:3333 --fpga sim         (offline: fake_pool.py + FpgaSim)

Per pool job the relay picks the nonce layout (extranonce 4 B | salt 8 B | zeros 20 B | fixed 24 B
| chip counter 8 B), computes the chip's base state (midstate, Python), and sends a 'J' frame.
Every candidate the chip reports is re-hashed here through P5: if its top 64 bits do not meet the
chip threshold it is a hardware error; if the full 512-bit hash beats the share target it is
submitted. The chip threshold is set looser than the share target ("monitor" candidates) so the
verified-candidate rate measures the real hashrate and catches computation errors continuously.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]
import qpow_ref as q           # noqa: E402
import qpow_job as qj          # noqa: E402
import qtc_link as L           # noqa: E402
from fpga_port import FpgaSim, SerialPort   # noqa: E402
from stratum import StratumClient           # noqa: E402

log = logging.getLogger('relay')
U512_MAX = (1 << 512) - 1
POOLS = {'kryptex-hk': ('qtc-hk.kryptex.network', 8049, True)}
FREQS = L.FREQ_VCU118             # the bitstream's clock table, set by --freq-table
DEFAULT_VID = 0x79                 # 0.850 V, the VCU118 VCCINT default (VR12 VID, qtc_link.vid_volts)
AGGREGATOR_CFG = Path(r'C:\Users\pjunm\dev\miner-status-ios\pc-service\aggregator.json')


def kryptex_account():
    """The Kryptex account name lives only in the aggregator's local config; never copy it."""
    return json.loads(AGGREGATOR_CFG.read_text(encoding='utf-8'))['earnings']['account']


class Relay:
    def __init__(self, link, pool, worker, monitor_rate, expected_mhs, temp_limit_c, dry_run=False, freq_idx=None,
                 vid=None, reg_limit_c=None):
        self.link, self.pool, self.worker = link, pool, worker
        self.monitor_rate, self.expected_hs = monitor_rate, expected_mhs * 1e6
        self.temp_limit_c, self.dry_run, self.freq_idx = temp_limit_c, dry_run, freq_idx
        self.reg_limit_c = reg_limit_c         # VCCINT regulator stop, C (None = no check)
        self.vid, self.vid_asked, self.vid_hw_errors = vid, 0.0, None   # VCCINT setpoint (VID code), None = leave it
        self.dec = L.Decoder()
        self.jobs = {}                 # chip job id -> dict
        self.chip_job = 0
        self.current = None            # chip job id of the newest pool job
        self.stats = dict(cands=0, monitor_ok=0, hw_errors=0, shares=0, accepted=0, rejected=0,
                          stale_local=0, overflow=0)
        self.status = None
        self.loop = None
        self.t0 = time.monotonic()
        self.hash0 = None
        self.running = False
        self.heat_stop = False

    # ---------------- FPGA side ----------------
    def send(self, data):
        self.link.write(data)

    async def fpga_reader(self):
        while True:
            data = await asyncio.to_thread(self.link.read, 0.1)
            for t, p in self.dec.feed(data):
                if t == 'C':
                    await self.on_candidate(*L.parse_candidate(p))
                elif t == 'S':
                    self.on_status(L.parse_status(p))
                elif t == 'P':
                    log.info('FPGA pong: %s, clock %d MHz', p[:3].decode(errors='replace'), int.from_bytes(p[4:6], 'big'))

    def on_status(self, st):
        # The FPGA gates itself (>= 85 C stop, <= 75 C resume); this is a second, PC-side guard.
        # 10/29: the VCCINT power stage (no airflow) is the hottest part, so it gets the same stop/resume.
        self.status = st
        reg = st.reg_temp_c if self.reg_limit_c and st.has_pwr else None
        if self.running and (st.temp_c >= self.temp_limit_c or (reg is not None and reg >= self.reg_limit_c)):
            log.error('FPGA %.1f C (limit %.1f), regulator %s C (limit %s): stopping', st.temp_c, self.temp_limit_c,
                      reg, self.reg_limit_c)
            self.set_run(False)
            self.heat_stop = True
        elif (self.heat_stop and st.temp_c <= self.temp_limit_c - 15 and (reg is None or reg <= self.reg_limit_c - 15)
              and self.current is not None):
            log.warning('cooled (FPGA %.1f C, regulator %s C): resuming', st.temp_c, reg)
            self.heat_stop = False
            self.set_run(True)
        if st.over_temp:
            log.warning('FPGA thermal gate active (%.1f C)', st.temp_c)
        if st.overflow:
            self.stats['overflow'] += 1
        self.keep_vccint(st)

    def keep_vccint(self, st):
        """Holds the VCCINT regulator at the requested VID (asks again after an FPGA reload or a refused
        request). Any hardware error while below the default voltage puts it back to the default."""
        if self.vid is None or st.pwr_ver != 4:
            return
        if self.vid != DEFAULT_VID:
            if self.vid_hw_errors is None:
                self.vid_hw_errors = self.stats['hw_errors']
            elif self.stats['hw_errors'] > self.vid_hw_errors:
                log.error('hardware error at VCCINT %.3f V: back to %.3f V', L.vid_volts(self.vid), L.vid_volts(DEFAULT_VID))
                self.vid = DEFAULT_VID
        if st.vout_cmd & 0xFF != self.vid and time.monotonic() - self.vid_asked > 10:
            log.info('VCCINT setpoint %.3f V -> %.3f V (FPGA refused %d so far)',
                     st.vout_set_v, L.vid_volts(self.vid), st.pwr_rej)
            self.send(L.vid_frame(self.vid))
            self.vid_asked = time.monotonic()

    def set_run(self, on):
        self.running = on
        self.send(L.run_frame(on))

    # ---------------- jobs ----------------
    def on_pool_job(self, job):
        try:
            header = bytes.fromhex(job['mining_hash'])
            difficulty = int(job['difficulty'])
            extranonce = bytes.fromhex(job.get('extranonce', ''))
        except (KeyError, ValueError, TypeError) as e:
            log.warning('bad job %r: %s', job, e)
            return
        if len(header) != 32 or difficulty <= 0 or len(extranonce) > 12:
            log.warning('unexpected job fields: header %d B, difficulty %s, extranonce %d B', len(header), difficulty, len(extranonce))
            return
        if self.current is not None and self.jobs[self.current]['job_id'] == job.get('job_id') \
                and self.jobs[self.current]['difficulty'] == difficulty:
            return                                          # same job again: keep scanning
        target = U512_MAX // difficulty
        salt = os.urandom(12 - len(extranonce))
        nonce_hi = extranonce + salt + bytes(20)
        fixed24 = os.urandom(24)
        base = qj.chip_base(header, nonce_hi, fixed24)
        monitor64 = int((1 << 64) * self.monitor_rate / self.expected_hs) if self.expected_hs else 0
        target64 = min(max(target >> 448, monitor64), (1 << 64) - 1)
        self.chip_job = self.chip_job % 255 + 1
        self.jobs[self.chip_job] = dict(job_id=job.get('job_id'), header=header, difficulty=difficulty,
                                        target=target, target64=target64, nonce_hi=nonce_hi, fixed24=fixed24,
                                        t=time.monotonic())
        self.current = self.chip_job
        for k in [k for k, v in self.jobs.items() if time.monotonic() - v['t'] > 600 and k != self.current]:
            del self.jobs[k]
        self.send(L.job_frame(self.chip_job, target64, 0, base))
        if not self.running:
            self.set_run(True)
        log.info('job %s diff %d -> chip job %d (target64 %#x)', job.get('job_id'), difficulty, self.chip_job, target64)

    async def on_candidate(self, chip_job, cnt):
        self.stats['cands'] += 1
        jb = self.jobs.get(chip_job)
        if jb is None:
            return
        nonce = qj.nonce_be(jb['nonce_hi'], jb['fixed24'], cnt)
        h = await asyncio.to_thread(q.nonce_hash, jb['header'], nonce)
        if qj.top64(h) > jb['target64']:
            self.stats['hw_errors'] += 1
            log.error('HW error: chip job %d counter %#x does not meet its threshold', chip_job, cnt)
            return
        self.stats['monitor_ok'] += 1
        if int.from_bytes(h, 'big') >= jb['target']:
            return
        self.stats['shares'] += 1
        if chip_job != self.current:
            self.stats['stale_local'] += 1
            return
        if self.dry_run or not self.pool.session:
            log.info('share found (not submitted): job %s', jb['job_id'])
            return
        ok, msg = await self.pool.submit(jb['job_id'], nonce, h)
        self.stats['accepted' if ok else 'rejected'] += 1
        log.log(logging.INFO if ok else logging.WARNING, 'share %s: %s', 'accepted' if ok else 'rejected', msg)

    # ---------------- housekeeping ----------------
    async def stats_loop(self, every=5.0):
        last = None
        while True:
            self.send(L.query_frame())                     # also feeds the FPGA watchdog
            await asyncio.sleep(every)
            if self.running and not self.pool.connected.is_set():
                log.warning('pool offline: stopping the FPGA')
                self.set_run(False)
                self.current = None
            st = self.status
            if st is None:
                continue
            if self.hash0 is None:
                self.hash0 = (st.hashes, time.monotonic())
            if last:
                rate = (st.hashes - last[0]) / max(time.monotonic() - last[1], 1e-9)
                s = self.stats
                # The power part goes last: office_aggregator.py RELAY_STATUS parses the line up to 'rej N'.
                pwr = f' | {st.pwr_text()}' if st.has_pwr else ''
                log.info('%.1f MHz %.3f MH/s | %.1f C %.3f V | cands %d (hw err %d) shares %d acc %d rej %d | flags %#x%s',
                         st.freq_mhz, rate / 1e6, st.temp_c, st.vccint_v, s['cands'], s['hw_errors'], s['shares'],
                         s['accepted'], s['rejected'], st.flags, pwr)
            last = (st.hashes, time.monotonic())

    def summary(self):
        st = self.status
        out = dict(self.stats)
        if st and self.hash0:
            out['hashes'] = st.hashes - self.hash0[0]
            out['avg_hs'] = out['hashes'] / max(time.monotonic() - self.hash0[1], 1e-9)
        return out

    async def run(self, duration=None):
        self.loop = asyncio.get_running_loop()
        self.send(L.ping_frame())
        self.set_run(False)
        if self.freq_idx is not None:
            log.info('mining clock -> %.1f MHz', FREQS[self.freq_idx])
            self.send(L.freq_frame(self.freq_idx))
        tasks = [asyncio.create_task(c) for c in (self.pool.run_forever(), self.fpga_reader(), self.stats_loop())]
        try:
            if duration:
                await asyncio.sleep(duration)
            else:
                await asyncio.gather(*tasks)
        finally:
            self.set_run(False)
            await asyncio.sleep(1.5)        # let submits already in flight get their answers
            for t in tasks:
                t.cancel()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pool', required=True, help="'kryptex-hk' or 'local:PORT' (fake_pool.py)")
    ap.add_argument('--fpga', required=True, help="serial port (e.g. COM7) or 'sim'")
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--worker', default='vcu118')
    ap.add_argument('--login', help='override login (local pools only)')
    ap.add_argument('--no-tls', action='store_true')
    ap.add_argument('--fingerprint', help='expected pool certificate SHA-256 (hex)')
    ap.add_argument('--expected-mhs', type=float, default=150.0, help='for the monitor threshold')
    ap.add_argument('--monitor-rate', type=float, default=2.0, help='monitor candidates per second')
    ap.add_argument('--freq', type=float, default=300.0,
                    help='mining clock MHz, nearest table entry (the bitstream powers up at 355.6, above its timing)')
    ap.add_argument('--freq-table', choices=sorted(L.FREQ_TABLES), default='vcu118',
                    help="the bitstream's clock table (U250: vcu118)")
    ap.add_argument('--temp-limit', type=float, default=90.0, help='PC-side stop (FPGA itself stops at 85 C)')
    ap.add_argument('--reg-limit', type=float, default=110.0,
                    help='PC-side stop on the VCCINT regulator temperature, C (resumes 15 C lower)')
    ap.add_argument('--vout', type=float,
                    help='VCCINT setpoint in volts, 5 mV steps (the FPGA accepts 0.720..0.850); default: leave it')
    ap.add_argument('--duration', type=float, help='stop after this many seconds')
    ap.add_argument('--dry-run', action='store_true', help='never submit shares')
    ap.add_argument('--sim-fault-rate', type=float, default=0.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

    if args.pool.startswith('local:'):
        host, port, tls = '127.0.0.1', int(args.pool.split(':')[1]), not args.no_tls
        login = args.login or f'testaccount/{args.worker}'
    else:
        host, port, tls = POOLS[args.pool]
        login = f'{kryptex_account()}/{args.worker}'
    link = FpgaSim(fault_rate=args.sim_fault_rate) if args.fpga == 'sim' else SerialPort(args.fpga, args.baud)
    relay = None
    pool = StratumClient(host, port, login, tls=tls, fingerprint=args.fingerprint,
                         on_job=lambda job: relay.on_pool_job(job))
    global FREQS
    FREQS = L.FREQ_TABLES[args.freq_table]
    freq_idx = None
    if args.freq:
        freq_idx = min(range(len(FREQS)), key=lambda i: abs(FREQS[i] - args.freq))
    relay = Relay(link, pool, args.worker, args.monitor_rate, args.expected_mhs, args.temp_limit, args.dry_run, freq_idx,
                  L.volts_vid(args.vout) if args.vout else None, args.reg_limit)
    try:
        asyncio.run(relay.run(args.duration))
    except KeyboardInterrupt:
        pass
    finally:
        link.write(L.run_frame(False))
        print('SUMMARY', json.dumps(relay.summary()))
        link.close()


if __name__ == '__main__':
    main()
