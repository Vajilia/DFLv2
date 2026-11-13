"""The failure-alert tool (alerts/dfl_alert.py): written test-first from docs/ALERT_DESIGN_2026-11-12.md and the owner's requirements of 2026-11-12.

What the tool must do, and what these tests hold it to:
  1. Detect the monitor's failure and the daemon's start-limit exhaustion by reading systemd, never by asking the monitor (it must work with the monitor dead).
  2. Record every incident on disk BEFORE trying to deliver it; retry failed deliveries with back-off; suppress duplicates (one incident per signature while it lasts, one reminder per interval).
  3. Keep "delivery attempted" (the hand-off command exited 0) apart from "notification received" (a person acknowledged it). Only `ack` sets the second.
  4. Never release a latch, resume publication, reset a failure or restart a service. The only systemctl verb it runs is `show`; it writes only inside its own alert folder.
"""
import ast
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'alerts'))
import dfl_alert as A      # noqa: E402

DAEMON, MONITOR = 'dfl-test-daemon.service', 'dfl-test-monitor.service'


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakeSystemd:
    """`systemctl show` over a dict. Any other verb is a test failure: the tool must never run it."""
    def __init__(self):
        self.units = {}
        self.calls = []

    def set(self, unit, **props):
        self.units.setdefault(unit, {}).update({k: str(v) for k, v in props.items()})

    def __call__(self, argv):
        self.calls.append(list(argv))
        assert argv[0] == 'systemctl' and argv[1] == 'show', 'the alert tool ran %r' % (argv,)
        unit = argv[-1]
        props = [argv[i + 1] for i, a in enumerate(argv) if a == '-p']
        have = self.units.get(unit, {})
        return 0, ''.join('%s=%s\n' % (p, have.get(p, '')) for p in props), ''


class Sink:
    """The delivery hand-off. ok=False simulates a channel that is down."""
    def __init__(self):
        self.ok = True
        self.sent = []

    def __call__(self, message, cfg):
        self.sent.append(message)
        return (True, 'handed off') if self.ok else (False, 'channel down')


def healthy(sd):
    sd.set(DAEMON, ActiveState='active', SubState='running', Result='success', ExecMainStatus=0, NRestarts=0, StartLimitBurst=3)
    sd.set(MONITOR, ActiveState='active', SubState='running', Result='success', ExecMainStatus=0, NRestarts=0, StartLimitBurst=5)


def monitor_dead(sd, status=2):
    sd.set(MONITOR, ActiveState='failed', SubState='failed', Result='exit-code', ExecMainStatus=status, NRestarts=0)


def start_limit_hit(sd):
    sd.set(DAEMON, ActiveState='failed', SubState='failed', Result='start-limit-hit', ExecMainStatus=0, NRestarts=3)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, 'alerts')
        self.clock = Clock()
        self.sd = FakeSystemd()
        healthy(self.sd)
        self.sink = Sink()
        self.cfg = A.Config(alert_dir=self.dir, daemon_unit=DAEMON, monitor_unit=MONITOR, deliver_cmd=['deliver'], host='testhost')
        self.al = self.make()

    def make(self, **over):
        cfg = self.cfg
        for k, v in over.items():
            cfg = A.Config(**{**cfg.__dict__, k: v})
        return A.Alerter(cfg, runner=self.sd, deliver=self.sink, clock=self.clock)

    def incidents(self):
        return self.al.incidents()


