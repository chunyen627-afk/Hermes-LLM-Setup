"""Pure checks: no miner launch, GPU settings or account/network access."""
import unittest
from portable_guard import check

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

    def test_unvalidated_template_never_touches_gpu(self):
        import session, tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.local.json').write_text(json.dumps({'validated_for_this_machine':False}))
            with patch.object(session, 'ROOT', root), patch.object(session.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=True), patch.object(session, 'device') as probe:
                with self.assertRaises(RuntimeError): session.main()
                probe.assert_not_called()

if __name__ == '__main__': unittest.main()
