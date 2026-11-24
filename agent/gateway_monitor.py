#!/usr/bin/env python3
"""Read-only incremental gateway monitor using systemd journal."""

import configparser
import datetime
import json
from pathlib import Path
import re
import subprocess
import time

GATEWAYS = (
    'dmrgateway',
    'ysfgateway',
    'dstargateway',
    'aprsgateway',
)

# A successful historical event is not proof of a live connection.
# Keep the observed state, but expire its verification.
VERIFY_SECONDS = 300


DMR_CONFIG = '/opt/cfg/DMRGateway.ini'


def enabled_dmr_networks(config_path=DMR_CONFIG):
    path = Path(config_path)
    if not path.is_file():
        return None

    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read(path, encoding='utf-8')

    return {
        cfg.get(section, 'Name').strip()
        for section in cfg.sections()
        if section.startswith('DMR Network ')
        and cfg.getboolean(section, 'Enabled', fallback=False)
        and cfg.get(section, 'Name', fallback='').strip()
    }



def command(args, timeout=20):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout


def timestamp(entry):
    value = int(entry['__REALTIME_TIMESTAMP']) / 1000000
    return datetime.datetime.fromtimestamp(
        value, datetime.timezone.utc
    ).isoformat()


def clean(message):
    return re.sub(
        r'^[A-Z]:\s+\d{4}-\d\d-\d\d\s+'
        r'\d\d:\d\d:\d\d\.\d+\s+',
        '',
        message,
    )


def classify(gateway, message):
    text = clean(message)

    if gateway == 'dmrgateway':
        match = re.match(
            r'(.+?),\s+Logged into the master successfully',
            text,
        )
        if match:
            return match.group(1), 'connected'

        match = re.match(
            r'(.+?),\s+(?:Connection to the master has timed out'
            r'|Login to the master has failed'
            r'|Master is closing down'
            r'|Closing DMR Network)',
            text,
        )
        if match:
            return match.group(1), 'disconnected'

        match = re.match(
            r'(.+?),\s+Opening DMR Network',
            text,
        )
        if match:
            return match.group(1), 'linking'

    elif gateway == 'ysfgateway':
        match = re.search(r'\bLinked to (.+)', text)
        if match:
            return match.group(1).strip(), 'connected'

        if 'Link has failed' in text:
            return None, 'disconnected'

    elif gateway == 'dstargateway':
        match = re.search(
            r'DExtra link to (.+?) established', text
        )
        if match:
            return match.group(1).strip(), 'connected'

        match = re.search(
            r'DExtra link to (.+?) has failed', text
        )
        if match:
            return match.group(1).strip(), 'disconnected'

        match = re.search(
            r'Linking .+? to (.+)', text
        )
        if match:
            return match.group(1).strip(), 'linking'

        match = re.search(
            r'Unlinking from DExtra reflector (.+)', text
        )
        if match:
            return match.group(1).strip(), 'disconnected'

    elif gateway == 'aprsgateway':
        if (
            re.search(r'logresp .* verified\b', text)
            and 'unverified' not in text
        ):
            return 'APRS-IS', 'connected'

        if (
            'unverified' in text
            or 'Connect attempt to the APRS server has failed' in text
        ):
            return 'APRS-IS', 'disconnected'

    return None


class GatewayMonitor:
    def __init__(self, dmr_config=DMR_CONFIG):
        self.cache = {}
        self.dmr_config = Path(dmr_config)

    def invocation(self, gateway):
        output = command([
            '/bin/systemctl', 'show',
            gateway + '.service',
            '--property=ActiveState',
            '--property=InvocationID',
        ], timeout=5)

        values = dict(
            line.split('=', 1)
            for line in output.splitlines()
            if '=' in line
        )
        return (
            values.get('ActiveState', 'unknown'),
            values.get('InvocationID', ''),
        )

    def read(self, invocation, cursor=None):
        args = [
            '/usr/bin/journalctl',
            '_SYSTEMD_INVOCATION_ID=' + invocation,
            '--no-pager',
            '-o', 'json',
            '-q',
        ]
        if cursor:
            args.extend(['--after-cursor', cursor])

        output = command(args, timeout=30)
        for line in output.splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def collect(self, gateway):
        active, invocation = self.invocation(gateway)

        previous = self.cache.get(gateway)
        if not previous or previous['invocation'] != invocation:
            previous = {
                'invocation': invocation,
                'cursor': None,
                'connections': {},
            }
            self.cache[gateway] = previous

        if active != 'active' or not invocation:
            previous['connections'] = {}
            return {
                'gateway': gateway,
                'service': active,
                'connections': {},
            }

        states = previous['connections']

        for entry in self.read(invocation, previous['cursor']):
            cursor = entry.get('__CURSOR')
            if cursor:
                previous['cursor'] = cursor

            message = entry.get('MESSAGE', '')
            text = clean(message)

            if re.search(
                r'\bis (?:starting|stopping)\b',
                text, re.I
            ):
                states.clear()

            if gateway == 'ysfgateway' and 'YSFPREFLECTOR' in message:
                try:
                    received_epoch = int(entry['__REALTIME_TIMESTAMP']) / 1000000
                    received_time = timestamp(entry)
                except (KeyError, ValueError, TypeError):
                    continue

                for target, info in states.items():
                    if info['state'] == 'connected':
                        info['epoch'] = received_epoch
                        info['timestamp'] = received_time
                        info['message'] = 'YSF reflector keepalive received'
                continue

            event = classify(gateway, message)
            if not event:
                continue

            target, state = event

            try:
                event_time = timestamp(entry)
                event_epoch = (
                    int(entry['__REALTIME_TIMESTAMP']) / 1000000
                )
            except (KeyError, ValueError, TypeError):
                continue

            if target is None:
                for key in states:
                    states[key] = {
                        'state': state,
                        'timestamp': event_time,
                        'epoch': event_epoch,
                        'message': message,
                    }
            else:
                states[target] = {
                    'state': state,
                    'timestamp': event_time,
                    'epoch': event_epoch,
                    'message': message,
                }

        if gateway == 'dmrgateway':
            enabled = enabled_dmr_networks(self.dmr_config)
            if enabled is not None:
                for target in list(states):
                    if target not in enabled:
                        del states[target]

        now = time.time()
        connections = {}

        for target, info in states.items():
            age = max(0, now - info['epoch'])
            connections[target] = {
                'state': info['state'],
                'verified': (
                    info['state'] == 'connected'
                    and age <= VERIFY_SECONDS
                ),
                'fresh': age <= VERIFY_SECONDS,
                'timestamp': info['timestamp'],
                'message': info['message'],
            }

        return {
            'gateway': gateway,
            'service': active,
            'connections': connections,
        }

    def collect_all(self):
        result = {}
        for gateway in GATEWAYS:
            try:
                result[gateway] = self.collect(gateway)
            except (OSError, subprocess.SubprocessError, ValueError):
                result[gateway] = {
                    'gateway': gateway,
                    'service': 'unknown',
                    'connections': {},
                    'error': 'journal_unavailable',
                }
        return result


if __name__ == '__main__':
    monitor = GatewayMonitor()
    print(json.dumps(
        monitor.collect_all(),
        indent=2,
        ensure_ascii=False,
    ))
