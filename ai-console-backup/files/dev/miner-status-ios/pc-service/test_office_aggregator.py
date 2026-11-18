"""Offline tests: fake state files, logs and RTX payloads only. No miner, no real network peers."""
import base64
import json
import subprocess
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import jsonschema

import office_aggregator as agg

# Tests never read the calibration file the running service keeps next to this one.
agg.CALIBRATION = Path(tempfile.gettempdir()) / 'miner-status-tests-no-calibration.json'
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / 'schema' / 'miner-status.schema.json').read_text(encoding='utf-8'))
UTC = timezone.utc
RTX_OK = {
    'schema_version': 1, 'computer_name': 'HOST', 'worker': 'rtx5080', 'expected_state': 'running',
    'miner_running': True, 'state': 'running', 'data_status': 'fresh',
    'hashrate': {'value': 170.6, 'unit': 'TH/s', 'data_at': '2026-10-24T07:54:23.958000+00:00'},
    'shares': {'accepted': 28, 'stale': 0, 'rejected': 0},
    'shares_data_at': '2026-10-24T07:54:23.958000+00:00', 'last_accepted_share_at': '2026-10-24T07:51:59.174000+00:00',
    'gpus': [{'index': 0, 'temperature_c': 60, 'fan_percent': 65, 'fans_percent': [65], 'power_w': 207.417}],
    'gpu_data_at': '2026-10-24T07:54:29.542312+00:00', 'data_at': '2026-10-24T07:54:29.542312+00:00',
    'stop_reason': None,
}


def config(state_file, port=0, networks=('127.0.0.0/8',), url='http://127.0.0.1:9/status'):
    return {'bind_ip': '127.0.0.1', 'port': port, 'allowed_networks': list(networks),
            'office': {'machine_id': 'office', 'display_name': 'office', 'worker': None, 'state_file': str(state_file)},
            'rtx': {'machine_id': 'rtx5080', 'display_name': 'RTX 5080', 'worker': 'rtx5080',
                    'url': url, 'poll_seconds': 10, 'timeout_seconds': 1}}


LAPTOP = {'machine_id': 'laptop3070', 'display_name': 'RTX 3070 laptop', 'worker': 'laptop3070',
          'status_path': r'C:\Miner\laptop-status.json', 'poll_seconds': 10, 'timeout_seconds': 8,
          'stop_path': r'C:\Miner\STOP', 'guardian': {'kill_at': 82, 'shutdown_at': 90, 'stale_seconds': 90},
          'ssh': {'client': 'ssh', 'key': 'key', 'known_hosts': 'known', 'user': 'miner', 'host': '192.0.2.10'}}


class LaptopTests(unittest.TestCase):
    """The laptop guard writes the RTX status shape; the aggregator reads it over SSH."""

    def setUp(self):
        self.cfg = dict(config('unused'), laptop=json.loads(json.dumps(LAPTOP)))
        self.poller = agg.SshStatusPoller(self.cfg, 'laptop')

    def run_with(self, returncode, stdout=b'', error=None):
        self.commands = []
        def run(command, **kwargs):
            self.commands.append(command)
            if error:
                raise error
            return subprocess.CompletedProcess(command, returncode, stdout, b'')
        self.poller.poll_once(run=run)
        return self.poller.latest()

    def test_reads_status_file_without_running_anything_else(self):
        checked, status, payload = self.run_with(0, json.dumps(RTX_OK).encode('utf-8'))
        self.assertEqual(status, 'ok')
        self.assertEqual(payload['worker'], 'rtx5080')
        read = self.commands[0]
        self.assertEqual(read[-1], r'type C:\Miner\laptop-status.json')
        self.assertIn('BatchMode=yes', read)
        self.assertIn('StrictHostKeyChecking=yes', read)
        # The sample is old and says the miner runs: the guardian treats that as an unguarded miner.
        self.assertIn('taskkill /f /im krig-miner.exe', self.commands[1][-1])

    def test_failures_map_to_source_status(self):
        self.assertEqual(self.run_with(255)[1], 'unreachable')
        self.assertEqual(self.run_with(1)[1], 'noDataYet')
        self.assertEqual(self.run_with(0, b'not json')[1], 'error')
        self.assertEqual(self.run_with(0, b'[1, 2]')[1], 'error')
        self.assertEqual(self.run_with(0, error=subprocess.TimeoutExpired('ssh', 13))[1], 'unreachable')

    def test_machine_uses_laptop_section(self):
        now = datetime(2026, 10, 24, 7, 55, tzinfo=UTC)
        m = agg.rtx_machine(self.cfg, (now, 'ok', RTX_OK), agg.SessionTracker('laptop3070'), now, 'laptop')
        self.assertEqual((m['machineId'], m['displayName'], m['worker']), ('laptop3070', 'RTX 3070 laptop', 'laptop3070'))
        self.assertEqual(m['minerRunning']['state'], 'running')
        self.assertTrue(m['sessionId'].startswith('laptop3070-'))

    def guard_case(self, temp, running=True, age=5, notify=True):
        now = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
        payload = dict(RTX_OK, miner_running=running, data_at=(now - timedelta(seconds=age)).isoformat(),
                       gpus=[{'index': 0, 'temperature_c': temp, 'fan_percent': None, 'power_w': 90.0}])
        self.sent, self.remote = [], []
        self.poller.notify = (lambda *a: self.sent.append(a)) if notify else None
        def run(command, **kwargs):
            self.remote.append(command[-1])
            return subprocess.CompletedProcess(command, 0, b'', b'')
        return self.poller.guard(payload, now, run)

    def test_guardian_leaves_a_healthy_machine_alone(self):
        self.assertIsNone(self.guard_case(74))
        self.assertEqual(self.remote, [])
        self.assertIsNone(self.guard_case(84, running=False))  # its own guard already stopped mining

    def test_guardian_kills_hot_or_unguarded_miner_once(self):
        self.assertEqual(self.guard_case(83), 'kill-hot')
        self.assertIn('taskkill /f /im krig-miner.exe', self.remote[0])
        self.assertIn(r'type nul > C:\Miner\STOP', self.remote[0])
        self.assertEqual(self.sent[0][2], 5)
        self.assertIsNone(self.guard_case(83))  # same episode: no second kill or message
        self.assertIsNone(self.guard_case(70))  # recovered: episode ends
        self.assertEqual(self.guard_case(70, age=200), 'kill-stale')

    def test_guardian_shuts_down_when_dangerously_hot(self):
        self.assertEqual(self.guard_case(91, running=False), 'shutdown')
        self.assertTrue(self.remote[0].startswith('shutdown /s'))

    def test_guardian_needs_its_config(self):
        del self.poller.cfg['guardian']
        self.assertIsNone(self.guard_case(95))

    def test_aggregator_lists_extra_machine(self):
        class Fixed:
            def __init__(self, result): self.result = result
            def latest(self): return self.result
        now = datetime.now(UTC)
        aggregator = agg.Aggregator(self.cfg, Fixed(None), extra={'laptop': Fixed((now, 'unreachable', None))})
        ids = [m['machineId'] for m in aggregator.feed(now)['machines']]
        self.assertEqual(ids, ['office', 'rtx5080', 'laptop3070'])


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / 'mining-20261024-153342.log'
        self.state = self.dir / 'panel-mining-state.json'
        self.now = datetime(2026, 10, 25, 0, 21, 30, tzinfo=UTC).astimezone()
        self.tracker = agg.SessionTracker('office')

    def write_log(self, lines, mtime):
        self.log.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
        os.utime(self.log, (mtime.timestamp(), mtime.timestamp()))

    def write_state(self, **fields):
        state = {'token': 'SECRET-TOKEN', 'status': 'running', 'accepted': 272, 'th_s': 71.9, 'pid': 4321,
                 'log': str(self.log), 'updated': self.now.timestamp() - 2,
                 'gpus': [{'index': 0, 'temperature': 69.0, 'watts': 109.8, 'limit': 120.0, 'core': 1500.0, 'fan': 91.0}]}
        state.update(fields)
        self.state.write_text(json.dumps(state), encoding='utf-8')

    def machine(self):
        return agg.office_machine(config(self.state), self.tracker, self.now)

    def test_running_session_reads_log_and_state(self):
        local = self.now.astimezone()
        t = local.strftime('%H:%M:%S')
        self.write_log([f'{(local - timedelta(minutes=5)).strftime("%H:%M:%S")}.580 share accepted: GPU1 272ms',
                        f'{t}.963 Total: 71.90 TH/s shares: 272 accepted 0 stale 0 rejected'], local)
        self.write_state()
        m = self.machine()
        self.assertEqual(m['sourceStatus'], 'ok')
        self.assertEqual(m['sessionId'], 'office-20261024-153342')
        self.assertEqual(m['sampleSequence'], 1)
        self.assertEqual(m['minerRunning']['state'], 'running')
        self.assertEqual(m['hashrate']['value'], 71.9)
        self.assertEqual(m['hashrate']['unit'], 'TH/s')
        self.assertEqual(m['shares']['accepted'], 272)
        self.assertIsNotNone(m['shares']['lastAcceptedAt'])
        self.assertEqual(m['gpus'][0]['powerW'], 109.8)
        self.assertIsNone(m['stop']['reason'])

    def test_srbminer_numbers_come_from_the_state_when_the_log_has_no_total_line(self):
        local = self.now.astimezone()
        self.write_log(['[2026-10-25 08:21:00] Connected to prl-hk.kryptex.network:8048 [0]'], local)
        self.write_state(miner='srbminer', th_s=98.4, accepted=12, rejected=0,
                         data_at=(local - timedelta(seconds=2)).isoformat(),
                         last_accepted_at=(local - timedelta(minutes=1)).isoformat())
        m = self.machine()
        self.assertEqual(m['hashrate']['value'], 98.4)
        self.assertEqual(m['hashrate']['unit'], 'TH/s')
        self.assertEqual((m['shares']['accepted'], m['shares']['rejected']), (12, 0))
        self.assertIsNotNone(m['shares']['lastAcceptedAt'])
        # A krig log still wins over the state.
        self.write_log([f'{local.strftime("%H:%M:%S")}.100 Total: 71.90 TH/s shares: 272 accepted 0 stale 0 rejected'], local)
        self.assertEqual(self.machine()['hashrate']['value'], 71.9)

    def test_sequence_advances_with_every_new_observed_time(self):
        local = self.now.astimezone()
        self.write_log([f'{local.strftime("%H:%M:%S")}.100 Total: 71.90 TH/s shares: 272 accepted 0 stale 0 rejected'], local)
        self.write_state()
        first = self.machine()
        self.assertEqual(first['sampleSequence'], 1)
        self.assertEqual(self.machine()['sampleSequence'], 1)  # same sample re-read
        # The guard rewrote the state (new observedAt) but the log has no new Total line yet:
        # the app needs a higher sequence for the newer observedAt, or it rejects a replay.
        self.write_state(updated=self.now.timestamp() + 10)
        self.now += timedelta(seconds=12)
        second = self.machine()
        self.assertGreater(second['observedAt'], first['observedAt'])
        self.assertEqual(second['sampleSequence'], 2)
        self.assertEqual(second['shares']['observedAt'], first['shares']['observedAt'])

    def test_restart_instance_changes_session_id(self):
        local = self.now.astimezone()
        self.write_log([f'{local.strftime("%H:%M:%S")}.100 Total: 71.90 TH/s shares: 272 accepted 0 stale 0 rejected'], local)
        self.write_state()
        first = agg.office_machine(config(self.state), agg.SessionTracker('office', 'r1'), self.now)
        second = agg.office_machine(config(self.state), agg.SessionTracker('office', 'r2'), self.now)
        self.assertEqual(first['sessionId'], 'office-20261024-153342-r1')
        self.assertNotEqual(first['sessionId'], second['sessionId'])
        self.assertEqual(second['sampleSequence'], 1)

    def test_secrets_and_paths_never_leave(self):
        local = self.now.astimezone()
        self.write_log([f'{local.strftime("%H:%M:%S")}.100 Total: 71.90 TH/s shares: 272 accepted 0 stale 0 rejected'], local)
        self.write_state()
        text = json.dumps(self.machine())
        for secret in ('SECRET-TOKEN', '4321', str(self.dir), 'limit', 'core'):
            self.assertNotIn(secret, text)

    def test_missing_state_is_no_data_yet(self):
        self.assertEqual(self.machine()['sourceStatus'], 'noDataYet')

    def test_corrupt_state_is_error(self):
        self.state.write_text('{"status": "runn', encoding='utf-8')
        self.assertEqual(self.machine()['sourceStatus'], 'error')

    def test_stopped_state_reports_unknown_stop_for_session(self):
        self.write_state(status='stopped')
        m = self.machine()
        self.assertEqual(m['minerRunning']['state'], 'stopped')
        self.assertEqual(m['stop']['reason'], 'unknown')
        self.assertEqual(m['stop']['sessionId'], 'office-20261024-153342')

    def test_preparing_is_unknown_not_running(self):
        self.write_state(status='preparing')
        self.assertEqual(self.machine()['minerRunning']['state'], 'unknown')

    def test_future_state_time_is_not_trusted(self):
        self.write_state(updated=self.now.timestamp() + 3600)
        self.assertIsNone(self.machine()['observedAt'])

    def test_log_time_outside_session_is_dropped(self):
        old = datetime(2026, 10, 24, 15, 0, 0).astimezone()  # before the 15:33:42 session start
        self.write_log([f'{old.strftime("%H:%M:%S")}.100 Total: 71.90 TH/s shares: 5 accepted 0 stale 0 rejected'], old)
        self.write_state()
        m = self.machine()
        self.assertIsNone(m['hashrate']['value'])
        self.assertIsNone(m['shares']['accepted'])

    def test_bad_gpu_values_become_null(self):
        self.write_state(gpus=[{'index': 0, 'temperature': 999, 'watts': -5, 'fan': 'x'}, {'temperature': 50}])
        gpus = self.machine()['gpus']
        self.assertEqual(len(gpus), 1)
        self.assertEqual((gpus[0]['temperatureC'], gpus[0]['powerW'], gpus[0]['fanPercent']), (None, None, None))


class RtxTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config('unused')
        self.tracker = agg.SessionTracker('rtx5080')
        self.now = datetime(2026, 10, 24, 7, 55, tzinfo=UTC)

    def machine(self, payload, status='ok'):
        return agg.rtx_machine(self.cfg, (self.now, status, payload), self.tracker, self.now)

    def test_maps_monitor_status(self):
        m = self.machine(RTX_OK)
        self.assertEqual(m['minerRunning']['state'], 'running')
        self.assertEqual(m['hashrate'], {'value': 170.6, 'unit': 'TH/s', 'observedAt': '2026-10-24T15:54:23.958+08:00'})
        self.assertEqual(m['shares']['accepted'], 28)
        self.assertEqual(m['gpus'][0]['powerW'], 207.417)
        self.assertIs(m['expectedRunning'], True)
        self.assertTrue(m['sessionId'].startswith('rtx5080-'))
        self.assertEqual(m['sampleSequence'], 1)

    def test_new_session_when_accepted_drops(self):
        first = self.machine(RTX_OK)['sessionId']
        later = json.loads(json.dumps(RTX_OK))
        later['shares']['accepted'] = 3
        later['hashrate']['data_at'] = '2026-10-24T08:10:00+00:00'
        self.now += timedelta(minutes=15)
        m = self.machine(later)
        self.assertNotEqual(m['sessionId'], first)
        self.assertEqual(m['sampleSequence'], 1)

    def test_same_sample_does_not_advance_sequence(self):
        self.machine(RTX_OK)
        self.assertEqual(self.machine(RTX_OK)['sampleSequence'], 1)

    def test_new_sample_time_advances_sequence_without_new_hashrate(self):
        self.machine(RTX_OK)
        newer = dict(RTX_OK, data_at='2026-10-24T07:54:39.542312+00:00')  # same hashrate line, new sample
        self.assertEqual(self.machine(newer)['sampleSequence'], 2)

    def test_stop_reasons(self):
        self.machine(RTX_OK)
        for source, expected in (('user_requested', 'userRequested'), ('temperature_cutoff', 'protectionTriggered'),
                                 ('miner_exited', 'abnormalExit'), ('unknown', 'unknown'), ('weird', 'unknown')):
            stopped = dict(RTX_OK, miner_running=False, state='abnormal_exit', stop_reason=source)
            m = self.machine(stopped)
            self.assertEqual(m['minerRunning']['state'], 'stopped')
            self.assertEqual(m['stop']['reason'], expected)

    def test_unreachable_and_errors(self):
        self.assertEqual(self.machine(None, 'unreachable')['sourceStatus'], 'unreachable')
        self.assertEqual(self.machine(None, 'noDataYet')['sourceStatus'], 'noDataYet')
        self.assertEqual(agg.rtx_machine(self.cfg, None, self.tracker, self.now)['sourceStatus'], 'noDataYet')

    def test_unknown_unit_and_naive_time(self):
        odd = json.loads(json.dumps(RTX_OK))
        odd['hashrate']['unit'] = 'PH/s'
        odd['data_at'] = '2026-10-24T07:54:29'
        m = self.machine(odd)
        self.assertIsNone(m['hashrate']['value'])
        self.assertIsNone(m['observedAt'])


