"""Native NVIDIA fan control, independent of Afterburner."""
import pynvml as n

def target_for(temp):
    return max(40, min(100, round(40 + (temp-40)*60/35)))

class Fans:
    def __init__(self):
        n.nvmlInit()
        self.handles=[n.nvmlDeviceGetHandleByIndex(i) for i in range(3)]
        self.previous={}

    def update(self,rows):
        readings=[]
        for row in rows:
            index=row['index']; h=self.handles[index]
            target=target_for(row['temperature'])
            old=self.previous.get(index)
            if old is not None and target < old and old-target < 4:
                target=old
            for fan in range(n.nvmlDeviceGetNumFans(h)):
                n.nvmlDeviceSetFanSpeed_v2(h,fan,target)
                actual=n.nvmlDeviceGetTargetFanSpeed(h,fan)
                if actual != target:
                    raise RuntimeError(f'GPU{index} fan{fan} target not applied: {actual}/{target}')
                readings.append({'gpu':index,'fan':fan,'target':target,'speed':n.nvmlDeviceGetFanSpeed_v2(h,fan)})
            self.previous[index]=target
        return readings

    def restore(self):
        errors=[]
        for h in self.handles:
            for fan in range(n.nvmlDeviceGetNumFans(h)):
                try: n.nvmlDeviceSetDefaultFanSpeed_v2(h,fan)
                except Exception as error: errors.append(str(error))
        if errors: raise RuntimeError('; '.join(errors))

if __name__=='__main__':
    Fans().restore()
    print('All fans restored to NVIDIA automatic policy.')
