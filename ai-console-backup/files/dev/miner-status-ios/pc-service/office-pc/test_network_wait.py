import unittest
from guarded_miner import NetworkWait

class NetworkWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000; self.up = True; self.calls = 0
        def reachable():
            self.calls += 1; return self.up
        self.net = NetworkWait(reachable, lambda: self.now)
    def test_network_fine_means_the_miner_failed(self):
        self.assertEqual(self.net.check(), 'fail')
    def test_outage_waits_and_rechecks_once_a_minute(self):
        self.up = False
        self.assertEqual(self.net.check(), 'wait')
        self.now += 30; self.assertEqual(self.net.check(), 'wait')
        self.assertEqual(self.calls, 1)
        self.now += 31; self.assertEqual(self.net.check(), 'wait')
        self.assertEqual(self.calls, 2)
        self.now += 3600 * 8; self.assertEqual(self.net.check(), 'wait')  # hours: still waiting
    def test_return_restarts_the_window_then_failures_count_again(self):
        self.up = False; self.net.check()
        self.up = True; self.now += 60
        self.assertEqual(self.net.check(), 'back')
        self.assertFalse(self.net.down)
        self.now += 900; self.assertEqual(self.net.check(), 'fail')

if __name__ == '__main__': unittest.main()