def assessed_machine(now, state='running', source='ok', reason=None, last_share=None, temp=60.0,
                     rate=170.0, observed_age=5, pool_row=None, limit=70):
    """A machine dict with the aggregator's assessment and health attached."""
    m = agg.empty_machine('rtx5080', 'RTX 5080', 'rtx5080', source, now)
    if source == 'ok':
        m.update(sessionId='rtx5080-20261025T000000Z', sampleSequence=1,
                 observedAt=agg.iso(now - timedelta(seconds=observed_age)),
                 minerRunning={'state': state, 'observedAt': agg.iso(now)})
        m['shares'].update(accepted=3, stale=0, rejected=0,
                           lastAcceptedAt=agg.iso(last_share or now - timedelta(seconds=60)))
        m['hashrate'] = {'value': rate, 'unit': 'TH/s', 'observedAt': agg.iso(now)}
        m['gpus'] = [{'index': 0, 'temperatureC': temp, 'fanPercent': 60, 'powerW': 200, 'observedAt': agg.iso(now)}]
        m['stop']['reason'] = reason
    m['assessment'], m['health'] = agg.assess(m, pool_row, limit, now)
    return m


class AssessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)

    def assessment(self, **kwargs):
        return assessed_machine(self.now, **kwargs)['assessment']

    def test_healthy_is_green_with_last_share(self):
        a = self.assessment()
        self.assertEqual((a['light'], a['title']), ('green', '挖礦中'))
        self.assertIn('最後份額 1 分鐘前', a['reason'])

    def test_stale_or_unreachable_is_gray(self):
        self.assertEqual(self.assessment(observed_age=300)['light'], 'gray')
        self.assertEqual(self.assessment(source='unreachable')['light'], 'gray')

    def test_stopped_is_red_with_reason(self):
        a = self.assessment(state='stopped', reason='protectionTriggered')
        self.assertEqual(a['light'], 'red')
        self.assertIn('保護機制', a['reason'])

    def test_hot_zero_rate_and_pool_offline_are_yellow(self):
        self.assertEqual(self.assessment(temp=68)['title'], '溫度偏高')
        self.assertEqual(self.assessment(rate=0)['title'], '算力為 0')
        self.assertEqual(self.assessment(pool_row={'status': 'offline'})['title'], '礦池顯示離線')

    def test_a_missing_rate_reading_is_judged_by_shares_not_shown_as_zero(self):
        self.assertEqual(self.assessment(rate=None)['title'], '挖礦中')
        quiet = self.now - timedelta(hours=1)
        self.assertEqual(self.assessment(rate=None, last_share=quiet)['title'], '太久沒有份額')

    def test_a_slow_miner_on_a_busy_card_is_giving_way_not_a_fault(self):
        m = assessed_machine(self.now, rate=0, last_share=self.now - timedelta(hours=1))
        m['sessionId'] = 'rtx5080-20261025-160000'  # local time: started an hour ago
        way = {'normal_th': 200, 'min_gpu_w': 100}
        self.assertEqual(agg.assess(dict(m, sessionId='rtx5080-20261025-165900'), None, 75, self.now,
                                    giving_way=way)[0]['title'], '算力為 0')  # just started: ramping, not giving way
        a, _ = agg.assess(m, None, 75, self.now, giving_way=way)
        self.assertEqual((a['light'], a['title']), ('green', '讓路中'))
        self.assertIn('沒有停', a['reason'])
        m['sessionId'] = 'rtx5080-20261025T080000Z'  # the monitor's UTC form: also an hour ago
        self.assertEqual(agg.assess(m, None, 75, self.now, giving_way=way)[0]['title'], '讓路中')
        m['gpus'][0]['powerW'] = 40  # the card idles: the miner itself is slow, so the usual checks decide
        self.assertEqual(agg.assess(m, None, 75, self.now, giving_way=way)[0]['title'], '算力為 0')
        m['gpus'][0]['powerW'] = 200
        self.assertEqual(agg.assess(m, None, 75, self.now)[0]['title'], '算力為 0')  # other machines: unchanged
        m['hashrate']['value'] = 190
        self.assertEqual(agg.assess(m, None, 75, self.now, giving_way=way)[0]['title'], '太久沒有份額')

    def rhythm_machine(self, rhythm, at, accepted, session='rtx5080-a', last_share=None):
        m = agg.empty_machine('rtx5080', 'RTX 5080', 'rtx5080', 'ok', at)
        m.update(sessionId=session, observedAt=agg.iso(at), minerRunning={'state': 'running', 'observedAt': agg.iso(at)})
        m['shares'].update(accepted=accepted, lastAcceptedAt=agg.iso(last_share or at))
        m['hashrate'] = {'value': 170.0, 'unit': 'TH/s', 'observedAt': agg.iso(at)}
        return agg.assess(m, {'status': 'online', 'valid': 720}, 70, at, rhythm=rhythm)

    def test_quiet_share_threshold_follows_the_machines_rhythm(self):
        rhythm = agg.ShareRhythm()
        self.rhythm_machine(rhythm, self.now, 100)
        # Five shares in 10 minutes: one per 2 minutes, so up to 14 minutes of quiet stays green.
        later = self.now + timedelta(minutes=10)
        self.rhythm_machine(rhythm, later, 105)
        quiet = later + timedelta(minutes=13)
        self.assertEqual(self.rhythm_machine(rhythm, quiet, 105, last_share=later)[0]['light'], 'green')
        late, health = self.rhythm_machine(rhythm, later + timedelta(minutes=15), 105, last_share=later)
        self.assertEqual((late['light'], late['title']), ('yellow', '太久沒有份額'))
        self.assertIn('平常約 2 分鐘一個', late['reason'])
        self.assertEqual(health['expectedShareSeconds'], 120)

    def test_rhythm_survives_a_reconnect(self):
        rhythm = agg.ShareRhythm()
        self.rhythm_machine(rhythm, self.now, 100)
        self.rhythm_machine(rhythm, self.now + timedelta(minutes=10), 105)
        # New session (reconnect / restart): the counter starts again; the rhythm is kept, not 0.
        _, health = self.rhythm_machine(rhythm, self.now + timedelta(minutes=11), 0, session='rtx5080-b')
        self.assertEqual(health['expectedShareSeconds'], 120)

    def test_rough_rhythm_from_pool_until_measured(self):
        _, health = self.rhythm_machine(agg.ShareRhythm(), self.now, 3)
        self.assertEqual(health['expectedShareSeconds'], 120)  # 86400 s / 720 shares in 24 h

    def test_health_details(self):
        pool = {'status': 'online', 'opened_at': (self.now - timedelta(hours=1)).timestamp() * 1000,
                'valid': 99, 'stale': 1, 'invalid': 0, 'avg_hashrate_30m': '1.5e14', 'last_share':
                (self.now - timedelta(seconds=20)).timestamp() * 1000}
        h = assessed_machine(self.now, pool_row=pool)['health']
        self.assertAlmostEqual(h['uptimeSeconds'], 3600)
        self.assertAlmostEqual(h['validSharePercent'], 99.0)
        self.assertIs(h['poolOnline'], True)
        self.assertEqual(h['poolHashrate'], 1.5e14)
        self.assertEqual(h['lastShareAt'], agg.iso(self.now - timedelta(seconds=20)))  # pool share is newer
        self.assertEqual((h['maxGpuTemperatureC'], h['temperatureLimitC']), (60.0, 70))


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        cfg = {'notify': {'server': 'https://example.invalid', 'topic': 't', 'check_seconds': 15,
                          'offline_after_seconds': 180, 'stopped_after_seconds': 60,
                          'hot_after_seconds': 60, 'attention_after_seconds': 600}}
        self.notifier = agg.Notifier(cfg, send=lambda *message: self.sent.append(message))
        self.t0 = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)

    def run_for(self, seconds, step=15, **kwargs):
        for offset in range(0, seconds + 1, step):
            now = self.t0 + timedelta(seconds=offset)
            self.notifier.check({'machines': [assessed_machine(now, **kwargs)]}, now)
        self.t0 += timedelta(seconds=seconds + step)

    def titles(self):
        return [message[0] for message in self.sent]

    def test_healthy_miner_sends_nothing(self):
        self.run_for(30 * 60, step=60)
        self.assertEqual(self.sent, [])

    def test_stop_notifies_once_after_a_minute_and_recovery_is_silent(self):
        self.run_for(45, state='stopped', reason='protectionTriggered')
        self.assertEqual(self.sent, [])
        self.run_for(120, state='stopped', reason='protectionTriggered')
        self.assertEqual(self.titles(), ['🛑 RTX 5080 已停止挖礦'])
        self.assertIn('保護機制', self.sent[0][1])
        self.run_for(30)
        self.assertEqual(self.titles(), ['🛑 RTX 5080 已停止挖礦'], 'nothing to do once it is back (11/02)')
        self.run_for(120, state='stopped', reason='protectionTriggered')
        self.assertEqual(len(self.sent), 2, 'a later stop is told again')

    def test_a_null_delay_turns_a_kind_off(self):
        self.notifier.cfg['attention_after_seconds'] = None
        quiet_since = self.t0
        for offset in range(0, 3600, 60):
            now = self.t0 + timedelta(seconds=offset)
            self.notifier.check({'machines': [assessed_machine(now, last_share=quiet_since)]}, now)
        self.assertEqual(self.sent, [])

    def test_offline_after_three_minutes(self):
        self.run_for(150, source='unreachable')
        self.assertEqual(self.sent, [])
        self.run_for(60, source='unreachable')
        self.assertEqual(self.titles(), ['⚠️ RTX 5080 失聯'])

    def test_hot_after_a_minute(self):
        self.run_for(90, temp=68)
        self.assertEqual(self.titles(), ['🌡️ RTX 5080 溫度偏高'])
        self.assertIn('距保護線 2°C', self.sent[0][1])

    def test_long_quiet_notifies_after_yellow_lasts_ten_minutes(self):
        quiet_since = self.t0
        for offset in range(0, 1700, 15):
            now = self.t0 + timedelta(seconds=offset)
            self.notifier.check({'machines': [assessed_machine(now, last_share=quiet_since)]}, now)
            if offset == 1400:
                self.assertEqual(self.sent, [])  # yellow after 15 min, only 8 min of it so far
        self.assertEqual(self.titles(), ['⚠️ RTX 5080 需要注意：太久沒有份額'])

    def test_failed_send_retries_next_check(self):
        calls = []

        def flaky(*message):
            calls.append(message)
            if len(calls) == 1:
                raise OSError('network down')
            self.sent.append(message)

        self.notifier.send = flaky
        self.run_for(120, state='stopped')
        self.assertEqual(self.titles(), ['🛑 RTX 5080 已停止挖礦'])

    def test_post_builds_ntfy_json(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return b'{}'

        def fake_urlopen(request, timeout):
            captured['url'], captured['body'] = request.full_url, json.loads(request.data)
            return Response()

        original = agg.urllib.request.urlopen
        agg.urllib.request.urlopen = fake_urlopen
        try:
            agg.Notifier({'notify': {'server': 'https://ntfy.example', 'topic': 'abc'}}).post('標題', '內容', 3, ['warning'])
        finally:
            agg.urllib.request.urlopen = original
        self.assertEqual(captured['url'], 'https://ntfy.example')
        self.assertEqual(captured['body'], {'topic': 'abc', 'title': '標題', 'message': '內容', 'priority': 3, 'tags': ['warning']})


class EarningsTests(unittest.TestCase):
    CFG = {'earnings': {'account': 'acct', 'pool_coin': 'prl', 'coin': 'PRL', 'poll_seconds': 600,
                        'workers': {'office': 'llmrig', 'rtx5080': 'rtx5080'},
                        'electricity_billed': {'office': False, 'rtx5080': True}}}

    def responses(self, **overrides):
        data = {
            '/prl/api/v1/pool/stats': {'net_hashrate': 8.0e19, 'fee': 0.03, 'block_time': 200},
            '/prl/api/v1/pool/blocks': {'results': [{'reward': '2000'}, {'reward': '2200.5'}, {'reward': '2100'}, {'reward': 'bad'}]},
            '/prl/api/v3/miner/workers/acct': {'results': [
                {'worker': 'rtx5080', 'avg_hashrate_3h': '1.6e14', 'avg_hashrate_30m': '2.1e14'},
                {'worker': 'llmrig', 'avg_hashrate_3h': None, 'avg_hashrate_30m': '7.2e13'}]},
            '/api/v1/coin/prl/price/chart?time_range=day': [{'price': 1.8}, {'price': 1.87}],
            '/api/v1/coin/btc/price/chart?time_range=day': [{'price': 79646.06}],
            'coingecko': {'bitcoin': {'usd': 80000.0, 'twd': 2560000.0}},
        }
        data.update(overrides)

        def fetch(url):
            if 'coingecko' in url:
                value = data['coingecko']
            else:
                value = data[url.split('pool.kryptex.com', 1)[1]]
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    def test_pps_formula(self):
        self.assertAlmostEqual(agg.coin_per_day(1.6e14, 8.0e19, 200, 2100, 0.03), 1.6e14 / 8.0e19 * 432 * 2100 * 0.97)
        self.assertIsNone(agg.coin_per_day(None, 8.0e19, 200, 2100, 0.03))
        self.assertIsNone(agg.coin_per_day(1.6e14, 0, 200, 2100, 0.03))

    def test_refresh_builds_market_and_worker_estimates(self):
        poller = agg.EarningsPoller(self.CFG, fetch=self.responses())
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
        result = poller.latest()
        self.assertEqual(result['market'], {'coin': 'PRL', 'coinUsd': 1.87, 'btcUsd': 79646.06, 'usdTwd': 32.0,
                                            'observedAt': '2026-10-25T09:00:00.000+08:00'})
        rtx_rate, rtx_day = result['workers']['rtx5080']
        self.assertEqual(rtx_rate, 2.1e14)  # the 30 min average, as the pool's web page uses
        # No published estimate: the network share of the median valid block reward.
        self.assertAlmostEqual(rtx_day, agg.coin_per_day(2.1e14, 8.0e19, 200, 2100, 0.03))
        self.assertAlmostEqual(result['per_th_day'], agg.coin_per_day(1e12, 8.0e19, 200, 2100, 0.03))
        self.assertEqual(result['workers']['llmrig'][0], 7.2e13)

    def test_the_pools_own_estimate_is_used_when_published(self):
        stats = {'net_hashrate': 8.0e19, 'fee': 0.03, 'block_time': 200, 'estimated_profit_day': 1.38e-14}
        poller = agg.EarningsPoller(self.CFG, fetch=self.responses(**{'/prl/api/v1/pool/stats': stats}))
        poller.refresh()
        result = poller.latest()
        self.assertAlmostEqual(result['workers']['rtx5080'][1], 2.1e14 * 1.38e-14)  # what the web page shows
        self.assertAlmostEqual(result['per_th_day'], 0.0138)
        # A worker with no hashrate falls back to the 3 h average, and none at all has no estimate.
        rows = {'results': [{'worker': 'w', 'avg_hashrate_30m': None, 'avg_hashrate_3h': '5e13'},
                            {'worker': 'idle', 'avg_hashrate_30m': '0', 'avg_hashrate_3h': None}]}
        poller = agg.EarningsPoller(self.CFG, fetch=self.responses(**{'/prl/api/v1/pool/stats': stats,
                                                                     '/prl/api/v3/miner/workers/acct': rows}))
        poller.refresh()
        self.assertAlmostEqual(poller.latest()['workers']['w'][1], 5e13 * 1.38e-14)
        self.assertIsNone(poller.latest()['workers']['idle'][1])

    def test_the_calibration_factor_scales_the_estimate_and_bad_values_are_ignored(self):
        saved = agg.CALIBRATION
        try:
            with tempfile.TemporaryDirectory() as folder:
                agg.CALIBRATION = Path(folder) / 'calibration.json'
                agg.CALIBRATION.write_text(json.dumps({'PRL': {'factor': 0.9}, 'QTC': {'factor': 5}}), encoding='utf-8')
                poller = agg.EarningsPoller(self.CFG, fetch=self.responses())
                poller.refresh()
                base = agg.coin_per_day(1e12, 8.0e19, 200, 2100, 0.03)
                self.assertAlmostEqual(poller.latest()['per_th_day'], 0.9 * base)
                self.assertAlmostEqual(poller.latest()['workers']['rtx5080'][1], 0.9 * base * 210)
                self.assertEqual(agg.calibration_factor('QTC'), 1.0)  # outside 0.5–2: ignored
                self.assertEqual(agg.calibration_factor('ETH'), 1.0)
                agg.CALIBRATION.write_text('not json', encoding='utf-8')
                self.assertEqual(agg.calibration_factor('PRL'), 1.0)
        finally:
            agg.CALIBRATION = saved

    def test_price_failures_become_null_not_stale(self):
        poller = agg.EarningsPoller(self.CFG, fetch=self.responses(coingecko=OSError('down')))
        poller.refresh()
        self.assertIsNone(poller.latest()['market']['usdTwd'])
        self.assertEqual(poller.latest()['market']['coinUsd'], 1.87)

    def test_earnings_object_per_machine(self):
        poller = agg.EarningsPoller(self.CFG, fetch=self.responses())
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
        rtx = agg.earnings_for(self.CFG, 'rtx5080', poller.latest())
        self.assertIs(rtx['electricityBilled'], True)
        self.assertIs(agg.earnings_for(self.CFG, 'office', poller.latest())['electricityBilled'], False)
        self.assertIsNone(agg.earnings_for(self.CFG, 'unknown-machine', poller.latest()))
        self.assertIsNone(agg.earnings_for(self.CFG, 'rtx5080', None))


TAIPOWER_DOC = {
    'metadata': {'名稱': '台灣電力公司各類電價表', '實施日期': '2025-10-01'},
    'data': {'簡易型時間電價(二段式)': {'流動電費': {
        '週一至週五': {
            '尖峰時間': {'夏月': {'時段': '09:00~24:00', '單價': 5.16},
                       '非夏月': {'時段': '06:00~11:00/14:00~24:00', '單價': 4.93}},
            '離峰時間': {'夏月': {'時段': '00:00~09:00', '單價': 2.06},
                       '非夏月': {'時段': '00:00~06:00/11:00~14:00', '單價': 1.99}}},
        '週六、週日及離峰日': {'離峰時間': {'夏月': {'時段': '全日', '單價': 2.06}, '非夏月': {'時段': '全日', '單價': 1.99}}},
    }}},
}
CALENDAR_DATASET = {'result': {'distribution': [
    {'resourceDescription': '115年中華民國政府行政機關辦公日曆表', 'resourceFormat': 'CSV', 'resourceDownloadUrl': 'cal-115'},
    {'resourceDescription': '115年中華民國政府行政機關辦公日曆表_Google行事曆專用', 'resourceFormat': 'CSV', 'resourceDownloadUrl': 'google-115'},
    {'resourceDescription': '116年中華民國政府行政機關辦公日曆表', 'resourceFormat': 'CSV', 'resourceDownloadUrl': 'cal-116-old'},
    {'resourceDescription': '116年中華民國政府行政機關辦公日曆表(1161020更新)', 'resourceFormat': 'CSV', 'resourceDownloadUrl': 'cal-116'},
]}}
CAL_115 = '西元日期,星期,是否放假,備註\n20261024,六,2,\n20261025,日,2,\n20261026,一,2,補假\n20261027,二,0,\n20261225,五,2,行憲紀念日\n'
CAL_116 = '西元日期,星期,是否放假,備註\n20270101,五,2,開國紀念日\n'


class TariffTests(unittest.TestCase):
    def poller(self, doc=TAIPOWER_DOC, dataset=CALENDAR_DATASET, sent=None):
        cache = Path(tempfile.mkdtemp()) / 'tariff-cache.json'

        def fetch(url):
            if isinstance(doc, Exception) and 'taipower' in url:
                raise doc
            return doc if 'taipower' in url else dataset

        texts = {'cal-115': CAL_115, 'cal-116': CAL_116}
        notify = (lambda *message: sent.append(message)) if sent is not None else None
        return agg.TariffPoller({}, fetch=fetch, fetch_text=texts.__getitem__, cache=cache, notify=notify), cache

    def test_parses_rates_effective_date_and_windows(self):
        parsed = agg.parse_taipower_tariff(TAIPOWER_DOC)
        self.assertEqual((parsed['summerPeak'], parsed['summerOffPeak'], parsed['nonSummerPeak'], parsed['nonSummerOffPeak']),
                         (5.16, 2.06, 4.93, 1.99))
        self.assertEqual(parsed['effectiveDate'], '2025-10-01')
        self.assertIs(parsed['periodsMatch'], True)

    def test_changed_windows_are_flagged_and_reported_once(self):
        doc = json.loads(json.dumps(TAIPOWER_DOC))
        doc['data']['簡易型時間電價(二段式)']['流動電費']['週一至週五']['尖峰時間']['夏月']['時段'] = '10:00~24:00'
        self.assertIs(agg.parse_taipower_tariff(doc)['periodsMatch'], False)
        sent = []
        poller, _ = self.poller(doc=doc, sent=sent)
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
        poller.refresh(datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
        self.assertEqual([message[0] for message in sent], ['⚠️ 台電時間電價時段已變更'])

    def test_picks_newest_non_google_calendar(self):
        self.assertEqual(agg.calendar_resource(CALENDAR_DATASET, 115), 'cal-115')
        self.assertEqual(agg.calendar_resource(CALENDAR_DATASET, 116), 'cal-116')
        self.assertIsNone(agg.calendar_resource(CALENDAR_DATASET, 117))

    def test_weekday_holidays_only(self):
        self.assertEqual(agg.weekday_holidays(CAL_115), ['2026-10-26', '2026-12-25'])

    def test_refresh_combines_rates_and_upcoming_holidays_and_caches(self):
        poller, cache = self.poller()
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))  # 09:00 Taipei, Sunday
        result = poller.latest()
        self.assertEqual(result['offPeakDates'], ['2026-10-26', '2026-12-25', '2027-01-01'])
        self.assertEqual(result['nonSummerOffPeak'], 1.99)
        self.assertEqual(json.loads(cache.read_text(encoding='utf-8'))['effectiveDate'], '2025-10-01')

    def test_failure_keeps_last_good_tariff_including_from_disk(self):
        poller, cache = self.poller()
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
        restarted = agg.TariffPoller({}, fetch=lambda url: (_ for _ in ()).throw(OSError('down')), cache=cache)
        with self.assertRaises(OSError):
            restarted.refresh()
        self.assertEqual(restarted.latest()['summerPeak'], 5.16)

    def test_feed_tariff_drops_past_holidays(self):
        poller, _ = self.poller()
        poller.refresh(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
        public = agg.tariff_for_feed(poller.latest(), datetime(2026, 10, 27, 1, 0, tzinfo=UTC))
        self.assertEqual(public['offPeakDates'], ['2026-12-25', '2027-01-01'])
        self.assertEqual(set(public), set(agg.TARIFF_KEYS))
        feed = {'schemaVersion': 1, 'dataOrigin': 'live', 'demoNotice': None, 'publishedAt': None,
                'machines': [], 'tariff': public}
        jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())


class FeedAndServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        state = self.dir / 'panel-mining-state.json'
        state.write_text(json.dumps({'status': 'running', 'updated': time.time(), 'gpus': [], 'token': 'X'}), encoding='utf-8')
        self.cfg = config(state)

    class FixedPoller:
        def __init__(self, result):
            self.result = result

        def latest(self):
            return self.result

    def test_feed_matches_app_schema(self):
        now = datetime.now(UTC)
        for result in (None, (now, 'ok', RTX_OK), (now, 'unreachable', None)):
            feed = agg.Aggregator(self.cfg, self.FixedPoller(result)).feed(now)
            jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())
            self.assertEqual(feed['dataOrigin'], 'live')
            self.assertEqual([m['machineId'] for m in feed['machines']], ['office', 'rtx5080'])

    class FixedEarnings:
        def worker(self, name):
            return None

        def latest(self):
            return {'checked_at': datetime(2026, 10, 25, 1, 0, tzinfo=UTC),
                    'workers': {'rtx5080': (1.6e14, 2.0)},
                    'market': {'coin': 'PRL', 'coinUsd': 1.87, 'btcUsd': 79646.06, 'usdTwd': 32.0,
                               'observedAt': '2026-10-25T09:00:00.000+08:00'}}

    def test_earnings_extension_only_when_asked(self):
        cfg = dict(self.cfg, earnings=EarningsTests.CFG['earnings'])
        aggregator = agg.Aggregator(cfg, self.FixedPoller((datetime.now(UTC), 'ok', RTX_OK)), self.FixedEarnings())
        base = aggregator.feed()
        self.assertNotIn('market', base)
        self.assertNotIn('earnings', base['machines'][0])
        extended = aggregator.feed(include_earnings=True)
        jsonschema.validate(extended, SCHEMA, format_checker=jsonschema.FormatChecker())
        self.assertEqual(extended['market']['usdTwd'], 32.0)
        self.assertEqual(extended['machines'][1]['earnings']['coinPerDay'], 2.0)
        self.assertIsNone(extended['machines'][0]['earnings']['coinPerDay'])  # office worker not in the pool data
        assessed = aggregator.feed(include_earnings=True, include_assessment=True)
        jsonschema.validate(assessed, SCHEMA, format_checker=jsonschema.FormatChecker())
        self.assertIn(assessed['machines'][1]['assessment']['light'], ('green', 'yellow', 'red', 'gray'))
        self.assertNotIn('assessment', extended['machines'][0])

    def test_cumulative_extension_only_when_asked_and_schema_valid(self):
        cfg = dict(self.cfg, earnings=EarningsTests.CFG['earnings'])
        now = datetime.now(UTC)
        aggregator = agg.Aggregator(cfg, self.FixedPoller((now, 'ok', RTX_OK)), self.FixedEarnings())
        aggregator.ledger = agg.EarningsLedger(aggregator, self.dir / 'ledger.json')
        self.assertIsNone(aggregator.feed(include_cumulative=True)['cumulative'])  # nothing accrued yet
        aggregator.ledger.step(now)
        aggregator.ledger.step(now + timedelta(seconds=300))
        self.assertNotIn('cumulative', aggregator.feed(include_earnings=True))
        feed = aggregator.feed(include_earnings=True, include_cumulative=True)
        jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())
        self.assertEqual([row['machineId'] for row in feed['cumulative']['machines']], ['rtx5080'])
        self.assertAlmostEqual(feed['cumulative']['totalTwd'], 2.0 * 300 / 86400 * 1.87 * 32.0)
        self.assertNotIn('today', feed)
        today = aggregator.feed(now + timedelta(seconds=300), include_earnings=True, include_today=True)
        jsonschema.validate(today, SCHEMA, format_checker=jsonschema.FormatChecker())
        rows = {row['machineId']: row for row in today['today']['machines']}
        self.assertEqual(rows['office']['twd'], 0.0)  # running without a pool estimate: power only
        self.assertAlmostEqual(rows['rtx5080']['twd'], feed['cumulative']['totalTwd'])
        self.assertAlmostEqual(sum(hour['miningSeconds'] for hour in rows['rtx5080']['hours']), 300)

    def test_pool_balance_extension_reads_the_last_dashboard_reading(self):
        cfg = dict(self.cfg, earnings=EarningsTests.CFG['earnings'])
        aggregator = agg.Aggregator(cfg, self.FixedPoller((datetime.now(UTC), 'ok', RTX_OK)), self.FixedEarnings())
        previous = agg.POOL_BALANCE
        agg.POOL_BALANCE = self.dir / 'pool-balance.json'
        try:
            self.assertNotIn('poolBalance', aggregator.feed(include_earnings=True))
            self.assertIsNone(aggregator.feed(include_balance=True)['poolBalance'])  # no reading yet
            agg.POOL_BALANCE.write_text(json.dumps({'balanceBtc': 0.00043477, 'observedAt': '2026-11-01T10:00:00+08:00'}),
                                        encoding='utf-8')
            feed = aggregator.feed(include_earnings=True, include_balance=True)
            jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())
            market = feed['market']
            self.assertAlmostEqual(feed['poolBalance']['twd'], 0.00043477 * market['btcUsd'] * market['usdTwd'])
            self.assertEqual(feed['poolBalance']['observedAt'], '2026-11-01T10:00:00.000+08:00')
            agg.POOL_BALANCE.write_text('{"balanceBtc": "lots"}', encoding='utf-8')  # garbage: no extension value
            self.assertIsNone(aggregator.feed(include_balance=True)['poolBalance'])
        finally:
            agg.POOL_BALANCE = previous

    def test_http_header_selects_extension(self):
        cfg = dict(self.cfg, earnings=EarningsTests.CFG['earnings'])
        import ipaddress
        aggregator = agg.Aggregator(cfg, self.FixedPoller((datetime.now(UTC), 'ok', RTX_OK)), self.FixedEarnings())
        server = ThreadingHTTPServer(('127.0.0.1', 0), agg.handler_for(aggregator, [ipaddress.ip_network('127.0.0.0/8')]))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}/status'
        with urllib.request.urlopen(base, timeout=5) as response:
            self.assertNotIn('market', json.loads(response.read()))
        request = urllib.request.Request(base, headers={'X-Miner-Status-Features': 'earnings'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertIn('market', json.loads(response.read()))

    def serve(self, networks):
        aggregator = agg.Aggregator(self.cfg, self.FixedPoller((datetime.now(UTC), 'ok', RTX_OK)))
        import ipaddress
        server = ThreadingHTTPServer(('127.0.0.1', 0), agg.handler_for(aggregator, [ipaddress.ip_network(n) for n in networks]))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def test_http_status_health_and_methods(self):
        base = self.serve(['127.0.0.0/8'])
        with urllib.request.urlopen(base + '/status', timeout=5) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/json; charset=utf-8')
            jsonschema.validate(json.loads(response.read()), SCHEMA)
        with urllib.request.urlopen(base + '/health', timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {'ok': True})
        for method in ('POST', 'PUT', 'DELETE'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(base + '/status', method=method, data=b'x'), timeout=5)
            self.assertEqual(caught.exception.code, 405)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + '/other', timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_peer_outside_networks_is_forbidden(self):
        base = self.serve(['10.35.219.0/24'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + '/status', timeout=5)
        self.assertEqual(caught.exception.code, 403)

    def test_config_rejects_wildcard_bind(self):
        path = self.dir / 'bad.json'
        path.write_text(json.dumps(dict(self.cfg, bind_ip='0.0.0.0', allowed_networks=['0.0.0.0/0'])), encoding='utf-8')
        with self.assertRaises(RuntimeError):
            agg.load_config(path)
        path.write_text(json.dumps(dict(self.cfg, bind_ip='192.168.1.5', allowed_networks=['10.35.219.0/24'])), encoding='utf-8')
        with self.assertRaises(RuntimeError):
            agg.load_config(path)

    def test_unreachable_rtx_url_is_reported(self):
        poller = agg.RtxPoller(config(self.dir / 's.json', url='http://127.0.0.1:9/status'))
        poller.poll_once()
        self.assertEqual(poller.latest()[1], 'unreachable')


class NodeWatchTests(unittest.TestCase):
    NODES = ['hk.example', 'sg.example', 'global.example']
    RULES = {'min_gain_ms': 80, 'min_ratio': 1.5, 'cooldown_seconds': 21600}

    def setUp(self):
        self.now = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)

    def probe(self, current, hk=40, sg=80, world=240, running=True):
        return {'latency': {'hk.example': hk, 'sg.example': sg, 'global.example': world},
                'current': current, 'running': running}

    def decide(self, probe, last_switch=None):
        return agg.NodeWatcher.decide(probe, self.NODES, last_switch, self.now, self.RULES)

    def test_far_node_moves_to_the_fastest(self):
        self.assertEqual(self.decide(self.probe('global.example')), ['hk.example', 'sg.example', 'global.example'])

    def test_small_gain_is_not_worth_a_restart(self):
        self.assertIsNone(self.decide(self.probe('sg.example')))           # 40 ms faster: below 80
        self.assertIsNone(self.decide(self.probe('sg.example', sg=110)))   # 70 ms faster
        self.assertIsNone(self.decide(self.probe('hk.example')))           # already the best

    def test_unreachable_current_node_moves(self):
        order = self.decide(self.probe('hk.example', hk=None))
        self.assertEqual(order, ['sg.example', 'global.example', 'hk.example'])

    def test_cooldown_and_unknowns_leave_it_alone(self):
        self.assertIsNone(self.decide(self.probe('global.example'), last_switch=self.now - timedelta(hours=2)))
        self.assertIsNotNone(self.decide(self.probe('global.example'), last_switch=self.now - timedelta(hours=7)))
        self.assertIsNone(self.decide(self.probe(None)))                    # node in use unknown
        self.assertIsNone(self.decide(self.probe('global.example', running=False)))
        self.assertIsNone(self.decide(self.probe('global.example', hk=None, sg=None, world=None)))

    def test_check_once_writes_the_switch_file(self):
        cfg = {'node_watch': dict(self.RULES, interval_seconds=1800, nodes=self.NODES, machines={
            'rig': {'display_name': 'Rig', 'log_dir': r'C:\Miner\logs', 'switch_path': r'C:\Miner\SWITCH_NODES',
                    'ssh': {'client': 'ssh', 'key': 'k', 'known_hosts': 'kh', 'user': 'u', 'host': '192.0.2.5'}}})}
        scripts, sent = [], []

        def run(command, **kwargs):
            script = base64.b64decode(command[-1].split('-EncodedCommand ', 1)[1]).decode('utf-16-le')
            scripts.append(script)
            out = json.dumps(self.probe('global.example')) if 'TcpClient' in script else 'ok'
            return subprocess.CompletedProcess(command, 0, out.encode('utf-8'), b'')

        watcher = agg.NodeWatcher(cfg, notify=lambda *a: sent.append(a), run=run)
        watcher.check_once(self.now)
        self.assertEqual(len(scripts), 2)
        self.assertIn("Set-Content -Path 'C:\\Miner\\SWITCH_NODES'", scripts[1])
        self.assertIn('hk.example\r\nsg.example\r\nglobal.example', scripts[1])
        self.assertEqual(sent, [], 'an automatic switch is logged, not sent to the phone')
        snap = watcher.snapshot()['rig']
        self.assertEqual((snap['current'], snap['lastSwitch']), ('global.example', agg.iso(self.now)))
        # Within the cooldown nothing is written again.
        watcher.check_once(self.now + timedelta(minutes=30))
        self.assertEqual(len(scripts), 3)

    def test_failed_probe_is_recorded(self):
        cfg = {'node_watch': dict(self.RULES, interval_seconds=1800, nodes=self.NODES, machines={
            'rig': {'log_dir': 'x', 'switch_path': 'y'}})}
        watcher = agg.NodeWatcher(cfg, run=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b'', b''))
        watcher.check_once(self.now)
        self.assertEqual(watcher.snapshot()['rig']['error'], 'exit 1')


class ControlTests(unittest.TestCase):
    """The one write path: start/stop the laptop's guard, behind a token."""

    TOKEN = 'correct-horse-battery-staple'

    def setUp(self):
        laptop = dict(json.loads(json.dumps(LAPTOP)), guard_path=r'C:\Miner\laptop_miner.ps1',
                      guard_console=r'C:\Miner\guard-console.log')
        self.cfg = dict(config('unused'), laptop=laptop, control={'token': self.TOKEN, 'machines': ['laptop3070']})
        self.now = 1000.0
        self.answer = (0, 'started')
        self.commands, self.sent = [], []

        def run(command, **kwargs):
            self.commands.append(command)
            code, text = self.answer
            return subprocess.CompletedProcess(command, code, (text + '\r\n').encode('utf-8'), b'')

        self.poller = agg.SshStatusPoller(self.cfg, 'laptop')
        self.controller = agg.Controller(self.cfg, {'laptop': self.poller}, notify=lambda *a: self.sent.append(a),
                                         run=run, clock=lambda: self.now)

    def script(self, command):
        encoded = command[-1].split('-EncodedCommand ', 1)[1]
        return base64.b64decode(encoded).decode('utf-16-le')

    def test_start_runs_the_guard(self):
        code, body = self.controller.handle('laptop3070', 'start', self.TOKEN)
        self.assertEqual((code, body['result'], body['ok']), (200, 'started', True))
        script = self.script(self.commands[0])
        self.assertIn('Invoke-CimMethod', script)
        self.assertIn(r'-File C:\Miner\laptop_miner.ps1', script)
        self.assertIn("CurrentDirectory='C:\\Miner'", script)
        self.assertIn("'already-running'", script)
        self.assertIn('BatchMode=yes', self.commands[0])
        self.assertEqual(self.sent, [], 'an action from the phone is not sent back to the phone')

    def test_stop_uses_the_stop_file(self):
        self.answer = (0, 'stop-requested')
        code, body = self.controller.handle('laptop3070', 'stop', self.TOKEN)
        self.assertEqual((code, body['result']), (200, 'stop-requested'))
        self.assertIn(r"-Path 'C:\Miner\STOP'", self.script(self.commands[0]))

    def test_no_op_answers_do_not_notify(self):
        self.answer = (0, 'already-running')
        self.assertEqual(self.controller.handle('laptop3070', 'start', self.TOKEN)[1]['result'], 'already-running')
        self.assertEqual(self.sent, [])

    def test_wrong_token_and_lockout(self):
        for _ in range(4):
            self.assertEqual(self.controller.handle('laptop3070', 'start', 'nope')[0], 401)
        self.assertEqual(self.controller.handle('laptop3070', 'start', '')[0], 401)
        # Five failures lock control, even for the right token, without running anything.
        self.assertEqual(self.controller.handle('laptop3070', 'start', self.TOKEN)[0], 429)
        self.assertEqual(self.commands, [])
        self.now += agg.Controller.LOCK + 1
        self.assertEqual(self.controller.handle('laptop3070', 'start', self.TOKEN)[0], 200)

    def test_cooldown_between_actions(self):
        self.assertEqual(self.controller.handle('laptop3070', 'start', self.TOKEN)[0], 200)
        self.now += 5
        self.assertEqual(self.controller.handle('laptop3070', 'stop', self.TOKEN)[0], 429)
        self.now += agg.Controller.COOLDOWN
        self.answer = (0, 'stop-requested')
        self.assertEqual(self.controller.handle('laptop3070', 'stop', self.TOKEN)[0], 200)

    def test_only_listed_machines_and_actions(self):
        self.assertEqual(self.controller.handle('rtx5080', 'start', self.TOKEN)[0], 404)
        self.assertEqual(self.controller.handle('laptop3070', 'reboot', self.TOKEN)[0], 404)
        self.assertEqual(self.commands, [])

    def test_unreachable_machine(self):
        self.answer = (255, '')
        self.assertEqual(self.controller.handle('laptop3070', 'start', self.TOKEN)[0], 502)

    def test_disabled_without_token(self):
        cfg = dict(self.cfg, control={'token': '', 'machines': ['laptop3070']})
        controller = agg.Controller(cfg, {'laptop': self.poller})
        self.assertEqual(controller.handle('laptop3070', 'start', '')[0], 503)
        self.assertFalse(controller.can_control('laptop3070'))

    def add_wake(self):
        self.poller.cfg['wake'] = {'mac': '<REDACTED>', 'broadcasts': ['192.168.1.255', '255.255.255.255'],
                                   'relay': {'client': 'ssh', 'key': 'k', 'known_hosts': 'kh', 'user': 'u',
                                             'host': '10.0.0.2'}}

    def set_poll(self, status, running=None):
        self.poller.result = (datetime.now(UTC), status, None)
        self.poller.last_running = running

    def test_wake_goes_through_the_relay_only_when_off(self):
        self.add_wake()
        self.set_poll('unreachable', False)
        self.assertTrue(self.controller.can_wake('laptop3070'))
        manual = []
        self.controller.on_manual = manual.append
        self.answer = (0, 'woke')
        code, body = self.controller.handle('laptop3070', 'wake', self.TOKEN)
        self.assertEqual((code, body['result']), (200, 'woke'))
        self.assertEqual(self.commands[0][-2], 'u@10.0.0.2', 'the packet must be sent by the relay')
        script = self.script(self.commands[0])
        self.assertIn('0xB0,0x25,0xAA,0x48,0x86,0x7A', script)
        self.assertIn("'192.168.1.255','255.255.255.255'", script)
        self.assertEqual(manual, ['laptop3070'])
        self.assertEqual(self.sent, [])
        # Switched on (or lost while mining): no wake button, and a wake does nothing.
        self.now += agg.Controller.COOLDOWN + 1
        self.set_poll('ok', False)
        self.assertFalse(self.controller.can_wake('laptop3070'))
        self.assertEqual(self.controller.handle('laptop3070', 'wake', self.TOKEN)[1]['result'], 'already-on')
        self.set_poll('unreachable', True)
        self.assertFalse(self.controller.can_wake('laptop3070'))

    def test_wake_needs_wake_config_and_reports_a_dead_relay(self):
        self.set_poll('unreachable', False)
        self.assertFalse(self.controller.can_wake('laptop3070'))
        self.assertEqual(self.controller.handle('laptop3070', 'wake', self.TOKEN)[0], 404)
        self.add_wake()
        self.answer = (255, '')
        code, body = self.controller.handle('laptop3070', 'wake', self.TOKEN)
        self.assertEqual(code, 502)
        self.assertIn('代發', body['message'])

    def test_feed_marks_controllable_machines_only_when_asked(self):
        now = datetime.now(UTC)
        class Fixed:
            def __init__(self, result): self.result = result
            def latest(self): return self.result
        running = dict(RTX_OK, data_at=now.isoformat())
        aggregator = agg.Aggregator(self.cfg, Fixed(None), extra={'laptop': Fixed((now, 'ok', running))})
        aggregator.controller = self.controller
        plain = aggregator.feed(now)['machines']
        self.assertTrue(all('control' not in m for m in plain))
        machines = {m['machineId']: m for m in aggregator.feed(now, include_control=True)['machines']}
        self.assertNotIn('control', machines['rtx5080'])
        self.assertEqual(machines['laptop3070']['control'], {'canStart': False, 'canStop': True})

    def test_http_control_endpoint(self):
        aggregator = agg.Aggregator(self.cfg, FeedAndServerTests.FixedPoller((datetime.now(UTC), 'ok', RTX_OK)))
        aggregator.controller = self.controller
        import ipaddress
        server = ThreadingHTTPServer(('127.0.0.1', 0), agg.handler_for(aggregator, [ipaddress.ip_network('127.0.0.0/8')]))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        request = urllib.request.Request(base + '/control/laptop3070/start', method='POST',
                                         headers={'Authorization': f'Bearer {self.TOKEN}'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.loads(response.read())['result'], 'started')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(base + '/control/laptop3070/stop', method='POST',
                                                          headers={'Authorization': 'Bearer wrong'}), timeout=5)
        self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(base + '/control/laptop3070', method='POST'), timeout=5)
        self.assertEqual(caught.exception.code, 405)


