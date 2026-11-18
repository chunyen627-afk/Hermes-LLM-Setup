import unittest
from test_panel_integration import panel

class Tests(unittest.TestCase):
    def light(self, **changes):
        args=dict(mode='Mining',busy=False,error='',mining=dict(token='1',status='running',updated=100,th_s=74,accepted=3,gpus=[dict(temperature=70)]),token='1',current={},now=101)
        args.update(changes)
        return panel.health_indicator(**args)[0]
    def test_normal(self): self.assertEqual(self.light(),'#15803d')
    def test_manual_stop_ignores_old_error(self): self.assertEqual(self.light(mode=None,error='old'),'#6b7280')
    def test_stale_not_green(self): self.assertEqual(self.light(now=120),'#b77900')
    def test_crashed(self): self.assertEqual(self.light(mining=dict(token='1',status='stopped')),'#b42318')
    def test_missing(self): self.assertEqual(self.light(mining={}),'#b77900')
    def test_hot(self): self.assertEqual(self.light(mining=dict(token='1',status='running',updated=100,gpus=[dict(temperature=80)])),'#b77900')
    def test_ai_dead(self): self.assertEqual(self.light(mode='Image',ai_alive=False),'#b42318')

if __name__=='__main__': unittest.main()
