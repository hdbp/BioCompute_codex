"""Passive/approved discovery only: no arbitrary subnet sweeps or remote code execution."""
from __future__ import annotations
import json
import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timezone

import psutil
from sqlalchemy.orm import Session
from .models import Node


def _run_json(args: list[str], timeout: int = 5):
    if not shutil.which(args[0]):
        return None
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if p.returncode == 0:
            return json.loads(p.stdout)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return None


def _update(session: Session, row: dict):
    existing = session.get(Node, row["id"])
    if existing is None:
        existing = Node(**row)
        session.add(existing)
    else:
        # Discovery should not overwrite explicit user decisions.
        for key in ("hostname", "os", "arch", "cpu_total", "ram_total", "address", "last_seen", "details"):
            if key in row:
                setattr(existing, key, row[key])
        if existing.source != "local" and row.get("source"):
            existing.source = row["source"]
    return existing


def discover(session: Session) -> dict:
    now = datetime.now(timezone.utc)
    host = socket.gethostname()
    physical = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True) or 1
    memory = psutil.virtual_memory()
    local = {
        "id": "local-controller", "hostname": host, "os": platform.system(), "arch": platform.machine(),
        "cpu_total": logical, "ram_total": memory.total,
        "source": "local", "address": "localhost", "status": "online", "authorized": True,
        "enabled": True, "cpu_limit": logical, "ram_limit": memory.total,
        "last_seen": now,
        "details": {"physical_cores": physical, "cpu_percent": psutil.cpu_percent(interval=None), "ram_used": memory.used, "python": platform.python_version()},
    }
    node = _update(session, local)
    node.status = "online"
    node.authorized = True
    node.enabled = True
    if node.cpu_limit <= 0:
        node.cpu_limit = logical
    if node.ram_limit <= 0:
        node.ram_limit = memory.total
    discovered = {"local": 1, "tailscale": 0, "htcondor": 0, "lan_neighbors": 0}

    tail = _run_json(["tailscale", "status", "--json"])
    if isinstance(tail, dict):
        peers = tail.get("Peer", {})
        for key, peer in peers.items():
            if not isinstance(peer, dict):
                continue
            name = peer.get("HostName") or peer.get("DNSName") or str(key)
            ips = peer.get("TailscaleIPs") or []
            address = ips[0] if ips else ""
            machine_id = peer.get("ID", key)
            _update(session, {
                "id": "tailscale-" + str(machine_id)[:96], "hostname": str(name)[:180],
                "os": str(peer.get("OS", "unknown"))[:80], "arch": "unknown", "cpu_total": 0, "ram_total": 0,
                "source": "tailscale", "address": str(address)[:240], "status": "discovered", "authorized": False,
                "enabled": False, "cpu_limit": 0, "ram_limit": 0, "last_seen": now if peer.get("Online") else None,
                "details": {"network_online": bool(peer.get("Online")), "note": "Tailnet presence is not proof of worker availability"},
            })
            discovered["tailscale"] += 1

    condor = _run_json(["condor_status", "-json"])
    if isinstance(condor, list):
        for machine in condor:
            name = str(machine.get("Machine", machine.get("Name", "unknown")))
            if not name or name == "unknown":
                continue
            _update(session, {
                "id": "condor-" + name[:110], "hostname": name[:180],
                "os": str(machine.get("OpSys", "unknown"))[:80],
                "arch": str(machine.get("Arch", "unknown"))[:80],
                "cpu_total": int(machine.get("Cpus", 0) or 0),
                "ram_total": int(machine.get("Memory", 0) or 0) * 1024 * 1024,
                "source": "htcondor", "address": name[:240], "status": "discovered",
                "authorized": False, "enabled": False, "cpu_limit": 0, "ram_limit": 0,
                "last_seen": now, "details": {"htcondor_state": str(machine.get("State", "unknown"))},
            })
            discovered["htcondor"] += 1
    # Passive kernel neighbor inventory (no probing, no port scanning).
    # Reachability and permission are intentionally unknown for these devices.
    neighbors = _run_json(['ip', '-j', 'neigh', 'show'])
    if isinstance(neighbors, list):
        for item in neighbors:
            if not isinstance(item, dict):
                continue
            address = str(item.get('dst', ''))
            state = item.get('state', [])
            if isinstance(state, str):
                state = [state]
            if not address or 'FAILED' in state or 'INCOMPLETE' in state:
                continue
            if address.startswith('fe80:'):
                continue
            _update(session, {
                'id': 'lan-' + address.replace(':','_').replace('.','-')[:114],
                'hostname': address, 'os': 'unknown', 'arch': 'unknown',
                'cpu_total': 0, 'ram_total': 0, 'source':'lan-neighbor',
                'address':address, 'status':'discovered', 'authorized':False,
                'enabled':False, 'cpu_limit':0, 'ram_limit':0, 'last_seen':None,
                'details':{'interface':str(item.get('dev','')),
                           'neighbor_state':state,
                           'note':'Passive neighbor cache; no machine access or active probe performed'},
            })
            discovered['lan_neighbors'] += 1
    session.commit()
    return discovered


def node_info(row: Node) -> dict:
    return {
        "id": row.id, "hostname": row.hostname, "os": row.os, "arch": row.arch,
        "cpu_total": row.cpu_total, "ram_total": row.ram_total,
        "source": row.source, "address": row.address, "status": row.status,
        "authorized": row.authorized, "enabled": row.enabled,
        "cpu_limit": row.cpu_limit, "ram_limit": row.ram_limit,
        "idle_only": row.idle_only,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        "details": row.details or {},
        "can_execute_now": row.id == "local-controller" and row.enabled,
    }
