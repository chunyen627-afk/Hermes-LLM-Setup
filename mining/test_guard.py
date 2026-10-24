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

if __name__ == '__main__': unittest.main()
