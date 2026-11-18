"""Start / stop FPGA pool mining in the background (used by the phone app through
miner-status-ios/pc-service/office_aggregator.py). The last line printed is always one of:
  start: started | already-running | start-failed
  stop:  stopped | not-running

  python mining_ctl.py [--board vcu118|u250|kv260|zcu102] start
  python mining_ctl.py [--board vcu118|u250|kv260|zcu102] stop

--board defaults to vcu118. Each board has its own port, pid file, stop mark and log prefix, so
both can mine at once; the u250 files are named so the vcu118 globs (mining_*.err) skip them.

start checks the board first (ping, temperature, VCCINT, a short mining run whose every candidate is
re-hashed; a KV260 that does not answer is first loaded over JTAG, see below) and then launches relay.py detached, logging to logs/<log prefix><date>_<time>.err and writing
the board's pid file. stop ends the relay and sends the FPGA a stop frame (the relay is killed, so its own
cleanup does not run). Never touches flash; only boards with a 'load' script (KV260: JTAG-loads the
bitstream, PS setup and the R5 UART bridge, all volatile) are ever programmed. Returns within 60 s (KV260: 90 s).

The running parameters below are the current stable choice; change them here only.
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / 'logs'
POOL = 'kryptex-hk'
START_TEMP_MAX = 75.0                         # C, the FPGA's own resume threshold

# port 'auto' = the FT4232 serial port whose FPGA answers ping with this board's pipeline count.
# vout = VCCINT setpoint (V) for the relay, None = board default. pid / stop mark / log prefix are
# under LOGS; the stop mark tells mining_watch.py the stop was on purpose.
BOARDS = {
    # 10/28: board fan on a fixed 7 V; 300 MHz trips the VCCINT supply once the die passes ~60 C.
    # 10/29: the VCCINT power stage (no extra airflow) is the limit: 282.4 MHz @ 0.850 V reached 122-133 C.
    # 240 MHz @ 0.770 V: ~66 A, regulator ~92 C, no wrong results down to 0.750 V (relay stops at 110 C).
    'vcu118': dict(port='COM3', freq=240.0, mhs=120.0, pipes=1, vout=0.770,
                   pid='mining.pid', stop='mining.stopped', log='mining_'),
    # 10/31, not on a board yet: two pipelines, hash rate = clock; timing guaranteed to 311.8 MHz.
    # VCCINT is fixed at 0.85 V by the card's satellite controller (no I2C power readings either).
    # Estimated card power 150-175 W at 240 MHz, 170-205 W at 300 MHz (card limit 215 W, passive cooling).
    'u250': dict(port='auto', freq=240.0, mhs=240.0, pipes=2, vout=None,
                 pid='u250_mining.pid', stop='u250_mining.stopped', log='u250_mining_'),
    # 11/03, not on a board yet: folded core (hash rate = clock / 16), K26 -2LV, VCCINT not adjustable.
    # Build k3 meets timing at 266.7 MHz (Vivado estimate 9.4 W on chip). kf1 (11/04) = k3 with a quieter fan
    # (25 % up to 60 C) and the FPGA stopping at 75 C / resuming at 65 C; the relay stops at 72 C.
    # Reached through the PS: the board answers only after load_kv260.bat (JTAG) has run since power-up.
    'kv260': dict(port='auto', freq=266.7, mhs=266.7 / 16, pipes=0.125, vout=None, freqs='kv260', vcc=(0.68, 0.90),
                  load=[str(HERE.parent / 'synth' / 'kv260' / 'load_kv260.bat'), 'kf1'],
                  temp_limit=72.0,
                  pid='kv260_mining.pid', stop='kv260_mining.stopped', log='kv260_mining_'),
    # 11/03, not on a board yet: fold-4 core (hash rate = clock / 8), XCZU9EG -2, VCCINT 0.85 V on a 40 A
    # regulator the PL cannot reach. PL UART = CP2108 channel 2 (micro-USB J83); channel 3 of that chip is
    # the board's system controller and is never opened. A board that does not answer is first loaded over
    # JTAG (micro-USB J2, volatile, SW6 = JTAG boot) by program_zcu102.bat.
    # 11/04 z2 bitstream: timing met at 300 MHz (WNS +0.004 ns); Vivado estimates VCCINT 22.7 A there.
    'zcu102': dict(port='auto', usb=(0x10C4, 0xEA71, 'Interface 2'), freq=300.0, mhs=37.5, pipes=0.25, vout=None,
                   load=[str(HERE.parent / 'synth' / 'zcu102' / 'program_zcu102.bat')],
                   pid='zcu102_mining.pid', stop='zcu102_mining.stopped', log='zcu102_mining_'),
}
BOARD = 'vcu118'                              # set by main()
B = BOARDS[BOARD]
SELFTEST_S = 8.0

sys.path[:0] = [str(HERE), str(HERE.parent / 'ref_model')]


def relay_pid():
    """PID from the board's pid file if that process is alive and is relay.py, else None."""
    try:
        pid = int((LOGS / B['pid']).read_text().strip())
    except (OSError, ValueError):
        return None
    out = command_line(pid)
    return pid if 'relay.py' in out else None