class MachineControlTests(unittest.TestCase):
    """Start/stop for the RTX 5080 (SSH), the FPGA (its own script) and this PC (through the AI console)."""

    TOKEN = ControlTests.TOKEN
    SSH = {'client': 'ssh', 'key': 'k', 'known_hosts': 'kh', 'user': 'u', 'host': '10.0.0.3'}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / 'panel-mining-state.json'
        self.console = self.dir / 'Strata'
        self.console.mkdir()
        self.cfg = dict(config(self.state), control={'token': self.TOKEN, 'machines': ['office', 'rtx5080', 'vcu118']})
        self.cfg['office'] = dict(self.cfg['office'], control={'console_dir': str(self.console), 'console_python': 'pythonw'})
        self.now, self.launched, self.on_tick = 5000.0, [], None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_tick:
            self.on_tick()

    def office(self, alive=True):
        return agg.OfficeControl(self.cfg, launch=lambda *a, **k: self.launched.append(a[0]), clock=self.clock,
                                 sleep=self.sleep, alive=lambda pid: alive)

    def mining(self, status, age=0):
        self.state.write_text(json.dumps({'status': status, 'updated': self.now - age}), encoding='utf-8')

    def console_says(self, status, pid=4242):
        (self.console / 'media-control-state.json').write_text(json.dumps({'pid': pid, 'status': status, 'closed': False}),
                                                               encoding='utf-8')

    def request(self):
        path = self.console / 'media-control-request.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def take_request(self):
        request = self.request()
        if request:
            (self.console / 'media-control-request.json').unlink()
        return request

    def test_office_start_asks_the_running_console_and_waits_for_the_miner(self):
        self.mining('stopped')
        self.console_says('已停止控制台管理的服務。')
        seen = []

        def console_takes_it():
            request = self.take_request()
            if request:
                seen.append(request['action'])
                self.mining('preparing')
        self.on_tick = console_takes_it
        self.assertEqual(self.office().act('start'), 'started')
        self.assertEqual(seen, ['Mining'])
        self.assertEqual(self.launched, [])

    def test_office_start_reports_the_consoles_refusal(self):
        self.mining('stopped')
        self.console_says('有圖片／影片正在生成，請等完成後再切換。')
        self.on_tick = self.take_request
        self.assertEqual(self.office().act('start'), 'start-refused')

    def test_office_start_opens_a_closed_console_in_mining_mode(self):
        self.mining('stopped')
        self.on_tick = lambda: self.mining('preparing') if self.launched else None
        self.assertEqual(self.office(alive=False).act('start'), 'started')
        self.assertEqual(self.launched[0][-2:], ['--mode', 'Mining'])
        self.assertIsNone(self.request())

    def test_office_start_and_stop_when_already_there(self):
        self.mining('running')
        self.assertEqual(self.office().act('start'), 'already-running')
        self.mining('stopped')
        self.assertEqual(self.office().act('stop'), 'not-running')
        self.mining('running', age=600)
        self.assertEqual(self.office().act('stop'), 'not-running', 'a state nobody updates is not mining')

    def test_office_stop_through_the_console(self):
        self.mining('running')
        self.console_says('珍珠挖礦中')
        seen = []

        def console_stops():
            request = self.take_request()
            if request:
                seen.append(request['action'])
                self.mining('stopped')
        self.on_tick = console_stops
        self.assertEqual(self.office().act('stop'), 'stopped')
        self.assertEqual(seen, ['Stop'])

    def test_office_start_without_an_answer_fails(self):
        self.mining('stopped')
        self.console_says('已停止控制台管理的服務。')
        self.assertEqual(self.office().act('start'), 'start-failed')

    def test_rtx_scripts(self):
        commands = agg.rtx_control_commands({'ssh': self.SSH, 'root': r'C:\Rig\Pearl-mining'})

        def decode(command):
            return base64.b64decode(command[-1].split('-EncodedCommand ', 1)[1]).decode('utf-16-le')
        start, stop = decode(commands['start']), decode(commands['stop'])
        self.assertEqual(commands['start'][-2], 'u@10.0.0.3')
        self.assertIn("'already-running'", start)
        self.assertIn(r"$root = 'C:\Rig\Pearl-mining'", start)
        self.assertIn(r"(Join-Path $root '.venv\Scripts\python.exe')", start)
        self.assertIn("(Join-Path $root 'session.py')", start)
        self.assertIn('STOP_REQUEST', stop)
        self.assertIn("-notmatch '--restore'", stop, 'the restore watcher must survive a stop')
        self.assertIn("'stopped'", stop)
        # The supervisor task: start runs it and clears HOLD; stop leaves HOLD before anything else,
        # even with no miner running (the supervisor may be cooling down between sessions).
        self.assertIn("Start-ScheduledTask -TaskName 'PearlAutoStart'", start)
        self.assertIn(r'"$root\HOLD"', start.split('Start-ScheduledTask')[0])
        self.assertLess(stop.index('HOLD'), stop.index("'not-running'"))

    def test_controller_uses_each_machines_own_driver(self):
        answers = {'rtx5080': 'started', 'vcu118': 'stopped'}

        class Driver:
            def __init__(self, machine_id):
                self.machine_id, self.cfg = machine_id, {'display_name': machine_id.upper()}

            def act(self, action):
                return answers.get(self.machine_id, 'start-refused')
        drivers = {m: Driver(m) for m in ('office', 'rtx5080', 'vcu118', 'laptop3070')}
        sent = []
        controller = agg.Controller(self.cfg, {}, notify=lambda *a: sent.append(a), clock=self.clock, drivers=drivers)
        self.assertFalse(controller.can_control('laptop3070'), 'only machines listed in control.machines')
        self.assertTrue(controller.can_control('rtx5080'))
        self.assertFalse(controller.can_wake('rtx5080'))
        self.assertEqual(controller.handle('rtx5080', 'start', self.TOKEN)[1]['result'], 'started')
        code, body = controller.handle('vcu118', 'stop', self.TOKEN)
        self.assertEqual((code, body['ok'], body['message']), (200, True, '已停止挖礦'))
        code, body = controller.handle('office', 'start', self.TOKEN)
        self.assertEqual((code, body['ok']), (200, False))
        self.assertEqual(controller.handle('rtx5080', 'wake', self.TOKEN)[0], 404)
        self.assertEqual(sent, [])

    def test_command_control_takes_the_last_line(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, b'checking temperature\r\nstarted\r\n', b'')
        control = agg.CommandControl(self.cfg, 'rtx', {'start': ['fpga', 'start'], 'stop': ['fpga', 'stop']}, run=run)
        self.assertEqual(control.act('start'), 'started')
        self.assertEqual(calls, [['fpga', 'start']])
        refused = agg.CommandControl(self.cfg, 'rtx', {'start': ['x']},
                                     run=lambda c, **k: subprocess.CompletedProcess(c, 1, b'start-failed\r\n', b''))
        self.assertEqual(refused.act('start'), 'start-failed', 'the answer counts whatever the exit code')
        unreachable = agg.CommandControl(self.cfg, 'rtx', {'stop': ['x']},
                                         run=lambda c, **k: subprocess.CompletedProcess(c, 255, b'', b'connection refused'))
        self.assertIsNone(unreachable.act('stop'))


