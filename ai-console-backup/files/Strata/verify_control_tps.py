"""Read-only UI verification; never starts or stops the user's services."""
import json
from pathlib import Path
import time
import media_control as mc

mc.STATE = mc.ROOT / 'control-tps-preview-state.json'
mc.REQUEST = mc.ROOT / 'control-tps-preview-request.json'
mc.REQUEST.unlink(missing_ok=True)

class ReadOnlyJob:
    def pids(self): return []
    def exit_code(self): return 0
    def close(self): pass

panel = mc.Panel(None)
panel.root.title('本機 AI 控制台｜TPS 唯讀驗證')
panel.root.update()
assert panel.job is None and panel.mode is None
assert not any('launch' in str(panel.root.tk.call('after', 'info', token))
               for token in panel.root.tk.call('after', 'info'))
checks = {'manual_open_idle': True, 'no_services_owned': True}
assert '20.5' in mc.speed_text({'state':'generating','tok_s':20.5,'tok_s_mean':19.7})
assert '19.7' in mc.speed_text({'state':'generating','tok_s':20.5,'tok_s_mean':19.7})
assert '讀取輸入' in mc.speed_text({'state':'reading','tok_s':20.5})
assert '未生成' in mc.speed_text({'state':'idle','tok_s':20.5})
assert '尚未取得' in mc.speed_text({})
checks['speed_states_passed'] = True

def disable_actions(widget):
    for child in widget.winfo_children():
        if child.winfo_class() in ('Button', 'TButton'): child.configure(state='disabled')
        disable_actions(child)

panel.job = ReadOnlyJob()
panel.mode = 'Image'
disable_actions(panel.root)
samples = []

def sample():
    disable_actions(panel.root)
    if panel.current:
        samples.append({'time':time.time(), 'live':panel.current.get('live'), 'shown':panel.speed.get()})
    panel.root.after(1500, sample)

def finish():
    checks['live_samples'] = samples
    checks['live_metric_observed'] = any(s.get('live',{}).get('state') == 'generating' for s in samples)
    checks['status'] = 'passed' if checks['live_metric_observed'] else 'no_generation_observed'
    mc.save_json(mc.ROOT/'control-tps-display-verification.json', checks)
    panel.close()

panel.root.after(2000, sample)
panel.root.after(45000, finish)
panel.root.mainloop()
