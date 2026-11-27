"""Send a sample IoT observation from a separate local process."""
import argparse
from datetime import datetime
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

SOURCES = {"gateway":"gateway-local", "gateway-secondary":"gateway-secondary-local"}
KINDS = ("heartbeat","battery","port_fault","port_status","parking_enter","parking_exit","charging_started","charging_ended")


def main():
    cmd = argparse.ArgumentParser(description=__doc__)
    cmd.add_argument("kind",choices=KINDS)
    cmd.add_argument("resource",help="Hub ID, vehicle ID, parking space ID, or port ID.")
    cmd.add_argument("--vehicle")
    cmd.add_argument("--value",type=int)
    cmd.add_argument("--battery",type=int)
    cmd.add_argument("--slot-id")
    cmd.add_argument("--sequence",type=int)
    cmd.add_argument("--event-id",default=None)
    cmd.add_argument("--observed-at",default=None,help="ISO timestamp. Defaults to the local time with offset.")
    cmd.add_argument("--port",type=int,default=8080)
    cmd.add_argument("--source",choices=SOURCES,default="gateway")
    args = cmd.parse_args()
    payload = {
        "id":args.event_id or f"OBS-{uuid4().hex[:12]}", "kind":args.kind, "resource":args.resource,
        "sequence":args.sequence if args.sequence is not None else time.time_ns()//1000,
        "observedAt":args.observed_at or datetime.now().astimezone().isoformat(),
    }
    for name,value in (("vehicle",args.vehicle),("value",args.value),("battery",args.battery),("slotId",args.slot_id)):
        if value is not None:
            payload[name]=value
    if args.kind == "port_fault" and "value" not in payload:
        payload["value"] = 1
    request = Request(f"http://127.0.0.1:{args.port}/api/ingest",json.dumps(payload).encode(),headers={
        "Content-Type":"application/json", "X-IoT-Source":args.source,"X-Demo-IoT":SOURCES[args.source],
    })
    try:
        with urlopen(request,timeout=10) as response:
            print(response.read().decode("utf-8"))
    except HTTPError as exc:
        cmd.exit(1,exc.read().decode("utf-8")+"\n")
    except URLError as exc:
        cmd.exit(1,f"{exc.reason}\n")


if __name__ == "__main__":
    main()
