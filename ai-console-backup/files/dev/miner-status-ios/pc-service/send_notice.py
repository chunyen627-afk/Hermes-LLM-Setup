"""Sends one phone notification through the status service's ntfy settings, for scheduled checks that
must not see or copy the topic. Usage: python send_notice.py "title" "message" [priority 1-5]"""
import sys
from pathlib import Path

import office_aggregator as agg


def main():
    title, message = sys.argv[1], sys.argv[2]
    priority = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    cfg, _ = agg.load_config(Path(agg.__file__).with_name('aggregator.json'))
    agg.Notifier(cfg).post(title, message, priority, ['bell'])
    print('sent')


if __name__ == '__main__':
    main()