class PowerStateTests(unittest.TestCase):
    """A machine that may be switched off: no 失聯 while off, and a fresh 'stopped' after waking."""

    def setUp(self):
        self.cfg = dict(config('unused'), laptop=dict(json.loads(json.dumps(LAPTOP)), hide_when_off=True))
        self.poller = agg.SshStatusPoller(self.cfg, 'laptop')
        self.payload = None

    def poll(self, code, payload=None):
        text = json.dumps(payload) if payload is not None else ''

        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, code, text.encode('utf-8'), b'')
        self.poller.poll_once(run)

    def test_powered_off_only_when_last_seen_stopped(self):
        self.poll(255)
        self.assertTrue(self.poller.powered_off(), 'never seen and unreachable: treated as off')
        self.poll(0, dict(RTX_OK, miner_running=True))
        self.poll(255)
        self.assertFalse(self.poller.powered_off(), 'dropped off while mining is lost, not off')
        self.poll(0, dict(RTX_OK, miner_running=False, state='stopped', stop_reason='user_requested'))
        self.poll(255)
        self.assertTrue(self.poller.powered_off())

    def test_stale_stopped_file_reads_as_stopped_now(self):
        old = dict(RTX_OK, miner_running=False, state='stopped', stop_reason='user_requested',
                   data_at='2026-10-20T00:00:00+00:00')
        self.poll(0, old)
        checked, status, payload = self.poller.latest()
        self.assertEqual(payload['data_at'], checked.isoformat())
        # A stale file that still says "running" is left alone for the guardian to judge.
        self.poll(0, dict(old, miner_running=True))
        self.assertEqual(self.poller.latest()[2]['data_at'], '2026-10-20T00:00:00+00:00')

    def test_switched_off_machine_leaves_the_feed(self):
        aggregator = agg.Aggregator(self.cfg, agg.RtxPoller(self.cfg), extra={'laptop': self.poller})
        self.poll(255)
        self.assertNotIn('laptop3070', [m['machineId'] for m in aggregator.feed()['machines']])
        self.poll(0, dict(RTX_OK, miner_running=True))
        self.poll(255)
        self.assertIn('laptop3070', [m['machineId'] for m in aggregator.feed()['machines']])

    def test_requested_stop_does_not_notify(self):
        sent = []
        notifier = agg.Notifier({'notify': {'server': 'x', 'topic': 't', 'check_seconds': 1, 'offline_after_seconds': 60,
                                            'stopped_after_seconds': 60}}, send=lambda *a: sent.append(a))
        now = datetime(2026, 10, 27, tzinfo=UTC)
        for reason, expected in (('userRequested', 0), ('protectionTriggered', 1)):
            machine = {'machineId': reason, 'displayName': 'x', 'stop': {'reason': reason},
                       'assessment': {'light': 'red', 'title': '已停止', 'reason': 'r'}, 'health': {}}
            notifier.check({'machines': [machine]}, now)
            notifier.check({'machines': [machine]}, now + timedelta(seconds=120))
            self.assertEqual(len(sent), expected)


class AutoMinerTests(unittest.TestCase):
    TARIFF = {'summerPeak': 5.16, 'summerOffPeak': 2.06, 'nonSummerPeak': 4.93, 'nonSummerOffPeak': 1.99}
    PROFILE = {'display_name': '筆電', 'hashrate_th': 25.6, 'gpu_w': 82, 'extra_watts': 10,
               'adapter_efficiency': 0.88, 'idle_watts': 20, 'auto': True, 'start_at': 300, 'stop_below': 250}
    OFF_PEAK = datetime(2026, 10, 27, 1, 0, tzinfo=agg.TAIPEI)  # Tuesday 01:00
    PEAK = datetime(2026, 10, 27, 20, 0, tzinfo=agg.TAIPEI)

    class Source:
        def __init__(self, value):
            self.value = value

        def latest(self):
            return self.value

    class Poller:
        def __init__(self):
            self.cfg = {'timeout_seconds': 5}
            self.result, self.last_running, self.remote_calls = None, None, []

        def set(self, state):
            self.result = (None, 'unreachable' if state == 'off' else 'ok', {})
            self.last_running = {'off': False, 'on': False, 'mining': True}[state]

        def latest(self):
            return self.result

        def powered_off(self):
            return self.result[1] == 'unreachable' and self.last_running is not True

        def command(self, remote):
            return ['ssh', remote]

        def remote(self, script):
            self.remote_calls.append(script)
            return 'shutting-down'

    class Controller:
        def __init__(self, poller):
            self.machines = {'laptop3070': poller}
            self.actions, self.idle_text = [], 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active 3:15 10/27 08:00\n'

        def wake(self, machine_id):
            self.actions.append('wake')
            return 'woke'

        def act(self, machine_id, action):
            self.actions.append(action)
            return {'start': 'started', 'stop': 'stop-requested'}[action]

        def run(self, command, **kwargs):
            return subprocess.CompletedProcess(command, 0, self.idle_text.encode('cp950'), b'')

    def make(self, folder, per_th=0.00999):
        self.poller = self.Poller()
        self.controller = self.Controller(self.poller)
        self.sent = []
        cfg = {'auto_mine': {'machines': {'laptop3070': {'min_net_per_hour': 0.05, 'user_idle_minutes': 15}}},
               'advice': {'machines': {'laptop3070': self.PROFILE}}}
        earnings = self.Source({'per_th_day': per_th, 'market': {'coinUsd': 1.74, 'usdTwd': 32.58}})
        return agg.AutoMiner(cfg, self.controller, earnings, self.Source(self.TARIFF),
                             notify=lambda *a: self.sent.append(a), path=Path(folder) / 'auto.json')

    def test_user_idle_minutes(self):
        header = ' 使用者名稱 工作階段名稱 識別碼 狀態 閒置時間 登入時間\n'
        self.assertEqual(agg.user_idle_minutes(header + '>pjunman console 1 使用中 . 2026/10/27 08:00\n'), 0)
        self.assertEqual(agg.user_idle_minutes(header + '>pjunman console 1 使用中 無 2026/10/27 08:00\n'), 0)
        self.assertEqual(agg.user_idle_minutes(header + '>pjunman console 1 Active 1:05 10/27 08:00\n'), 65)
        self.assertEqual(agg.user_idle_minutes(header + '>pjunman console 1 Active 2+00:10 10/27 08:00\n'), 2890)
        self.assertEqual(agg.user_idle_minutes(header + ' pjunman 2 Disc 5 10/27 08:00\n'), None)
        self.assertIsNone(agg.user_idle_minutes(''))

    def test_next_price_change(self):
        change = agg.next_price_change(datetime(2026, 10, 27, 12, 30, tzinfo=agg.TAIPEI)).astimezone(agg.TAIPEI)
        self.assertEqual((change.day, change.hour), (27, 14))
        change = agg.next_price_change(datetime(2026, 10, 27, 23, 30, tzinfo=agg.TAIPEI)).astimezone(agg.TAIPEI)
        self.assertEqual((change.day, change.hour), (28, 0))

    def test_off_peak_wakes_then_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            self.poller.set('off')
            decision = auto.step_machine('laptop3070', auto.settings['machines']['laptop3070'], self.OFF_PEAK)
            self.assertEqual(decision[0], 'mine')
            self.assertIn('離峰 1.99', decision[1])
            self.assertEqual(self.controller.actions, ['wake'])
            self.assertEqual(self.sent, [], 'what the automation did on its own is not a notification')
            self.poller.set('on')
            auto.step_machine('laptop3070', auto.settings['machines']['laptop3070'], self.OFF_PEAK + timedelta(minutes=2))
            self.assertEqual(self.controller.actions, ['wake', 'start'])

    def test_peak_stops_and_shuts_down_only_what_it_woke(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = auto.settings['machines']['laptop3070']
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, self.PEAK)
            self.assertEqual(decision[0], 'rest')
            self.assertIn('尖峰 4.93', decision[1])
            self.assertEqual(self.controller.actions, ['stop'])
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=2))
            self.assertEqual(self.poller.remote_calls, [], 'never switched on by the automation: left on')
            auto.entry('laptop3070')['owned'] = True
            auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=3))
            self.assertEqual(len(self.poller.remote_calls), 1)
            self.assertIn('shutdown /s', self.poller.remote_calls[0])

    def test_left_on_and_idle_is_shut_down_when_asked(self):
        # 2026-11-10: a hand-made stop/start cleared `owned` and the laptop sat on, not mining, for a day.
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = dict(auto.settings['machines']['laptop3070'], shutdown_when_idle=True, idle_shutdown_minutes=30)
            self.poller.set('on')
            self.controller.idle_text = 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active 20 10/27 08:00\n'
            decision = auto.step_machine('laptop3070', settings, self.PEAK)
            self.assertIn('沒人用滿 30 分鐘就關機', decision[1])
            self.assertEqual(self.poller.remote_calls, [])
            self.controller.idle_text = 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active 31 10/27 08:00\n'
            auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=11))
            self.assertEqual(len(self.poller.remote_calls), 1)
            self.assertIn('shutdown /s', self.poller.remote_calls[0])
            # Someone at the keyboard: never.
            self.poller.remote_calls.clear()
            self.controller.idle_text = 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active . 10/27 08:00\n'
            auto.entry('laptop3070')['last_try'].clear()
            decision = auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=30))
            self.assertIn('不自動關機', decision[1])
            self.assertEqual(self.poller.remote_calls, [])
            # Without the setting the old rule holds: only what it switched on.
            self.controller.idle_text = 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active 3:15 10/27 08:00\n'
            auto.step_machine('laptop3070', auto.settings['machines']['laptop3070'], self.PEAK + timedelta(minutes=45))
            self.assertEqual(self.poller.remote_calls, [])

    def test_someone_using_it_blocks_start_and_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = auto.settings['machines']['laptop3070']
            self.controller.idle_text = 'USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n>pjunman console 1 Active . 10/27 08:00\n'
            self.poller.set('on')
            decision = auto.step_machine('laptop3070', settings, self.OFF_PEAK)
            self.assertIn('有人正在使用', decision[1])
            self.assertEqual(self.controller.actions, [])
            auto.entry('laptop3070')['owned'] = True
            decision = auto.step_machine('laptop3070', settings, self.PEAK)
            self.assertIn('不自動關機', decision[1])
            self.assertEqual(self.poller.remote_calls, [])
            self.assertFalse(auto.entry('laptop3070')['owned'])

    def test_manual_action_pauses_until_the_price_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = auto.settings['machines']['laptop3070']
            auto.override('laptop3070', self.PEAK)
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=5))
            self.assertIn('暫停到 00:00', decision[1])
            self.assertEqual(self.controller.actions, [])
            auto.step_machine('laptop3070', settings, self.PEAK + timedelta(hours=5))  # Next day 01:00, off-peak.
            self.assertEqual(self.controller.actions, [])  # Already mining at an off-peak price: nothing to do.

    def test_wake_gives_up_after_three_tries_per_period(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = auto.settings['machines']['laptop3070']
            self.poller.set('off')
            for minutes in (0, 11, 22, 33, 44):
                decision = auto.step_machine('laptop3070', settings, self.OFF_PEAK + timedelta(minutes=minutes))
            self.assertEqual(self.controller.actions, ['wake'] * 3)
            self.assertIn('叫不醒', decision[1])
            self.assertTrue(any('自動開機失敗' in s[0] for s in self.sent))

    def test_two_thresholds_and_a_minimum_time_stop_the_flapping(self):
        # 2026-10-27: at peak the net crossed a single threshold back and forth and the laptop was
        # woken and shut down twice in a morning.
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = {'start_net_per_hour': 1.0, 'stop_net_per_hour': 0.0, 'min_state_minutes': 60,
                        'user_idle_minutes': 15}
            self.poller.set('off')
            decision = auto.step_machine('laptop3070', settings, self.OFF_PEAK)
            self.assertEqual(decision[0], 'rest', 'NT$0.37/h is below a NT$1/h start threshold')
            self.assertIn('NT$1.00', decision[1])
            self.assertEqual(self.controller.actions, [])
            # Once mining (here: started by hand), it carries on while the net stays above the stop threshold.
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, self.OFF_PEAK + timedelta(minutes=1))
            self.assertEqual((decision[0], self.controller.actions), ('mine', []))
            # A loss stops it, but not within the minimum time after the automation started it.
            entry = auto.entry('laptop3070')
            entry['active'], entry['changed_at'] = True, (self.PEAK - timedelta(minutes=30)).isoformat()
            decision = auto.step_machine('laptop3070', settings, self.PEAK)
            self.assertEqual((decision[0], self.controller.actions), ('mine', []))
            decision = auto.step_machine('laptop3070', settings, self.PEAK + timedelta(minutes=31))
            self.assertEqual(decision[0], 'rest')
            self.assertEqual(self.controller.actions, ['stop'])

    def test_off_days_mine_on_their_own_threshold_and_stop_with_the_off_day(self):
        # 2026-11-01: weekends and national holidays are off-peak all day; mine then even though
        # the net is below the weekday start threshold.
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = {'start_net_per_hour': 1.0, 'stop_net_per_hour': 0.0, 'off_day_start_net_per_hour': 0.0,
                        'min_state_minutes': 60, 'user_idle_minutes': 15}
            saturday = datetime(2026, 10, 31, 10, 0, tzinfo=agg.TAIPEI)
            self.poller.set('off')
            decision = auto.step_machine('laptop3070', settings, saturday)
            self.assertEqual(decision[0], 'mine')
            self.assertIn('假日離峰 1.99', decision[1])
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, saturday + timedelta(minutes=2))
            self.assertEqual(self.controller.actions, ['wake', 'start'])
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, datetime(2026, 11, 1, 23, 50, tzinfo=agg.TAIPEI))
            self.assertEqual((decision[0], self.controller.actions), ('mine', ['wake', 'start']))
            # Monday 00:10 is a weekday but still off-peak at the same price: the weekend run carries on.
            decision = auto.step_machine('laptop3070', settings, datetime(2026, 11, 2, 0, 10, tzinfo=agg.TAIPEI))
            self.assertEqual(decision[0], 'mine')
            self.assertIn('離峰 1.99', decision[1])
            self.assertNotIn('假日', decision[1])
            self.assertEqual(self.controller.actions, ['wake', 'start'])
            # 06:10, the first peak price: the run ends, and the laptop it woke is shut down.
            monday = datetime(2026, 11, 2, 6, 10, tzinfo=agg.TAIPEI)
            decision = auto.step_machine('laptop3070', settings, monday)
            self.assertEqual(decision[0], 'rest')
            self.assertIn('尖峰 4.93', decision[1])
            self.assertEqual(self.controller.actions, ['wake', 'start', 'stop'])
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, monday + timedelta(minutes=2))
            self.assertEqual(len(self.poller.remote_calls), 1)
            self.assertIn('shutdown /s', self.poller.remote_calls[0])

    def test_a_run_it_started_on_a_machine_left_on_ends_with_a_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            settings = {'start_net_per_hour': 1.0, 'off_day_start_net_per_hour': 0.0, 'user_idle_minutes': 15}
            self.poller.set('on')  # Switched on by hand and left idle (3 h 15 min).
            auto.step_machine('laptop3070', settings, datetime(2026, 11, 1, 21, 25, tzinfo=agg.TAIPEI))
            self.assertEqual(self.controller.actions, ['start'])
            self.poller.set('mining')
            auto.step_machine('laptop3070', settings, datetime(2026, 11, 2, 6, 10, tzinfo=agg.TAIPEI))
            self.assertEqual(self.controller.actions, ['start', 'stop'])
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, datetime(2026, 11, 2, 6, 12, tzinfo=agg.TAIPEI))
            self.assertEqual(len(self.poller.remote_calls), 1)
            self.assertIn('shutdown /s', self.poller.remote_calls[0])

    def test_a_weekend_run_ends_at_peak_even_while_it_still_pays_a_little(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder, per_th=0.018)  # About NT$0.5/h at peak: below the weekday NT$1 start.
            settings = {'start_net_per_hour': 1.0, 'stop_net_per_hour': 0.0, 'off_day_start_net_per_hour': 0.0,
                        'min_state_minutes': 60, 'user_idle_minutes': 15}
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, datetime(2026, 11, 1, 21, 25, tzinfo=agg.TAIPEI))
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, datetime(2026, 11, 2, 6, 10, tzinfo=agg.TAIPEI))
            self.assertIn('尖峰 4.93，每小時淨賺', decision[1].replace(' 元／度', ''))
            self.assertEqual((decision[0], self.controller.actions), ('rest', ['start', 'stop']))

    def test_weekday_nights_mine_on_their_own_threshold_and_end_at_peak(self):
        # 2026-11-10: the user wanted weekday 00–06 off-peak mining too, on a lower threshold.
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder, per_th=0.018)  # Still pays about NT$0.5/h at peak.
            settings = {'start_net_per_hour': 1.0, 'stop_net_per_hour': 0.0, 'off_day_start_net_per_hour': 0.0,
                        'night_start_net_per_hour': 0.3, 'night_hours': [0, 6],
                        'min_state_minutes': 60, 'user_idle_minutes': 15}
            self.poller.set('off')
            decision = auto.step_machine('laptop3070', settings, self.OFF_PEAK)  # Tuesday 01:00
            self.assertEqual(decision[0], 'mine')
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, self.OFF_PEAK + timedelta(minutes=2))
            self.assertEqual(self.controller.actions, ['wake', 'start'])
            self.poller.set('mining')
            decision = auto.step_machine('laptop3070', settings, datetime(2026, 10, 27, 6, 10, tzinfo=agg.TAIPEI))
            self.assertEqual((decision[0], self.controller.actions), ('rest', ['wake', 'start', 'stop']))
            self.poller.set('on')
            auto.step_machine('laptop3070', settings, datetime(2026, 10, 27, 6, 12, tzinfo=agg.TAIPEI))
            self.assertIn('shutdown /s', self.poller.remote_calls[0])

    def test_night_threshold_only_inside_the_night_hours(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)  # NT$0.37/h off-peak.
            settings = {'start_net_per_hour': 1.0, 'night_start_net_per_hour': 0.3, 'night_hours': [0, 6],
                        'user_idle_minutes': 15}
            self.poller.set('off')
            noon = datetime(2026, 10, 27, 12, 0, tzinfo=agg.TAIPEI)  # Weekday off-peak, not night.
            self.assertEqual(auto.step_machine('laptop3070', settings, noon)[0], 'rest')
            self.assertEqual(self.controller.actions, [])
            strict = dict(settings, night_start_net_per_hour=0.5)
            self.assertEqual(auto.step_machine('laptop3070', strict, self.OFF_PEAK)[0], 'rest', '0.37 < 0.5')
            self.assertEqual(auto.step_machine('laptop3070', settings, self.OFF_PEAK)[0], 'mine')

    def test_advice_row_with_night_mining_says_tonight(self):
        class Advisor:
            def __init__(self):
                self.reasons = []

            def set_auto(self, machine_id, recommendation, reason, now):
                self.reasons.append(reason)

        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            auto.advisor = Advisor()
            auto.settings['machines']['laptop3070'].update(start_net_per_hour=1.0, night_start_net_per_hour=0.3,
                                                           night_hours=[0, 6])
            self.poller.set('off')
            auto.step(datetime(2026, 11, 3, 10, 0, tzinfo=agg.TAIPEI))  # Tuesday: rests
            self.assertTrue(auto.advisor.reasons[-1].endswith('下次自動開機挖：明天 00:00'), auto.advisor.reasons[-1])
            auto.step(datetime(2026, 11, 6, 10, 0, tzinfo=agg.TAIPEI))  # Friday: Saturday 00:00 either way
            self.assertTrue(auto.advisor.reasons[-1].endswith('下次自動開機挖：明天 00:00'), auto.advisor.reasons[-1])

    def test_a_weekday_holiday_counts_as_an_off_day(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            auto.tariff = self.Source(dict(self.TARIFF, offPeakDates=['2026-10-27']))
            settings = {'start_net_per_hour': 1.0, 'off_day_start_net_per_hour': 0.0, 'user_idle_minutes': 15}
            self.poller.set('on')
            decision = auto.step_machine('laptop3070', settings, self.PEAK)  # Tuesday 20:00, a holiday.
            self.assertEqual(decision[0], 'mine')
            self.assertIn('假日離峰 1.99', decision[1])
            self.assertEqual(self.controller.actions, ['start'])

    def test_advice_row_says_whether_the_machine_is_on(self):
        class Advisor:
            def __init__(self):
                self.reasons = []

            def set_auto(self, machine_id, recommendation, reason, now):
                self.reasons.append(reason)

        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            auto.advisor = Advisor()
            for state in ('off', 'on', 'mining'):
                self.poller.set(state)
                auto.step(self.PEAK)
            self.assertEqual([reason.split('｜')[0] for reason in auto.advisor.reasons],
                             ['關機中', '開機・沒在挖', '開機・挖礦中'])

    def test_advice_row_says_when_it_acts_next(self):
        # 2026-11-01: the user asked when the laptop would next shut down and start; the row says it.
        class Advisor:
            def __init__(self):
                self.reasons = []

            def set_auto(self, machine_id, recommendation, reason, now):
                self.reasons.append(reason)

        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder)
            auto.advisor = Advisor()
            auto.settings['machines']['laptop3070'].update(start_net_per_hour=1.0, off_day_start_net_per_hour=0.0)
            self.poller.set('on')
            auto.step(datetime(2026, 11, 1, 21, 25, tzinfo=agg.TAIPEI))  # Sunday: starts
            self.assertTrue(auto.advisor.reasons[-1].endswith('預計 明天 06:00 停挖關機'), auto.advisor.reasons[-1])
            self.poller.set('off')
            auto.entry('laptop3070').update(active=False, owned=False, changed_at=None)
            auto.step(datetime(2026, 11, 3, 10, 0, tzinfo=agg.TAIPEI))  # Tuesday: rests
            self.assertTrue(auto.advisor.reasons[-1].endswith('下次自動開機挖：11/7（六）00:00'), auto.advisor.reasons[-1])
        self.assertEqual(agg.when_text(agg.first_weekday_peak(self.TARIFF, datetime(2026, 7, 4, 12, 0, tzinfo=agg.TAIPEI)),
                                       datetime(2026, 7, 4, 12, 0, tzinfo=agg.TAIPEI)), '7/6（一）09:00', 'summer: 09:00')
        holiday = dict(self.TARIFF, offPeakDates=['2026-12-25'])
        self.assertEqual(agg.next_off_day(holiday, datetime(2026, 12, 23, 12, 0, tzinfo=agg.TAIPEI)).day, 25)

    def test_worse_market_means_no_off_peak_mining_either(self):
        with tempfile.TemporaryDirectory() as folder:
            auto = self.make(folder, per_th=0.003)
            self.poller.set('off')
            decision = auto.step_machine('laptop3070', auto.settings['machines']['laptop3070'], self.OFF_PEAK)
            self.assertEqual(decision[0], 'rest')
            self.assertEqual(self.controller.actions, [])


