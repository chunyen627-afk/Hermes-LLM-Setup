"""Thermal derating, and recovery once cool again.

Recovery added 2026-10-29 (the user: slow down and keep mining, stop only when it runs away): a hot
night used to leave the cards derated until the next session. Three minutes at or below 70 C steps
a derated card back by +5 W / +150 MHz a minute, never past the current base (BASE, or QUIET on
workdays 08:00-18:00). The 80 C emergency, the failed-cooling stop and the 82 C cutoff are unchanged."""
import csv
import datetime
import pathlib
import subprocess
import time

# 2026-10-29: GPU1 (the sandwiched 3060) 600 -> 750 MHz once memory ran at 5001 (62 W / 66 C at 600).
# With SRBMiner the same clocks drew 61 / 39 / 80 W at 59-63 C (krig: 110 / 68 / 125 W), so the core
# caps went up; the power limits still bound each card. At 1500/1100/1800 the 3070 sat on its 110 W
# limit at 65 C and the middle 3060 used 50 W at 64 C. Tried next: 3070 at 125 W (+2.4 TH/s, 65 -> 72 C,
# and it heats the middle card) and the 3060 at 1500 (76 C, derated to 1350): kept 110 W and 1350.
BASE = [(110,1500),(100,1350),(125,1800)]
# 2026-11-03 (the user: too loud, lower the power so the fans stay near 80 %, weekdays 08:00-18:00
# only, the usual settings otherwise): 100 W is the driver minimum, so the core caps carry the cut.
QUIET = [(100,1250),(100,1100),(100,1600)]
# Workdays come from the DGPA government office calendar (政府行政機關辦公日曆表, data.gov.tw 14718),
# one CSV per year in calendar/: a weekday holiday mines as usual, a make-up Saturday is quiet.
# A year without its file falls back to Monday-Friday; DGPA publishes the next year around June.
CALENDAR = pathlib.Path(__file__).with_name('calendar')

def load_offdays(folder=CALENDAR):
    offdays = {}
    for path in sorted(folder.glob('*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as source:
            for row in csv.reader(source):
                if len(row) >= 3 and row[0].isdigit() and row[2] in ('0','2'):
                    offdays[datetime.date(int(row[0][:4]),int(row[0][4:6]),int(row[0][6:8]))] = row[2]=='2'
    return offdays

OFFDAYS = load_offdays()

def workday(day, offdays=OFFDAYS):
    return not offdays[day] if day in offdays else day.weekday() < 5

def base_now(now=None, offdays=OFFDAYS):
    now = now or datetime.datetime.now()
    return QUIET if workday(now.date(), offdays) and 8 <= now.hour < 18 else BASE

def apply_gpu(index,power,core):
    for args in (['-pl',str(power)],['-lgc',f'{core},{core}']):
        subprocess.run(['nvidia-smi','-i',str(index),*args],check=True,timeout=8,capture_output=True,text=True)

class Thermal:
    def __init__(self,apply=apply_gpu,clock=time.monotonic,profile=base_now):
        self.apply,self.clock,self.profile=apply,clock,profile
        self.base=list(BASE)  # what SET-LOW-CLOCKS.ps1 applied before the session
        self.settings=list(BASE)
        self.changed=[-1e9]*3
        self.emergency=[None]*3
        self.cool_since=[None]*3

    def update(self,rows):
        now=self.clock()
        # Global cutoff checked before spending time on any setting commands.
        if any(r['temperature']>=82 for r in rows):
            raise RuntimeError('82C emergency cutoff')
        events=[]
        base=list(self.profile())
        if base!=self.base:
            # Quiet hours start or end: a card on the old base moves to the new one; a derated card
            # never goes above what it has (recovery raises it later); an emergency card stays put.
            old,self.base=self.base,base
            for i in range(3):
                target=base[i] if self.settings[i]==old[i] else tuple(map(min,self.settings[i],base[i]))
                if self.emergency[i] is None and self.settings[i]!=target:
                    self.apply(i,*target)
                    self.settings[i]=target
                    self.changed[i]=now
                    self.cool_since[i]=None
                    events.append({'gpu':i,'profile':'quiet' if base==QUIET else 'normal','power_w':target[0],'core_mhz':target[1]})
        for r in rows:
            i=r['index']; temp=r['temperature']
            power,core=self.settings[i]
            if now-self.changed[i]>5 and (r['limit']>power+1 or r['core']>core+30):
                raise RuntimeError(f'GPU{i} derating not applied or settings changed')
            if temp<78:
                self.emergency[i]=None
            if self.emergency[i] is not None and now-self.emergency[i]>=30:
                raise RuntimeError(f'GPU{i} did not cool below 78C within 30s of emergency derating')
            target=None
            if temp>=80:
                if self.emergency[i] is None: self.emergency[i]=now
                target=(100,300)
            elif temp>=76 and now-self.changed[i]>=30:
                target=(max(100,power-5),max(300,core-150))
            if temp>70 or self.emergency[i] is not None or (power,core)==self.base[i]:
                self.cool_since[i]=None
            elif self.cool_since[i] is None:
                self.cool_since[i]=now
            elif now-self.cool_since[i]>=180 and now-self.changed[i]>=60:
                target=(min(self.base[i][0],power+5),min(self.base[i][1],core+150))
            if target is not None and target!=(power,core):
                self.apply(i,*target)
                self.settings[i]=target
                self.changed[i]=now
                events.append({'gpu':i,'temperature':temp,'power_w':target[0],'core_mhz':target[1]})
        return events