def command_line(pid):
    return subprocess.run(['powershell', '-NoProfile', '-Command',
                           f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
                          capture_output=True, text=True, timeout=20).stdout


def find_port(loaded=False):
    """B['port'], or for 'auto' the port whose FPGA answers ping with B['pipes'] pipelines. Only ports
    matching B['usb'] = (vid, pid[, interface]) are opened (default: FTDI FT4232 0403:6011), never the
    VCU118's CP2105 port; with an interface, only ports named "Interface n" (or at USB location x.n)."""
    if B['port'] != 'auto':
        return B['port']
    import qtc_link as L
    from serial.tools import list_ports
    from bringup import ask
    from fpga_port import SerialPort
    seen = []
    for info in sorted(list_ports.comports(), key=lambda i: i.device):
        usb = B.get('usb', (0x0403, 0x6011))
        if (info.vid, info.pid) != usb[:2] or info.device == BOARDS['vcu118']['port']:
            continue
        if len(usb) > 2 and usb[2] not in (info.description or '') \
                and not (info.location or '').endswith('x.' + usb[2].split()[-1]):
            continue
        seen.append(info.device)
        try:
            link = SerialPort(info.device)
        except Exception:                     # held by another program (relay, terminal)
            continue
        try:
            p = ask(link, L.ping_frame(), 'P', timeout=1.0)
        finally:
            link.close()
        if p and p[:3] == b'QTC' and L.pong_pipes(p) == B['pipes']:
            return info.device
    if B.get('load') and not loaded:          # nothing answers: load the board over JTAG, then look again
        r = subprocess.run(['cmd', '/c'] + B['load'], capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip().splitlines()
        print(f"JTAG load: {'LOADED' if 'LOADED' in r.stdout else 'failed'}")
        if 'LOADED' not in r.stdout:
            raise RuntimeError('JTAG load failed: ' + (out[-1] if out else 'no output'))
        time.sleep(1)
        return find_port(loaded=True)
    raise RuntimeError(f'no {BOARD} FPGA answering on the {BOARD} USB-UART ports {seen or "(none found)"}')


def stop_fpga(port):
    import qtc_link as L
    from fpga_port import SerialPort
    link = SerialPort(port)
    try:
        link.write(L.run_frame(False))
        time.sleep(0.2)
    finally:
        link.close()


def selftest(port):
    """Returns an error string, or None when the board is fit to mine."""
    import qtc_link as L
    from bringup import ask, VCCINT_OK
    from fpga_port import SerialPort
    from freq_sweep import run_step
    link = SerialPort(port)
    try:
        link.write(L.run_frame(False))
        p = ask(link, L.ping_frame(), 'P')
        if not p or p[:3] != b'QTC':
            return 'FPGA does not answer (not programmed? board off?)'
        if L.pong_pipes(p) != B['pipes']:
            return f'{L.pong_pipes(p)}-pipeline bitstream on {port}, {BOARD} expects {B["pipes"]}'
        st = L.parse_status(ask(link, L.query_frame(), 'S'))
        print(f'board: {st.temp_c:.1f} C, VCCINT {st.vccint_v:.3f} V, flags {st.flags:#x}')
        if not (5.0 <= st.temp_c <= START_TEMP_MAX) or st.over_temp:
            return f'temperature {st.temp_c:.1f} C outside 5..{START_TEMP_MAX} C'
        # the regulator keeps the relay's lowered setpoint across a relay restart
        vlo, vhi = B.get('vcc', VCCINT_OK)
        lo = min(vlo, B['vout'] - 0.02) if B['vout'] else vlo
        if not lo <= st.vccint_v <= vhi or st.freq_fail:
            return f'VCCINT {st.vccint_v:.3f} V or clock failure (flags {st.flags:#x})'
        freqs = L.FREQ_TABLES[B.get('freqs', 'vcu118')]
        idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - B['freq']))
        expected = freqs[idx] * 1e6 / 2 * L.pong_pipes(p)
        r = run_step(link, idx, SELFTEST_S, 50.0, expected, freqs)
        print(f'selftest {freqs[idx]} MHz: {r}')
        if r.get('error'):
            return r['error']
        if not r['verified'] or r['wrong'] or r['missing'] or r['over_temp'] or not 0.97 <= r['hs'] / expected <= 1.01:
            return 'selftest failed'
        return None
    finally:
        link.write(L.run_frame(False))
        link.close()


