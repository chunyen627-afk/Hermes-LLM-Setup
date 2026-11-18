"""End-to-end offline test: fake_pool (TLS, self-signed) <-> relay <-> FpgaSim (software FPGA).

Nothing leaves this machine: the pool listens on 127.0.0.1 and the "FPGA" is the Python model.
Run 1 is clean (expects accepted shares, no hardware errors, at most a few stale rejects at job
switches); run 2 injects wrong chip results (expects them caught as hardware errors, never submitted).

  python test_offline.py [seconds_per_run]
"""
import asyncio
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_pool                     # noqa: E402
from fpga_port import FpgaSim        # noqa: E402
from relay import Relay              # noqa: E402
from stratum import StratumClient    # noqa: E402

SIM_HS = 2000            # rough FpgaSim speed (H/s) for the monitor threshold


def make_cert(d):
    cert, key = str(Path(d) / 'cert.pem'), str(Path(d) / 'key.pem')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=fake-pool',
                    '-keyout', key, '-out', cert], check=True, capture_output=True)
    return cert, key


async def one_run(port, cert, key, seconds, fault_rate):
    ready = asyncio.get_running_loop().create_future()
    server = asyncio.create_task(fake_pool.serve(port, 1000, 12.0, cert, key, ready))
    pool_state = await ready
    link = FpgaSim(fault_rate=fault_rate, seed=5)
    relay = None
    client = StratumClient('127.0.0.1', port, 'testaccount/vcu118', tls=True, on_job=lambda j: relay.on_pool_job(j))
    relay = Relay(link, client, 'vcu118', monitor_rate=3.0, expected_mhs=SIM_HS / 1e6, temp_limit_c=85.0)
    try:
        await relay.run(seconds)
    finally:
        link.close()
        server.cancel()
    return relay.summary(), pool_state.stats


def thermal_check():
    """FpgaSim alone: running, heat to 86 C -> must stop by itself; 80 C -> still stopped
    (hysteresis); 70 C -> must resume. The host never sends another command."""
    import time
    import qtc_link as L
    sim = FpgaSim(status_s=0.2)
    sim.write(L.job_frame(1, 0, 0, [0] * 12) + L.run_frame(True))
    dec, seen = L.Decoder(), []

    def flags_after(temp, wait=0.8):
        sim.temp_c = temp
        time.sleep(wait)
        st = [L.parse_status(p) for t, p in dec.feed(sim.read(0.2)) if t == 'S']
        return st[-1]

    try:
        seen.append(flags_after(45).running)
        seen.append(not flags_after(86).running)
        seen.append(not flags_after(80).running)
        seen.append(flags_after(70).running)
    finally:
        sim.close()
    print(f'[thermal] running@45C, stopped@86C, stopped@80C, running@70C: {seen}')
    return all(seen)


def power_check():
    """Status v4 power fields through FpgaSim: VID read-back, an in-range voltage request is applied,
    an out-of-range one is refused."""
    import time
    import qtc_link as L
    sim = FpgaSim(status_s=10)
    dec = L.Decoder()

    def status_after(frames=b''):
        sim.write(frames + L.query_frame())
        time.sleep(0.3)
        return [L.parse_status(p) for t, p in dec.feed(sim.read(0.2)) if t == 'S'][-1]

    try:
        s0 = status_after()
        s1 = status_after(L.vid_frame(L.volts_vid(0.840)))
        s2 = status_after(L.vid_frame(0x5E))          # just under 0.720 V
    finally:
        sim.close()
    seen = [s0.pwr_ver == 4 and abs(s0.vout_set_v - 0.85) < 1e-9 and s0.reg_temp_c == 55,
            abs(s1.vout_set_v - 0.84) < 1e-9 and s1.pwr_wr == 1,
            abs(s2.vout_set_v - 0.84) < 1e-9 and s2.pwr_rej == 1 and s2.pwr_wr == 1]
    print(f'[power] {s1.pwr_text()}: {seen}')
    return all(seen)


