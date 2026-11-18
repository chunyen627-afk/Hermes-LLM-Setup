import json, time
import pynvml as n
from pathlib import Path
n.nvmlInit()
results=[]
try:
    for index in range(3):
        h=n.nvmlDeviceGetHandleByIndex(index)
        for fan in range(n.nvmlDeviceGetNumFans(h)):
            before=n.nvmlDeviceGetFanSpeed_v2(h,fan)
            try:
                n.nvmlDeviceSetFanSpeed_v2(h,fan,90)
                time.sleep(1)
                manual=n.nvmlDeviceGetFanSpeed_v2(h,fan)
            finally:
                n.nvmlDeviceSetDefaultFanSpeed_v2(h,fan)
            results.append({'gpu':index,'fan':fan,'before':before,'manual_readback':manual,'auto_restored':True})
    Path(__file__).with_suffix('.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results),flush=True)
finally:
    n.nvmlShutdown()
