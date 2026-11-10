"""Exercise elevation routing with shell functions; no real elevation occurs."""
from pathlib import Path
import os
import re
import runpy
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class ElevationTests(unittest.TestCase):
    def function(self, filename):
        text = (ROOT / filename).read_text()
        start = text.index('elevate() {')
        return text[start:text.index('\n}\n', start) + 3]

    def run_route(self, filename, sudo_body):
        script = '\n'.join([
            'set -u',
            'sudo() { ' + sudo_body + '; }',
            'pkexec() { printf "PKEXEC_ARG:%s\\n" "$@"; }',
            self.function(filename),
            'elevate /usr/bin/install -m 755 source /usr/local/libexec/example',
        ])
        return subprocess.run(['/bin/bash', '-c', script], capture_output=True, text=True)

    def test_authentication_failure_uses_pkexec_without_unsupported_separator(self):
        for filename in ('setup.sh', 'uninstall.sh'):
            with self.subTest(filename=filename):
                result = self.run_route(filename, 'printf "sudo: a password is required\\n" >&2; return 1')
                self.assertEqual(result.returncode, 0, result.stderr)
                args = [line.removeprefix('PKEXEC_ARG:') for line in result.stdout.splitlines()]
                self.assertEqual(args, ['/usr/bin/install', '-m', '755', 'source', '/usr/local/libexec/example'])

    def test_executed_command_failure_is_not_retried_with_pkexec(self):
        for filename in ('setup.sh', 'uninstall.sh'):
            with self.subTest(filename=filename):
                result = self.run_route(filename, 'printf "install: cannot create destination\\n" >&2; return 42')
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assertNotIn('PKEXEC_ARG:', result.stdout)

    def test_successful_sudo_does_not_invoke_pkexec(self):
        for filename in ('setup.sh', 'uninstall.sh'):
            with self.subTest(filename=filename):
                result = self.run_route(filename, 'printf "SUDO_ARG:%s\\n" "$@"; return 0')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('SUDO_ARG:-n', result.stdout)
                self.assertNotIn('PKEXEC_ARG:', result.stdout)