def regulator_guard_check():
    """Relay's PC-side regulator guard: running at 90 C, stop at 110 C, still stopped at 100 C
    (hysteresis), resume at 94 C; the FPGA die stays cool throughout."""
    import time
    import qtc_link as L
    sim = FpgaSim(status_s=10)
    relay = Relay(sim, None, 'vcu118', monitor_rate=3.0, expected_mhs=1.0, temp_limit_c=85.0, reg_limit_c=110.0)
    relay.current = 1
    relay.set_run(True)
    dec = L.Decoder()

    def running_at(reg_c):
        sim.reg_c = reg_c
        sim.write(L.query_frame())
        time.sleep(0.3)
        for t, p in dec.feed(sim.read(0.2)):
            if t == 'S':
                relay.on_status(L.parse_status(p))
        return relay.running

    try:
        seen = [running_at(90), not running_at(110), not running_at(100), running_at(94)]
    finally:
        sim.close()
    print(f'[regulator] running@90C, stopped@110C, stopped@100C, running@94C: {seen}')
    return all(seen)


def board_config_check():
    """mining_ctl.py board profiles: the vcu118 one keeps the running choice, the u250 one expects
    clock/2 per pipeline, and its pid / stop / log names miss the vcu118 ones and their globs
    (mining_*.err in mining_watch.py and office_aggregator.py)."""
    from fnmatch import fnmatch
    import mining_ctl as M
    import qtc_link as L
    v, u = M.BOARDS['vcu118'], M.BOARDS['u250']
    seen = [(v['port'], v['freq'], v['vout'], v['pid'], v['stop'], v['log']) ==
            ('COM3', 240.0, 0.770, 'mining.pid', 'mining.stopped', 'mining_'),
            all(b['mhs'] == b['freq'] / 2 * b['pipes'] for b in (v, u)),
            u['vout'] is None and u['port'] == 'auto',
            not any(fnmatch(u['log'] + '20261031_000000.err', 'mining_*.err') or u[k] == v[k]
                    for k in ('pid', 'stop')),
            L.pong_pipes(b'QTC\x14') == 2 and L.pong_pipes(b'QTC\x04') == 1]
    # kv260: folded core (pong high nibble F = 1/8 pipeline), files apart, clock tables = the RTL's
    k = M.BOARDS['kv260']
    rtl = Path(__file__).resolve().parents[1] / 'rtl' / 'gen'

    def svh_mhz(name):
        m = re.search(r"FREQ_MHZ10 \[FREQ_N\] = '\{([^}]*)\}", (rtl / f'mmcm_freq_{name}.svh').read_text())
        return [int(x) / 10 for x in m.group(1).split(',')]
    seen += [k['mhs'] == k['freq'] / 2 * k['pipes'] and L.pong_pipes(b'QTC\xf4') == k['pipes'] == 0.125,
             not fnmatch(k['log'] + '20261103_000000.err', 'mining_*.err') and k['pid'] not in (v['pid'], u['pid']),
             all(abs(a - b) < 0.06 for name in ('vcu118', 'kv260')
                 for a, b in zip(L.FREQ_TABLES[name], svh_mhz(name), strict=True)),
             k['freq'] in L.FREQ_KV260]
    print(f'[boards] vcu118 unchanged, mhs=clock/2*pipes, u250 auto port, u250 files apart, pong pipes, '
          f'kv260 rate, kv260 files apart, clock tables = RTL, kv260 freq in table: {seen}')
    ok = all(seen)

    # zcu102: fold-4 core (pong high nibble E = 1/4 pipeline), files apart, VCU118 clock table (the top
    # includes that header), and find_port opens only CP2108 interface 2 (interface 3 is the system
    # controller) while the other auto boards still open only FT4232 ports.
    z = M.BOARDS['zcu102']
    top = (Path(__file__).resolve().parents[1] / 'rtl' / 'zcu102_top.sv').read_text()
    zs = [z['mhs'] == z['freq'] / 2 * z['pipes'] and L.pong_pipes(b'QTC\xe4') == z['pipes'] == 0.25,
          not fnmatch(z['log'] + '20261103_000000.err', 'mining_*.err')
          and z['pid'] not in (v['pid'], u['pid'], k['pid']) and z['stop'] not in (v['stop'], u['stop'], k['stop']),
          '`include "mmcm_freq_vcu118.svh"' in top and z.get('freqs', 'vcu118') == 'vcu118' and z['freq'] in L.FREQ_VCU118]

    class Info:
        def __init__(self, dev, vid, pid, desc, loc):
            self.device, self.vid, self.pid, self.description, self.location = dev, vid, pid, desc, loc
    fake = [Info('COM3', 0x10C4, 0xEA70, 'Silicon Labs Dual CP2105 USB to UART Bridge: Standard COM Port (COM3)', '1-3.4:x.1'),
            Info('COM16', 0x0403, 0x6011, 'USB Serial Port (COM16)', None)]
    fake += [Info(f'COM2{n}', 0x10C4, 0xEA71, f'Silicon Labs Quad CP2108 USB to UART Bridge: Interface {n} (COM2{n})',
                  f'1-5:x.{n}') for n in range(4)]
    fake += [Info('COM30', 0x10C4, 0xEA71, 'Silicon Labs Quad CP2108 USB to UART Bridge (COM30)', '1-6:x.2'),
             Info('COM31', 0x10C4, 0xEA71, 'Silicon Labs Quad CP2108 USB to UART Bridge (COM31)', '1-6:x.3')]
    import fpga_port
    from serial.tools import list_ports
    opened = []

    def no_open(dev, *a, **kw):
        opened.append(dev)
        raise OSError('test: not opened')
    saved = (list_ports.comports, fpga_port.SerialPort, M.B)
    list_ports.comports, fpga_port.SerialPort = (lambda: fake), no_open
    try:
        for name, want in (('zcu102', ['COM22', 'COM30']), ('u250', ['COM16'])):
            opened.clear()
            M.B = {kk: vv for kk, vv in M.BOARDS[name].items() if kk != 'load'}   # never JTAG-load here
            try:
                M.find_port()
            except RuntimeError:
                pass
            zs.append(opened == want)
    finally:
        list_ports.comports, fpga_port.SerialPort, M.B = saved
    # 11/04: only FTDI ports poll in SerialPort.read (the CP210x boards keep their read); kv260 loads kf1 and
    # stops at 72 C on the PC, the vcu118 relay keeps the default limit
    import serial
    saved = (list_ports.comports, serial.Serial)
    list_ports.comports, serial.Serial = (lambda: fake), (lambda *a, **kw: None)
    try:
        polls = [fpga_port.SerialPort(dev).poll for dev in ('COM3', 'COM22', 'COM16')]
    finally:
        list_ports.comports, serial.Serial = saved
    kbit = Path(k['load'][0]).parent / k['load'][1] / 'qtc_kv260.bit'
    zs += [polls == [False, False, True], 'temp_limit' not in v and k['temp_limit'] == 72.0 and kbit.exists()]
    print(f'[boards] zcu102 rate, zcu102 files apart, zcu102 clock table, zcu102 opens only CP2108 '
          f'interface 2, u250 still only FT4232, only FTDI polls, kv260 kf1 + 72 C / vcu118 unchanged: {zs}')
    return ok and all(zs)


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    ok = True
    with tempfile.TemporaryDirectory() as d:
        cert, key = make_cert(d)
        for name, fault, port in (('clean', 0.0, 34301), ('fault-injected', 0.002, 34302)):
            relay_stats, pool_stats = asyncio.run(one_run(port, cert, key, seconds, fault))
            print(f'[{name}] relay: {relay_stats}')
            print(f'[{name}] pool:  {pool_stats}')
            # A share submitted while the pool is switching jobs can come back stale (also on the real
            # pool); anything else rejected is a failure.
            stale = pool_stats['reasons'].get('Stale share', 0)
            good = pool_stats['accepted'] > 0 and relay_stats['accepted'] == pool_stats['accepted']
            good &= pool_stats['rejected'] == stale and stale <= 0.05 * (pool_stats['accepted'] + stale)
            good &= relay_stats['monitor_ok'] > 0
            good &= (relay_stats['hw_errors'] == 0) if fault == 0 else (relay_stats['hw_errors'] > 0)
            print(f'[{name}]', 'PASS' if good else 'FAIL')
            ok &= good
    ok &= thermal_check()
    ok &= power_check()
    ok &= regulator_guard_check()
    ok &= board_config_check()
    print('RESULT', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
