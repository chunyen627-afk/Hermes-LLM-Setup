"""QTC revenue per MH/s from Kryptex's public pool API (same sources as the miner-status aggregator).

  python revenue.py [MH/s ...]
"""
import json
import statistics
import sys
import urllib.request

POOL = 'https://pool.kryptex.com'


def get(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'qtc-fpga-revenue/1'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read(4 << 20).decode('utf-8'))


def market():
    stats = get(f'{POOL}/qtc/api/v1/pool/stats')
    blocks = get(f'{POOL}/qtc/api/v1/pool/blocks')
    rewards = [float(b['reward']) for b in blocks.get('results', []) if b.get('reward')]
    if not rewards:                    # the newest page is often all PENDING (reward null)
        rewards = [float(stats['block_reward'])]
    price = get(f'{POOL}/api/v1/coin/qtc/price/chart?time_range=day')[-1]['price']
    fx = get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,twd')['bitcoin']
    return dict(net_hs=float(stats['net_hashrate']), block_time=float(stats['block_time']),
                reward=statistics.median(rewards), fee=float(stats['fee']), qtc_usd=float(price),
                usd_twd=fx['twd'] / fx['usd'])


def twd_per_month(mhs, m):
    coins_day = mhs * 1e6 / m['net_hs'] * 86400 / m['block_time'] * m['reward'] * (1 - m['fee'])
    return coins_day * 30 * m['qtc_usd'] * m['usd_twd'], coins_day


if __name__ == '__main__':
    m = market()
    print(f"network {m['net_hs'] / 1e12:.2f} TH/s, block {m['block_time']:.1f} s, reward {m['reward']:.4f} QTC, "
          f"fee {m['fee'] * 100:.1f}%, QTC ${m['qtc_usd']:.2f}, USD/TWD {m['usd_twd']:.2f}")
    for mhs in [float(v) for v in sys.argv[1:]] or [1, 65, 150, 175]:
        twd, coins = twd_per_month(mhs, m)
        print(f'{mhs:8.1f} MH/s -> {coins:.4f} QTC/day, NT${twd:,.0f}/month')
