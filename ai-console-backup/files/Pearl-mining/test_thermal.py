import unittest
import datetime
from thermal_control import Thermal,BASE,QUIET,base_now,OFFDAYS

class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.now=0; self.calls=[]; self.profile=BASE
        self.control=Thermal(lambda *x:self.calls.append(x),lambda:self.now,lambda:self.profile)
    def rows(self,temp=70):
        return [dict(index=i,temperature=temp,limit=p,core=c) for i,(p,c) in enumerate(self.control.settings)]
    def test_normal(self):
        self.assertEqual(self.control.update(self.rows()),[])
    def test_step(self):
        self.control.update(self.rows(76))
        self.assertEqual(self.control.settings,[(105,1350),(100,1200),(120,1650)])
        self.now=10; self.control.update(self.rows(76))
        self.assertEqual(len(self.calls),3)
    def test_80_derates_instead_of_stopping(self):
        self.control.update(self.rows(80))
        self.assertEqual(self.control.settings,[(100,300)]*3)
        self.now=20; self.control.update(self.rows(79))
        self.now=29; self.control.update(self.rows(77))
        self.now=60; self.control.update(self.rows(70))
        self.assertEqual(self.control.settings,[(100,300)]*3)
    def test_recovers_after_three_cool_minutes(self):
        self.control.update(self.rows(76))
        derated=list(self.control.settings)
        self.now=40; self.control.update(self.rows(70))   # cool from here
        self.now=200; self.control.update(self.rows(70))  # 160 s: not yet
        self.assertEqual(self.control.settings,derated)
        self.now=221; self.control.update(self.rows(70))  # 181 s: one step back up
        self.assertEqual(self.control.settings,list(BASE))
        self.now=400; self.control.update(self.rows(70))
        self.assertEqual(self.control.settings,list(BASE))  # never past BASE
    def test_warm_resets_the_cool_timer(self):
        self.control.update(self.rows(76))
        derated=list(self.control.settings)
        self.now=40; self.control.update(self.rows(70))
        self.now=150; self.control.update(self.rows(72))
        self.now=250; self.control.update(self.rows(70))
        self.assertEqual(self.control.settings,derated)
    def test_failed_cooling(self):
        self.control.update(self.rows(80)); self.now=30
        with self.assertRaises(RuntimeError): self.control.update(self.rows(79))
    def test_hard_cutoff(self):
        with self.assertRaises(RuntimeError): self.control.update(self.rows(82))
        self.assertEqual(self.calls,[])
    def test_apply_failure(self):
        def fail(*args): raise RuntimeError('driver failure')
        self.control.apply=fail
        with self.assertRaises(RuntimeError): self.control.update(self.rows(80))
    def test_readback_failure(self):
        old=self.rows(80); self.control.update(old); self.now=6
        with self.assertRaises(RuntimeError): self.control.update(old)

    def test_quiet_hours_are_weekdays_eight_to_six(self):
        at=lambda d,h,m=0: base_now(datetime.datetime(2026,11,d,h,m))  # 11/02 is a Monday
        self.assertEqual(at(3,8),QUIET); self.assertEqual(at(3,17,59),QUIET); self.assertEqual(at(6,12),QUIET)
        self.assertEqual(at(3,7,59),BASE); self.assertEqual(at(3,18),BASE)
        self.assertEqual(at(7,12),BASE); self.assertEqual(at(8,12),BASE)  # Saturday, Sunday
    def test_holidays_follow_the_government_calendar(self):
        noon=lambda y,m,d: base_now(datetime.datetime(y,m,d,12))
        self.assertEqual(len([d for d in OFFDAYS if d.year in (2026,2027)]),730)
        self.assertEqual(noon(2026,12,25),BASE)  # 行憲紀念日, a Friday
        self.assertEqual(noon(2026,12,24),QUIET)
        self.assertEqual(noon(2027,2,9),BASE)    # 春節補假, a Tuesday
        self.assertEqual(noon(2028,1,3),QUIET)   # no 2028 file yet: Monday-Friday
        made_up={datetime.date(2026,11,7):False}  # a make-up Saturday
        self.assertEqual(base_now(datetime.datetime(2026,11,7,12),made_up),QUIET)
    def test_quiet_hours_switch_without_restart(self):
        self.profile=QUIET; events=self.control.update(self.rows(65))
        self.assertEqual(self.control.settings,QUIET)
        self.assertEqual(self.calls,[(i,*QUIET[i]) for i in range(3)])
        self.assertEqual(len(events),3)
        self.now=10; self.control.update(self.rows(65))  # readback now matches: no stop
        self.profile=BASE; self.now=20; self.control.update(self.rows(65))
        self.assertEqual(self.control.settings,BASE)
    def test_recovery_stops_at_the_quiet_base(self):
        self.profile=QUIET; self.control.update(self.rows(65))
        self.now=40; self.control.update(self.rows(76))
        self.assertEqual(self.control.settings[0],(100,1100))
        for self.now in range(80,800,20): self.control.update(self.rows(70))
        self.assertEqual(self.control.settings,QUIET)
    def test_derated_card_is_not_raised_by_the_switch(self):
        self.control.update(self.rows(76))  # normal hours: (105,1350),(100,1200),(120,1650)
        self.profile=QUIET; self.now=2; self.control.update(self.rows(70))
        self.assertEqual(self.control.settings,[(100,1250),(100,1100),(100,1600)])
        self.control.settings[1]=(100,900); self.control.base=list(QUIET)
        self.profile=BASE; self.now=4; self.control.update(self.rows(70))
        self.assertEqual(self.control.settings[1],(100,900))
        self.assertEqual(self.control.settings[0],BASE[0])
    def test_emergency_card_keeps_its_minimum_when_hours_change(self):
        self.control.update(self.rows(80))
        self.profile=QUIET; self.now=2; self.control.update(self.rows(79))
        self.assertEqual(self.control.settings,[(100,300)]*3)

if __name__=='__main__': unittest.main()
