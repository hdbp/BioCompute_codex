"""Opt-in, inventory-only BioCompute worker agent.

Does NOT execute jobs, install software, receive commands, or expose network ports.
The operator supplies a controller URL and a discovery token explicitly.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psutil


def identity_path() -> Path:
    base=Path(os.environ.get('BIOCOMPUTE_AGENT_STATE', Path.home() / '.config' / 'biocompute'))
    base.mkdir(parents=True,exist_ok=True)
    return base/'machine-id'


def stable_machine_id() -> str:
    p=identity_path()
    if p.exists():
        return p.read_text().strip()
    value=uuid.uuid4().hex
    p.write_text(value+'\n')
    p.chmod(0o600)
    return value


def heartbeat_payload():
    mem=psutil.virtual_memory()
    return {'machine_id':stable_machine_id(),'hostname':socket.gethostname(),
            'os':platform.system(),'arch':platform.machine(),
            'cpu_total':psutil.cpu_count(logical=True) or 1,
            'ram_total':mem.total,'cpu_percent':psutil.cpu_percent(interval=0.1),
            'ram_used':mem.used,'address':''}


def main():
    controller=os.environ.get('BIOCOMPUTE_CONTROLLER_URL','').rstrip('/')
    token=os.environ.get('BIOCOMPUTE_AGENT_TOKEN','')
    if not controller.startswith(('https://','http://')) or not token:
        sys.exit('Set BIOCOMPUTE_CONTROLLER_URL and BIOCOMPUTE_AGENT_TOKEN before starting this agent')
    if controller.startswith('http://') and os.environ.get('BIOCOMPUTE_ALLOW_INSECURE_HTTP') != '1':
        sys.exit('Use HTTPS for remote agent discovery (or explicitly set BIOCOMPUTE_ALLOW_INSECURE_HTTP=1 on a trusted test network)')
    interval=max(15,int(os.environ.get('BIOCOMPUTE_AGENT_INTERVAL','30')))
    print(f'BioCompute inventory agent -> {controller}; ID {stable_machine_id()}; no commands accepted',flush=True)
    while True:
        data=json.dumps(heartbeat_payload()).encode()
        request=Request(controller+'/api/agents/heartbeat',data=data,method='POST',
                        headers={'Content-Type':'application/json','X-Agent-Token':token})
        try:
            with urlopen(request,timeout=10) as response:
                payload=json.loads(response.read())
                print('Inventory reported; remote execution remains disabled.',flush=True)
        except (HTTPError,URLError,ValueError,TimeoutError) as exc:
            print('Controller heartbeat failed:',str(exc),file=sys.stderr,flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
