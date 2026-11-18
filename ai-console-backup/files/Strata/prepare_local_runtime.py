"""Prepare only Strata's private runtime. Do not download or start a model."""
import json
import os
from pathlib import Path
import setup

root = Path(__file__).resolve().parent
assert root == setup.ROOT == Path(r'C:\Users\pjunm\Strata')
assert Path(os.sys.prefix).resolve() == (root / '.venv').resolve()
cards = setup.gpus()
print('Detected GPUs:', json.dumps(cards), flush=True)
setup.pip_install(setup.requirement_lines(), 'Strata private Python packages')
setup.pip_cuda_libs()
card = next(card for card in cards if card['index'] == 2)
engine = setup.get_prebuilt(setup.PREBUILT_URL, card, 'none')
if engine is None:
    raise RuntimeError('Compatible prebuilt engine unavailable; no build tools installed')
llama = setup.get_llama_cpp()
print('Runtime ready:', json.dumps({'engine': str(engine), 'llama': str(llama)}), flush=True)