def start():
    if relay_pid():
        return 'already-running'
    try:
        port = find_port()
        err = selftest(port)
    except Exception as e:                    # COM port busy, missing driver, no board found, ...
        err = f'{type(e).__name__}: {e}'
    if err:
        print(err)
        return 'start-failed'
    LOGS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = open(LOGS / f'{B["log"]}{stamp}.log', 'ab')
    errf = open(LOGS / f'{B["log"]}{stamp}.err', 'ab')
    args = [sys.executable, '-u', 'relay.py', '--pool', POOL, '--fpga', port, '--worker', BOARD,
            '--freq', str(B['freq']), '--freq-table', B.get('freqs', 'vcu118'),
            '--expected-mhs', str(B['mhs'])] + (['--vout', str(B['vout'])] if B['vout'] else []) \
        + (['--temp-limit', str(B['temp_limit'])] if 'temp_limit' in B else [])
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:      # leave the caller's job object so the relay outlives it
        p = subprocess.Popen(args, cwd=HERE, stdout=out, stderr=errf, stdin=subprocess.DEVNULL,
                             creationflags=flags | 0x01000000)          # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        p = subprocess.Popen(args, cwd=HERE, stdout=out, stderr=errf, stdin=subprocess.DEVNULL, creationflags=flags)
    (LOGS / B['pid']).write_text(str(p.pid))
    (LOGS / B['stop']).unlink(missing_ok=True)
    time.sleep(12)                            # relay opens the port, sets the clock, connects
    if p.poll() is not None:
        print(f'relay exited with {p.returncode}, see {errf.name}')
        return 'start-failed'
    print(f'relay PID {p.pid}, log {errf.name}')
    return 'started'


def stop():
    pid = relay_pid()
    if not pid:
        return 'not-running'
    (LOGS / B['stop']).write_text(datetime.now().isoformat(timespec='seconds'))
    words = command_line(pid).split()         # the port the relay was started on ('auto' boards)
    port = words[words.index('--fpga') + 1] if '--fpga' in words[:-1] else B['port']
    subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True, timeout=20)
    for _ in range(20):                       # wait for the COM port to be released
        time.sleep(0.5)
        try:
            stop_fpga(port)
            break
        except Exception:
            continue
    else:
        print('could not send the FPGA stop frame (its 60 s watchdog stops it anyway)')
    return 'stopped'


def main():
    global B, BOARD
    args = sys.argv[1:]
    if '--board' in args:
        i = args.index('--board')
        BOARD = args[i + 1] if i + 1 < len(args) else ''
        del args[i:i + 2]
    if len(args) != 1 or args[0] not in ('start', 'stop') or BOARD not in BOARDS:
        print(__doc__)
        return 2
    B = BOARDS[BOARD]
    result = start() if args[0] == 'start' else stop()
    print(result)
    return 0 if result in ('started', 'already-running', 'stopped', 'not-running') else 1


if __name__ == '__main__':
    sys.exit(main())
