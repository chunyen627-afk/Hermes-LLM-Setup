"""Offline integration checks: never launches a miner or changes clocks."""
import importlib.util, pathlib, sys, unittest
from unittest.mock import patch, Mock
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('panel',ROOT.parent/'Strata'/'media_control.py')
panel=importlib.util.module_from_spec(spec); spec.loader.exec_module(panel)
import panel_bridge

class Tests(unittest.TestCase):
    def test_window_controls(self):
        p=panel.Panel(None)
        try:
            def labels(w):
                result=[]
                for child in w.winfo_children():
                    try: result.append(child.cget('text'))
                    except Exception: pass
                    result+=labels(child)
                return result
            self.assertIn('本機 AI 控制台',labels(p.root))  # 珍珠挖礦 moved to the GPU tab's rig list (11/10: the old check had been failing)
            self.assertIn('停止 AI 服務・回到挖礦',labels(p.root))
            self.assertIsNone(p.job)
            p.root.update_idletasks()
        finally: p.root.destroy()
    def test_owner_exit_does_not_cancel(self):
        # 2026-11-10: the console closing or restarting leaves mining running; only a stop request does.
        with patch.object(panel_bridge.win32event,'WaitForSingleObject',return_value=0),patch.object(pathlib.Path,'read_text',side_effect=FileNotFoundError):
            self.assertFalse(panel_bridge.cancelled(None,0))
    def test_console_start_takes_over_running_mining_and_defaults_to_mining(self):
        p=Mock(closed=False)
        with patch.object(panel,'mining_state',return_value={'status':'running','token':'t1','updated':panel.time.time()}):
            panel.Panel.default_mode(p)
        p.adopt_mining.assert_called_once(); p.start.assert_not_called()
        p=Mock(closed=False)
        with patch.object(panel,'mining_state',return_value={}),patch.object(panel,'fetch',return_value={}):
            panel.Panel.default_mode(p)
        p.start.assert_called_once_with('Mining')
        p=Mock(closed=False)  # a model started outside the console keeps its GPUs
        with patch.object(panel,'mining_state',return_value={}),patch.object(panel,'fetch',return_value={'loaded':True}):
            panel.Panel.default_mode(p)
        p.start.assert_not_called()
    def test_closing_the_window_keeps_mining(self):
        p=panel.Panel(None)
        try:
            p.root.after_cancel  # the 100 ms default_mode call never runs: no mainloop
            p.mode, p.mining_token = 'Mining', 't1'
            written=[]
            with patch.object(panel,'save_json',side_effect=lambda path,value: written.append(path.name)):
                p.close()
            self.assertNotIn('panel-stop.json',written)
            self.assertIn(panel.STATE.name,written)
            self.assertEqual(p.mode,'Mining')
        except Exception:
            try: p.root.destroy()
            except Exception: pass
            raise
    def test_old_launch_cannot_run_after_stop(self):
        with patch.object(panel_bridge.win32event,'WaitForSingleObject',return_value=258),patch.object(pathlib.Path,'read_text',return_value='{"time":200}'):
            self.assertTrue(panel_bridge.cancelled(None,100))
            self.assertFalse(panel_bridge.cancelled(None,300))
    def test_cancelled_generation_never_launches(self):
        p=Mock(closed=False,busy=True,generation=2)
        panel.Panel.prepare_mode(p,'Mining',1,9999999999)
        p.root.after.assert_not_called()
    def test_elevation_denial_is_reported(self):
        p=Mock()
        p.task_launch.return_value=None  # no PearlPanelBridge task: the RunAs fallback is what is tested
        with patch.object(panel.subprocess,'run',return_value=Mock(returncode=1,stderr='UAC cancelled')):
            panel.Panel.admin_launch(p,'Mining','123',5)
        p.admin_result.put.assert_called_once_with((5,'Mining','123','UAC cancelled'))

if __name__=='__main__': unittest.main()
