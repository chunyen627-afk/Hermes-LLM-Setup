"""把被 archive（軟隱藏）的 session 拿回來。

Hermes 的 `sessions archive` 只有單向，沒有 unarchive 指令，
所以還原要直接改 state.db 的 archived 欄位。

用法：
    python _unarchive.py              # 列出目前被隱藏的 session
    python _unarchive.py <session_id> # 還原指定的那一個
    python _unarchive.py --all        # 全部還原
"""
import datetime
import os
import sqlite3
import sys

DB = os.path.join(os.environ['LOCALAPPDATA'], 'hermes', 'state.db')


def rows(where='', args=()):
    c = sqlite3.connect(DB)
    q = ("SELECT id, source, coalesce(title,''), started_at, archived, hidden "
         "FROM sessions " + where + " ORDER BY started_at DESC")
    r = c.execute(q, args).fetchall()
    c.close()
    return r


def show(rs, header):
    out = [header]
    for sid, src, title, st, arc, hid in rs:
        d = datetime.datetime.fromtimestamp(st).strftime('%Y-%m-%d %H:%M')
        c = sqlite3.connect(DB)
        n = c.execute('SELECT count(*) FROM messages WHERE session_id=?',
                      (sid,)).fetchone()[0]
        c.close()
        out.append('  %-24s %-8s %s  msgs=%-5d %s'
                   % (sid, src, d, n, title[:45]))
    sys.stdout.buffer.write(('\n'.join(out) + '\n').encode('utf-8', 'replace'))


def unarchive(ids):
    c = sqlite3.connect(DB)
    done = 0
    for sid in ids:
        cur = c.execute(
            'UPDATE sessions SET archived=0, hidden=0 WHERE id=?', (sid,))
        if cur.rowcount:
            done += 1
            sys.stdout.buffer.write(
                ('已還原 %s\n' % sid).encode('utf-8', 'replace'))
        else:
            sys.stdout.buffer.write(
                ('找不到 %s\n' % sid).encode('utf-8', 'replace'))
    c.commit()
    c.close()
    sys.stdout.buffer.write(
        ('共還原 %d 個。重開 Hermes 才會在列表看到。\n' % done)
        .encode('utf-8', 'replace'))


def main():
    args = sys.argv[1:]
    hidden = rows('WHERE archived=1 OR hidden=1')

    if not args:
        if hidden:
            show(hidden, '目前被隱藏的 session（%d 個）：' % len(hidden))
            sys.stdout.buffer.write(
                '\n還原用：python _unarchive.py <session_id>\n'
                '全部還原：python _unarchive.py --all\n'
                .encode('utf-8', 'replace'))
        else:
            sys.stdout.buffer.write('沒有被隱藏的 session。\n'
                                    .encode('utf-8', 'replace'))
        return

    if args[0] == '--all':
        unarchive([r[0] for r in hidden])
    else:
        unarchive(args)


if __name__ == '__main__':
    main()
