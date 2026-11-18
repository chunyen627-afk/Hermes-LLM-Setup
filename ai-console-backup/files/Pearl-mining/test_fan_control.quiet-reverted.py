import unittest
from fan_control import Quiet, FLOOR, TARGET

class QuietTests(unittest.TestCase):
    def test_starts_on_the_old_curve(self):
        self.assertEqual(Quiet(68,0).fan,88)
    def test_lowers_one_point_every_ten_seconds_when_cool(self):
        q=Quiet(68,0)
        for now in range(2,22,2): q.update(68,now)
        self.assertEqual(q.fan,86)
    def test_never_below_floor(self):
        q=Quiet(50,0)
        for now in range(2,2000,2): q.update(50,now)
        self.assertEqual(q.fan,FLOOR)
    def test_holds_at_target(self):
        q=Quiet(68,0); q.fan=70
        for now in range(2,60,2): q.update(TARGET,now)
        self.assertEqual(q.fan,70)
    def test_rises_above_target(self):
        q=Quiet(68,0); q.fan=70
        for now in range(2,12,2): q.update(TARGET+1,now)
        self.assertEqual(q.fan,75)
    def test_three_over_jumps_to_old_curve(self):
        q=Quiet(68,0); q.fan=60
        self.assertEqual(q.update(TARGET+3,2),100)
    def test_derated_card_aims_lower(self):
        q=Quiet(68,0); q.fan=70
        q.update(70,2,derated=True)
        self.assertEqual(q.fan,71)
    def test_settles_near_target_on_a_simple_card(self):
        # Steady state temperature falls 0.3C per fan point, one-minute lag.
        q=Quiet(68,0); temp=68.0
        for now in range(2,3600,2):
            steady=68+(88-q.fan)*0.3
            temp+=(steady-temp)*2/60
            q.update(round(temp),now)
            self.assertLess(round(temp),TARGET+3)
        self.assertLess(q.fan,80)
        self.assertLessEqual(abs(round(temp)-TARGET),1)

if __name__=='__main__': unittest.main()
