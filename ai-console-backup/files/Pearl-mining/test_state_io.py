"""Real Windows sharing-lock regression tests; no mining or GPU changes."""
import json, pathlib, tempfile, threading, time, unittest
from unittest.mock import patch
import panel_bridge as bridge

class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name)/'state.json'
        self.mock = patch.object(bridge,'STATE',self.path)
        self.mock.start()
        bridge.save({'status':'old'})
    def tearDown(self):
        self.mock.stop()
        self.tmp.cleanup()
    def test_real_locked_file_does_not_abort_telemetry(self):
        with self.path.open('rb'):
            self.assertFalse(bridge.save({'status':'new'},strict=False))
            self.assertEqual(json.loads(self.path.read_text())['status'],'old')
        self.assertTrue(bridge.save({'status':'new'},strict=False))
        self.assertEqual(json.loads(self.path.read_text())['status'],'new')
        self.assertFalse(list(self.path.parent.glob('*.tmp')))
    def test_retry_succeeds_when_reader_releases(self):
        reader=self.path.open('rb')
        timer=threading.Timer(.08,reader.close)
        timer.start()
        try: self.assertTrue(bridge.save({'status':'retried'}))
        finally: timer.join(); reader.close()
    def test_critical_writes_still_report_failure(self):
        with self.path.open('rb'):
            with self.assertRaises(PermissionError): bridge.save({'status':'critical'})
    def test_concurrent_readers_always_get_complete_json(self):
        done=threading.Event(); errors=[]
        def read():
            while not done.is_set():
                try: json.loads(self.path.read_text())
                # The panel already treats a transient read denial as an
                # unavailable snapshot and polls again; never consume partial JSON.
                except PermissionError: pass
                except Exception as exc: errors.append(exc)
                time.sleep(.001)
        thread=threading.Thread(target=read); thread.start()
        try:
            for n in range(100): bridge.save({'counter':n},strict=False)
        finally: done.set(); thread.join()
        self.assertEqual(errors,[])

if __name__=='__main__': unittest.main()
