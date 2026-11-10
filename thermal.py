#!/usr/bin/python3
"""Read-only SMC telemetry. Fixture paths are injectable in tests, never the CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

BROKER = Path('/usr/local/libexec/apple-silicon-fan-control')
ACTION = re.compile(r'(?:auto|[1-7][0-9]{3})\Z')
FAN_INDEX = re.compile(r'(?:[1-9]|[1-5][0-9]|6[0-4])\Z')
PERMISSION_TIMEOUT = 0.6  # Four probes + status stay inside the widget read deadline.


def saved_fan_map(value) -> list[dict] | None:
    if not isinstance(value, list) or len(value) > 64:
        return None
    result = []
    indices = set()
    for fan in value:
        if (not isinstance(fan, dict) or set(fan) != {'index', 'rpm'}
                or type(fan['index']) is not int or not 1 <= fan['index'] <= 64
                or fan['index'] in indices or type(fan['rpm']) is not int
                or not 1000 <= fan['rpm'] <= 7999):
            return None
        indices.add(fan['index'])
        result.append({'index': fan['index'], 'rpm': fan['rpm']})
    return sorted(result, key=lambda fan: fan['index'])


def read_text(path: Path) -> str | None:
    try:
        with path.open('rb') as stream:
            value = stream.read(4097)
        if len(value) > 4096:
            return None
        return value.decode('utf-8', errors='replace').replace('\x00', '').strip()
    except (OSError, ValueError):
        return None


def read_number(path: Path, low: int, high: int) -> int | None:
    value = read_text(path)
    if value is None or not re.fullmatch(r'-?[0-9]{1,15}', value):
        return None
    number = int(value)
    return number if low <= number <= high else None


def trusted_broker(path: Path = BROKER) -> bool:
    try:
        if not path.is_absolute():
            return False
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            return False
        if info.st_mode & 0o022 or not os.access(path, os.X_OK):
            return False
        # Inspect each symlink hop, not only the final resolved path. An alias
        # through a writable directory can be redirected after this check even
        # when it currently points back into a secure root-owned directory.
        pending = list(path.parents)
        seen = set()
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if len(seen) > 256:
                return False
            pending.extend(parent.parents)
            info = parent.lstat()
            if info.st_uid != 0:
                return False
            if stat.S_ISLNK(info.st_mode):
                target = Path(os.readlink(parent))
                pending.append(target if target.is_absolute() else parent.parent / target)
                if not parent.is_dir():
                    return False
            elif not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
                return False
        return True
    except (OSError, RuntimeError):
        return False


def command_result(command: list[str], timeout: float) -> dict:
    """Bound child execution time and output. Broker output is small and trusted."""
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdout=output, stderr=output,
                                    timeout=timeout, check=False,
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            output.seek(0)
            raw = output.read(16385)
        if len(raw) > 16384:
            return {'ok': False, 'error': 'Control helper returned too much output.'}
        message = raw.decode('utf-8', errors='replace').strip()
        if result.returncode:
            return {'ok': False, 'error': message[:1024] or 'Control command failed.', 'exit_code': result.returncode}
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return {'ok': False, 'error': 'Control helper returned invalid JSON.'}
        if not isinstance(data, dict):
            return {'ok': False, 'error': 'Control helper returned invalid data.'}
        return {'ok': True, 'data': data}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'Control helper timed out.'}
    except OSError:
        return {'ok': False, 'error': 'Control helper could not be started.'}


def broker_status() -> dict:
    unavailable = {'mode': 'unknown', 'ready': False, 'permitted': False, 'error': None}
    if not trusted_broker():
        return {**unavailable, 'error': 'Installed control helper has unsafe ownership or permissions.'} if BROKER.exists() else unavailable
    response = command_result([str(BROKER), 'status'], 1.5)
    if not response['ok']:
        return {**unavailable, 'error': response['error']}
    data = response['data']
    mode = data.get('mode')
    ready = data.get('ready') is True
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1 or mode not in ('auto', 'manual', 'unknown'):
        return {**unavailable, 'error': 'Control service protocol is incompatible.'}
    try:
        permitted = subprocess.run(
            ['/usr/bin/sudo', '-n', '-l', str(BROKER), 'auto'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PERMISSION_TIMEOUT, check=False,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        permitted = False
    per_fan_permitted = False
    if permitted and data.get('per_fan_control') is True:
        # Detect partial upgrades: an older policy can grant Auto/shared RPM
        # while lacking the new fan action. Probe without executing anything.
        targets = data.get('targets')
        indices = [fan['index'] for fan in targets[:64]
                   if isinstance(fan, dict) and type(fan.get('index')) is int
                   and 1 <= fan['index'] <= 64] if isinstance(targets, list) else []
        index = min(indices) if indices else 1
        try:
            per_fan_permitted = subprocess.run(
                ['/usr/bin/sudo', '-n', '-l', str(BROKER), 'fan', str(index), '1000'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=PERMISSION_TIMEOUT, check=False,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    separate_permitted = False
    if permitted and data.get('separate_memory') is True:
        try:
            separate_permitted = subprocess.run(
                ['/usr/bin/sudo', '-n', '-l', str(BROKER), 'separate'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=PERMISSION_TIMEOUT, check=False,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    error = data.get('error')
    linked_permitted = False
    if permitted and data.get('linked_memory') is True:
        try:
            linked_permitted = subprocess.run(
                ['/usr/bin/sudo', '-n', '-l', str(BROKER), 'linked'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=PERMISSION_TIMEOUT, check=False,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not ready and not error:
        error = 'Manual-control watchdog is not ready.'
    if ready and not permitted:
        error = 'This user is not authorized for manual control.'
    elif ready and data.get('per_fan_control') is True and not per_fan_permitted:
        error = 'Per-fan permission is not installed. Update the control service setup.'
    elif ready and data.get('separate_memory') is True and not separate_permitted:
        error = 'Separate-profile permission is not installed. Update the control service setup.'
    elif ready and data.get('linked_memory') is True and not linked_permitted:
        error = 'Linked-profile permission is not installed. Update the control service setup.'
    return {**data, 'mode': mode if ready else 'unknown', 'ready': ready,
            'per_fan_control': per_fan_permitted and (data.get('separate_memory') is not True or separate_permitted)
                and (data.get('linked_memory') is not True or linked_permitted),
            'permitted': permitted, 'error': str(error)[:1024] if error else None}


class Telemetry:
    def __init__(self, sys_root: Path = Path('/sys'), proc_root: Path = Path('/proc')):
        self.sys_root = sys_root
        self.proc_root = proc_root

    def hwmon(self) -> Path | None:
        try:
            for path in sorted((self.sys_root / 'class/hwmon').iterdir()):
                if re.fullmatch(r'hwmon[0-9]+', path.name) and read_text(path / 'name') == 'macsmc_hwmon':
                    return path
        except OSError:
            pass
        return None

    def get(self, control: dict | None = None) -> dict:
        directory = self.hwmon()
        model = (read_text(self.proc_root / 'device-tree/model') or
                 read_text(self.sys_root / 'firmware/devicetree/base/model'))
        compatible = read_text(self.proc_root / 'device-tree/compatible') or ''
        apple = bool(directory or (model and model.startswith('Apple')) or 'apple,' in compatible)
        control = control or {'mode': 'unknown', 'ready': False, 'permitted': False}
        available = control.get('ready') is True and control.get('permitted') is True
        mode = control.get('mode') if control.get('ready') is True else 'unknown'
        if mode not in ('auto', 'manual', 'unknown'):
            mode = 'unknown'
        persistent = control.get('persistent') is True
        saved_mode = control.get('saved_mode')
        if saved_mode not in ('auto', 'manual'):
            saved_mode = 'unknown'
        saved_rpm = control.get('saved_rpm')
        if type(saved_rpm) is not int or not 1000 <= saved_rpm <= 7999:
            saved_rpm = None
        requested_rpm = control.get('requested_rpm')
        if type(requested_rpm) is not int or not 1000 <= requested_rpm <= 7999:
            requested_rpm = None
        saved_owner = control.get('saved_owner_uid')
        if type(saved_owner) is not int or saved_owner < 0:
            saved_owner = None
        saved_control_mode = control.get('saved_control_mode')
        if saved_control_mode not in ('auto', 'shared', 'individual'):
            saved_control_mode = 'unknown'
        control_mode = control.get('control_mode')
        if control_mode not in ('auto', 'shared', 'individual'):
            control_mode = 'unknown'
        saved_fans = saved_fan_map(control.get('saved_fans', []))
        preference_error = str(control['preference_error'])[:1024] if control.get('preference_error') else None
        if saved_fans is None:
            saved_fans = []
            preference_error = preference_error or 'Control service returned an invalid saved fan map.'
        separate_memory = control.get('separate_memory') is True
        separate_fans = saved_fan_map(control.get('saved_separate_fans')) if separate_memory else []
        if separate_fans is None:
            separate_fans = []
            preference_error = preference_error or 'Control service returned an invalid remembered separate profile.'
        if separate_memory and saved_mode == 'manual' and saved_control_mode == 'individual' and separate_fans != saved_fans:
            preference_error = preference_error or 'Active separate targets do not match the remembered separate profile.'
        linked_memory = control.get('linked_memory') is True
        linked_rpm = control.get('saved_linked_rpm') if linked_memory else None
        if linked_memory and 'saved_linked_rpm' not in control:
            preference_error = preference_error or 'Control service omitted the remembered linked speed.'
        if linked_rpm is not None and (type(linked_rpm) is not int or not 1000 <= linked_rpm <= 7999):
            linked_rpm = None
            preference_error = preference_error or 'Control service returned an invalid remembered linked speed.'
        if linked_memory and saved_mode == 'manual' and saved_control_mode == 'shared' and linked_rpm != saved_rpm:
            preference_error = preference_error or 'Active linked speed does not match the remembered linked speed.'
        paused = None
        if persistent and saved_mode == 'manual' and mode != 'manual':
            paused = control.get('paused_reason') or control.get('pause_reason') or control.get('error')
            paused = str(paused)[:1024] if paused else None
        data = {
            'schema_version': 2, 'timestamp_ms': int(time.time() * 1000),
            'is_apple_silicon': apple, 'device_model': (model or ('Apple Silicon Mac' if apple else 'Non-Apple hardware'))[:256],
            'has_fan': False, 'fan_count': 0, 'fans': [], 'fan_rpm': None,
            'fan_min': None, 'fan_max': None, 'fan_target': None,
            'mode': mode, 'manual_mode': mode == 'manual',
            'persistent': persistent, 'saved_mode': saved_mode, 'saved_rpm': saved_rpm,
            'per_fan_control': control.get('per_fan_control') is True and preference_error is None,
            'saved_fans': saved_fans, 'saved_control_mode': saved_control_mode,
            'separate_memory': separate_memory, 'saved_separate_fans': separate_fans,
            'linked_memory': linked_memory, 'saved_linked_rpm': linked_rpm,
            'control_mode': control_mode,
            'saved_owner_uid': saved_owner, 'requested_rpm': requested_rpm,
            'paused_reason': paused,
            'preference_error': preference_error,
            'control_available': available, 'fan_control_enabled': False,
            'lease_owned': (mode == 'manual' or control.get('recovering') is True) and control.get('owner_uid') == os.getuid(),
            'lease_recovering': control.get('recovering') is True,
            'broker_generation': str(control.get('generation') or '')[:128],
            'control_error': control.get('error'),
            'control_notice': str(control['notice'])[:1024] if control.get('notice') else None,
            'max_temp': None, 'power_watts': None,
            'sensors': dict.fromkeys(('nand', 'battery', 'regulator', 'wifi')),
            'error': None, 'error_message': None, 'warnings': [],
        }
        if not apple:
            data.update(error='unsupported_hardware', error_message='Apple Silicon hardware was not found.')
            return data
        if directory is None:
            data.update(error='macsmc_hwmon_missing', error_message='The macsmc_hwmon driver is unavailable.')
            return data
        try:
            files = list(directory.iterdir())
        except OSError:
            data.update(error='sensor_read_failed', error_message='The hardware sensor directory cannot be read.')
            return data
        indices = sorted({int(m.group(1)) for path in files
                          if (m := re.fullmatch(r'fan([0-9]{1,3})_input', path.name))})
        if len(indices) > 32:
            data.update(error='unsupported_fan_count', error_message='More than 32 fan channels are not supported.')
            return data
        for index in indices:
            stem = directory / f'fan{index}'
            fan = {'index': index, 'label': (read_text(Path(f'{stem}_label')) or f'Fan {index}')[:128]}
            for key, attribute in (('rpm', 'input'), ('min', 'min'), ('max', 'max'), ('target', 'target')):
                fan[key] = read_number(Path(f'{stem}_{attribute}'), 0, 100000)
            fan['limits_valid'] = (fan['min'] is not None and fan['max'] is not None and
                                   0 < fan['min'] <= fan['max'] < 8000)
            if fan['rpm'] is None:
                data['warnings'].append(f'Fan {index} speed is unavailable.')
            if not fan['limits_valid']:
                data['warnings'].append(f'Fan {index} control limits are unavailable or unsupported.')
            data['fans'].append(fan)
        fans = data['fans']
        data.update(has_fan=bool(fans), fan_count=len(fans))
        for key, fan_key, aggregate in (('fan_rpm', 'rpm', max), ('fan_target', 'target', max),
                                        ('fan_min', 'min', min), ('fan_max', 'max', max)):
            values = [f[fan_key] for f in fans if f[fan_key] is not None]
            data[key] = aggregate(values) if values else None
        data['fan_control_enabled'] = bool(available and not preference_error and fans and all(f['limits_valid'] for f in fans))
        for path in sorted(files):
            match = re.fullmatch(r'temp([0-9]{1,3})_label', path.name)
            if not match:
                continue
            label = (read_text(path) or '').lower()
            raw = read_number(directory / f'temp{match.group(1)}_input', -100000, 250000)
            key = next((key for text, key in (('nand', 'nand'), ('battery', 'battery'),
                                             ('regulator', 'regulator'), ('wifi', 'wifi'), ('bt', 'wifi'))
                        if text in label), None)
            if key:
                value = round(raw / 1000, 1) if raw is not None else None
                previous = data['sensors'][key]
                if value is not None and (previous is None or value > previous):
                    data['sensors'][key] = value
                if value is None:
                    data['warnings'].append(f'{key} temperature is unavailable.')
        temperatures = [v for v in data['sensors'].values() if v is not None]
        data['max_temp'] = max(temperatures) if temperatures else None
        for path in sorted(files):
            match = re.fullmatch(r'power([0-9]{1,3})_label', path.name)
            if match and (read_text(path) or '').lower() == 'total system power':
                raw = read_number(directory / f'power{match.group(1)}_input', 0, 10**10)
                data['power_watts'] = round(raw / 1000000, 2) if raw is not None else None
                break
        if not temperatures and not any(f['rpm'] is not None for f in fans) and data['power_watts'] is None:
            data.update(error='sensor_read_failed', error_message='No valid hardware readings are available.')
        return data


def main(argv: list[str]) -> int:
    if not argv or argv == ['get']:
        print(json.dumps(Telemetry().get(broker_status()), ensure_ascii=True, allow_nan=False, separators=(',', ':')))
        return 0
    is_set = len(argv) == 2 and argv[0] == 'set' and ACTION.fullmatch(argv[1])
    is_set_fan = (len(argv) == 3 and argv[0] == 'set-fan' and FAN_INDEX.fullmatch(argv[1])
                  and re.fullmatch(r'[1-7][0-9]{3}', argv[2]))
    is_set_separate = argv == ['set-separate']
    is_set_linked = argv == ['set-linked']
    is_heartbeat = len(argv) == 2 and argv[0] == 'heartbeat' and re.fullmatch(r'[0-9a-f]{32}', argv[1])
    if is_set or is_set_fan or is_set_separate or is_set_linked or is_heartbeat:
        if not trusted_broker():
            print(json.dumps({'ok': False, 'error': 'The trusted manual-control service is not installed.'}))
            return 1
        arguments = (['linked'] if is_set_linked else ['separate'] if is_set_separate else [argv[1]] if is_set
                     else ['fan', argv[1], argv[2]] if is_set_fan else ['heartbeat', argv[1]])
        response = command_result(['/usr/bin/sudo', '-n', str(BROKER), *arguments], 3.5)
        if response['ok']:
            print(json.dumps(response['data'], allow_nan=False))
            return 0
        print(json.dumps({'ok': False, 'error': response['error']}, ensure_ascii=True))
        return 1
    print(json.dumps({'ok': False, 'error': 'Usage: helper.sh get | set <auto|RPM> | set-fan <index> <RPM> | set-separate | set-linked | heartbeat <lease-id>'}))
    return 64


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': 'helper_failed', 'error_message': str(exc)[:512]}))
        raise SystemExit(1)