class KeeperTests(unittest.TestCase):
    NOW = datetime(2026, 11, 1, 13, 30, tzinfo=timezone.utc)

    class Controller:
        def __init__(self):
            self.actions, self.answer = [], 'started'

        def can_control(self, machine_id):
            return machine_id != 'unlisted'

        def act(self, machine_id, action):
            self.actions.append((machine_id, action))
            return self.answer

    class Aggregator:
        def __init__(self):
            self.machines, self.controller = [], None

        def feed(self, now=None):
            return {'machines': self.machines}

    def setUp(self):
        self.tick = [1000.0]
        self.notices = []
        self.aggregator = self.Aggregator()
        self.aggregator.controller = self.controller = self.Controller()
        cfg = {'keep_mining': {'machines': ['office2', 'vcu118'], 'delays_seconds': [60, 300], 'warn_after': 2}}
        self.keeper = agg.Keeper(cfg, self.aggregator, notify=lambda *args: self.notices.append(args),
                                 clock=lambda: self.tick[0])

    def machine(self, state='stopped', reason='abnormalExit', source='ok', temp=None, temp_age=0, machine_id='office2'):
        gpus = None if temp is None else [{'temperatureC': temp, 'observedAt': agg.iso(self.NOW - timedelta(seconds=temp_age))}]
        return {'machineId': machine_id, 'displayName': '辦公室 2', 'sourceStatus': source,
                'minerRunning': {'state': state}, 'stop': {'reason': reason}, 'gpus': gpus}

    def run_for(self, seconds, step=30):
        for _ in range(int(seconds // step)):
            self.tick[0] += step
            self.keeper.step(self.NOW)

    def test_a_crashed_machine_is_started_after_the_first_delay(self):
        self.aggregator.machines = [self.machine()]
        self.keeper.step(self.NOW)
        self.run_for(30)
        self.assertEqual(self.controller.actions, [])
        self.run_for(30)
        self.assertEqual(self.controller.actions, [('office2', 'start')])

    def test_a_stop_someone_asked_for_is_left_alone(self):
        self.aggregator.machines = [self.machine(reason='userRequested')]
        self.keeper.step(self.NOW)
        self.run_for(3600)
        self.assertEqual(self.controller.actions, [])

    def test_unreachable_unknown_unlisted_and_running_machines_are_not_started(self):
        self.aggregator.machines = [self.machine(source='unreachable'), self.machine(state='unknown', machine_id='vcu118'),
                                    self.machine(machine_id='unlisted'), self.machine(machine_id='rtx5080')]
        self.keeper.step(self.NOW)
        self.run_for(3600)
        self.assertEqual(self.controller.actions, [])
        self.aggregator.machines = [self.machine(state='running', reason=None)]
        self.run_for(3600)
        self.assertEqual(self.controller.actions, [])

    def test_a_fresh_hot_reading_waits_but_an_old_one_does_not(self):
        self.aggregator.machines = [self.machine(temp=75)]
        self.keeper.step(self.NOW)
        self.run_for(600)
        self.assertEqual(self.controller.actions, [])
        self.aggregator.machines = [self.machine(temp=75, temp_age=600)]  # the guard's own start check decides
        self.run_for(30)
        self.assertEqual(self.controller.actions, [('office2', 'start')])

    def test_quick_failures_wait_longer_warn_once_and_reset_after_half_an_hour(self):
        self.aggregator.machines = [self.machine()]
        self.keeper.step(self.NOW)
        self.run_for(60)
        self.assertEqual(len(self.controller.actions), 1)
        self.run_for(270)
        self.assertEqual(len(self.controller.actions), 1)  # second wait is 300 s
        self.run_for(30)
        self.assertEqual(len(self.controller.actions), 2)
        self.assertEqual(len(self.notices), 1)
        self.assertIn('warning', self.notices[0][3])
        self.run_for(900)
        self.assertEqual(len(self.notices), 1)
        count = len(self.controller.actions)
        self.aggregator.machines = [self.machine(state='running', reason=None)]
        self.run_for(1830)
        self.aggregator.machines = [self.machine()]
        self.run_for(60)  # seen stopped, then 30 s later
        self.assertEqual(len(self.controller.actions), count)
        self.run_for(30)
        self.assertEqual(len(self.controller.actions), count + 1)  # back to the 60 s delay
        self.assertEqual(len(self.notices), 1)


class RampUpEarningsTests(unittest.TestCase):
    def machine(self, state='running', value=25.0):
        return {'minerRunning': {'state': state}, 'hashrate': {'value': value, 'unit': 'TH/s'}}

    def test_fresh_start_uses_local_hashrate(self):
        earnings = {'coinPerDay': 0.1, 'poolHashrate': 10e12, 'electricityBilled': True, 'observedAt': None}
        fixed = agg.ramp_up_earnings(self.machine(), earnings, 0.01)
        self.assertAlmostEqual(fixed['coinPerDay'], 0.25)
        self.assertEqual(fixed['poolHashrate'], 10e12, 'the pool figure itself is reported as is')

    def test_settled_stopped_or_unknown_keep_the_pool_estimate(self):
        earnings = {'coinPerDay': 0.2, 'poolHashrate': 20e12, 'electricityBilled': True, 'observedAt': None}
        self.assertIs(agg.ramp_up_earnings(self.machine(), earnings, 0.01), earnings)
        low = dict(earnings, poolHashrate=5e12)
        self.assertIs(agg.ramp_up_earnings(self.machine('stopped'), low, 0.01), low)
        self.assertIs(agg.ramp_up_earnings(self.machine(value=None), low, 0.01), low)
        self.assertIs(agg.ramp_up_earnings(self.machine(), low, None), low)
        self.assertIsNone(agg.ramp_up_earnings(self.machine(), None, 0.01))


class ProfitLogTests(unittest.TestCase):
    TARIFF = {'summerPeak': 5.16, 'summerOffPeak': 2.06, 'nonSummerPeak': 4.93, 'nonSummerOffPeak': 1.99,
              'offPeakDates': ['2026-10-26']}
    SETTINGS = {'extra_watts': 10, 'adapter_efficiency': 0.8, 'idle_watts': 20}

    def at(self, text):
        return datetime.fromisoformat(text)

    def test_tou_price_windows(self):
        price = agg.tou_price
        self.assertEqual(price(self.TARIFF, self.at('2026-10-27T10:00+08:00')), 4.93)  # Tue non-summer peak
        self.assertEqual(price(self.TARIFF, self.at('2026-10-27T12:30+08:00')), 1.99)  # midday off-peak
        self.assertEqual(price(self.TARIFF, self.at('2026-10-27T05:59+08:00')), 1.99)
        self.assertEqual(price(self.TARIFF, self.at('2026-10-26T20:00+08:00')), 1.99)  # published off-peak day
        self.assertEqual(price(self.TARIFF, self.at('2026-10-31T20:00+08:00')), 1.99)  # Saturday
        self.assertEqual(price(self.TARIFF, self.at('2026-07-01T08:59+08:00')), 2.06)  # summer
        self.assertEqual(price(self.TARIFF, self.at('2026-07-01T09:00+08:00')), 5.16)
        self.assertEqual(price(self.TARIFF, self.at('2026-10-27T02:00Z')), 4.93)  # UTC input, 10:00 Taipei
        self.assertIsNone(price(None, self.at('2026-10-27T10:00+08:00')))

    def machine(self, state='running'):
        return {'machineId': 'laptop3070', 'minerRunning': {'state': state},
                'hashrate': {'value': 25.0, 'unit': 'TH/s'}, 'gpus': [{'powerW': 70.0}],
                'earnings': {'coinPerDay': 0.2, 'poolHashrate': 20e12}}

    def test_sample_running(self):
        row = agg.profit_sample(self.machine(), {'coinUsd': 2.0, 'usdTwd': 30.0}, self.TARIFF, self.SETTINGS,
                                self.at('2026-10-27T10:00+08:00'), 3600)
        self.assertEqual(row['wall_w'], 100.0)
        self.assertAlmostEqual(row['revenue_twd'], 25 * 0.01 * 2 * 30 / 24)
        self.assertAlmostEqual(row['cost_twd'], 0.1 * 4.93)
        self.assertAlmostEqual(row['idle_cost_twd'], 0.02 * 4.93)

    def test_stopped_earns_and_draws_nothing(self):
        row = agg.profit_sample(self.machine('stopped'), {'coinUsd': 2.0, 'usdTwd': 30.0}, self.TARIFF,
                                self.SETTINGS, self.at('2026-10-27T10:00+08:00'), 3600)
        self.assertEqual((row['running'], row['revenue_twd'], row['cost_twd']), (0, 0.0, 0.0))
        self.assertAlmostEqual(row['idle_cost_twd'], 0.02 * 4.93)

    def test_missing_market_leaves_revenue_blank(self):
        row = agg.profit_sample(self.machine(), None, self.TARIFF, self.SETTINGS,
                                self.at('2026-10-27T10:00+08:00'), 300)
        self.assertIsNone(row['revenue_twd'])
        self.assertIsNotNone(row['cost_twd'])

    def test_log_caps_gaps_and_writes_csv(self):
        machine = self.machine()

        class Fake:
            tariff = None

            def feed(self, now, include_earnings=False):
                return {'machines': [machine, {'machineId': 'office'}], 'market': {'coinUsd': 2.0, 'usdTwd': 30.0}}

        with tempfile.TemporaryDirectory() as folder:
            log = agg.ProfitLog({'profit_log': {'machines': ['laptop3070'], 'interval_seconds': 300}}, Fake(), folder)
            start = self.at('2026-10-27T10:00+08:00')
            log.sample(start)
            log.sample(start + timedelta(seconds=300))
            log.sample(start + timedelta(hours=3))
            rows = (Path(folder) / 'laptop3070.csv').read_text(encoding='utf-8').splitlines()
            self.assertFalse((Path(folder) / 'office.csv').exists())
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.split(',')[1] for r in rows[1:]], ['300.0', '600.0'])