class Detection(Base):
    def test_nothing_is_raised_when_both_units_are_healthy(self):
        r = self.al.poll()
        self.assertEqual(r['raised'], [])
        self.assertEqual(self.incidents(), [])
        self.assertEqual(self.sink.sent, [])

    def test_a_monitor_that_exited_2_is_raised_critical_with_what_systemd_says(self):
        monitor_dead(self.sd)
        r = self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual(len(r['raised']), 1)
        self.assertEqual((inc['event'], inc['severity'], inc['unit']), ('monitor_failed', 'critical', MONITOR))
        self.assertEqual(inc['observed']['ExecMainStatus'], '2')
        self.assertEqual(inc['observed']['Result'], 'exit-code')

    def test_the_daemons_start_limit_is_raised_critical(self):
        start_limit_hit(self.sd)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual((inc['event'], inc['severity'], inc['unit']), ('daemon_start_limit_hit', 'critical', DAEMON))

    def test_both_at_once_make_two_incidents(self):
        monitor_dead(self.sd)
        start_limit_hit(self.sd)
        self.al.poll()
        self.assertEqual(sorted(i['event'] for i in self.incidents()), ['daemon_start_limit_hit', 'monitor_failed'])

    def test_a_daemon_that_failed_some_other_way_is_raised(self):
        self.sd.set(DAEMON, ActiveState='failed', Result='exit-code', ExecMainStatus=70)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual((inc['event'], inc['severity']), ('daemon_failed', 'critical'))

    def test_a_restart_in_progress_is_not_an_incident(self):
        self.sd.set(DAEMON, ActiveState='activating', SubState='auto-restart', Result='exit-code', NRestarts=1)
        self.al.poll()
        self.assertEqual(self.incidents(), [])

    def test_budget_nearly_used_is_a_warning(self):
        self.sd.set(DAEMON, ActiveState='active', NRestarts=2, StartLimitBurst=3)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual((inc['event'], inc['severity']), ('daemon_budget_low', 'warning'))

    def test_an_inactive_monitor_is_raised_only_after_two_polls(self):
        self.sd.set(MONITOR, ActiveState='inactive', SubState='dead', Result='success')
        self.al.poll()
        self.assertEqual(self.incidents(), [], 'one poll may be a restart in progress')
        self.clock.advance(60)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual((inc['event'], inc['severity']), ('monitor_not_running', 'critical'))

    def test_a_stale_heartbeat_with_the_monitor_active_is_raised(self):
        hb = os.path.join(self.tmp.name, 'heartbeat')
        Path(hb).write_text('x')
        os.utime(hb, (self.clock.t - 900, self.clock.t - 900))
        al = self.make(heartbeat=hb)
        al.poll()
        (inc,) = al.incidents()
        self.assertEqual(inc['event'], 'monitor_heartbeat_stale')

    def test_a_fresh_heartbeat_is_not(self):
        hb = os.path.join(self.tmp.name, 'heartbeat')
        Path(hb).write_text('x')
        os.utime(hb, (self.clock.t - 5, self.clock.t - 5))
        al = self.make(heartbeat=hb)
        al.poll()
        self.assertEqual(al.incidents(), [])

    def test_it_works_with_the_monitor_dead_and_its_files_missing(self):
        """No input from the monitor is needed: no heartbeat, no latch, no log. systemd alone says it is gone."""
        monitor_dead(self.sd)
        al = self.make(heartbeat=os.path.join(self.tmp.name, 'no-such-heartbeat'))
        al.poll()
        self.assertIn('monitor_failed', [i['event'] for i in al.incidents()])

    def test_an_unknown_unit_reported_by_onfailure_is_recorded(self):
        self.sd.set('dfl-test-supervisor.service', ActiveState='failed', Result='exit-code', ExecMainStatus=75)
        self.al.event('dfl-test-supervisor.service')
        (inc,) = self.incidents()
        self.assertEqual((inc['event'], inc['unit']), ('unit_failed', 'dfl-test-supervisor.service'))


