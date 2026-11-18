"""Real Windows job inheritance/termination check using harmless sleeping children."""
import json
import os
from pathlib import Path
import sys
import time
import psutil
from media_control import ServiceJob

job=ServiceJob()
pids=[]
try:
    code="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);time.sleep(60)"
    job.launch([sys.executable,'-c',code])
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        pids=job.pids()
        if len(pids)>=2: break
        time.sleep(.2)
    assert len(pids)>=2, 'Child did not inherit job ownership'
    assert os.getpid() not in pids
finally: job.close()
deadline=time.monotonic()+10
while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic()<deadline: time.sleep(.2)
assert not any(psutil.pid_exists(pid) for pid in pids), 'A child survived closing the owner'
report={'status':'passed','child_pids':pids,'all_exited':True,'scope':'Real nested process inheritance and kill-on-close; no models involved.'}
Path(__file__).with_name('control-lifecycle-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