class EarningsLedgerTests(unittest.TestCase):
    @staticmethod
    def at(text):
        return datetime.fromisoformat(text)

    class Fake:
        def __init__(self, machines):
            self.machines = machines

        def feed(self, now, include_earnings=False, include_coins=False):
            return {'machines': self.machines, 'market': {'coin': 'PRL', 'coinUsd': 2.0, 'btcUsd': 80000.0, 'usdTwd': 30.0}}

    @staticmethod
    def machine(machine_id, state='running', per_day=2.4, **earnings):
        return {'machineId': machine_id, 'minerRunning': {'state': state},
                'earnings': dict({'coinPerDay': per_day}, **earnings)}

    def test_running_machines_add_their_estimate_in_their_own_coin_and_price(self):
        machines = [self.machine('rtx5080'), self.machine('office', state='stopped'),
                    self.machine('vcu118', per_day=0.0096, coin='QTC', coinUsd=75.0)]
        with tempfile.TemporaryDirectory() as folder:
            ledger = agg.EarningsLedger(self.Fake(machines), Path(folder) / 'ledger.json')
            start = self.at('2026-10-28T10:00+08:00')
            ledger.step(start)
            self.assertIsNone(ledger.for_feed())  # The first step only starts the clock.
            ledger.step(start + timedelta(hours=1))
            ledger.step(start + timedelta(hours=10))  # A gap counts at most two intervals (600 s).
            feed = ledger.for_feed()
        self.assertEqual(feed['since'], '2026-10-28T10:00:00.000+08:00')
        rows = {row['machineId']: row for row in feed['machines']}
        self.assertNotIn('office', rows)
        # Each step is capped at 600 s: 1200 s in all.
        self.assertAlmostEqual(rows['rtx5080']['amount'], 2.4 * 1200 / 86400)
        self.assertAlmostEqual(rows['rtx5080']['twd'], 2.4 * 1200 / 86400 * 2.0 * 30.0)
        self.assertEqual(rows['vcu118']['coin'], 'QTC')
        self.assertAlmostEqual(rows['vcu118']['twd'], 0.0096 * 1200 / 86400 * 75.0 * 30.0)
        self.assertAlmostEqual(feed['totalTwd'], rows['rtx5080']['twd'] + rows['vcu118']['twd'])
        # In BTC at each moment's price, the unit the pool account is kept in.
        self.assertAlmostEqual(feed['totalBtc'], (2.4 * 2.0 + 0.0096 * 75.0) * 1200 / 86400 / 80000.0)

    def test_the_total_starts_over_on_the_first_and_keeps_the_month_that_ended(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            ledger = agg.EarningsLedger(self.Fake([self.machine('rtx5080')]), path)
            start = self.at('2026-10-31T23:50+08:00')
            ledger.step(start)
            ledger.step(start + timedelta(minutes=5))   # 23:55, still October
            october = ledger.for_feed()['totalTwd']
            ledger.step(start + timedelta(minutes=10))  # 00:00 on November 1st: the step that crosses counts there
            ledger.step(start + timedelta(minutes=15))
            feed = ledger.for_feed()
            self.assertEqual(feed['since'], '2026-11-01T00:00:00.000+08:00')
            self.assertAlmostEqual(feed['machines'][0]['amount'], 2.4 * 600 / 86400)
            again = agg.EarningsLedger(self.Fake([self.machine('rtx5080')]), path)  # kept across a restart
            self.assertEqual(len(again.months), 1)
            self.assertEqual((again.months[0]['since'], again.months[0]['until']),
                             ('2026-10-31T23:50:00.000+08:00', '2026-11-01T00:00:00.000+08:00'))
            self.assertAlmostEqual(again.months[0]['totalTwd'], october)
            self.assertEqual(again.since, '2026-11-01T00:00:00.000+08:00')

    def test_a_total_saved_before_btc_was_kept_gets_it_at_todays_price(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            path.write_text(json.dumps({'since': '2026-10-28T10:21:02.903+08:00', 'updated': None,
                                        'machines': {'rtx5080': {'coin': 'PRL', 'amount': 1.0, 'twd': 60.0}}}),
                            encoding='utf-8')
            ledger = agg.EarningsLedger(self.Fake([self.machine('rtx5080')]), path)
            self.assertIsNone(ledger.for_feed()['totalBtc'])
            start = self.at('2026-10-28T11:00+08:00')
            ledger.step(start)
            ledger.step(start + timedelta(seconds=300))
            feed = ledger.for_feed()
        self.assertAlmostEqual(feed['totalBtc'], 60.0 / 30.0 / 80000.0 + 2.4 * 300 / 86400 * 2.0 / 80000.0)

    def test_the_total_survives_a_restart_and_keeps_its_start(self):
        machines = [self.machine('rtx5080')]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            first = agg.EarningsLedger(self.Fake(machines), path)
            start = self.at('2026-10-28T10:00+08:00')
            first.step(start)
            first.step(start + timedelta(seconds=300))
            again = agg.EarningsLedger(self.Fake(machines), path)
            again.step(start + timedelta(days=1))
            again.step(start + timedelta(days=1, seconds=300))
            feed = again.for_feed()
        self.assertEqual(feed['since'], '2026-10-28T10:00:00.000+08:00')
        self.assertAlmostEqual(feed['machines'][0]['amount'], 2.4 * 600 / 86400)

    def test_each_hour_keeps_the_estimate_before_calibration_for_eight_days(self):
        machines = [self.machine('vcu118', per_day=0.0096, coin='QTC', coinUsd=75.0)]
        with tempfile.TemporaryDirectory() as folder:
            calibration = Path(folder) / 'calibration.json'
            calibration.write_text(json.dumps({'QTC': {'factor': 2.0}}), encoding='utf-8')
            saved, agg.CALIBRATION = agg.CALIBRATION, calibration
            try:
                ledger = agg.EarningsLedger(self.Fake(machines), Path(folder) / 'ledger.json')
                start = self.at('2026-10-28T10:57:00+08:00')
                ledger.step(start)
                ledger.step(start + timedelta(seconds=300))  # midpoint 10:59:30
                ledger.step(start + timedelta(seconds=600))  # midpoint 11:04:30
                again = agg.EarningsLedger(self.Fake(machines), Path(folder) / 'ledger.json')
                self.assertEqual(again.hours, ledger.hours)
                step = 0.0096 * 300 / 86400 * 75.0 / 80000.0 / 2.0
                self.assertAlmostEqual(ledger.hours['vcu118']['2026-10-28T10'], step)
                self.assertAlmostEqual(ledger.hours['vcu118']['2026-10-28T11'], step)
                ledger.step(start + timedelta(days=9))
                ledger.step(start + timedelta(days=9, seconds=300))
                self.assertEqual(list(ledger.hours['vcu118']), ['2026-11-06T10'])
            finally:
                agg.CALIBRATION = saved

    def test_today_keeps_each_hours_mining_seconds_and_gpu_watt_hours(self):
        laptop = dict(self.machine('laptop3070'), gpus=[{'powerW': 60.0}, {'powerW': None}])
        machines = [laptop, dict(self.machine('office', state='stopped'), gpus=[{'powerW': 200.0}])]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            ledger = agg.EarningsLedger(self.Fake(machines), path)
            start = self.at('2026-11-12T00:57:00+08:00')
            ledger.step(start)
            ledger.step(start + timedelta(seconds=300))  # midpoint 00:59:30
            ledger.step(start + timedelta(seconds=600))  # midpoint 01:04:30
            today = ledger.today_for_feed(start + timedelta(hours=8))  # the laptop is off by then
            again = agg.EarningsLedger(self.Fake(machines), path)  # kept across a restart
            self.assertEqual(again.today_for_feed(start + timedelta(hours=8)), today)
        self.assertEqual(today['date'], '2026-11-12')
        self.assertEqual([row['machineId'] for row in today['machines']], ['laptop3070'])  # stopped adds nothing
        row = today['machines'][0]
        self.assertAlmostEqual(row['twd'], 2.4 * 600 / 86400 * 2.0 * 30.0)
        self.assertEqual([hour['hour'] for hour in row['hours']], [0, 1])
        self.assertAlmostEqual(row['hours'][0]['miningSeconds'], 300)
        self.assertAlmostEqual(row['hours'][0]['gpuWh'], 60.0 * 300 / 3600)

    def test_today_starts_over_at_midnight_and_counts_power_without_a_price(self):
        laptop = dict(self.machine('laptop3070', per_day=None), gpus=[{'powerW': 60.0}])
        with tempfile.TemporaryDirectory() as folder:
            ledger = agg.EarningsLedger(self.Fake([laptop]), Path(folder) / 'ledger.json')
            start = self.at('2026-11-11T23:50+08:00')
            ledger.step(start)
            ledger.step(start + timedelta(minutes=5))   # midpoint 23:52:30, still the 11th
            self.assertEqual(ledger.today_for_feed(start + timedelta(minutes=5))['machines'][0]['hours'][0]['hour'], 23)
            self.assertEqual(ledger.today_for_feed(start + timedelta(minutes=15))['machines'], [])  # the 12th: nothing yet
            ledger.step(start + timedelta(minutes=15))  # midpoint 00:00, the 12th
            today = ledger.today_for_feed(start + timedelta(minutes=15))
        self.assertEqual(today['date'], '2026-11-12')
        row = today['machines'][0]
        self.assertEqual(row['twd'], 0.0)  # no estimate: no income, but the electricity was spent
        self.assertEqual(row['hours'], [{'hour': 0, 'miningSeconds': 600.0, 'gpuWh': 10.0}])
        self.assertIsNone(ledger.for_feed())  # the cumulative total still only counts priced income

    def test_missing_prices_or_estimates_add_nothing(self):
        machines = [self.machine('rtx5080', per_day=None), self.machine('vcu118', coin='QTC', coinUsd=None)]
        with tempfile.TemporaryDirectory() as folder:
            ledger = agg.EarningsLedger(self.Fake(machines), Path(folder) / 'ledger.json')
            start = self.at('2026-10-28T10:00+08:00')
            ledger.step(start)
            ledger.step(start + timedelta(seconds=300))
            self.assertIsNone(ledger.for_feed())


class AdvisorTests(unittest.TestCase):
    TARIFF = {'summerPeak': 5.16, 'summerOffPeak': 2.06, 'nonSummerPeak': 4.93, 'nonSummerOffPeak': 1.99}
    LAPTOP = {'display_name': 'RTX 3070 筆電', 'hashrate_th': 25.6, 'gpu_w': 82, 'extra_watts': 10,
              'adapter_efficiency': 0.88, 'idle_watts': 20, 'count_idle': True,
              'start_at': 300, 'stop_below': 250, 'initial': 'rest'}

    class Source:
        def __init__(self, value):
            self.value = value

        def latest(self):
            return self.value

    def advisor(self, folder, per_th_day, sent):
        cfg = {'advice': {'hold_hours': 1, 'interval_seconds': 600, 'machines': {'laptop3070': self.LAPTOP}}}
        earnings = self.Source({'per_th_day': per_th_day, 'market': {'coinUsd': 1.74, 'usdTwd': 32.58}})
        return agg.Advisor(cfg, earnings, self.Source(self.TARIFF), lambda *a: sent.append(a),
                           Path(folder) / 'state.json'), earnings

    def test_average_price_and_net(self):
        price = agg.average_price(self.TARIFF, datetime(2026, 10, 27, tzinfo=UTC))
        self.assertAlmostEqual(price, 4.93 * 75 / 168 + 1.99 * 93 / 168)
        net = agg.monthly_net(self.LAPTOP, 0.00999, 1.74, 32.58, price)
        self.assertTrue(100 < net < 180, net)  # About NT$140, the figure behind shutting it down.
        self.assertIsNone(agg.monthly_net(self.LAPTOP, None, 1.74, 32.58, price))

    def test_a_metered_wall_draw_replaces_the_gpu_estimate(self):
        rig = {'hashrate_th': 175, 'gpu_w': 205, 'extra_watts': 50, 'adapter_efficiency': 0.9, 'wall_watts': 380}
        income = 175 * 0.00999 * 1.74 * 32.58 * 30
        self.assertAlmostEqual(agg.monthly_net(rig, 0.00999, 1.74, 32.58, 3.0), income - 380 * 24 * 30 / 1000 * 3.0)
        self.assertAlmostEqual(agg.profile_watts(dict(rig, idle_watts=20), idle=True), 400)
        self.assertAlmostEqual(agg.profile_watts({'gpu_w': 82, 'extra_watts': 10, 'adapter_efficiency': 0.92}), 100)

    def test_turns_to_mine_after_hold_and_back_with_hysteresis(self):
        sent = []
        start = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            advisor, earnings = self.advisor(folder, 0.00999, sent)
            for i in range(6):
                advisor.step(start + timedelta(minutes=10 * i))
            self.assertEqual(advisor.for_feed()[0]['recommendation'], 'rest')
            earnings.value = dict(earnings.value, per_th_day=0.02)  # Income doubles.
            advisor.step(start + timedelta(minutes=60))
            self.assertEqual(advisor.for_feed()[0]['recommendation'], 'rest')  # One good sample is not enough.
            for i in range(7, 14):
                advisor.step(start + timedelta(minutes=10 * i))
            item = advisor.for_feed()[0]
            self.assertEqual(item['recommendation'], 'mine')
            self.assertEqual(len(sent), 1)
            self.assertIn('可以開機挖', sent[0][0])
            self.assertIn('NT$', sent[0][1])
            self.assertNotIn('laptop3070', sent[0][0] + sent[0][1])
            # Reloading keeps the state, so a restart does not notify again.
            again, _ = self.advisor(folder, 0.02, sent)
            again.step(start + timedelta(minutes=140))
            self.assertEqual(again.for_feed()[0]['recommendation'], 'mine')
            self.assertEqual(len(sent), 1)
            earnings.value = dict(earnings.value, per_th_day=0.00999)
            for i in range(15, 23):
                advisor.step(start + timedelta(minutes=10 * i))
            self.assertEqual(advisor.for_feed()[0]['recommendation'], 'rest')
            self.assertEqual(len(sent), 2)
            self.assertIn('停挖關機', sent[1][0])

    def test_no_market_data_keeps_recommendation(self):
        sent = []
        with tempfile.TemporaryDirectory() as folder:
            advisor, earnings = self.advisor(folder, None, sent)
            advisor.step(datetime(2026, 10, 27, tzinfo=UTC))
            item = advisor.for_feed()[0]
        self.assertEqual((item['recommendation'], item['monthlyNetTwd'], item['since']), ('rest', None, None))
        self.assertEqual(item['reason'], '收益資料不足，暫時無法估算')
        self.assertEqual(sent, [])

    def test_feed_only_when_asked(self):
        feed_cfg = config(Path('missing.json'))
        aggregator = agg.Aggregator(feed_cfg, agg.RtxPoller(feed_cfg))
        aggregator.advisor = type('A', (), {'for_feed': lambda self, wake=None: [
            dict({'machineId': 'x'}, **({'canWake': wake('x')} if wake else {}))]})()
        self.assertNotIn('advice', aggregator.feed())
        self.assertEqual(aggregator.feed(include_advice=True)['advice'], [{'machineId': 'x'}])
        # canWake only for apps that ask for `wake`; without a controller it is always false.
        self.assertEqual(aggregator.feed(include_advice=True, include_wake=True)['advice'],
                         [{'machineId': 'x', 'canWake': False}])


class KryptexRoutesTests(unittest.TestCase):
    RELAY = {'client': 'ssh', 'key': 'key', 'known_hosts': 'known', 'user': 'miner', 'host': '192.0.2.20'}
    URL = 'https://pool.kryptex.com/qtc/api/v1/pool/stats'

    def setUp(self):
        self.now, self.direct_calls, self.relay_calls = 1000.0, [], []
        self.direct_fails = self.relay_fails = False

    def direct(self, url):
        self.direct_calls.append(url)
        if self.direct_fails:
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
        return {'via': 'direct'}

    def ssh(self, command, **kwargs):  # not `run`: that is TestCase's own
        self.relay_calls.append(command)
        code = b'403' if self.relay_fails else b'200'
        return subprocess.CompletedProcess(command, 0, b'{"via": "relay"}\n' + code, b'')

    def routes(self):
        return agg.KryptexRoutes(self.RELAY, direct=self.direct, run=self.ssh, clock=lambda: self.now, rest_seconds=1800)

    def test_a_refused_route_hands_over_and_rests(self):
        routes = self.routes()
        self.assertEqual(routes.get_json(self.URL), {'via': 'direct'})
        self.direct_fails = True
        self.assertEqual(routes.get_json(self.URL), {'via': 'relay'})
        self.assertIn(self.URL, self.relay_calls[-1][-1])
        # While the direct route rests, the relay is asked first and the blocked address is left alone.
        calls = len(self.direct_calls)
        self.now += 600
        self.assertEqual(routes.get_json(self.URL), {'via': 'relay'})
        self.assertEqual(len(self.direct_calls), calls)
        # After the rest the direct route is tried first again, and used once it answers.
        self.now += 1800
        self.direct_fails = False
        self.assertEqual(routes.get_json(self.URL), {'via': 'direct'})

    def test_the_relay_failing_falls_back_to_this_pc_and_both_failing_raises(self):
        routes = self.routes()
        self.direct_fails = True
        routes.get_json(self.URL)  # direct now rests, relay answers
        self.relay_fails, self.direct_fails = True, False
        self.assertEqual(routes.get_json(self.URL), {'via': 'direct'})
        self.direct_fails = True
        with self.assertRaises(OSError):
            routes.get_json(self.URL)

    def test_without_a_relay_only_this_pc_is_used_and_odd_urls_never_reach_a_shell(self):
        routes = agg.KryptexRoutes(None, direct=self.direct, run=self.ssh)
        self.direct_fails = True
        with self.assertRaises(urllib.error.HTTPError):
            routes.get_json(self.URL)
        self.assertEqual(self.relay_calls, [])
        with self.assertRaises(ValueError):
            self.routes().via_relay('https://pool.kryptex.com/x" & del *')
        price = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,twd'
        self.assertEqual(self.routes().via_relay(price), {'via': 'relay'})


class FpgaRelayTests(unittest.TestCase):
    """The VCU118 relay's log, read as a machine of its own and priced in its own coin."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        state = self.dir / 'panel-mining-state.json'
        state.write_text(json.dumps({'status': 'running', 'updated': time.time(), 'gpus': []}), encoding='utf-8')
        self.cfg = dict(config(state), vcu118={'machine_id': 'vcu118', 'display_name': 'VCU118', 'worker': 'vcu118',
                                               'log_dir': str(self.dir), 'poll_seconds': 10})
        self.now = datetime.now(UTC)

    def write_log(self, seconds_ago=3):
        stamp = lambda s: (self.now - timedelta(seconds=s)).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        lines = [f'{stamp(40)},100 relay INFO share accepted: OK',
                 f'{stamp(30)},119 relay INFO 300.0 MHz 149.521 MH/s | 52.0 C 0.842 V | cands 255 (hw err 0) shares 1 acc 1 rej 0 | flags 0x1',
                 f'{stamp(20)},797 relay INFO job 617b2926_1 diff 1 -> chip job 15 (target64 0x39)',
                 f'{stamp(seconds_ago)},120 relay INFO 300.0 MHz 150.000 MH/s | 51.9 C 0.841 V | cands 414 (hw err 0) shares 2 acc 2 rej 0 | flags 0x1']
        (self.dir / 'mining_20261027.err').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (self.dir / 'mining.pid').write_text('30664\n', encoding='ascii')

    def machine(self, alive, include_earnings=False, include_coins=False, coins=None):
        poller = agg.RelayLogPoller(self.cfg, 'vcu118', alive=lambda pid: alive)
        poller.poll_once(self.now)
        aggregator = agg.Aggregator(self.cfg, FeedAndServerTests.FixedPoller(None), FeedAndServerTests.FixedEarnings(),
                                    extra={'vcu118': poller})
        aggregator.coins = coins or {}
        feed = aggregator.feed(self.now, include_earnings=include_earnings, include_assessment=True,
                               include_coins=include_coins)
        return feed, feed['machines'][-1]

    def test_a_running_relay_is_a_mining_machine(self):
        self.write_log()
        feed, m = self.machine(alive=True)
        jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())
        self.assertEqual(m['machineId'], 'vcu118')
        self.assertEqual(m['minerRunning']['state'], 'running')
        self.assertEqual((m['hashrate']['value'], m['hashrate']['unit']), (149.76, 'MH/s'))  # mean of the two lines
        self.assertEqual(m['gpus'][0]['temperatureC'], 51.9)
        self.assertEqual((m['shares']['accepted'], m['shares']['rejected']), (2, 0))
        self.assertEqual((m['assessment']['light'], m['assessment']['title']), ('green', '挖礦中'))

    def test_an_exited_relay_is_stopped_and_a_silent_one_is_lost(self):
        self.write_log()
        self.assertEqual(self.machine(alive=False)[1]['assessment']['light'], 'red')
        self.write_log(seconds_ago=600)
        self.assertEqual(self.machine(alive=True)[1]['assessment']['light'], 'gray')

    def test_a_stop_someone_asked_for_is_not_trouble(self):
        # mining_ctl.py stop leaves mining.stopped: a manual stop, which raises no notification.
        self.write_log()
        self.assertEqual(self.machine(alive=False)[1]['stop']['reason'], 'unknown')
        (self.dir / 'mining.stopped').write_text('2026-10-28T10:48:00', encoding='utf-8')
        m = self.machine(alive=False)[1]
        self.assertEqual(m['stop']['reason'], 'userRequested')
        notifier = agg.Notifier({'notify': {'topic': 't', 'server': 's', 'stopped_after_seconds': 60,
                                            'offline_after_seconds': 180}}, send=lambda *a: None)
        track = {}
        notifier.conditions(m, track, self.now)
        self.assertEqual(notifier.conditions(m, track, self.now + timedelta(minutes=5)), {})

    def test_a_machine_stopped_from_the_phone_stays_a_manual_stop_until_it_runs(self):
        self.write_log()
        poller = agg.RelayLogPoller(self.cfg, 'vcu118', alive=lambda pid: False)
        poller.poll_once(self.now)
        aggregator = agg.Aggregator(self.cfg, FeedAndServerTests.FixedPoller(None), extra={'vcu118': poller})
        aggregator.controller = agg.Controller(dict(self.cfg, control={'token': 't', 'machines': []}), {})
        aggregator.controller.stopped_by_phone.add('vcu118')
        self.assertEqual(aggregator.feed(self.now)['machines'][-1]['stop']['reason'], 'userRequested')
        poller.alive = lambda pid: True
        poller.poll_once(self.now)
        aggregator.feed(self.now)
        self.assertNotIn('vcu118', aggregator.controller.stopped_by_phone, 'running again clears it')

    def test_its_coin_is_priced_only_for_the_console(self):
        self.write_log()
        self.cfg['earnings'] = EarningsTests.CFG['earnings']
        self.cfg['earnings_qtc'] = {'pool_coin': 'qtc', 'coin': 'QTC', 'workers': {'vcu118': 'vcu118'},
                                    'electricity_billed': {'vcu118': False}}

        class Qtc:
            def worker(self, name):
                return {'status': 'online'} if name == 'vcu118' else None

            def latest(self):
                return {'checked_at': datetime(2026, 10, 27, 15, 0, tzinfo=UTC), 'workers': {'vcu118': (1.5e8, 0.0043)},
                        'per_th_day': 28.0, 'market': {'coin': 'QTC', 'coinUsd': 72.0, 'usdTwd': 32.5}}

        feed, m = self.machine(alive=True, include_earnings=True, coins={'earnings_qtc': Qtc()})
        self.assertIsNone(m['earnings'])  # the app would price it as PRL
        feed, m = self.machine(alive=True, include_earnings=True, include_coins=True, coins={'earnings_qtc': Qtc()})
        self.assertEqual((m['earnings']['coin'], m['earnings']['coinPerDay']), ('QTC', 0.0043))
        self.assertEqual((m['earnings']['coinUsd'], m['earnings']['electricityBilled']), (72.0, False))
        jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())  # what the new app accepts

    def test_a_kv260_in_the_same_folder_reads_only_its_own_files(self):
        # 2026-11-04: the KV260's relay logs beside the VCU118's under kv260_mining_* with its own pid file.
        self.write_log()
        stamp = (self.now - timedelta(seconds=3)).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        (self.dir / 'kv260_mining_20261104_143337.err').write_text(
            f'{stamp},074 relay INFO 266.7 MHz 16.613 MH/s | 39.9 C 0.713 V | cands 217 (hw err 0) shares 0 acc 0 rej 0 | flags 0x1 | '
            'VCCINT 0.0 A, set 0.000 V read 0.000 V, regulator 0 C (INA226 0.000 mV, pmbus status 0x0)\n', encoding='utf-8')
        (self.dir / 'kv260_mining.pid').write_text('34292\n', encoding='ascii')
        self.cfg['kv260'] = {'machine_id': 'kv260', 'display_name': 'KV260 FPGA', 'worker': 'kv260', 'log_dir': str(self.dir),
                             'log_prefix': 'kv260_mining_', 'pid_file': 'kv260_mining.pid', 'stop_mark': 'kv260_mining.stopped'}
        pids = []
        kv = agg.RelayLogPoller(self.cfg, 'kv260', alive=lambda pid: pids.append(pid) or True)
        kv.poll_once(self.now)
        vcu = agg.RelayLogPoller(self.cfg, 'vcu118', alive=lambda pid: True)
        vcu.poll_once(self.now)
        aggregator = agg.Aggregator(self.cfg, FeedAndServerTests.FixedPoller(None), extra={'vcu118': vcu, 'kv260': kv})
        feed = aggregator.feed(self.now, include_assessment=True)
        jsonschema.validate(feed, SCHEMA, format_checker=jsonschema.FormatChecker())
        machines = {m['machineId']: m for m in feed['machines']}
        self.assertEqual(pids, [34292])
        self.assertEqual((machines['kv260']['hashrate']['value'], machines['kv260']['gpus'][0]['temperatureC']), (16.61, 39.9))
        self.assertEqual(machines['vcu118']['hashrate']['value'], 149.76)
        (self.dir / 'kv260_mining.stopped').write_text('2026-11-04T15:00:00', encoding='utf-8')
        kv.alive = lambda pid: False
        kv.poll_once(self.now)
        stopped = agg.rtx_machine(self.cfg, kv.latest(), agg.SessionTracker('kv260'), self.now, 'kv260')
        self.assertEqual(stopped['stop']['reason'], 'userRequested')
        self.assertEqual(vcu.latest()[2]['stop_reason'], None, 'the VCU118 is untouched')


class DetachedControlTests(unittest.TestCase):
    """mining_ctl.py runs detached: a slow start (a KV260 JTAG-loaded first) is never killed half way."""

    class Proc:
        def __init__(self, sink, words, done_after):
            self.sink, self.words, self.left, self.killed = sink, words, done_after, False

        def poll(self):
            self.left -= 1
            if self.left > 0:
                return None
            if self.words is not None:
                self.sink.write(self.words); self.words = None
            return 0

        def kill(self):
            self.killed = True

    def control(self, words, done_after, refuse_breakaway=False):
        self.dir = Path(tempfile.mkdtemp())
        self.launched, self.now = [], 0.0
        cfg = {'kv260': {'machine_id': 'kv260', 'log_dir': str(self.dir)}}

        def popen(command, stdout, stderr, stdin, creationflags):
            if refuse_breakaway and creationflags & agg.DetachedCommandControl.BREAKAWAY:
                raise OSError('access denied')
            stdout.close()  # the child keeps its own handle; write through a fresh one
            proc = self.Proc(open(stdout.name, 'ab', buffering=0), words, done_after)
            self.launched.append((command, creationflags, proc))
            return proc

        def sleep(seconds):
            self.now += seconds

        return agg.DetachedCommandControl(cfg, 'kv260', {'start': ['ctl', 'start'], 'stop': ['ctl', 'stop']},
                                          popen=popen, clock=lambda: self.now, sleep=sleep)

    def test_a_quick_answer_is_its_last_line(self):
        control = self.control(b'board: 40 C\nselftest ok\nstarted\n', done_after=3)
        self.assertEqual(control.act('start'), 'started')
        control = self.control(b'no kv260 FPGA answering\nstart-failed\n', done_after=3)
        self.assertEqual(control.act('start'), 'start-failed')
        self.assertTrue(self.launched[0][1] & agg.DetachedCommandControl.BREAKAWAY)

    def test_a_slow_start_is_left_running_and_reads_as_started(self):
        control = self.control(b'JTAG load: LOADED\nstarted\n', done_after=10_000)
        self.assertEqual(control.act('start'), 'started')
        self.assertLessEqual(self.now, 25.5, 'answers within the app wait')
        proc = self.launched[0][2]
        self.assertFalse(proc.killed)
        self.assertEqual(control.act('start'), 'started')  # pressed again while it is still loading
        self.assertEqual(len(self.launched), 1, 'not launched twice')

    def test_breakaway_refused_still_starts(self):
        control = self.control(b'stopped\n', done_after=2, refuse_breakaway=True)
        self.assertEqual(control.act('stop'), 'stopped')
        self.assertFalse(self.launched[0][1] & agg.DetachedCommandControl.BREAKAWAY)


class CalibrateTests(unittest.TestCase):
    @staticmethod
    def hour(day, h):
        return f'2026-10-{day}T{h:02d}'

    @staticmethod
    def row(day, h, sats):
        stamp = datetime(2026, 10, day, h, tzinfo=agg.TAIPEI).timestamp() * 1000
        return {'timestamp': stamp, 'vcu118': str(sats), 'rtx5080': '170.5'}

    def test_compares_mined_hours_and_the_late_credits_after_a_stop(self):
        import calibrate
        ledger = {self.hour(28, 8): 1e-6, self.hour(28, 9): 1e-6,  # the ledger's first hour and the next: left out
                  self.hour(28, 10): 3e-7, self.hour(28, 11): 3e-7}  # stopped during 11:xx
        web = [self.row(28, h, sats) for h, sats in ((8, 90), (9, 90), (10, 30), (11, 28), (12, 5), (13, 0), (14, 0))]
        now = datetime(2026, 10, 28, 14, 30, tzinfo=agg.TAIPEI)
        web_sats, est_sats, hours = calibrate.compare(web, ledger, 'vcu118', now)
        self.assertEqual((web_sats, hours), (63.0, 2))  # 10, 11 and the spill into 12
        self.assertAlmostEqual(est_sats, 60.0)
        # A run still going: the hour after the window is not counted on either side.
        ledger[self.hour(28, 12)] = ledger[self.hour(28, 13)] = 3e-7
        web_sats, est_sats, hours = calibrate.compare(web, ledger, 'vcu118', now)
        self.assertEqual((web_sats, hours), (63.0, 3))  # 10, 11, 12; 13 is the last complete hour
        self.assertAlmostEqual(est_sats, 90.0)
        self.assertEqual(calibrate.compare(web[:1], ledger, 'vcu118', now), (0.0, 0.0, 0))

    def test_the_factor_moves_halfway_only_past_the_noise_and_never_on_nonsense(self):
        import calibrate
        self.assertEqual(calibrate.next_factor(1.0, 1100, 1000)[0], 1.05)
        self.assertEqual(calibrate.next_factor(1.05, 1100, 1000)[0], 1.075)
        self.assertEqual(calibrate.next_factor(1.0, 1020, 1000)[0], 1.0)
        self.assertEqual(calibrate.next_factor(1.0, 500, 300)[0], 1.0)  # too little yet
        factor, note = calibrate.next_factor(1.0, 2000, 1000)
        self.assertEqual(factor, 1.0)
        self.assertTrue(note.startswith('WARN'))

    def test_main_writes_the_factor_the_service_reads(self):
        import calibrate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'aggregator.json').write_text(json.dumps({
                'earnings': {'coin': 'PRL', 'workers': {'rtx5080': 'rtx5080'}},
                'earnings_qtc': {'coin': 'QTC', 'workers': {'vcu118': 'vcu118'}}}), encoding='utf-8')
            start = datetime(2026, 10, 27, 0, tzinfo=agg.TAIPEI)
            hours = {(start + timedelta(hours=h)).strftime('%Y-%m-%dT%H'): 3e-7 for h in range(30)}
            (root / 'earnings-ledger.json').write_text(json.dumps({'hours': {'vcu118': hours}}), encoding='utf-8')
            web = [{'timestamp': (start + timedelta(hours=h)).timestamp() * 1000, 'vcu118': '33' if h < 30 else '0'}
                   for h in range(32)]  # stopped at the end of hour 29
            (root / 'web.json').write_text(json.dumps(web), encoding='utf-8')
            now = start + timedelta(hours=31, minutes=20)
            lines = calibrate.main([str(root / 'web.json')], now=now, root=root)
            saved = json.loads((root / 'calibration.json').read_text(encoding='utf-8'))
            # Hours 2–29 on both sides: 28 × 33 against 28 × 30 sats.
            self.assertEqual(saved['QTC']['history'][-1]['miningHours'], 28)
            self.assertAlmostEqual(saved['QTC']['factor'], 1.05)
            self.assertNotIn('PRL', saved)
            self.assertIn('factor 1.000 -> 1.050', lines[0])
            previous, agg.CALIBRATION = agg.CALIBRATION, root / 'calibration.json'
            try:
                self.assertEqual(agg.calibration_factor('QTC'), 1.05)
            finally:
                agg.CALIBRATION = previous

    def test_a_coin_mined_by_several_workers_is_fitted_on_their_sum(self):
        import calibrate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'aggregator.json').write_text(json.dumps({
                'earnings': {'coin': 'PRL', 'workers': {'office': 'llmrig', 'office2': 'rtx3070'}},
                'earnings_qtc': {'coin': 'QTC', 'workers': {'vcu118': 'vcu118'}}}), encoding='utf-8')
            start = datetime(2026, 10, 27, 0, tzinfo=agg.TAIPEI)
            office = {(start + timedelta(hours=h)).strftime('%Y-%m-%dT%H'): 2e-6 for h in range(40)}
            office2 = {(start + timedelta(hours=h)).strftime('%Y-%m-%dT%H'): 1e-6 for h in range(20, 40)}
            (root / 'earnings-ledger.json').write_text(json.dumps({'hours': {'office': office, 'office2': office2}}),
                                                       encoding='utf-8')
            web = {'start': start.timestamp() * 1000, 'step': 3600000,  # the compact form, one list per worker
                   'workers': {'llmrig': [160] * 41, 'rtx3070': [0] * 20 + [80] * 21, 'vcu118': [0] * 41}}
            (root / 'web.json').write_text(json.dumps(web), encoding='utf-8')
            now = start + timedelta(hours=40, minutes=20)
            lines = calibrate.main([str(root / 'web.json'), 'PRL', 'QTC'], now=now, root=root)
            saved = json.loads((root / 'calibration.json').read_text(encoding='utf-8'))
            # office hours 2–38 (37 h) and office2 hours 22–38 (17 h); the pool credited 80 % of the estimate.
            self.assertEqual(saved['PRL']['history'][-1]['miningHours'], 54)
            self.assertAlmostEqual(saved['PRL']['history'][-1]['estimateSats'], 37 * 200 + 17 * 100)
            self.assertAlmostEqual(saved['PRL']['factor'], 0.9)
            self.assertTrue(lines[0].startswith('PRL:'))
            self.assertEqual(saved['QTC']['factor'], 1.0)  # no hours for it: too little yet


if __name__ == '__main__':
    unittest.main(verbosity=1)