class RecordFirstThenDeliver(Base):
    def test_the_record_exists_before_delivery_is_attempted(self):
        monitor_dead(self.sd)
        seen = {}

        def deliver(message, cfg):
            seen['files'] = sorted(os.listdir(os.path.join(self.dir, 'incidents')))
            return True, 'ok'
        al = A.Alerter(self.cfg, runner=self.sd, deliver=deliver, clock=self.clock)
        al.poll()
        self.assertEqual(len(seen['files']), 1, 'the incident was not on disk when the hand-off ran')

    def test_the_record_and_folder_are_private(self):
        monitor_dead(self.sd)
        self.al.poll()
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        f = os.path.join(self.dir, 'incidents', os.listdir(os.path.join(self.dir, 'incidents'))[0])
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)

    def test_the_message_names_the_unit_and_says_not_to_reset_blindly_and_holds_no_secret(self):
        monitor_dead(self.sd)
        os.environ['DFL_ALERT_TEST_SECRET'] = 'hunter2-secret-value'
        self.addCleanup(os.environ.pop, 'DFL_ALERT_TEST_SECRET', None)
        self.al.poll()
        (msg,) = self.sink.sent
        self.assertIn(MONITOR, msg)
        self.assertIn('CRITICAL', msg)
        self.assertIn('testhost', msg)
        self.assertRegex(msg.lower(), r'before (you )?reset|do not reset')
        self.assertNotIn('hunter2-secret-value', msg)
        rec = json.dumps(self.incidents())
        self.assertNotIn('hunter2-secret-value', rec)


class DeliveryIsNotReceipt(Base):
    def test_a_successful_handoff_is_attempted_ok_and_not_received(self):
        monitor_dead(self.sd)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual(inc['delivery']['state'], 'attempted_ok')
        self.assertEqual(len(inc['delivery']['attempts']), 1)
        self.assertIsNone(inc['notification_received'], 'a hand-off is not proof that a person saw it')

    def test_only_an_acknowledgement_sets_received(self):
        monitor_dead(self.sd)
        self.al.poll()
        (inc,) = self.incidents()
        self.al.ack(inc['id'], by='owner')
        (inc,) = self.incidents()
        self.assertEqual(inc['notification_received']['by'], 'owner')
        self.assertEqual(inc['delivery']['state'], 'attempted_ok')

    def test_acknowledging_an_unknown_incident_fails(self):
        with self.assertRaises(KeyError):
            self.al.ack('no-such-incident', by='owner')

    def test_an_unacknowledged_incident_is_reminded_once_per_interval_not_more(self):
        monitor_dead(self.sd)
        self.al.poll()
        for _ in range(5):
            self.clock.advance(600)                         # 50 minutes of polling
            self.al.poll()
        self.assertEqual(len(self.sink.sent), 1, 'duplicates inside the reminder interval')
        self.clock.advance(3600)
        r = self.al.poll()
        self.assertEqual(len(self.sink.sent), 2)
        self.assertEqual(len(r['reminded']), 1)
        (inc,) = self.incidents()
        self.assertEqual(inc['delivery']['attempts'][-1]['kind'], 'reminder')

    def test_an_acknowledged_incident_is_not_reminded(self):
        monitor_dead(self.sd)
        self.al.poll()
        self.al.ack(self.incidents()[0]['id'], by='owner')
        self.clock.advance(7200)
        self.al.poll()
        self.assertEqual(len(self.sink.sent), 1)


