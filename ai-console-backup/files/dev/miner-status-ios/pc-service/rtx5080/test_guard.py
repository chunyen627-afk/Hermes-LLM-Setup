"""Pure checks: no miner launch, GPU settings or account/network access."""
import unittest
from portable_guard import check
from portable_guard import log_progress, progress_check
import tempfile
from pathlib import Path

class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {'gpus': [{'index': 0, 'uuid': 'GPU-test', 'power_w': 100, 'core_mhz': 750}], 'stop_c': 80, 'idle_memory_mib': 2048}
        self.rows = {0: {'uuid': 'GPU-test', 'temperature': 76, 'power': 100, 'core': 750, 'memory': 500}}

    def test_idle_ready(self):
        self.assertTrue(check(self.config, self.rows, idle=True))

    def test_gpu_not_released(self):
        self.rows[0]['memory'] = 5000
        self.assertFalse(check(self.config, self.rows, idle=True))

    def test_temperature_cutoff(self):
        self.rows[0]['temperature'] = 80
        with self.assertRaises(RuntimeError): check(self.config, self.rows)

    def test_wrong_card(self):
        self.rows[0]['uuid'] = 'GPU-other'
        with self.assertRaises(RuntimeError): check(self.config, self.rows)

    def test_power_changed(self):
        self.rows[0]['power'] = 125
        with self.assertRaises(RuntimeError): check(self.config, self.rows)

    def test_user_70_degree_cutoff(self):
        self.config['stop_c'] = 70
        self.rows[0]['temperature'] = 70
        with self.assertRaises(RuntimeError): check(self.config, self.rows)

    def test_nan_sensor_fails_closed(self):
        self.rows[0]['temperature'] = float('nan')
        with self.assertRaises(RuntimeError): check(self.config, self.rows)

    def test_suspended_child_job_lifecycle(self):
        import win32job, sys
        from portable_guard import JobProcess
        job = win32job.CreateJobObject(None, '')
        process = JobProcess([sys.executable, '-c', 'pass'], job)
        try:
            self.assertEqual(process.wait(10), 0)
        finally:
            process.handle.Close()
            job.Close()

    def test_background_child_has_no_console(self):
        import win32job, sys
        from portable_guard import JobProcess
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'console.txt'
            code = 'import ctypes; from pathlib import Path; Path('+repr(str(marker))+').write_text(str(ctypes.windll.kernel32.GetConsoleWindow()))'
            job = win32job.CreateJobObject(None, '')
            child = JobProcess([sys.executable, '-c', code], job)
            try:
                self.assertEqual(child.wait(10), 0)
                self.assertEqual(marker.read_text(), '0')
            finally:
                child.handle.Close()
                job.Close()

    def test_progress_stall_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'Miner telemetry stalled'):
            progress_check(b'12:00:00.000', b'12:00:00.000', 10, 131)
        with self.assertRaises(RuntimeError):
            progress_check(None, None, 10, 131)
        self.assertEqual(progress_check(b'12:00:30.000', b'12:00:00.000', 10, 30), (b'12:00:30.000',30))

    def test_log_progress_complete_lines_only(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'sample.log'
            self.assertEqual(log_progress(log), (None,0,None))
            log.write_bytes(b'12:00:00.000 Total: 185.0 TH/s shares: 3 accepted 0 stale 0 rejected\n12:00:30.000 Total: 190.0 TH/s shares: 4 accepted')
            stamp,count,summary = log_progress(log)
            self.assertEqual(stamp,b'12:00:00.000')
            self.assertEqual(count,3)
            self.assertIn(b'185.0 TH/s',summary)

class NetworkWaitTests(unittest.TestCase):
    def setUp(self):
        from portable_guard import NetworkWait
        self.now, self.up, self.calls = 1000, True, 0
        def reachable():
            self.calls += 1
            return self.up
        self.net = NetworkWait(reachable, lambda: self.now)

    def test_network_fine_means_the_miner_failed(self):
        self.assertEqual(self.net.check(), 'fail')

    def test_outage_waits_and_rechecks_once_a_minute(self):
        self.up = False
        self.assertEqual(self.net.check(), 'wait')
        self.now += 30
        self.assertEqual(self.net.check(), 'wait')
        self.assertEqual(self.calls, 1)
        self.now += 31
        self.assertEqual(self.net.check(), 'wait')
        self.assertEqual(self.calls, 2)
        self.now += 3600*8
        self.assertEqual(self.net.check(), 'wait')

    def test_return_restarts_the_window_then_failures_count_again(self):
        self.up = False
        self.net.check()
        self.up, self.now = True, self.now+60
        self.assertEqual(self.net.check(), 'back')
        self.assertFalse(self.net.down)
        self.now += 900
        self.assertEqual(self.net.check(), 'fail')

class YieldWatchTests(unittest.TestCase):
    def setUp(self):
        from portable_guard import YieldWatch
        self.now, self.busy = 1000, True
        self.watch = YieldWatch(lambda: self.busy, lambda: self.now)

    def step(self, rate, seconds=2):
        self.now += seconds
        return self.watch.update(rate)

    def test_a_game_pauses_the_timers_without_stopping(self):
        self.watch.update(200)
        self.assertEqual(self.step(200), 0)
        self.assertEqual(self.step(6), 0)  # the first slow tick only notices
        self.assertTrue(self.watch.yielding)
        self.assertEqual(self.step(0), 2)
        self.assertEqual(self.step(6, 3600), 3600)  # an hour of gaming is not an hour without shares
        self.assertEqual(self.step(195), 0)
        self.assertFalse(self.watch.yielding)

    def test_a_slow_miner_on_an_idle_card_still_counts(self):
        self.busy = False
        self.watch.update(200)
        self.assertEqual(self.step(0), 0)
        self.assertEqual(self.step(0, 900), 0)
        self.assertFalse(self.watch.yielding)

    def test_no_reading_or_no_best_yet_is_not_yielding(self):
        self.assertEqual(self.watch.update(None), 0)
        self.assertEqual(self.step(0), 0)
        self.assertFalse(self.watch.yielding)

class FlakyLineTests(unittest.TestCase):
    def test_the_miners_own_log_says_when_it_has_no_pool(self):
        from portable_guard import pool_down
        connected = b"[12:19:00] Connected to prl-hk.kryptex.network:8048\n"
        lost = b"[12:20:51] A0 not connected to a pool\n[12:20:59] Couldn't connect to pool prl.kryptex.network:8048\n"
        self.assertFalse(pool_down(connected))
        self.assertTrue(pool_down(connected + lost))
        self.assertFalse(pool_down(connected + lost + b"[12:23:00] Connected to prl-sg.kryptex.network:8048\n"))
        self.assertFalse(pool_down(b''))

    def test_a_changed_limit_is_put_back_once_then_checked_again(self):
        import portable_guard
        config = {'gpus': [{'index': 0, 'uuid': 'GPU-test', 'power_w': 250, 'core_mhz': 2700}], 'stop_c': 75, 'idle_memory_mib': 6144}
        row = lambda power: {0: {'uuid': 'GPU-test', 'temperature': 60, 'power': power, 'core': 1417, 'memory': 3000}}
        readings, applied = [row(400), row(250)], []
        rows = portable_guard.guarded_rows(config, lambda: applied.append(1), lambda: readings.pop(0), pause=0)
        self.assertEqual((rows[0]['power'], len(applied)), (250, 1))
        readings = [row(400), row(400)]  # it will not go back: stop as before
        with self.assertRaisesRegex(RuntimeError, 'GPU limits mismatch'):
            portable_guard.guarded_rows(config, lambda: None, lambda: readings.pop(0), pause=0)
        with self.assertRaisesRegex(RuntimeError, 'GPU limits mismatch'):  # no session hook: unchanged
            portable_guard.guarded_rows(config, None, lambda: row(400), pause=0)
        hot = {0: dict(row(250)[0], temperature=80)}
        with self.assertRaisesRegex(RuntimeError, 'Temperature cutoff'):  # never re-applied around heat
            portable_guard.guarded_rows(config, lambda: applied.append(1), lambda: hot, pause=0)

class ReadGpusRetryTests(unittest.TestCase):
    def test_a_brief_nvidia_smi_failure_is_retried(self):
        import subprocess
        from unittest import mock
        import portable_guard
        line = '0, GPU-test, 60, 200.5, 250.00, 2400, 3000\n'
        calls = [subprocess.CalledProcessError(255, 'nvidia-smi'), line]
        def fake(*args, **kwargs):
            value = calls.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        with mock.patch.object(portable_guard.subprocess, 'check_output', fake):
            rows = portable_guard.read_gpus(pause=0)
        self.assertEqual(rows[0]['core'], 2400.0)

    def test_a_lasting_failure_still_stops(self):
        import subprocess
        from unittest import mock
        import portable_guard
        def fail(*args, **kwargs):
            raise subprocess.CalledProcessError(255, 'nvidia-smi')
        with mock.patch.object(portable_guard.subprocess, 'check_output', fail):
            with self.assertRaises(subprocess.CalledProcessError):
                portable_guard.read_gpus(attempts=3, pause=0)

if __name__ == '__main__': unittest.main()
