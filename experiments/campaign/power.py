"""Mains power of the experiment machine (protocol §7 rule 5, amendment of 11 Nov 2026).

On a laptop, Windows limits CPU boost on battery. In the first main campaign, healthy runs
measured on the charger were about 40 % faster than runs on battery, which adds noise to
every comparison. Campaigns therefore record the power state of each run, exclude runs on
battery (`exclusion.require_ac_power`) and refuse to start on battery.

  python -m campaign.power <campaign-id>    # power state of each run of a past campaign, from the Windows event log
"""
from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
import sys
from pathlib import Path


def _powershell(command: str) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=60)
    return result.stdout.strip() if result.returncode == 0 else ""


def ac_online() -> bool | None:
    """True on mains power, False on battery, None if unknown (e.g. a desktop without battery)."""
    if platform.system() == "Windows":
        out = _powershell("(Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue"
                          " | Select-Object -First 1).PowerOnline")
        return {"True": True, "False": False}.get(out)
    supplies = [p for p in Path("/sys/class/power_supply").glob("*")
                if (p / "type").exists() and (p / "type").read_text().strip() == "Mains"]
    return any((p / "online").read_text().strip() == "1" for p in supplies) if supplies else None


HISTORY_SCRIPT = r"""
$since = [datetime]::Parse('SINCE')
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=105; StartTime=$since} -ErrorAction SilentlyContinue |
  ForEach-Object {
    $x = [xml]$_.ToXml()
    $ac = ($x.Event.EventData.Data | Where-Object Name -eq 'AcOnline').'#text'
    '{0}|{1}' -f $_.TimeCreated.ToUniversalTime().ToString('o'), $ac
  }
"""


def history(since: dt.datetime) -> list[tuple[dt.datetime, bool]]:
    """Power-source changes since `since`, from Windows Kernel-Power event 105 (times in UTC)."""
    out = _powershell(HISTORY_SCRIPT.replace("SINCE", since.astimezone().strftime("%Y-%m-%dT%H:%M:%S")))
    changes = []
    for line in out.splitlines():
        when, _, ac = line.partition("|")
        stamp = dt.datetime.fromisoformat(when.strip()[:26])
        changes.append((stamp.replace(tzinfo=dt.timezone.utc), ac.strip() == "true"))
    return sorted(changes)


def state_between(changes: list[tuple[dt.datetime, bool]], start: dt.datetime, end: dt.datetime) -> str:
    """'ac', 'battery' or 'mixed' for one interval; the state before the first change is its opposite."""
    if not changes:
        return "unknown"
    before = [ac for when, ac in changes if when <= start]
    state = before[-1] if before else not changes[0][1]
    during = {ac for when, ac in changes if start < when < end}
    return "mixed" if during - {state} else ("ac" if state else "battery")


def _parse(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def annotate(campaign_dir: Path) -> dict[str, str]:
    """Writes power.json: experiment id -> power state during the run (from the event log)."""
    progress = [json.loads(line) for line in (campaign_dir / "progress.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    runs = [p for p in progress if p["status"] == "completed"]
    changes = history(min(_parse(p["started_at"]) for p in runs) - dt.timedelta(days=1))
    states = {p["experiment_id"]: state_between(changes, _parse(p["started_at"]), _parse(p["ended_at"])) for p in runs}
    payload = {"source": "Windows event log, Kernel-Power event 105", "runs": states}
    (campaign_dir / "power.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return states


def main(argv: list[str] | None = None) -> int:
    from collections import Counter

    from .run import DATA_DIR
    campaign = (argv if argv is not None else sys.argv[1:])[0]
    states = annotate(DATA_DIR / "raw" / "campaigns" / campaign)
    print(f"power during the runs of {campaign}: {dict(Counter(states.values()))} -> data/raw/campaigns/{campaign}/power.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