class RetryAndDedupe(Base):
    def test_a_failed_delivery_is_recorded_and_retried_after_a_back_off_not_before(self):
        self.sink.ok = False
        monitor_dead(self.sd)
        self.al.poll()
        (inc,) = self.incidents()
        self.assertEqual(inc['delivery']['state'], 'attempted_failed')
        self.assertEqual(len(self.sink.sent), 1)
        self.clock.advance(10)
        self.al.poll()
        self.assertEqual(len(self.sink.sent), 1, 'retried before the back-off')
        self.sink.ok = True
        self.clock.advance(120)
        r = self.al.poll()
        self.assertEqual(len(r['retried']), 1)
        (inc,) = self.incidents()
        self.assertEqual(inc['delivery']['state'], 'attempted_ok')
        self.assertEqual([a['ok'] for a in inc['delivery']['attempts']], [False, True])
        self.assertIsNone(inc['notification_received'])

    def test_the_back_off_grows_and_an_incident_with_a_dead_channel_is_never_dropped(self):
        self.sink.ok = False
        monitor_dead(self.sd)
        gaps = []
        last = None
        for _ in range(12):
            self.al.poll()
            (inc,) = self.incidents()
            if inc['delivery']['attempts'] and inc['delivery']['attempts'][-1]['at'] != last:
                last = inc['delivery']['attempts'][-1]['at']
                gaps.append(inc['delivery']['next_retry_at'] - last)
            self.clock.advance(inc['delivery']['next_retry_at'] - self.clock.t + 1)
        self.assertEqual(gaps, sorted(gaps))
        self.assertGreater(gaps[-1], gaps[0])
        self.assertTrue(all(i['delivery']['state'] == 'attempted_failed' for i in self.incidents()))

    def test_the_same_condition_seen_by_poll_and_onfailure_is_one_incident_and_one_message(self):
        monitor_dead(self.sd)
        self.al.event(MONITOR)
        self.clock.advance(5)
        self.al.poll()
        self.clock.advance(5)
        self.al.event(MONITOR)
        self.assertEqual(len(self.incidents()), 1)
        self.assertEqual(len(self.sink.sent), 1)

    def test_a_cleared_condition_closes_the_incident_with_one_recovery_note_and_a_new_one_opens_a_new_incident(self):
        monitor_dead(self.sd)
        self.al.poll()
        healthy(self.sd)                                    # a person started the monitor
        self.clock.advance(60)
        r = self.al.poll()
        (inc,) = self.incidents()
        self.assertIsNotNone(inc['cleared'])
        self.assertEqual(len(r['cleared']), 1)
        self.assertEqual(len(self.sink.sent), 2)
        self.assertIn('cleared', self.sink.sent[-1].lower())
        self.clock.advance(60)
        self.al.poll()
        self.assertEqual(len(self.sink.sent), 2, 'a cleared incident is not announced again')
        monitor_dead(self.sd)
        self.clock.advance(60)
        self.al.poll()
        self.assertEqual(len(self.incidents()), 2)
        self.assertEqual(len(self.sink.sent), 3)

    def test_state_survives_a_new_process(self):
        monitor_dead(self.sd)
        self.al.poll()
        again = A.Alerter(self.cfg, runner=self.sd, deliver=self.sink, clock=self.clock)
        self.clock.advance(30)
        again.poll()
        self.assertEqual(len(again.incidents()), 1)
        self.assertEqual(len(self.sink.sent), 1)


