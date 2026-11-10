import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('thermal', ROOT / 'thermal.py')
thermal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thermal)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sys = self.root / 'sys'
        self.proc = self.root / 'proc'
        self.hw = self.sys / 'class/hwmon/hwmon7'
        self.hw.mkdir(parents=True)
        self.write(self.hw / 'name', 'macsmc_hwmon')
        self.write(self.proc / 'device-tree/model', 'Apple MacBook Pro\x00')
        self.reader = thermal.Telemetry(self.sys, self.proc)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text))

    def fan(self, index=1, minimum=1200, maximum=5779, target=2300, rpm=2298):
        for key, value in {'min': minimum, 'max': maximum, 'target': target, 'input': rpm, 'label': f'Fan {index}'}.items():
            self.write(self.hw / f'fan{index}_{key}', value)

    def sensor(self, index, label, value):
        self.write(self.hw / f'temp{index}_label', label)
        self.write(self.hw / f'temp{index}_input', value)

    def control(self, mode='auto'):
        return {'mode': mode, 'ready': True, 'permitted': True, 'owner_uid': os.getuid()}

    def test_two_fans_have_independent_limits(self):
        self.fan()
        self.fan(2, maximum=6241, rpm=2500, target=2502)
        result = self.reader.get(self.control())
        self.assertEqual(result['fan_count'], 2)
        self.assertEqual(result['fan_max'], 6241)
        self.assertEqual(result['fans'][0]['max'], 5779)
        self.assertTrue(result['fan_control_enabled'])

    def test_positive_firmware_target_is_not_manual_mode(self):
        self.fan()
        self.assertEqual(self.reader.get()['mode'], 'unknown')
        self.assertFalse(self.reader.get()['manual_mode'])
        self.assertEqual(self.reader.get(self.control())['mode'], 'auto')
        self.assertTrue(self.reader.get(self.control('manual'))['manual_mode'])

    def test_stale_watchdog_cannot_claim_manual(self):
        self.fan()
        result = self.reader.get({**self.control('manual'), 'ready': False})
        self.assertEqual(result['mode'], 'unknown')
        self.assertFalse(result['fan_control_enabled'])

    def test_recovering_lease_preserves_owner_but_disables_numeric_control(self):
        self.fan()
        result = self.reader.get({**self.control('unknown'), 'ready': False, 'recovering': True})
        self.assertTrue(result['lease_owned'])
        self.assertTrue(result['lease_recovering'])
        self.assertFalse(result['fan_control_enabled'])
        self.assertEqual(result['mode'], 'unknown')
        result = self.reader.get({**self.control('unknown'), 'ready': False, 'recovering': True, 'owner_uid': os.getuid()+1})
        self.assertFalse(result['lease_owned'])

    def test_no_authorization_disables_controls(self):
        self.fan()
        result = self.reader.get({**self.control(), 'permitted': False})
        self.assertFalse(result['control_available'])

    def test_durable_preference_is_distinct_from_actual_fallback(self):
        self.fan()
        control = {**self.control(), 'persistent': True, 'saved_mode': 'manual',
                   'saved_rpm': 4000, 'saved_owner_uid': os.getuid(),
                   'requested_rpm': 4000, 'error': 'Temperature guard paused manual control'}
        result = self.reader.get(control)
        self.assertEqual(result['mode'], 'auto')
        self.assertEqual(result['saved_mode'], 'manual')
        self.assertEqual(result['saved_rpm'], 4000)
        self.assertTrue(result['persistent'])
        self.assertIn('Temperature guard', result['paused_reason'])

    def test_invalid_persistent_fields_are_not_coerced(self):
        self.fan()
        result = self.reader.get({**self.control('manual'), 'persistent': 'true',
                                  'saved_mode': 'garbage', 'saved_rpm': '4000',
                                  'saved_owner_uid': True, 'requested_rpm': float('nan')})
        self.assertFalse(result['persistent'])
        self.assertEqual(result['saved_mode'], 'unknown')
        self.assertIsNone(result['saved_rpm'])
        self.assertIsNone(result['requested_rpm'])
        self.assertIsNone(result['saved_owner_uid'])

    def test_legacy_broker_does_not_claim_durable_preference(self):
        self.fan()
        result = self.reader.get(self.control('manual'))
        self.assertFalse(result['persistent'])
        self.assertEqual(result['saved_mode'], 'unknown')

    def test_individual_saved_values_remain_distinct(self):
        self.fan()
        self.fan(2, maximum=6241)
        result = self.reader.get({**self.control('manual'), 'persistent': True,
                                  'per_fan_control': True, 'saved_mode': 'manual',
                                  'saved_control_mode': 'individual', 'control_mode': 'individual',
                                  'saved_fans': [{'index': 2, 'rpm': 4500}, {'index': 1, 'rpm': 4000}]})
        self.assertTrue(result['per_fan_control'])
        self.assertEqual(result['saved_fans'], [{'index': 1, 'rpm': 4000}, {'index': 2, 'rpm': 4500}])
        self.assertEqual(result['saved_control_mode'], 'individual')

    def test_invalid_saved_map_disables_individual_capability(self):
        self.fan()
        for value in ([{'index': 1, 'rpm': 4000}, {'index': 1, 'rpm': 4500}],
                      [{'index': 1, 'rpm': 4000.5}], [{'index': True, 'rpm': 4000}],
                      [{'index': 65, 'rpm': 4000}], [{'index': 1, 'rpm': '4000'}]):
            with self.subTest(value=value):
                result = self.reader.get({**self.control('manual'), 'per_fan_control': True, 'saved_fans': value})
                self.assertFalse(result['per_fan_control'])
                self.assertTrue(result['preference_error'])

    def test_missing_or_invalid_fan_limits_disable_numeric_control(self):
        self.fan()
        for value in ['missing', 'NaN', '-1', '1000000000000000000', '10000']:
            with self.subTest(value=value):
                self.write(self.hw / 'fan1_max', value)
                result = self.reader.get(self.control())
                self.assertFalse(result['fan_control_enabled'])
                self.assertTrue(result['control_available'])

    def test_missing_fan_reading_is_not_zero(self):
        self.fan(rpm='broken')
        result = self.reader.get()
        self.assertIsNone(result['fan_rpm'])
        self.assertTrue(result['warnings'])

    def test_sparse_sensors_and_subzero_and_zero_temperatures(self):
        self.sensor(2, 'Battery Hotspot', 0)
        self.sensor(8, 'NAND Flash Temperature', -12500)
        result = self.reader.get()
        self.assertEqual(result['sensors']['battery'], 0)
        self.assertEqual(result['sensors']['nand'], -12.5)
        self.assertEqual(result['max_temp'], 0)
        self.assertIsNone(result['sensors']['wifi'])
        self.assertFalse(result['has_fan'])
        self.assertIsNone(result['error'])

    def test_duplicate_sensor_classes_keep_hottest(self):
        self.sensor(1, 'Battery Hotspot', 45000)
        self.sensor(2, 'Battery Sensor', 35000)
        self.assertEqual(self.reader.get()['sensors']['battery'], 45)

    def test_system_power_found_by_label(self):
        self.write(self.hw / 'power1_label', 'AC Input Power')
        self.write(self.hw / 'power1_input', 50000000)
        self.write(self.hw / 'power4_label', 'Total System Power')
        self.write(self.hw / 'power4_input', 12345000)
        self.assertEqual(self.reader.get()['power_watts'], 12.35)

    def test_unknown_power_is_not_assumed_system_power(self):
        self.write(self.hw / 'power1_input', 50000000)
        self.assertIsNone(self.reader.get()['power_watts'])

    def test_model_and_label_json_roundtrip(self):
        model = 'Apple "Mac" \\ test\n<img src="https://example.invalid">'
        self.write(self.proc / 'device-tree/model', model)
        self.fan()
        self.write(self.hw / 'fan1_label', 'Fan "A"\\\n<b>bold</b>')
        result = self.reader.get()
        self.assertEqual(json.loads(json.dumps(result))['device_model'], model)
        self.assertIn('<b>', result['fans'][0]['label'])

    def test_missing_driver_is_error_not_fanless(self):
        self.write(self.hw / 'name', 'nvme')
        result = self.reader.get()
        self.assertEqual(result['error'], 'macsmc_hwmon_missing')
        self.assertIsNone(result['max_temp'])

    def test_non_apple_is_explicit(self):
        self.write(self.hw / 'name', 'nvme')
        self.write(self.proc / 'device-tree/model', 'Generic ARM VM')
        result = self.reader.get()
        self.assertFalse(result['is_apple_silicon'])
        self.assertEqual(result['error'], 'unsupported_hardware')

    def test_compatible_detects_apple_without_model(self):
        (self.proc / 'device-tree/model').unlink()
        self.write(self.hw / 'name', 'nvme')
        self.write(self.proc / 'device-tree/compatible', 'apple,j314\x00apple,t6000\x00')
        self.assertTrue(self.reader.get()['is_apple_silicon'])

    def test_empty_hwmon_is_error(self):
        self.assertEqual(self.reader.get()['error'], 'sensor_read_failed')

    def test_hwmon_renumbering(self):
        self.fan()
        renamed = self.hw.with_name('hwmon29')
        self.hw.rename(renamed)
        self.assertEqual(self.reader.get()['fan_count'], 1)

    def test_excessive_fan_count_is_explicit_error(self):
        for index in range(1, 34):
            self.fan(index)
        self.assertEqual(self.reader.get()['error'], 'unsupported_fan_count')

    def test_oversized_and_injected_numbers_rejected(self):
        for value in ['1; system("touch pwned")', 'NaN', 'inf', '1e6', '9' * 5000]:
            with self.subTest(value=value[:20]):
                self.sensor(1, 'Battery Hotspot', value)
                self.assertIsNone(self.reader.get()['sensors']['battery'])

    def test_mutating_cli_rejects_extra_args_and_shell_payloads(self):
        for argv in [['set', 'auto', 'extra'], ['set', '9999'], ['set', '1200;id'],
                     ['heartbeat', 'extra'], ['--sys-root', '/tmp'],
                     ['set-fan', '0', '4000'], ['set-fan', '65', '4000'],
                     ['set-fan', '01', '4000'], ['set-fan', '../fan1', '4000'],
                     ['set-fan', '1', '4000;id'], ['set-fan', '1', '4000', 'extra']]:
            with self.subTest(argv=argv), patch('builtins.print'):
                self.assertEqual(thermal.main(argv), 64)

    def test_per_fan_cli_passes_separate_bounded_arguments(self):
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value={'ok': True, 'data': {'mode': 'manual'}}) as run, \
                patch('builtins.print'):
            self.assertEqual(thermal.main(['set-fan', '2', '4500']), 0)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', str(thermal.BROKER), 'fan', '2', '4500'])

    def test_separate_profile_cli_uses_one_exact_broker_token(self):
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value={'ok': True, 'data': {'mode': 'manual'}}) as run, \
                patch('builtins.print'):
            self.assertEqual(thermal.main(['set-separate']), 0)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', str(thermal.BROKER), 'separate'])
        for argv in (['set-separate', 'extra'], ['set-separate', '4000'], ['set-separate;id'], ['set', 'separate']):
            with self.subTest(argv=argv), patch('builtins.print'):
                self.assertEqual(thermal.main(argv), 64)

    def test_linked_profile_cli_uses_one_exact_broker_token(self):
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value={'ok': True, 'data': {'mode': 'manual'}}) as run, \
                patch('builtins.print'):
            self.assertEqual(thermal.main(['set-linked']), 0)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', str(thermal.BROKER), 'linked'])
        for argv in (['set-linked', 'extra'], ['set-linked', '3000'], ['set-linked;id'], ['set', 'linked']):
            with self.subTest(argv=argv), patch('builtins.print'):
                self.assertEqual(thermal.main(argv), 64)

    def test_linked_memory_is_distinct_from_active_individual_targets(self):
        self.fan()
        self.fan(2, maximum=6241)
        result = self.reader.get({**self.control('manual'), 'persistent': True, 'per_fan_control': True,
                                  'linked_memory': True, 'saved_linked_rpm': 3000, 'saved_mode': 'manual',
                                  'saved_control_mode': 'individual',
                                  'saved_fans': [{'index': 1, 'rpm': 2300}, {'index': 2, 'rpm': 2502}]})
        self.assertTrue(result['linked_memory'])
        self.assertEqual(result['saved_linked_rpm'], 3000)
        self.assertTrue(result['fan_control_enabled'])

    def test_invalid_claimed_linked_memory_fails_numeric_closed(self):
        self.fan()
        for value in ('missing', 3000.5, True, '3000', 999, 8000, [], {}):
            with self.subTest(value=value):
                control = {**self.control('auto'), 'per_fan_control': True, 'linked_memory': True}
                if value != 'missing':
                    control['saved_linked_rpm'] = value
                result = self.reader.get(control)
                self.assertFalse(result['per_fan_control'])
                self.assertFalse(result['fan_control_enabled'])
                self.assertTrue(result['control_available'])
                self.assertTrue(result['preference_error'])
        self.assertIsNone(self.reader.get({**self.control('auto'), 'linked_memory': True, 'saved_linked_rpm': None})['preference_error'])

    def test_active_shared_target_must_match_claimed_linked_bank(self):
        self.fan()
        result = self.reader.get({**self.control('manual'), 'linked_memory': True, 'saved_linked_rpm': 3000,
                                  'saved_mode': 'manual', 'saved_control_mode': 'shared', 'saved_rpm': 4000})
        self.assertFalse(result['fan_control_enabled'])
        self.assertTrue(result['preference_error'])

    def test_separate_memory_is_bounded_and_independent_of_current_shared_target(self):
        self.fan()
        self.fan(2, maximum=6241)
        memory = [{'index': 2, 'rpm': 2502}, {'index': 1, 'rpm': 2300}]
        result = self.reader.get({**self.control('manual'), 'persistent': True, 'per_fan_control': True,
                                  'separate_memory': True, 'saved_mode': 'manual', 'saved_control_mode': 'shared',
                                  'saved_rpm': 4000, 'saved_fans': [], 'saved_separate_fans': memory})
        self.assertTrue(result['separate_memory'])
        self.assertEqual(result['saved_rpm'], 4000)
        self.assertEqual(result['saved_separate_fans'], list(reversed(memory)))
        self.assertTrue(result['fan_control_enabled'])

    def test_invalid_claimed_separate_memory_fails_numeric_closed(self):
        self.fan()
        for memory in (None, {}, [{'index': 1, 'rpm': 4000.5}], [{'index': 1, 'rpm': 4000}] * 2,
                       [{'index': 65, 'rpm': 4000}], [{'index': 1, 'rpm': '4000'}]):
            with self.subTest(memory=memory):
                result = self.reader.get({**self.control('manual'), 'per_fan_control': True,
                                          'separate_memory': True, 'saved_separate_fans': memory})
                self.assertFalse(result['per_fan_control'])
                self.assertFalse(result['fan_control_enabled'])
                self.assertTrue(result['control_available'])
                self.assertTrue(result['preference_error'])

    def test_legacy_separate_capability_cannot_be_inferred_from_an_optional_field(self):
        self.fan()
        result = self.reader.get({**self.control('manual'), 'saved_separate_fans': [{'index': 1, 'rpm': 4000}]})
        self.assertFalse(result['separate_memory'])
        self.assertEqual(result['saved_separate_fans'], [])

    def test_inactive_inventory_mismatch_does_not_break_shared_control(self):
        self.fan()
        result = self.reader.get({**self.control('manual'), 'per_fan_control': True,
                                  'separate_memory': True, 'saved_mode': 'manual', 'saved_control_mode': 'shared',
                                  'saved_rpm': 4000, 'saved_fans': [],
                                  'saved_separate_fans': [{'index': 2, 'rpm': 4500}]})
        self.assertTrue(result['fan_control_enabled'])
        self.assertIsNone(result['preference_error'])

    def test_control_command_errors_and_invalid_json(self):
        for program in ['print("not json")', 'print("[]")', 'raise SystemExit(2)', 'print("x"*20000)']:
            with self.subTest(program=program):
                self.assertFalse(thermal.command_result(['/usr/bin/python3', '-c', program], 1)['ok'])

    def test_control_command_timeout(self):
        result = thermal.command_result(['/usr/bin/python3', '-c', 'import time;time.sleep(2)'], .05)
        self.assertFalse(result['ok'])
        self.assertIn('timed out', result['error'])

    def test_user_writable_broker_rejected(self):
        path = self.root / 'broker'
        self.write(path, '#!/bin/false')
        path.chmod(0o755)
        self.assertFalse(thermal.trusted_broker(path))

    def test_permission_probe_does_not_require_obsolete_heartbeat_grant(self):
        commands = []
        def check(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0 if command[-1] == 'auto' else 1)
        reply = {'ok': True, 'data': {'schema_version': 1, 'mode': 'auto', 'ready': True, 'persistent': True}}
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value=reply), \
                patch.object(thermal.subprocess, 'run', side_effect=check):
            result = thermal.broker_status()
        self.assertTrue(result['permitted'])
        self.assertEqual(commands[0][-1], 'auto')
        self.assertIn('-l', commands[0])

    def test_boolean_or_string_protocol_version_cannot_authorize_control(self):
        for version in (True, False, '1', None, 1.0):
            reply = {'ok': True, 'data': {'schema_version': version, 'mode': 'auto', 'ready': True}}
            with self.subTest(version=version), patch.object(thermal, 'trusted_broker', return_value=True), \
                    patch.object(thermal, 'command_result', return_value=reply), \
                    patch.object(thermal.subprocess, 'run') as probe:
                result = thermal.broker_status()
                self.assertFalse(result['ready'])
                self.assertFalse(result['permitted'])
                probe.assert_not_called()

    def test_partial_policy_upgrade_preserves_auto_but_disables_per_fan(self):
        def check(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0 if command[-1] == 'auto' else 1)
        reply = {'ok': True, 'data': {'schema_version': 1, 'mode': 'auto', 'ready': True,
                                     'persistent': True, 'per_fan_control': True}}
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value=reply), \
                patch.object(thermal.subprocess, 'run', side_effect=check):
            result = thermal.broker_status()
        self.assertTrue(result['permitted'])
        self.assertFalse(result['per_fan_control'])
        self.assertIn('Per-fan permission', result['error'])

    def test_per_fan_permission_probe_uses_separate_arguments(self):
        commands = []
        def check(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        reply = {'ok': True, 'data': {'schema_version': 1, 'mode': 'manual', 'ready': True,
                                     'per_fan_control': True, 'targets': [{'index': 2, 'target': 4000}]}}
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value=reply), \
                patch.object(thermal.subprocess, 'run', side_effect=check):
            result = thermal.broker_status()
        self.assertTrue(result['per_fan_control'])
        self.assertEqual(commands[1][-3:], ['fan', '2', '1000'])

    def test_separate_permission_upgrade_is_read_only_and_preserves_auto(self):
        commands = []
        def check(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1 if command[-1] == 'separate' else 0)
        reply = {'ok': True, 'data': {'schema_version': 1, 'mode': 'manual', 'ready': True,
                                     'per_fan_control': True, 'separate_memory': True, 'saved_separate_fans': []}}
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value=reply), \
                patch.object(thermal.subprocess, 'run', side_effect=check):
            result = thermal.broker_status()
        self.assertTrue(result['permitted'])
        self.assertTrue(result['separate_memory'])
        self.assertFalse(result['per_fan_control'])
        self.assertIn('Separate-profile permission', result['error'])
        self.assertEqual(commands[-1], ['/usr/bin/sudo', '-n', '-l', str(thermal.BROKER), 'separate'])

    def test_linked_permission_probe_timeout_is_bounded_and_preserves_auto(self):
        commands = []
        def check(command, **kwargs):
            commands.append((command, kwargs['timeout']))
            if command[-1] == 'linked':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return subprocess.CompletedProcess(command, 0)
        reply = {'ok': True, 'data': {'schema_version': 1, 'mode': 'manual', 'ready': True,
                                     'per_fan_control': True, 'separate_memory': True, 'linked_memory': True}}
        with patch.object(thermal, 'trusted_broker', return_value=True), \
                patch.object(thermal, 'command_result', return_value=reply), \
                patch.object(thermal.subprocess, 'run', side_effect=check):
            result = thermal.broker_status()
        self.assertTrue(result['permitted'])
        self.assertTrue(result['linked_memory'])
        self.assertFalse(result['per_fan_control'])
        self.assertIn('Linked-profile permission', result['error'])
        self.assertEqual(commands[-1][0], ['/usr/bin/sudo', '-n', '-l', str(thermal.BROKER), 'linked'])
        self.assertLess(1.5 + sum(timeout for _command, timeout in commands), 5)


if __name__ == '__main__':
    unittest.main()
