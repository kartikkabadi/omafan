"""Fixture-only broker tests. Never request root or access the host fan devices."""
import contextlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'system/apple-silicon-fan-control'
loader = importlib.machinery.SourceFileLoader('fan_broker', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker = importlib.util.module_from_spec(spec)
loader.exec_module(broker)
PRODUCTION_ENABLE = broker.Hardware.enable
PRODUCTION_SLEEP_FINISHED = broker.sleep_operation_finished


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hw = self.root / 'sys/devices/macsmc/hwmon/hwmon3'
        self.hw.mkdir(parents=True)
        self.runtime = self.root / 'run'
        self.runtime.mkdir()
        self.persistence = self.root / 'persistent'
        self.persistence.mkdir()
        (self.root / 'sys/class/hwmon').mkdir(parents=True)
        (self.root / 'sys/class/hwmon/hwmon3').symlink_to(self.hw)
        self.param = self.root / 'sys/module/macsmc_hwmon/parameters/fan_control'
        self.param.parent.mkdir(parents=True)
        self.param.write_text('N\n')
        (self.hw / 'name').write_text('macsmc_hwmon\n')
        for index, maximum in ((1, 5779), (2, 6241)):
            for suffix, value in (('min', 1200), ('max', maximum), ('target', 2400), ('input', 2400)):
                (self.hw / ('fan%d_%s' % (index, suffix))).write_text(str(value) + '\n')
        (self.hw / 'temp1_input').write_text('45000\n')
        self.now = 100.0
        self.writes = []
        self._patch('SYSFS', self.root / 'sys')
        self._patch('RUNTIME', self.runtime)
        self._patch('PERSISTENCE', self.persistence)
        self._patch('secure_persistence', lambda create=False: None)
        self._patch('clock', lambda: self.now)
        self._patch('sleep_operation_finished', lambda: True)
        self._patch('time', types.SimpleNamespace(sleep=lambda seconds: None))
        self._patch('boot_id', lambda: 'fixture-boot')
        self._patch('secure_runtime', lambda create=False: None)
        self._patch('secure_regular', self.open_fixture)
        self._patch('locked', self.fixture_lock)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(broker.Hardware, 'write', lambda hardware, path, value: self.write_fixture(hardware, path, value)).start()
        mock.patch.object(broker.Hardware, 'enable', lambda hardware: self.param.write_text('1\n')).start()
        self.health()
        state = broker.state_default()
        state["mode"] = "auto"
        broker.save(state)

    def _patch(self, name, value):
        mock.patch.object(broker, name, value).start()

    @contextlib.contextmanager
    def fixture_lock(self):
        yield

    def open_fixture(self, path, writable=False):
        # Explicit fixture-only redirection; production has no test-root argument.
        self.assertTrue(Path(path).resolve().is_relative_to(self.root))
        return os.open(path, (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW)

    def write_fixture(self, hardware, path, value):
        self.assertTrue(path.is_relative_to(self.hw))
        self.writes.append((path.name, value))
        path.write_text(str(value) + '\n')

    def health(self):
        broker.atomic_json(self.runtime / 'watchdog.json', {'boot_id': 'fixture-boot', 'updated_at': self.now})

    def test_manual_clamps_each_hardware_limit(self):
        with mock.patch.dict(os.environ, {'SUDO_UID': '1000'}):
            broker.command('7999')
        self.assertEqual(self.writes, [('fan1_target', 0), ('fan1_target', 5779), ('fan2_target', 0), ('fan2_target', 6241)])
        state = broker.status()
        self.assertEqual((state['mode'], state['owner_uid'], state['expires_in']), ('manual', 1000, 0))

    def test_manual_clamps_below_minimum(self):
        broker.command('1000')
        self.assertEqual(self.writes, [('fan1_target', 0), ('fan1_target', 1200), ('fan2_target', 0), ('fan2_target', 1200)])

    def test_auto_primes_max_then_zero_for_every_fan(self):
        broker.command('auto')
        self.assertEqual(self.writes, [('fan1_target', 5779), ('fan1_target', 0), ('fan2_target', 6241), ('fan2_target', 0)])
        self.assertEqual(broker.status()['mode'], 'auto')

    def test_failure_on_one_reset_still_attempts_other_fans(self):
        original = self.write_fixture
        def failing(hardware, path, value):
            if path.name == 'fan1_target' and value == 5779:
                raise OSError('fixture I/O failure')
            original(hardware, path, value)
        with mock.patch.object(broker.Hardware, 'write', failing):
            with self.assertRaisesRegex(RuntimeError, 'Could not restore'):
                broker.command('auto')
        self.assertIn(('fan1_target', 0), self.writes)
        self.assertIn(('fan2_target', 0), self.writes)
        self.assertEqual(broker.status()['mode'], 'unknown')

    def test_failed_partial_manual_request_recovers_every_fan(self):
        original = self.write_fixture
        def failing(hardware, path, value):
            if path.name == 'fan2_target' and value == 4000:
                raise OSError('fixture I/O failure')
            original(hardware, path, value)
        with mock.patch.object(broker.Hardware, 'write', failing):
            with self.assertRaisesRegex(RuntimeError, 'automatic recovery attempted'):
                broker.command('4000')
        self.assertEqual(self.writes[-1], ('fan2_target', 0))
        self.assertIn(('fan1_target', 0), self.writes)
        self.assertEqual(broker.status()['mode'], 'auto')

    def test_state_journal_failure_prevents_hardware_change(self):
        with mock.patch.object(broker, 'save', side_effect=OSError('state disk full')), mock.patch.object(broker.Hardware, 'manual') as manual:
            with self.assertRaises(OSError):
                broker.command('4000')
            manual.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_interrupted_manual_journal_is_recovered_by_watchdog(self):
        broker.save(broker.state_default('Manual fan change in progress'))
        (self.hw / 'fan1_target').write_text('4000')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(self.writes[-1], ('fan2_target', 0))

    def test_interrupted_auto_is_journaled_unknown_and_retried(self):
        def interrupted(_hardware):
            self.assertEqual(broker.load_state()['mode'], 'unknown')
            raise SystemExit('fixture interruption after prime')
        with mock.patch.object(broker.Hardware, 'automatic', interrupted):
            with self.assertRaises(SystemExit):
                broker.command('auto')
        self.assertEqual(broker.status()['mode'], 'unknown')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')

    def test_auto_journal_failure_does_not_prevent_hardware_release(self):
        with mock.patch.object(broker, 'save', side_effect=OSError('state disk full')), mock.patch.object(broker.Hardware, 'automatic') as automatic, contextlib.redirect_stderr(__import__('io').StringIO()):
            with self.assertRaises(OSError):
                broker.reset()
            automatic.assert_called_once()

    def test_manual_acquisition_refreshes_stale_cached_mode(self):
        cached = {1: True, 2: True}
        manual = {1: False, 2: False}
        def driver_write(hardware, path, value):
            index = int(path.name[3])
            if value == 0:
                if cached[index]:
                    manual[index] = False
                    cached[index] = False
            else:
                if not cached[index]:
                    manual[index] = True
                    cached[index] = True
                if manual[index]:
                    path.write_text(str(value))
        with mock.patch.object(broker.Hardware, 'write', driver_write):
            broker.command('4000')
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(manual, {1: True, 2: True})
        self.assertEqual((self.hw / 'fan1_target').read_text(), '4000')

    def test_initial_manual_target_must_pass_readback(self):
        original = self.write_fixture
        def ignored_write(hardware, path, value):
            if value == 4000:
                return
            original(hardware, path, value)
        with mock.patch.object(broker.Hardware, 'write', ignored_write):
            with self.assertRaisesRegex(RuntimeError, 'did not hold.*requested.*observed'):
                broker.command('4000')
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(self.writes[-1], ('fan2_target', 0))

    def test_already_enabled_parameter_is_not_written_again(self):
        self.param.write_text('Y\n')
        with mock.patch.object(broker, 'secure_regular', wraps=self.open_fixture) as opened:
            PRODUCTION_ENABLE(broker.Hardware())
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.kwargs, {})
        self.assertEqual(self.param.read_text(), 'Y\n')

    def test_invalid_repair_history_becomes_unknown(self):
        broker.command('4000')
        state = broker.load_state()
        for value in ([float('nan')], [1, 2, 3], ['100'], [-1]):
            (self.runtime / 'state.json').write_text(json.dumps({**state, 'repairs': value}))
            self.assertEqual(broker.status()['mode'], 'unknown')

    def test_bad_second_fan_limit_causes_no_manual_writes(self):
        (self.hw / 'fan2_max').write_text('bad\n')
        with self.assertRaises(RuntimeError):
            broker.Hardware().manual(4000)
        self.assertEqual(self.writes, [])

    def test_missing_target_refuses_manual(self):
        (self.hw / 'fan2_target').unlink()
        with self.assertRaisesRegex(RuntimeError, 'no controllable target'):
            broker.Hardware().manual(4000)
        self.assertEqual(self.writes, [])

    def test_hot_sensor_refuses_manual(self):
        (self.hw / 'temp1_input').write_text('80000\n')
        with self.assertRaisesRegex(RuntimeError, '80 C'):
            broker.Hardware().manual(4000)
        self.assertEqual(self.writes, [])

    def test_missing_and_invalid_sensor_refuse_manual(self):
        for raw in ('not a number', '-41000', '99999999'):
            (self.hw / 'temp1_input').write_text(raw)
            with self.assertRaises(RuntimeError):
                broker.Hardware().manual(4000)
        (self.hw / 'temp1_input').unlink()
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            broker.Hardware().manual(4000)

    def test_stale_watchdog_refuses_manual(self):
        self.now += 7
        with self.assertRaisesRegex(RuntimeError, 'watchdog is not running'):
            broker.command('4000')
        self.assertFalse(broker.status()['ready'])
        self.assertEqual(self.writes, [])

    def test_legacy_heartbeat_validates_owner_without_changing_lifetime(self):
        with mock.patch.dict(os.environ, {'SUDO_UID': '1000'}):
            broker.command('4000')
        with mock.patch.dict(os.environ, {'SUDO_UID': '1001'}):
            with self.assertRaisesRegex(RuntimeError, 'belongs to this user'):
                broker.command('heartbeat', broker.load_state()['generation'])
            with self.assertRaisesRegex(RuntimeError, 'Another user'):
                broker.command('4500')
        original = broker.load_state()
        self.now += 86400
        self.health()
        with mock.patch.dict(os.environ, {'SUDO_UID': '1000'}):
            broker.command('heartbeat', original['generation'])
        self.assertEqual(broker.load_state(), original)
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['saved_rpm'], 4000)

    def test_boot_mismatch_cannot_extend_lease(self):
        broker.command('4000')
        with mock.patch.object(broker, 'boot_id', return_value='different-boot'):
            self.assertEqual(broker.load_state()['mode'], 'unknown')
            self.assertFalse(broker.watchdog_ready())

    def test_watchdog_health_requires_a_finite_numeric_timestamp(self):
        for value in (True, False, '100', None, -1, self.now + 1, self.now - broker.WATCHDOG_MAX_AGE - 1):
            with self.subTest(value=value):
                broker.atomic_json(self.runtime / 'watchdog.json', {'boot_id': 'fixture-boot', 'updated_at': value})
                self.assertFalse(broker.watchdog_ready())
        self.health()
        self.assertTrue(broker.watchdog_ready())

    def test_runtime_state_requires_integer_schema_version(self):
        state = broker.load_state()
        state['schema_version'] = True
        broker.save(state)
        self.assertEqual(broker.load_state()['mode'], 'unknown')

    def test_unsupported_manual_inventory_keeps_auto_recovery_available(self):
        for suffix, value in (('min', 1200), ('max', 6000), ('target', 2400), ('input', 2400)):
            (self.hw / ('fan65_' + suffix)).write_text(str(value))
        hardware = broker.Hardware()
        with self.assertRaisesRegex(RuntimeError, 'inventory.*supported'):
            hardware.planned_targets(rpm=4000)
        self.assertEqual(self.writes, [])
        hardware.automatic()
        self.assertIn(('fan65_target', 0), self.writes)

    def test_excess_manual_fan_count_is_rejected_before_register_writes(self):
        for index in range(3, 34):
            for suffix, value in (('min', 1200), ('max', 6000), ('target', 2400), ('input', 2400)):
                (self.hw / f'fan{index}_{suffix}').write_text(str(value))
        with self.assertRaisesRegex(RuntimeError, 'inventory.*supported'):
            broker.Hardware().planned_targets(rpm=4000)
        self.assertEqual(self.writes, [])

    def test_firmware_override_detection(self):
        broker.command('4000')
        hardware = broker.Hardware()
        targets = broker.load_state()['targets']
        self.assertTrue(hardware.matches(targets))
        (self.hw / 'fan1_target').write_text('5779\n')
        self.assertFalse(hardware.matches(targets))

    def test_register_symlink_escape_refused(self):
        path = self.hw / 'fan1_target'
        path.unlink()
        path.symlink_to(self.param)
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            broker.Hardware()

    def test_missing_hardware(self):
        (self.hw / 'name').write_text('unrelated\n')
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            broker.Hardware()

    def test_ambiguous_hardware(self):
        second = self.root / 'sys/devices/other'
        second.mkdir()
        (second / 'name').write_text('macsmc_hwmon\n')
        (self.root / 'sys/class/hwmon/hwmon4').symlink_to(second)
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            broker.Hardware()

    def test_malformed_json_state_becomes_unknown(self):
        (self.runtime / 'state.json').write_text('bad JSON')
        self.assertEqual(broker.status()['mode'], 'unknown')

    def test_watchdog_keeps_manual_without_ui_or_heartbeat(self):
        broker.command('4000')
        original_writes = list(self.writes)
        self.now += 86400
        broker.watchdog_tick()
        state = broker.status()
        self.assertEqual(state['mode'], 'manual')
        self.assertEqual(state['saved_rpm'], 4000)
        self.assertEqual(state['expires_in'], 0)
        self.assertEqual(self.writes, original_writes)

    def test_watchdog_releases_hot_or_overridden_fans(self):
        broker.command('4000')
        (self.hw / 'temp1_input').write_text('85000')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        (self.hw / 'temp1_input').write_text('45000')
        broker.command('4000')
        (self.hw / 'fan1_target').write_text('5779')
        broker.watchdog_tick()
        self.assertIn('Recovered manual target drift', broker.status()['notice'])
        self.assertIsNone(broker.status()['error'])
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_unknown_recovery_state_refuses_numeric_request(self):
        broker.save(broker.state_default('Could not restore automatic cooling'))
        with mock.patch.object(broker.Hardware, 'manual') as manual:
            with self.assertRaisesRegex(RuntimeError, 'has not been established'):
                broker.command('4000')
            manual.assert_not_called()

    def test_sleep_inhibits_reacquisition_until_successful_resume(self):
        broker.command('4000')
        generation = broker.load_state()['generation']
        broker.command('sleep')
        self.assertFalse(broker.status()['ready'])
        for action in ('4000', 'heartbeat'):
            with self.assertRaisesRegex(RuntimeError, 'inhibited'):
                broker.command(action, generation)
        broker.watchdog_tick()
        self.assertTrue(broker.load_state()['inhibited'])
        broker.command('resume')
        self.assertTrue(broker.status()['ready'])
        self.assertEqual(broker.status()['mode'], 'auto')

    def test_failed_resume_retries_saved_manual_when_hardware_recovers(self):
        broker.command('4000')
        broker.command('sleep')
        with mock.patch.object(broker.Hardware, 'automatic', side_effect=OSError('I/O failure')):
            with self.assertRaises(RuntimeError):
                broker.command('resume')
        self.assertFalse(broker.load_state()['inhibited'])
        self.assertFalse(broker.status()['ready'])
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['saved_rpm'], 4000)

    def test_stale_generation_cannot_renew_new_manual_request(self):
        broker.command('4000')
        old_generation = broker.load_state()['generation']
        broker.command('4500')
        self.now += 3
        self.health()
        with self.assertRaisesRegex(RuntimeError, 'No active manual setting'):
            broker.command('heartbeat', old_generation)
        self.assertEqual(broker.load_state()['expires_at'], 0)

    def test_corrupt_state_fields_become_unknown(self):
        broker.command('4000')
        state = broker.load_state()
        for changes in ({'expires_at': float('nan')}, {'targets': []}, {'owner_uid': '1000'},
                        {'targets': [{'index': 1, 'target': '4000'}]}, {'generation': '../bad'},
                        {'inhibited': 'false'}, {'recovering': 'false'}):
            with self.subTest(changes=changes):
                (self.runtime / 'state.json').write_text(json.dumps({**state, **changes}))
                self.assertEqual(broker.status()['mode'], 'unknown')

    def test_enable_failure_still_attempts_every_automatic_write(self):
        with mock.patch.object(broker.Hardware, 'enable', side_effect=OSError('enable failure')):
            with self.assertRaises(RuntimeError):
                broker.command('auto')
        self.assertEqual(self.writes, [('fan1_target', 5779), ('fan1_target', 0), ('fan2_target', 6241), ('fan2_target', 0)])

    def test_saved_manual_restores_after_release_and_new_boot_without_ui(self):
        with mock.patch.dict(os.environ, {'SUDO_UID': '1000'}):
            broker.command('4000')
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        broker.command('release')
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['saved_mode'], 'manual')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['generation'], preference['generation'])
        with mock.patch.object(broker, 'boot_id', return_value='next-boot'):
            broker.reset()
            broker.watchdog_tick()
            state = broker.status()
            self.assertEqual((state['mode'], state['saved_rpm'], state['owner_uid']), ('manual', 4000, 1000))
        self.assertEqual(broker.load_preference()[0], preference)

    def test_user_auto_clears_saved_manual_before_physical_transition(self):
        broker.command('4000')
        original = broker.Hardware.automatic
        def checked(hardware):
            self.assertEqual(broker.load_preference()[0]['mode'], 'auto')
            original(hardware)
        with mock.patch.object(broker.Hardware, 'automatic', checked):
            broker.command('auto')
        self.now += 86400
        broker.watchdog_tick()
        self.assertEqual(broker.status()['saved_mode'], 'auto')
        self.assertEqual(broker.status()['mode'], 'auto')

    def test_hot_pause_retains_intent_and_resumes_only_below_hysteresis(self):
        broker.command('4000')
        saved = (self.persistence / 'preference.json').read_bytes()
        (self.hw / 'temp1_input').write_text('85000')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['saved_mode'], 'manual')
        self.assertTrue(broker.status()['thermal_paused'])
        (self.hw / 'temp1_input').write_text('76000')
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        (self.hw / 'temp1_input').write_text('74000')
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), saved)

    def test_corrupt_preference_is_not_automatically_overwritten(self):
        broker.command('4000')
        evidence = b'{"mode":"manual", broken'
        (self.persistence / 'preference.json').write_bytes(evidence)
        broker.watchdog_tick()
        writes = list(self.writes)
        broker.watchdog_tick()
        self.assertEqual(self.writes, writes)
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertIn('invalid', broker.status()['preference_error'])
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        broker.command('auto')
        self.assertIsNone(broker.status()['preference_error'])

    def test_failed_numeric_save_retains_last_acknowledged_preference(self):
        broker.command('4000')
        previous = broker.load_preference()[0]
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'prior saved intent'):
                broker.command('4500')
        self.assertEqual(broker.load_preference()[0], previous)
        self.assertEqual(broker.status()['mode'], 'auto')
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['requested_rpm'], 4000)

    def test_preference_atomic_write_syncs_file_and_parent_directory(self):
        original = broker.os.fsync
        synced = []
        def record(fd):
            synced.append(__import__('stat').S_ISDIR(os.fstat(fd).st_mode))
            original(fd)
        with mock.patch.object(broker.os, 'fsync', side_effect=record):
            broker.save_preference(broker.preference_default())
        self.assertIn(False, synced)
        self.assertIn(True, synced)
        self.assertEqual((self.persistence / 'preference.json').stat().st_mode & 0o777, 0o644)

    def test_failed_auto_save_blocks_old_manual_until_successful_user_change(self):
        broker.command('4000')
        old = broker.load_preference()[0]
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'Auto could not be saved'):
                broker.command('auto')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['restore_blocked_generation'], old['generation'])
        broker.reset()
        self.now += 3600
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        broker.command('4500')
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertIsNone(broker.status()['restore_blocked_generation'])

    def test_failed_auto_persistence_journals_restore_block_before_release(self):
        broker.command('4000')
        generation = broker.load_preference()[0]['generation']
        def interrupted(_hardware):
            self.assertEqual(broker.load_state()['restore_blocked_generation'], generation)
            raise SystemExit('interrupted physical Auto')
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')), mock.patch.object(broker.Hardware, 'automatic', interrupted):
            with self.assertRaises(SystemExit):
                broker.command('auto')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['saved_mode'], 'manual')

    def test_invalid_saved_profiles_are_rejected_strictly(self):
        manual = {'schema_version': 1, 'mode': 'manual', 'rpm': 4000,
                  'owner_uid': 1000, 'generation': 'a' * 32}
        for changes in ({'rpm': True}, {'rpm': '4000'}, {'rpm': 0}, {'owner_uid': -1},
                        {'owner_uid': True}, {'schema_version': True}, {'mode': 'unknown'},
                        {'extra': 'field'}, {'generation': '../profile'}):
            with self.subTest(changes=changes):
                (self.persistence / 'preference.json').write_text(json.dumps({**manual, **changes}))
                self.assertIsNotNone(broker.load_preference()[1])


    def test_independent_edit_preserves_other_fan_and_does_not_write_it(self):
        broker.command('4000')
        self.writes.clear()
        broker.command('fan', fan_index=1, fan_rpm=3500)
        self.assertEqual(self.writes, [('fan1_target', 0), ('fan1_target', 3500)])
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4000')
        state = broker.status()
        self.assertTrue(state['per_fan_control'])
        self.assertEqual(state['control_mode'], 'individual')
        self.assertIsNone(state['saved_rpm'])
        self.assertEqual(state['saved_fans'], [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4000}])
        self.writes.clear()
        broker.command('fan', fan_index=2, fan_rpm=5000)
        self.assertEqual(self.writes, [('fan2_target', 0), ('fan2_target', 5000)])
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3500')

    def test_shared_max_seed_uses_each_fans_clamped_limit(self):
        broker.command('6241')
        self.writes.clear()
        broker.command('fan', fan_index=2, fan_rpm=4000)
        self.assertEqual(self.writes, [('fan2_target', 0), ('fan2_target', 4000)])
        self.assertEqual(broker.status()['saved_fans'], [{'index': 1, 'rpm': 5779}, {'index': 2, 'rpm': 4000}])

    def test_first_individual_edit_from_auto_freezes_unchanged_fan_setpoint(self):
        (self.hw / 'fan1_target').write_text('2317')
        (self.hw / 'fan2_target').write_text('2502')
        broker.command('fan', fan_index=1, fan_rpm=3500)
        self.assertEqual(broker.status()['saved_fans'], [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 2502}])
        self.assertEqual(self.writes, [('fan1_target', 0), ('fan1_target', 3500), ('fan2_target', 0), ('fan2_target', 2502)])

    def test_individual_update_preserves_other_raw_intent_even_when_clamped(self):
        broker.command('fan', fan_index=2, fan_rpm=7999)
        self.writes.clear()
        broker.command('fan', fan_index=1, fan_rpm=3500)
        self.assertEqual(broker.status()['saved_fans'][1], {'index': 2, 'rpm': 7999})
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '6241')
        self.assertEqual(self.writes, [('fan1_target', 0), ('fan1_target', 3500)])

    def test_schema_one_is_read_only_migrated_until_explicit_setting_change(self):
        legacy = {'schema_version': 1, 'mode': 'manual', 'rpm': 4000,
                  'owner_uid': 0, 'generation': 'a' * 32}
        evidence = json.dumps(legacy).encode()
        path = self.persistence / 'preference.json'
        path.write_bytes(evidence)
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        self.assertEqual(preference['schema_version'], 4)
        self.assertEqual(preference['control_mode'], 'shared')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(path.read_bytes(), evidence)
        broker.command('fan', fan_index=1, fan_rpm=3500)
        stored = json.loads(path.read_text())
        self.assertEqual(stored['schema_version'], 4)
        self.assertEqual(stored['control_mode'], 'individual')

    def test_cold_boot_restores_unequal_targets_and_keeps_saved_map(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        evidence = (self.persistence / 'preference.json').read_bytes()
        generation = broker.status()['generation']
        with mock.patch.object(broker, 'boot_id', return_value='cold-reboot'):
            broker.reset()
            broker.watchdog_tick()
            state = broker.status()
            self.assertEqual(state['mode'], 'manual')
            self.assertEqual(state['generation'], generation)
            self.assertEqual(state['saved_fans'], [{'index': 1, 'rpm': 3200}, {'index': 2, 'rpm': 4700}])
            self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3200')
            self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)

    def test_missing_or_new_fan_pauses_individual_map_without_remapping(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        evidence = (self.persistence / 'preference.json').read_bytes()
        for suffix in ('input', 'target', 'min', 'max'):
            (self.hw / ('fan2_' + suffix)).unlink()
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertIn('inventory', broker.status()['error'])
        with self.assertRaisesRegex(RuntimeError, 'inventory'):
            broker.command('fan', fan_index=1, fan_rpm=3500)
        for index, maximum in ((2, 6241), (3, 5000)):
            for suffix, value in (('input', 2400), ('target', 2400), ('min', 1200), ('max', maximum)):
                (self.hw / ('fan%d_%s' % (index, suffix))).write_text(str(value))
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertIn('inventory', broker.status()['error'])
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)

    def test_selected_fan_can_replace_unrepresentable_old_auto_setpoint(self):
        (self.hw / 'fan1_min').write_text('100')
        (self.hw / 'fan1_max').write_text('900')
        (self.hw / 'fan1_target').write_text('600')
        broker.command('fan', fan_index=1, fan_rpm=1000)
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '900')
        self.assertEqual(broker.status()['saved_fans'][0], {'index': 1, 'rpm': 1000})

    def test_unchanged_unrepresentable_auto_setpoint_is_not_silently_changed(self):
        (self.hw / 'fan2_min').write_text('100')
        (self.hw / 'fan2_max').write_text('900')
        (self.hw / 'fan2_target').write_text('600')
        with self.assertRaisesRegex(RuntimeError, 'cannot be represented safely'):
            broker.command('fan', fan_index=1, fan_rpm=3500)
        self.assertEqual(self.writes, [])
        self.assertFalse((self.persistence / 'preference.json').exists())

    def test_active_limits_change_reclamps_without_rewriting_desired_map(self):
        broker.command('fan', fan_index=1, fan_rpm=5000)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        evidence = (self.persistence / 'preference.json').read_bytes()
        generation = broker.status()['generation']
        (self.hw / 'fan1_max').write_text('4000')
        broker.watchdog_tick()
        state = broker.status()
        self.assertEqual(state['mode'], 'manual')
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '4000')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')
        self.assertEqual(state['generation'], generation)
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)

    def test_nonexistent_fan_edit_is_rejected_before_hardware_or_preference_write(self):
        broker.command('4000')
        previous = (self.persistence / 'preference.json').read_bytes()
        self.writes.clear()
        with self.assertRaisesRegex(RuntimeError, 'does not exist'):
            broker.command('fan', fan_index=64, fan_rpm=3500)
        self.assertEqual(self.writes, [])
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), previous)

    def test_schema_two_individual_map_validation_is_strict(self):
        preference = {'schema_version': 2, 'mode': 'manual', 'control_mode': 'individual',
                      'rpm': None, 'fans': [{'index': 1, 'rpm': 3500}], 'owner_uid': 0,
                      'generation': 'b' * 32}
        invalid = ([], [{'index': 1, 'rpm': 3500}, {'index': 1, 'rpm': 4000}],
                   [{'index': 0, 'rpm': 3500}], [{'index': 65, 'rpm': 3500}],
                   [{'index': True, 'rpm': 3500}], [{'index': 1, 'rpm': True}],
                   [{'index': 1, 'rpm': 3500.5}], [{'index': 1, 'rpm': 999}],
                   [{'index': 1, 'rpm': 8000}], [{'index': 1, 'rpm': 3500, 'path': '/tmp'}])
        for fans in invalid:
            with self.subTest(fans=fans), self.assertRaises(RuntimeError):
                broker.validate_preference({**preference, 'fans': fans})
        for changes in ({'rpm': 3500}, {'control_mode': 'shared'}, {'mode': 'auto'}, {'owner_uid': True}):
            with self.assertRaises(RuntimeError):
                broker.validate_preference({**preference, **changes})

    def test_legacy_shared_command_replaces_individual_map_explicitly(self):
        broker.command('fan', fan_index=1, fan_rpm=3500)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        broker.command('4500')
        state = broker.status()
        self.assertEqual(state['control_mode'], 'shared')
        self.assertEqual(state['saved_rpm'], 4500)
        self.assertEqual(state['saved_fans'], [])
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '4500')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4500')


    def test_separate_profile_survives_shared_changes_and_restores_atomically(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        memory = [{'index': 1, 'rpm': 3200}, {'index': 2, 'rpm': 4700}]
        broker.command('6241')
        broker.command('4500')
        self.assertEqual(broker.status()['control_mode'], 'shared')
        self.assertEqual(broker.status()['saved_separate_fans'], memory)
        self.assertEqual(broker.status()['saved_fans'], [])
        broker.command('separate')
        state = broker.status()
        self.assertTrue(state['separate_memory'])
        self.assertEqual(state['saved_fans'], memory)
        self.assertEqual(state['saved_separate_fans'], memory)
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3200')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')

    def test_auto_preserves_inactive_profile_but_does_not_activate_it(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        memory = broker.status()['saved_fans']
        broker.command('auto')
        self.now += 86400
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['saved_fans'], [])
        self.assertEqual(broker.status()['saved_separate_fans'], memory)
        broker.command('separate')
        self.assertEqual(broker.status()['saved_fans'], memory)

    def test_separate_memory_survives_shared_sleep_reboot_and_service_release(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        broker.command('4000')
        evidence = (self.persistence / 'preference.json').read_bytes()
        broker.command('sleep')
        broker.command('resume')
        broker.watchdog_tick()
        broker.command('release')
        broker.watchdog_tick()
        with mock.patch.object(broker, 'boot_id', return_value='profile-reboot'):
            broker.reset()
            broker.watchdog_tick()
            self.assertEqual(broker.status()['control_mode'], 'shared')
            self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 3200}, {'index': 2, 'rpm': 4700}])
            self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
            broker.command('separate')
            self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3200')
            self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')

    def test_first_separate_selection_seeds_actual_clamped_shared_targets(self):
        broker.command('6241')
        self.writes.clear()
        broker.command('separate')
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 5779}, {'index': 2, 'rpm': 6241}])
        self.assertEqual(self.writes, [])

    def test_schema_two_individual_read_migrates_memory_without_file_write(self):
        legacy = {'schema_version': 2, 'mode': 'manual', 'control_mode': 'individual',
                  'rpm': None, 'fans': [{'index': 2, 'rpm': 4700}, {'index': 1, 'rpm': 3200}],
                  'owner_uid': 0, 'generation': 'b' * 32}
        path = self.persistence / 'preference.json'
        evidence = json.dumps(legacy).encode()
        path.write_bytes(evidence)
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        self.assertEqual(preference['schema_version'], 4)
        self.assertEqual(preference['fans'], preference['separate_fans'])
        broker.watchdog_tick()
        self.assertEqual(path.read_bytes(), evidence)
        broker.command('4000')
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 3200}, {'index': 2, 'rpm': 4700}])

    def test_inactive_inventory_mismatch_keeps_shared_healthy_and_restore_fails_without_writes(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        broker.command('4000')
        for suffix in ('min', 'max', 'target', 'input'):
            (self.hw / ('fan2_' + suffix)).rename(self.hw / ('fan3_' + suffix))
        broker.watchdog_tick()
        self.assertEqual(broker.status()['control_mode'], 'shared')
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertIsNone(broker.status()['error'])
        evidence = (self.persistence / 'preference.json').read_bytes()
        self.writes.clear()
        with self.assertRaisesRegex(RuntimeError, 'Remembered separate profile.*inventory'):
            broker.command('separate')
        self.assertEqual(self.writes, [])
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '4000')
        self.assertEqual((self.hw / 'fan3_target').read_text().strip(), '4000')

    def test_failed_separate_save_keeps_shared_selection_and_remembered_profile(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        broker.command('4000')
        evidence = (self.persistence / 'preference.json').read_bytes()
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):
                broker.command('separate')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['control_mode'], 'shared')
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 3200}, {'index': 2, 'rpm': 4700}])

    def test_failed_shared_save_does_not_erase_separate_selection_or_memory(self):
        broker.command('fan', fan_index=1, fan_rpm=3200)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        evidence = (self.persistence / 'preference.json').read_bytes()
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):
                broker.command('4500')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['control_mode'], 'individual')
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3200')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')

    def test_schema_three_memory_is_required_strict_and_equal_when_active(self):
        shared = {'schema_version': 3, 'mode': 'manual', 'control_mode': 'shared',
                  'rpm': 4000, 'fans': [], 'owner_uid': 0, 'generation': 'c' * 32}
        with self.assertRaises(RuntimeError):
            broker.validate_preference(shared)
        for memory in ('bad', [{'index': 1, 'rpm': 3200}, {'index': 1, 'rpm': 4700}],
                       [{'index': 65, 'rpm': 3200}], [{'index': 1, 'rpm': '3200'}]):
            with self.subTest(memory=memory), self.assertRaises(RuntimeError):
                broker.validate_preference({**shared, 'separate_fans': memory})
        valid = {**shared, 'separate_fans': [{'index': 64, 'rpm': 3200}]}
        self.assertEqual(broker.validate_preference(valid)['separate_fans'], valid['separate_fans'])
        with self.assertRaisesRegex(RuntimeError, 'must match'):
            broker.validate_preference({**shared, 'control_mode': 'individual', 'rpm': None,
                                       'fans': [{'index': 1, 'rpm': 3200}],
                                       'separate_fans': [{'index': 1, 'rpm': 4700}]})

    def test_auto_from_corrupt_preference_saves_empty_trusted_memory(self):
        (self.persistence / 'preference.json').write_text('{"schema_version":3,"separate_fans":"bad"}')
        broker.command('auto')
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        self.assertEqual(preference['schema_version'], 4)
        self.assertEqual(preference['mode'], 'auto')
        self.assertEqual(preference['separate_fans'], [])


    def test_linked_restores_lower_remembered_rpm_instead_of_raising_to_separate(self):
        broker.command('2000')
        broker.command('separate')
        broker.command('fan', fan_index=1, fan_rpm=3500)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        memory = [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}]
        self.assertEqual(broker.status()['saved_linked_rpm'], 2000)
        for _ in range(3):
            broker.command('linked')
            state = broker.status()
            self.assertTrue(state['linked_memory'])
            self.assertEqual(state['saved_rpm'], 2000)
            self.assertEqual(state['saved_linked_rpm'], 2000)
            self.assertEqual(state['saved_separate_fans'], memory)
            self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '2000')
            self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '2000')
            broker.command('separate')
            self.assertEqual(broker.status()['saved_fans'], memory)
            self.assertEqual(broker.status()['saved_linked_rpm'], 2000)

    def test_first_linked_without_history_uses_highest_current_clamped_setpoint(self):
        (self.hw / 'fan1_target').write_text('2317')
        (self.hw / 'fan2_target').write_text('2502')
        broker.command('linked')
        self.assertEqual(broker.status()['saved_linked_rpm'], 2502)
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '2502')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '2502')

    def test_first_linked_seed_uses_planned_clamped_targets_not_unbounded_raw_requests(self):
        broker.command('fan', fan_index=1, fan_rpm=7999)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        self.assertIsNone(broker.status()['saved_linked_rpm'])
        broker.command('linked')
        self.assertEqual(broker.status()['saved_linked_rpm'], 5779)
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '5779')
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 7999}, {'index': 2, 'rpm': 4700}])

    def test_both_banks_survive_auto_cold_boot_sleep_and_service_release(self):
        broker.command('2000')
        broker.command('separate')
        broker.command('fan', fan_index=1, fan_rpm=3500)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        broker.command('auto')
        evidence = (self.persistence / 'preference.json').read_bytes()
        broker.command('sleep')
        broker.command('resume')
        broker.command('release')
        with mock.patch.object(broker, 'boot_id', return_value='two-bank-reboot'):
            broker.reset()
            broker.watchdog_tick()
            self.assertEqual(broker.status()['mode'], 'auto')
            self.assertEqual(broker.status()['saved_linked_rpm'], 2000)
            self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}])
            self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
            broker.command('linked')
            self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '2000')
            broker.command('separate')
            self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3500')
            self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')

    def test_failed_linked_mode_save_keeps_both_banks_and_previous_separate_selection(self):
        broker.command('2000')
        broker.command('separate')
        broker.command('fan', fan_index=1, fan_rpm=3500)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        evidence = (self.persistence / 'preference.json').read_bytes()
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):
                broker.command('linked')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        self.now += 31
        broker.watchdog_tick()
        self.assertEqual(broker.status()['control_mode'], 'individual')
        self.assertEqual(broker.status()['saved_linked_rpm'], 2000)
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3500')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '4700')

    def test_failed_new_linked_rpm_save_does_not_replace_remembered_scalar(self):
        broker.command('2000')
        broker.command('separate')
        broker.command('fan', fan_index=2, fan_rpm=4700)
        with mock.patch.object(broker, 'save_preference', side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):
                broker.command('4500')
        self.assertEqual(broker.status()['saved_linked_rpm'], 2000)
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 2000}, {'index': 2, 'rpm': 4700}])

    def test_schema_three_shared_migrates_linked_bank_read_only(self):
        legacy = {'schema_version': 3, 'mode': 'manual', 'control_mode': 'shared',
                  'rpm': 3000, 'fans': [], 'separate_fans': [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}],
                  'owner_uid': 0, 'generation': 'b' * 32}
        path = self.persistence / 'preference.json'
        evidence = json.dumps(legacy).encode()
        path.write_bytes(evidence)
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        self.assertEqual(preference['schema_version'], 4)
        self.assertEqual(preference['linked_rpm'], 3000)
        broker.watchdog_tick()
        self.assertEqual(path.read_bytes(), evidence)
        broker.command('separate')
        self.assertEqual(broker.status()['saved_linked_rpm'], 3000)
        broker.command('linked')
        self.assertEqual((self.hw / 'fan1_target').read_text().strip(), '3000')
        self.assertEqual((self.hw / 'fan2_target').read_text().strip(), '3000')

    def test_schema_three_individual_does_not_invent_unknown_linked_history(self):
        legacy = {'schema_version': 3, 'mode': 'manual', 'control_mode': 'individual',
                  'rpm': None, 'fans': [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}],
                  'separate_fans': [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}],
                  'owner_uid': 0, 'generation': 'c' * 32}
        path = self.persistence / 'preference.json'
        evidence = json.dumps(legacy).encode()
        path.write_bytes(evidence)
        preference, error = broker.load_preference()
        self.assertIsNone(error)
        self.assertIsNone(preference['linked_rpm'])
        self.assertEqual(path.read_bytes(), evidence)

    def test_schema_four_requires_valid_linked_memory_and_shared_equality(self):
        shared = {'schema_version': 4, 'mode': 'manual', 'control_mode': 'shared',
                  'rpm': 3000, 'fans': [], 'separate_fans': [], 'owner_uid': 0, 'generation': 'd' * 32}
        with self.assertRaises(RuntimeError):
            broker.validate_preference(shared)
        for value in (None, True, '3000', 3000.5, 999, 8000, 4000):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                broker.validate_preference({**shared, 'linked_rpm': value})
        automatic = {**shared, 'mode': 'auto', 'control_mode': 'auto', 'rpm': None, 'owner_uid': None}
        self.assertEqual(broker.validate_preference({**automatic, 'linked_rpm': 3000})['linked_rpm'], 3000)

    def test_first_linked_can_recover_current_inventory_without_discarding_old_separate_bank(self):
        broker.command('fan', fan_index=1, fan_rpm=3500)
        broker.command('fan', fan_index=2, fan_rpm=4700)
        for suffix in ('min', 'max', 'input', 'target'):
            (self.hw / ('fan2_' + suffix)).rename(self.hw / ('fan3_' + suffix))
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'auto')
        broker.command('linked')
        self.assertEqual(broker.status()['control_mode'], 'shared')
        self.assertEqual(broker.status()['saved_separate_fans'], [{'index': 1, 'rpm': 3500}, {'index': 2, 'rpm': 4700}])
        with self.assertRaisesRegex(RuntimeError, 'inventory'):
            broker.command('separate')
        self.assertEqual(broker.status()['control_mode'], 'shared')


    def test_post_wake_signal_restores_manual_after_failed_pre_sleep_start(self):
        broker.command('4000')
        evidence = (self.persistence / 'preference.json').read_bytes()
        with mock.patch.object(broker.Hardware, 'automatic', side_effect=OSError('transient pre-sleep failure')):
            with self.assertRaises(RuntimeError):
                broker.command('sleep')
        self.assertTrue(broker.status()['inhibited'])
        # The failed oneshot never runs ExecStop=resume. The independent post
        # hook still delivers a notification after the actual wake.
        self.now += 1
        broker.command('wake-signal')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)

    def test_post_wake_signal_recovers_a_killed_or_timed_out_pre_sleep_process(self):
        broker.command('4000')
        with mock.patch.object(broker.Hardware, 'automatic', side_effect=SystemExit('simulated unit timeout/SIGKILL')):
            with self.assertRaises(SystemExit):
                broker.command('sleep')
        self.assertEqual(broker.status()['mode'], 'unknown')
        self.assertTrue(broker.status()['inhibited'])
        self.now += 1
        broker.command('wake-signal')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertFalse(broker.status()['inhibited'])

    def test_wake_event_is_idempotent_through_manual_restore_and_resume_fallback(self):
        broker.command('4000')
        broker.command('sleep')
        self.now += 1
        broker.command('wake-signal')
        broker.watchdog_tick()
        generation = broker.status()['wake_generation']
        self.writes.clear()
        broker.command('resume')  # Successful-start unit fallback after the hook.
        broker.watchdog_tick()
        self.assertEqual(self.writes, [])
        broker.command('fan', fan_index=1, fan_rpm=3500)
        self.assertEqual(broker.status()['wake_generation'], generation)
        self.writes.clear()
        broker.watchdog_tick()
        self.assertEqual(self.writes, [])

    def test_pending_post_wake_event_survives_supervisor_restart(self):
        broker.command('4000')
        broker.command('sleep')
        self.now += 1
        broker.command('wake-signal')
        broker.reset()  # New supervisor's initialization while inhibit is set.
        self.assertTrue(broker.status()['inhibited'])
        broker.watchdog_tick()
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_older_post_event_cannot_clear_a_newer_sleep_inhibit(self):
        broker.command('4000')
        self.now += 1
        broker.command('wake-signal')
        self.now += 1
        broker.command('sleep')
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        # An older post can finish its atomic write after the newer pre began.
        broker.atomic_json(self.runtime / 'wake.json', {'schema_version': 1, 'boot_id': 'fixture-boot',
                           'generation': 'b' * 32, 'created_at': self.now - 1})
        before = list(self.writes)
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertEqual(self.writes, before)
        self.now += 1
        broker.command('wake-signal')
        broker.watchdog_tick()
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_future_wake_event_is_retired_even_after_time_catches_up(self):
        broker.command('4000')
        broker.command('sleep')
        broker.atomic_json(self.runtime / 'wake.json', {'schema_version': 1, 'boot_id': 'fixture-boot',
                           'generation': 'c' * 32, 'created_at': self.now + 10})
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertEqual(broker.status()['wake_generation'], 'c' * 32)
        self.assertIn('future', broker.status()['error'])
        self.now += 31
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertFalse(broker.status()['ready'])
        self.now += 1
        broker.command('wake-signal')
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_malformed_wake_event_stays_fail_closed_without_erasing_intent(self):
        broker.command('4000')
        evidence = (self.persistence / 'preference.json').read_bytes()
        broker.command('sleep')
        (self.runtime / 'wake.json').write_text('{"generation":false}')
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertIn('Wake notification invalid', broker.status()['error'])
        with self.assertRaisesRegex(RuntimeError, 'Wake notification invalid'):
            broker.command('4000')
        self.assertEqual((self.persistence / 'preference.json').read_bytes(), evidence)
        broker.command('resume')  # Explicit root recovery repairs invalid metadata.
        broker.watchdog_tick()
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_old_boot_wake_event_is_ignored(self):
        broker.command('4000')
        broker.command('sleep')
        broker.atomic_json(self.runtime / 'wake.json', {'schema_version': 1, 'boot_id': 'previous-boot',
                           'generation': 'd' * 32, 'created_at': self.now + 1000})
        before = list(self.writes)
        broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertIsNone(broker.status()['wake_generation'])
        self.assertEqual(self.writes, before)

    def test_interrupted_wake_consumption_journals_unknown_and_recovers_once(self):
        broker.command('4000')
        broker.command('sleep')
        self.now += 1
        broker.command('wake-signal')
        with mock.patch.object(broker, 'reset', side_effect=SystemExit('interrupted after wake journal')):
            with self.assertRaises(SystemExit):
                broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'unknown')
        self.assertFalse(broker.status()['inhibited'])
        broker.watchdog_tick()
        self.assertEqual(broker.status()['mode'], 'manual')
        self.writes.clear()
        broker.watchdog_tick()
        self.assertEqual(self.writes, [])

    def test_intermediate_compound_sleep_post_waits_for_outer_target_end(self):
        broker.command('4000')
        broker.command('sleep')
        self.now += 1
        broker.command('wake-signal')  # RTC wake before a secondary hibernate.
        marker_generation = broker.load_wake()[0]['generation']
        with mock.patch.object(broker, 'sleep_operation_finished', return_value=False):
            broker.watchdog_tick()
            self.assertTrue(broker.status()['inhibited'])
            self.assertEqual(broker.status()['mode'], 'auto')
            self.assertNotEqual(broker.status()['wake_generation'], marker_generation)
            before = list(self.writes)
            self.now += 10
            broker.watchdog_tick()
            self.assertEqual(self.writes, before)
            self.assertTrue(broker.status()['inhibited'])
        broker.watchdog_tick()  # The full outer sleep operation has now ended.
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['wake_generation'], marker_generation)

    def test_outer_sleep_gate_inhibits_even_if_pre_timeout_preceded_journaling(self):
        broker.command('4000')
        # The pre command timed out waiting for an operation lock and never
        # wrote an inhibit; the independent post notification still must wait.
        self.now += 1
        broker.command('wake-signal')
        with mock.patch.object(broker, 'sleep_operation_finished', return_value=False):
            broker.watchdog_tick()
        self.assertTrue(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'auto')
        self.assertEqual(broker.status()['sleep_started_at'], 0)
        broker.watchdog_tick()
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')

    def test_failed_pre_signal_waits_for_queued_outer_sleep_then_recovers_aborted_attempt(self):
        broker.command('4000')
        with mock.patch.object(broker.Hardware, 'automatic', side_effect=OSError('pre-release failed')):
            with self.assertRaises(RuntimeError):
                broker.command('sleep')
        self.now += 1
        with mock.patch.dict(os.environ, {'SERVICE_RESULT': 'exit-code'}):
            broker.command('sleep-failed')  # ExecStopPost before queued target starts.
        generation = broker.load_wake()[0]['generation']
        # Use the actual manager-query function with a fake response; the
        # fixture's default gate otherwise assumes a completed outer operation.
        with mock.patch.object(broker, 'sleep_operation_finished', PRODUCTION_SLEEP_FINISHED), mock.patch.object(broker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='ActiveState=inactive\nJob=123 start\n')):
            broker.watchdog_tick()
            self.assertTrue(broker.status()['inhibited'])
            self.assertNotEqual(broker.status()['wake_generation'], generation)
        # Unsupported sleep aborts before all system-sleep hooks. No post marker
        # follows; the failure notification alone must resume after the job ends.
        broker.watchdog_tick()
        self.assertFalse(broker.status()['inhibited'])
        self.assertEqual(broker.status()['mode'], 'manual')
        self.assertEqual(broker.status()['wake_generation'], generation)