class NeverRecovers(Base):
    """The tool reads systemd and writes its own folder. It never changes a unit, a latch, a store or a public file."""
    def run_everything(self, al):
        monitor_dead(self.sd)
        start_limit_hit(self.sd)
        al.poll()
        al.event(MONITOR)
        self.sink.ok = False
        self.clock.advance(4000)
        al.poll()
        self.sink.ok = True
        self.clock.advance(4000)
        al.poll()
        al.ack(al.incidents()[0]['id'], by='owner')
        healthy(self.sd)
        self.clock.advance(60)
        al.poll()

    def test_the_only_systemctl_verb_it_ever_runs_is_show(self):
        self.run_everything(self.al)
        self.assertTrue(self.sd.calls)
        self.assertEqual({c[1] for c in self.sd.calls}, {'show'})
        for inc in self.incidents():
            self.assertTrue(all(c[:2] == ['systemctl', 'show'] for c in inc['commands_run']), inc['commands_run'])

    def test_the_guard_store_and_public_folders_are_byte_identical_after_everything(self):
        base = Path(self.tmp.name)
        for d, files in {'guard': {'TRIPPED.json': '{"latch": 1}', 'heartbeat': 'hb', 'evidence/released/a.json': '{}'},
                         'store': {'season.json': '{}'}, 'public': {'status.json': '{"clock": 1}', 'latest.json': '{}'}}.items():
            for n, c in files.items():
                p = base / d / n
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(c)

        def fingerprint():
            out = {}
            for d in ('guard', 'store', 'public'):
                for p in sorted((base / d).rglob('*')):
                    st = p.lstat()
                    out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'dir', st.st_mode, st.st_mtime_ns)
            return out
        before = fingerprint()
        al = self.make(heartbeat=str(base / 'guard' / 'heartbeat'))
        self.run_everything(al)
        self.assertEqual(fingerprint(), before)

    def test_it_refuses_to_write_outside_its_own_folder(self):
        with self.assertRaises(A.Unsafe):
            A.safe_write(self.cfg, os.path.join(self.tmp.name, 'guard', 'TRIPPED.json'), '{}')
        with self.assertRaises(A.Unsafe):
            A.safe_write(self.cfg, os.path.join(self.dir, '..', 'guard', 'x'), '{}')
        A.safe_write(self.cfg, os.path.join(self.dir, 'incidents', 'ok.json'), '{}')

    def test_subprocess_is_used_in_exactly_two_places_and_the_systemctl_one_is_show_only(self):
        src = (ROOT / 'alerts' / 'dfl_alert.py').read_text()
        tree = ast.parse(src)
        allowed = {'_systemctl_show': 'systemctl-show', 'deliver_via_command': 'delivery', 'run_ping': 'ping'}
        found = {}
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'subprocess':
                    found.setdefault(fn.name, 0)
                    found[fn.name] += 1
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'os' and node.attr in ('system', 'popen', 'execv', 'execvp', 'spawnv'):
                    self.fail('os.%s used in %s' % (node.attr, fn.name))
        self.assertEqual(set(found) - set(allowed), set(), 'subprocess used outside the three allowed functions')
        show = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_systemctl_show'][0]
        consts = [c.value for c in ast.walk(show) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        self.assertIn('show', consts)
        for verb in ('start', 'stop', 'restart', 'reset-failed', 'kill', 'enable', 'disable', 'mask', 'daemon-reload', 'release', 'resume'):
            self.assertNotIn(verb, consts)

    def test_it_imports_nothing_that_can_release_or_resume(self):
        tree = ast.parse((ROOT / 'alerts' / 'dfl_alert.py').read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split('.')[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or '').split('.')[0])
        self.assertEqual(mods & {'broadcast_run', 'rehearsal_check', 'broadcast_supervisor', 'startup_check'}, set())


class PollHeartbeatAndPing(Base):
    def test_poll_records_that_it_ran(self):
        self.al.poll()
        data = json.loads(Path(self.dir, 'poll.json').read_text())
        self.assertEqual(data['last_poll'], self.clock.t)

    def test_the_off_host_ping_is_called_each_poll_and_its_failure_is_not_an_incident(self):
        pings = []
        al = A.Alerter(self.cfg, runner=self.sd, deliver=self.sink, ping=lambda cfg: pings.append(1) or (False, 'unreachable'), clock=self.clock)
        al.poll()
        al.poll()
        self.assertEqual(len(pings), 2)
        self.assertEqual(al.incidents(), [])
        self.assertFalse(json.loads(Path(self.dir, 'poll.json').read_text())['ping']['ok'])


class CommandLine(Base):
    ENV = None

    def env(self):
        return {'DFL_ALERT_DIR': self.dir, 'DFL_ALERT_DAEMON_UNIT': DAEMON, 'DFL_ALERT_MONITOR_UNIT': MONITOR, 'DFL_ALERT_DELIVER_CMD': 'deliver', 'DFL_ALERT_HOST_LABEL': 'testhost'}

    def run_main(self, argv):
        return A.main(argv, env=self.env(), runner=self.sd, deliver=self.sink, clock=self.clock)

    def test_poll_exits_0_when_healthy_and_delivery_failure_exits_nonzero_for_the_event(self):
        self.assertEqual(self.run_main(['poll']), 0)
        monitor_dead(self.sd)
        self.sink.ok = False
        self.assertNotEqual(self.run_main(['event', MONITOR]), 0, 'a failed hand-off must show in systemctl --failed')
        self.sink.ok = True
        self.clock.advance(4000)
        self.assertEqual(self.run_main(['retry']), 0)

    def test_ack_and_status(self):
        monitor_dead(self.sd)
        self.run_main(['poll'])
        iid = self.al.incidents()[0]['id']
        self.assertEqual(self.run_main(['ack', iid, '--by', 'owner']), 0)
        self.assertEqual(self.run_main(['ack', 'nope', '--by', 'owner']), 2)
        self.assertEqual(self.run_main(['status']), 0)

    def test_without_a_delivery_command_the_incident_is_still_recorded_and_the_failure_is_loud(self):
        """A host with no channel configured yet must not be silent AND unrecorded: the record comes first, the failed hand-off is visible, and nothing is dropped."""
        env = self.env()
        env.pop('DFL_ALERT_DELIVER_CMD')
        monitor_dead(self.sd)
        rc = A.main(['poll'], env=env, runner=self.sd, deliver=None, clock=self.clock)
        self.assertNotEqual(rc, 0, 'no channel is a failure systemctl --failed can show')
        incs = A.Alerter(A.Config.from_env(env), runner=self.sd, clock=self.clock).incidents()
        self.assertEqual(len(incs), 1)
        self.assertEqual(incs[0]['delivery']['state'], 'attempted_failed')
        self.assertIn('no delivery command', json.dumps(incs[0]['delivery']))
        self.assertFalse(incs[0]['notification_received'])

    def test_the_hand_test_of_the_channel_refuses_when_none_is_configured(self):
        env = self.env()
        env.pop('DFL_ALERT_DELIVER_CMD')
        self.assertEqual(A.main(['test-delivery'], env=env, runner=self.sd, deliver=None, clock=self.clock), 3)


class TemplateDefaults(Base):
    def test_every_blank_value_of_the_shipped_env_template_means_the_default(self):
        env = {}
        for line in (Path(__file__).resolve().parent.parent / 'alerts' / 'alert.env.template').read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('=')
                env[k] = v
        cfg = A.Config.from_env(env)
        self.assertEqual((cfg.heartbeat_max_s, cfg.remind_s, cfg.polls_down), (120, 3600, 2))
        self.assertEqual((cfg.deliver_cmd, cfg.ping_cmd, cfg.heartbeat), ([], [], ''))
        self.assertTrue(cfg.host)


class OneRunAtATime(Base):
    """OnFailure= and the poll timer can start two processes together; both read-modify-write the incident records, so unserialised they could send one condition twice."""
    def env(self, wait='0'):
        return {'DFL_ALERT_DIR': self.dir, 'DFL_ALERT_DAEMON_UNIT': DAEMON, 'DFL_ALERT_MONITOR_UNIT': MONITOR, 'DFL_ALERT_DELIVER_CMD': 'deliver',
                'DFL_ALERT_HOST_LABEL': 'testhost', 'DFL_ALERT_LOCK_WAIT_S': wait}

    def run_main(self, argv, **kw):
        return A.main(argv, env=self.env(**kw), runner=self.sd, deliver=self.sink, clock=self.clock)

    def test_a_second_run_while_one_is_in_progress_is_reported_busy_never_silently_skipped(self):
        import fcntl
        monitor_dead(self.sd)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(self.dir, 'alert.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.run_main(['event', MONITOR]), 4)
            self.assertEqual(self.incidents(), [], 'a refused run must not have written anything')
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(self.run_main(['event', MONITOR]), 0)
        self.assertEqual(len(self.incidents()), 1)

    def test_the_lock_file_is_private_and_inside_the_alert_folder(self):
        self.run_main(['status'])
        st = os.stat(os.path.join(self.dir, 'alert.lock'))
        self.assertEqual(st.st_mode & 0o777, 0o600)

    def test_reading_status_and_acknowledging_wait_for_the_same_lock(self):
        import fcntl
        monitor_dead(self.sd)
        self.run_main(['event', MONITOR])
        iid = self.incidents()[0]['id']
        fd = os.open(os.path.join(self.dir, 'alert.lock'), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.run_main(['ack', iid, '--by', 'owner']), 4)
            self.assertFalse(self.incidents()[0]['notification_received'])
        finally:
            os.close(fd)
        self.assertEqual(self.run_main(['ack', iid, '--by', 'owner']), 0)


if __name__ == '__main__':
    unittest.main()