class InstallationBoundaryTests(unittest.TestCase):
    """Exercise the production preflight on isolated directories, without root."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thermals-install-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.namespace = runpy.run_path(str(ROOT / 'system/install-path-safety.py'), run_name='installer_boundary_fixture')
        self.runtime_trust = runpy.run_path(str(ROOT / 'thermal.py'), run_name='runtime_boundary_fixture')['trusted_broker']
        self.non_root_owned = set()
        self.real_lstat = os.lstat
        self.real_stat = os.stat

    def metadata(self, original, path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        fields = list(info)
        candidate = Path(path)
        # Ownership is synthetic so tests run as an ordinary CI account. The
        # fixture's real type, symlink targets and permission bits are retained.
        fields[4] = 1000 if candidate in self.non_root_owned else 0
        if not candidate.is_relative_to(self.root):
            fields[0] &= ~0o022  # Model secure ancestors outside the fixture.
        return os.stat_result(fields)

    def verify(self, path, directory=False, private_config=False, runtime=False):
        # Pathlib 3.10 caches OS accessors, whereas newer versions look them
        # up dynamically. Patch the Path seam so the same ownership simulation
        # reaches the production guard on every supported Python version.
        with mock.patch.object(Path, 'lstat', lambda path, *args, **kwargs: self.metadata(self.real_lstat, path, *args, **kwargs)), \
                mock.patch.object(Path, 'stat', lambda path, *args, **kwargs: self.metadata(self.real_stat, path, *args, **kwargs)):
            if runtime:
                return self.runtime_trust(path)
            return self.namespace['verify_install_destination'](path, directory=directory, private_config=private_config)

    def directory(self, name, mode=0o755):
        path = self.root / name
        path.mkdir(parents=True)
        path.chmod(mode)
        return path

    def test_missing_future_directories_under_secure_prefix_are_allowed(self):
        parent = self.directory('usr/local')
        self.verify(parent / 'libexec/future/broker')

    def test_root_owned_existing_executable_under_secure_prefix_is_allowed(self):
        parent = self.directory('usr/local/libexec')
        target = parent / 'broker'
        target.write_text('unchanged')
        target.chmod(0o755)
        self.verify(target)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_user_owned_ancestor_is_refused_even_without_write_bits(self):
        parent = self.directory('usr/local/libexec')
        self.non_root_owned.add(parent.parent)
        with self.assertRaisesRegex(RuntimeError, 'not root-owned'):
            self.verify(parent / 'broker')

    def test_group_or_world_writable_parent_is_refused_without_repairing_it(self):
        for mode in (0o775, 0o777):
            with self.subTest(mode=oct(mode)):
                parent = self.directory('prefix-' + str(mode), mode)
                with self.assertRaisesRegex(RuntimeError, 'group/world-writable'):
                    self.verify(parent / 'broker')
                self.assertEqual(stat.S_IMODE(parent.stat().st_mode), mode)
                self.assertEqual(list(parent.iterdir()), [])

    def test_secure_root_owned_directory_symlink_is_allowed(self):
        logical = self.directory('usr/local')
        physical = self.directory('opt/trusted/libexec')
        (logical / 'libexec').symlink_to(physical, target_is_directory=True)
        self.verify(logical / 'libexec/broker')

    def test_symlink_to_writable_or_user_owned_physical_directory_is_refused(self):
        logical = self.directory('usr/local')
        physical = self.directory('opt/trusted/libexec', 0o777)
        alias = logical / 'libexec'
        alias.symlink_to(physical, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'group/world-writable'):
            self.verify(alias / 'broker')
        physical.chmod(0o755)
        self.non_root_owned.add(physical.parent)
        with self.assertRaisesRegex(RuntimeError, 'not root-owned'):
            self.verify(alias / 'broker')

    def test_intermediate_symlink_detour_through_writable_directory_is_refused(self):
        logical = self.directory('usr/local')
        final = self.directory('opt/secure/libexec')
        detour = self.directory('writable-detour', 0o777)
        (detour / 'back-to-trusted').symlink_to(final, target_is_directory=True)
        (logical / 'libexec').symlink_to(detour / 'back-to-trusted', target_is_directory=True)
        self.assertEqual((logical / 'libexec').resolve(), final)
        with self.assertRaisesRegex(RuntimeError, 'group/world-writable'):
            self.verify(logical / 'libexec/broker')

    def test_runtime_helper_allows_secure_directory_aliases(self):
        logical = self.directory('usr/local')
        final = self.directory('opt/secure/libexec')
        target = final / 'broker'
        target.write_text('#!/bin/false\n')
        target.chmod(0o755)
        (logical / 'libexec').symlink_to(final, target_is_directory=True)
        self.assertTrue(self.verify(logical / 'libexec/broker', runtime=True))

    def test_runtime_helper_rejects_hidden_writable_symlink_hop(self):
        logical = self.directory('usr/local')
        final = self.directory('opt/secure/libexec')
        target = final / 'broker'
        target.write_text('#!/bin/false\n')
        target.chmod(0o755)
        detour = self.directory('writable-detour', 0o777)
        (detour / 'back-to-trusted').symlink_to(final, target_is_directory=True)
        (logical / 'libexec').symlink_to(detour / 'back-to-trusted', target_is_directory=True)
        self.assertEqual((logical / 'libexec').resolve(), final)
        self.assertFalse(self.verify(logical / 'libexec/broker', runtime=True))
        detour.chmod(0o755)
        self.non_root_owned.add(detour)
        self.assertFalse(self.verify(logical / 'libexec/broker', runtime=True))

    def test_runtime_helper_refuses_final_aliases_and_directory_loops(self):
        parent = self.directory('usr/local')
        target = parent / 'broker'
        target.write_text('#!/bin/false\n')
        target.chmod(0o755)
        (parent / 'alias').symlink_to(target)
        self.assertFalse(self.verify(parent / 'alias', runtime=True))
        (parent / 'loop').symlink_to(parent / 'loop', target_is_directory=True)
        self.assertFalse(self.verify(parent / 'loop/broker', runtime=True))

    def test_existing_file_and_final_symlink_must_not_weaken_the_boundary(self):
        parent = self.directory('usr/local/libexec')
        target = parent / 'broker'
        target.write_text('unchanged')
        target.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'secure root-owned file'):
            self.verify(target)
        target.chmod(0o755)
        self.non_root_owned.add(target)
        with self.assertRaisesRegex(RuntimeError, 'secure root-owned file'):
            self.verify(target)
        self.non_root_owned.clear()
        alias = parent / 'broker-link'
        alias.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'secure root-owned file'):
            self.verify(alias)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_dangling_directory_links_and_symlink_loops_are_refused(self):
        parent = self.directory('usr/local')
        (parent / 'dangling').symlink_to(self.root / 'missing-target', target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)):
            self.verify(parent / 'dangling/broker')
        (parent / 'loop').symlink_to(parent / 'loop', target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)):
            self.verify(parent / 'loop/broker')

    def test_directory_destinations_are_checked_too(self):
        parent = self.directory('var/lib/backups', 0o700)
        self.verify(parent, directory=True)
        parent.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'group/world-writable'):
            self.verify(parent, directory=True)

    def test_private_policy_filename_is_allowed_only_after_secure_parent_verification(self):
        parent = self.directory('etc/sudoers.d', 0o700)
        target = parent / 'fan-policy'
        original = self.real_lstat
        def private_lstat(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError('fixture private policy')
            return original(path, *args, **kwargs)
        self.real_lstat = private_lstat
        self.verify(target, private_config=True)
        with self.assertRaises(PermissionError):
            self.verify(target)
        parent.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'group/world-writable'):
            self.verify(target, private_config=True)

    def test_post_sleep_hook_is_installed_after_its_broker_and_removed_before_it(self):
        source = (ROOT / 'setup.sh').read_text()
        broker = source.index('elevate /usr/bin/install -o root -g root -m 755 -D "$source_dir/system/apple-silicon-fan-control"')
        hook = source.index('elevate /usr/bin/install -o root -g root -m 755 -D "$source_dir/system/apple-silicon-fan-wake"')
        self.assertGreater(hook, broker)
        removal = (ROOT / 'uninstall.sh').read_text().split('elevate /usr/bin/rm -f /usr/lib/systemd/system-sleep/', 1)[1]
        self.assertLess(removal.index('apple-silicon-fan-control'), removal.index('/usr/local/libexec/apple-silicon-fan-control'))
        self.assertIn('/run/apple-silicon-fan-control/wake.json', removal)

    def test_path_preflight_precedes_every_elevated_installation_or_removal_mutation(self):
        for filename in ('setup.sh', 'uninstall.sh'):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text()
                guard_end = source.index('/usr/bin/python3 -I "$source_dir/system/install-path-safety.py"')
                calls = list(re.finditer(r'^\s*elevate\s+\S', source, re.MULTILINE))
                self.assertTrue(calls)
                self.assertTrue(all(call.start() > guard_end for call in calls))


if __name__ == '__main__':
    unittest.main()