class Security(unittest.TestCase):
    def test_invalid_arguments_never_touch_hardware(self):
        for action in ('999', '9999', 'auto;id', '4000 extra', '--sysfs=/tmp', 'heartbeat', 'AUTO', ''):
            with self.subTest(action=action), mock.patch.object(broker.sys, 'argv', ['broker', action]), mock.patch.object(broker, 'Hardware') as hardware:
                with self.assertRaises(RuntimeError):
                    broker.main()
                hardware.assert_not_called()

    def test_per_fan_cli_rejects_invalid_indices_rpms_and_argument_counts(self):
        invalid = (['fan'], ['fan', '1'], ['fan', '1', '4000', 'extra'],
                   ['fan', '0', '4000'], ['fan', '65', '4000'], ['fan', '01', '4000'],
                   ['fan', '-1', '4000'], ['fan', '1', '999'], ['fan', '1', '8000'],
                   ['fan', '1', '4000;id'], ['fan', '1;id', '4000'], ['fan', '1', '04000'])
        for arguments in invalid:
            with self.subTest(arguments=arguments), mock.patch.object(broker.sys, 'argv', ['broker', *arguments]), mock.patch.object(broker, 'Hardware') as hardware:
                with self.assertRaises(RuntimeError):
                    broker.main()
                hardware.assert_not_called()

    def test_per_fan_cli_accepts_bounded_exact_protocol(self):
        for index in ('1', '9', '10', '59', '60', '64'):
            with self.subTest(index=index), mock.patch.object(broker.sys, 'argv', ['broker', 'fan', index, '4000']), mock.patch.object(broker.os, 'geteuid', return_value=0), mock.patch.object(broker, 'command') as command, mock.patch.object(broker, 'status', return_value={}), contextlib.redirect_stdout(__import__('io').StringIO()):
                broker.main()
                command.assert_called_once_with('fan', None, int(index), 4000)

    def test_separate_cli_requires_exact_single_token(self):
        with mock.patch.object(broker.sys, 'argv', ['broker', 'separate']), mock.patch.object(broker.os, 'geteuid', return_value=0), mock.patch.object(broker, 'command') as command, mock.patch.object(broker, 'status', return_value={}), contextlib.redirect_stdout(__import__('io').StringIO()):
            broker.main()
            command.assert_called_once_with('separate', None, None, None)
        for arguments in (['separate', 'extra'], ['separate;id'], ['Separate']):
            with self.subTest(arguments=arguments), mock.patch.object(broker.sys, 'argv', ['broker', *arguments]), mock.patch.object(broker, 'Hardware') as hardware:
                with self.assertRaises(RuntimeError):
                    broker.main()
                hardware.assert_not_called()

    def test_linked_cli_requires_exact_single_token(self):
        with mock.patch.object(broker.sys, 'argv', ['broker', 'linked']), mock.patch.object(broker.os, 'geteuid', return_value=0), mock.patch.object(broker, 'command') as command, mock.patch.object(broker, 'status', return_value={}), contextlib.redirect_stdout(__import__('io').StringIO()):
            broker.main()
            command.assert_called_once_with('linked', None, None, None)
        for arguments in (['linked', '2000'], ['linked;id'], ['Linked']):
            with self.subTest(arguments=arguments), mock.patch.object(broker.sys, 'argv', ['broker', *arguments]), mock.patch.object(broker, 'Hardware') as hardware:
                with self.assertRaises(RuntimeError):
                    broker.main()
                hardware.assert_not_called()

    def test_wake_signal_is_root_only_and_does_not_read_status_or_hardware(self):
        with mock.patch.object(broker.sys, 'argv', ['broker', 'wake-signal']), mock.patch.object(broker.os, 'geteuid', return_value=1000), mock.patch.object(broker, 'signal_wake') as wake:
            with self.assertRaisesRegex(RuntimeError, 'require'):
                broker.main()
            wake.assert_not_called()
        with mock.patch.object(broker.sys, 'argv', ['broker', 'wake-signal']), mock.patch.object(broker.os, 'geteuid', return_value=0), mock.patch.object(broker, 'signal_wake') as wake, mock.patch.object(broker, 'locked', side_effect=AssertionError('wake signal must not lock')), mock.patch.object(broker, 'status', side_effect=AssertionError('post hook must not read status')), contextlib.redirect_stdout(__import__('io').StringIO()):
            broker.main()
            wake.assert_called_once()

    def test_post_hook_dispatches_only_fixed_wake_signal(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            record = directory / 'record'
            fake = directory / 'fake-broker'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "' + str(record) + '"\n')
            fake.chmod(0o755)
            hook = directory / 'hook'
            source = (SOURCE.parent / 'apple-silicon-fan-wake').read_text()
            hook.write_text(source.replace('/usr/local/libexec/apple-silicon-fan-control', str(fake)))
            hook.chmod(0o755)
            self.assertEqual(subprocess.run([str(hook), 'pre', 'suspend'], check=False).returncode, 0)
            self.assertFalse(record.exists())
            self.assertEqual(subprocess.run([str(hook), 'post', 'suspend'], check=False).returncode, 0)
            self.assertEqual(record.read_text().splitlines(), ['wake-signal'])
            self.assertEqual(subprocess.run([str(hook), 'post'], check=False).returncode, 64)

    def test_sleep_target_query_is_bounded_fixed_and_fail_closed(self):
        cases = (('ActiveState=inactive\nJob=\n', True), ('Job=\nActiveState=failed\n', True),
                 ('ActiveState=active\nJob=\n', False), ('ActiveState=activating\nJob=123\n', False),
                 ('ActiveState=deactivating\nJob=123\n', False), ('ActiveState=inactive\nJob=123 start\n', False),
                 ('ActiveState=inactive\n', False), ('ActiveState=unknown\nJob=\n', False),
                 ('ActiveState=inactive\nJob=\nJob=123\n', False))
        for value, expected in cases:
            with self.subTest(value=value), mock.patch.object(broker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=value)) as run:
                self.assertEqual(broker.sleep_operation_finished(), expected)
                self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password', 'show', 'sleep.target', '--all', '--property=ActiveState', '--property=Job'])
                self.assertEqual(run.call_args.kwargs['timeout'], 1)
        for failure in (OSError('manager unavailable'), broker.subprocess.TimeoutExpired('systemctl', 1)):
            with self.subTest(failure=failure), mock.patch.object(broker.subprocess, 'run', side_effect=failure):
                self.assertFalse(broker.sleep_operation_finished())
        with mock.patch.object(broker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='ActiveState=inactive\nJob=')):
            self.assertFalse(broker.sleep_operation_finished())

    def test_failed_sleep_producer_is_root_only_nonlocking_and_suppressed_on_success(self):
        for service_result in ('exit-code', 'timeout', 'signal', 'resources'):
            with self.subTest(result=service_result), mock.patch.dict(os.environ, {'SERVICE_RESULT': service_result}), mock.patch.object(broker, 'signal_wake') as wake, mock.patch.object(broker, 'locked', side_effect=AssertionError('failure notification must not lock')):
                self.assertTrue(broker.command('sleep-failed'))
                wake.assert_called_once()
        for environment in ({'SERVICE_RESULT': 'success'}, {}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(broker, 'signal_wake') as wake:
                self.assertFalse(broker.command('sleep-failed'))
                wake.assert_not_called()
        with mock.patch.object(broker.sys, 'argv', ['broker', 'sleep-failed']), mock.patch.object(broker.os, 'geteuid', return_value=1000), mock.patch.object(broker, 'signal_wake') as wake:
            with self.assertRaisesRegex(RuntimeError, 'require'):
                broker.main()
            wake.assert_not_called()

    def test_extra_argument_rejected(self):
        with mock.patch.object(broker.sys, 'argv', ['broker', 'auto', 'extra']):
            with self.assertRaises(RuntimeError):
                broker.main()

    def test_nonroot_cannot_change_fans(self):
        with mock.patch.object(broker.sys, 'argv', ['broker', 'auto']), mock.patch.object(broker.os, 'geteuid', return_value=1000), mock.patch.object(broker, 'Hardware') as hardware:
            with self.assertRaisesRegex(RuntimeError, 'require'):
                broker.main()
            hardware.assert_not_called()

    def test_unprivileged_status_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(broker, 'RUNTIME', Path(root) / 'missing'):
            self.assertFalse(broker.status()['ready'])
            self.assertFalse((Path(root) / 'missing').exists())

    def test_symlink_state_refused(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'target'
            target.write_text('{}')
            link = Path(root) / 'link'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                broker.secure_regular(link)

    def test_fifo_state_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as root:
            fifo = Path(root) / 'fifo'
            os.mkfifo(fifo)
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                broker.secure_regular(fifo)
            self.assertLess(time.monotonic() - started, 1)

    def test_fifo_lock_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(broker, 'RUNTIME', Path(root)), mock.patch.object(broker, 'secure_runtime'):
            os.mkfifo(Path(root) / 'lock')
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                with broker.locked():
                    self.fail('An invalid lock was accepted')
            self.assertLess(time.monotonic() - started, 1)

    def test_runtime_symlink_refused(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'real'
            directory.mkdir()
            link = Path(root) / 'link'
            link.symlink_to(directory, target_is_directory=True)
            with mock.patch.object(broker, 'RUNTIME', link):
                with self.assertRaisesRegex(RuntimeError, 'runtime'):
                    broker.secure_runtime()

    def test_low_level_write_uses_root_owned_canonical_register(self):
        # Run the production write method on a regular fixture attribute. Only
        # ownership metadata is stubbed; open/fchmod/fdopen/close all run for real.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fan1_target'
            path.write_text('')
            path.chmod(0o444)
            real_fstat = os.fstat
            def root_metadata(fd):
                actual = real_fstat(fd)
                return types.SimpleNamespace(st_uid=0, st_mode=actual.st_mode)
            with mock.patch.object(broker.os, 'fstat', side_effect=root_metadata):
                hardware = broker.Hardware.__new__(broker.Hardware)
                hardware.write(path, 4000)
            self.assertEqual(path.read_text(), '4000\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_saved_preference_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            evidence = directory / 'evidence'
            evidence.write_text('do not modify')
            (directory / 'preference.json').symlink_to(evidence)
            with mock.patch.object(broker, 'PERSISTENCE', directory), mock.patch.object(broker, 'secure_persistence'):
                preference, error = broker.load_preference()
                self.assertEqual(preference['mode'], 'auto')
                self.assertIsNotNone(error)
                with self.assertRaises(OSError):
                    broker.save_preference(broker.preference_default())
            self.assertEqual(evidence.read_text(), 'do not modify')

    def test_saved_preference_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'actual'
            directory.mkdir()
            link = Path(root) / 'link'
            link.symlink_to(directory, target_is_directory=True)
            with mock.patch.object(broker, 'PERSISTENCE', link):
                self.assertIsNotNone(broker.load_preference()[1])
                with self.assertRaisesRegex(RuntimeError, 'directory'):
                    broker.save_preference(broker.preference_default())

    def test_duplicate_json_fields_and_excessive_nesting_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state'
            def fixture_open(path, writable=False):
                return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with mock.patch.object(broker, 'secure_regular', fixture_open):
                for payload in ('{"mode":"auto","mode":"manual"}', '{"a":' + '[' * 2000 + '0' + ']' * 2000 + '}'):
                    path.write_text(payload)
                    with self.assertRaisesRegex(RuntimeError, 'Invalid broker JSON'):
                        broker.read_json(path)

    @unittest.skipIf(os.getuid() == 0, 'Requires an unprivileged test runner')
    def test_user_owned_state_refused(self):
        with tempfile.NamedTemporaryFile() as stream:
            with self.assertRaisesRegex(RuntimeError, 'Insecure'):
                broker.secure_regular(Path(stream.name))


if __name__ == '__main__':
    unittest.main()
