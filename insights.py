"""Local reporting and isolated scenario simulation."""
from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta
from math import ceil

from services import VN, ResourceService, add_daily_event, by_id, iso, need, now, time_of


class DailySummaryService:
    RETRY_MINUTES = (1, 5, 15)

    @staticmethod
    def summarize(s, day):
        by_hub = defaultdict(lambda: dict(trip=set(), denial=set(), incident=set(), chargeStart={}))
        for event in s["dailyLedger"]:
            if time_of(event["stamp"]).astimezone(VN).date() != day:
                continue
            metrics = by_hub[event["hub"]]
            if event["kind"] == "chargeStart":
                metrics["chargeStart"][event["id"]] = event["waitMinutes"]
            else:
                metrics[event["kind"]].add(event["id"])
        day_start = datetime.combine(day, time.min, VN)
        partial = time_of(s["sessionStarted"]) > day_start
        demo = s.get("demoDay") == day.isoformat()
        rows = []
        for hub in s["hubs"]:
            metrics = by_hub[hub["id"]]
            waits = list(metrics["chargeStart"].values())
            rows.append(dict(day=day.isoformat(), hub=hub["id"], session=s["session"],
                             trips=len(metrics["trip"]), denials=len(metrics["denial"]),
                             incidents=len(metrics["incident"]), chargingSamples=len(waits),
                             averageWait=round(sum(waits)/len(waits), 2) if waits else None,
                             completeness="Demo" if demo else "Partial" if partial else "Complete"))
        return rows

    @staticmethod
    def tick(store, instant=None):
        instant = instant or now()
        today = instant.astimezone(VN).date()
        with store.lock:
            s = store.state
            day = time_of(s["sessionStarted"]).astimezone(VN).date()
            if s.get("demoDay"):
                day = min(day, date.fromisoformat(s["demoDay"]))
            while day < today:
                key = day.isoformat()
                job = s["dailyJobs"].get(key, {})
                if job.get("status") not in ("Completed","Failed") and (
                    not job.get("retryAt") or instant >= time_of(job["retryAt"])
                ):
                    attempts = job.get("attempts",0) + 1
                    try:
                        rows = DailySummaryService.summarize(s, day)
                    except Exception as exc:
                        job = dict(attempts=attempts, status="Retrying", lastAttemptAt=iso(instant),
                                   error=str(exc))
                        if attempts <= len(DailySummaryService.RETRY_MINUTES):
                            job["retryAt"] = iso(instant + timedelta(minutes=DailySummaryService.RETRY_MINUTES[attempts-1]))
                        else:
                            job["status"] = "Failed"
                        s["dailyJobs"][key] = job
                    else:
                        s["dailyReports"][key] = rows
                        s["dailyJobs"][key] = dict(status="Completed", attempts=attempts, lastAttemptAt=iso(instant))
                day += timedelta(days=1)

    @staticmethod
    def demo_data(s, instant=None):
        yesterday = (instant or now()).astimezone(VN).date() - timedelta(days=1)
        s["demoDay"] = yesterday.isoformat()
        for kind, amount in (("trip",3),("denial",2),("incident",1)):
            for index in range(amount):
                add_daily_event(s, kind, f"DEMO-{kind}-{index}", "MET",
                                iso(datetime.combine(yesterday,time(9,index),VN)))
        for index, wait in enumerate((4,8)):
            add_daily_event(s,"chargeStart",f"DEMO-charge-{index}","MET",
                            iso(datetime.combine(yesterday,time(10+index,wait),VN)),waitMinutes=wait)


class SimulationService:
    KINDS = {
        "demand": 100, "full_parking": 1, "charge_demand": 100, "port_failure": 20, "concentration": 100,
    }
    MODEL = "Hub Flow 2.0"

    @staticmethod
    def validate(minutes, changes):
        need(type(minutes) is int and 1 <= minutes <= 60, "Thời lượng mô phỏng phải từ 1 đến 60 phút.")
        need(isinstance(changes,list) and len(changes) <= len(SimulationService.KINDS), "Cấu hình kịch bản không hợp lệ.")
        seeds = {}
        for entry in changes:
            need(isinstance(entry,dict), "Cấu hình kịch bản không hợp lệ.")
            kind, amount = entry.get("kind"), entry.get("amount")
            need(kind in SimulationService.KINDS and kind not in seeds, "Mỗi tình huống chỉ được chọn một lần.")
            need(type(amount) is int and 0 <= amount <= SimulationService.KINDS[kind], "Mức thay đổi của kịch bản không hợp lệ.")
            seeds[kind] = amount
        return seeds

    @staticmethod
    def per_minute(total, minute, horizon):
        return (total * (minute+1) + horizon-1)//horizon - (total*minute + horizon-1)//horizon

    @staticmethod
    def model(snapshot, target, minutes, config, captured):
        hub = target["id"]
        spots = {x["id"]:x for x in snapshot["spaces"] if x["hub"] == hub and not x["blocked"]}
        occupied = {x["id"]:x["observedVehicle"] for x in spots.values() if x.get("observedVehicle")}
        observed = set(occupied.values())
        for vehicle in snapshot["vehicles"]:
            if vehicle["space"] in spots and vehicle["id"] not in observed:
                occupied.setdefault(vehicle["space"],vehicle["id"])
        vehicle_ids = {v["id"] for v in ResourceService.check(
            snapshot,"vehicle",hub,iso(captured),iso(captured+timedelta(minutes=minutes)),as_of=captured
        )["choices"]}
        available = deque((space,vehicle) for space,vehicle in sorted(occupied.items()) if vehicle in vehicle_ids)
        external_holds = [
            x for x in snapshot["schedule"] + snapshot["parking"] + snapshot["transfers"]
            if x["space"] in spots and x["status"] in ("Reserved","Occupied","Scheduled","Charging","Confirmed","InProgress")
            and time_of(x["end"]) > captured and time_of(x["start"]) < captured+timedelta(minutes=minutes)
        ]
        start_holds = {x["space"] for x in external_holds if time_of(x["start"]) <= captured}
        free_spaces = [spot for spot in spots if spot not in occupied and spot not in start_holds]
        extra_vehicles = config.get("concentration",0)
        overflow = max(0,extra_vehicles-len(free_spaces))
        for index, spot in enumerate(free_spaces[:extra_vehicles]):
            vehicle = f"Scenario-{index}"
            occupied[spot] = vehicle
            available.append((spot,vehicle))
        if config.get("full_parking"):
            for spot in free_spaces[extra_vehicles:]:
                occupied[spot] = "Scenario-Parked"
        initial_count = len(available)
        ports = [x["id"] for x in spots.values() if x["port"] and not x["portFault"]]
        need(config.get("port_failure",0) <= len(ports), "Số cổng hỏng giả định vượt quá số cổng đang hoạt động.")
        ports = ports[config.get("port_failure",0):]
        demand = round(target["demand"] * minutes / 30) + config.get("demand",0)
        extra_charge = config.get("charge_demand",0)
        incoming = []
        for request in snapshot["requests"]:
            if request["hub"] != hub or request["status"] not in ("Pending","Waiting") or request["plug"] != "Type 2":
                continue
            ready = max(0,ceil((time_of(request["start"])-captured).total_seconds()/60))
            deadline = (time_of(request["end"])-captured).total_seconds()/60
            if ready < minutes and deadline > 0:
                incoming.append((ready,request["duration"],deadline))
        for minute in range(minutes):
            incoming.extend([(minute,25,minutes+25)]*SimulationService.per_minute(extra_charge,minute,minutes))
        incoming = deque(sorted(incoming))
        pending = deque()
        in_charge = {}
        demand_served = 0
        waits = []
        unmet = 0
        peak = len(set(occupied) | start_holds)
        for minute in range(minutes):
            moment = captured+timedelta(minutes=minute)
            held = {x["space"] for x in external_holds if time_of(x["start"]) <= moment < time_of(x["end"])}
            for port in [x for x in in_charge if in_charge[x] <= minute]:
                del in_charge[port]
            arrivals = SimulationService.per_minute(demand,minute,minutes)
            for _ in range(min(arrivals,len(available))):
                spot,_ = available.popleft()
                occupied.pop(spot,None)
                demand_served += 1
            while incoming and incoming[0][0] <= minute:
                pending.append(incoming.popleft())
            eligible = deque(x for x in pending if minute+x[1] <= x[2])
            unmet += len(pending)-len(eligible)
            pending = eligible
            for port in ports:
                if not pending:
                    break
                if port in in_charge or port in occupied or port in held:
                    continue
                ready,duration,_ = pending[0]
                if any(x["space"] == port and time_of(x["start"]) < moment+timedelta(minutes=duration)
                       and time_of(x["end"]) > moment for x in external_holds):
                    continue
                pending.popleft()
                in_charge[port] = minute+duration
                waits.append(minute-ready)
            peak = max(peak,len(set(occupied)|held|set(in_charge)))
        return dict(demand=demand, initialVehicles=initial_count, served=demand_served, shortage=demand-demand_served,
                    demandRate=round(100*demand_served/demand,1) if demand else None, chargingStarted=len(waits),
                    chargeWait=round(sum(waits)/len(waits),1) if waits else None, unservedCharge=len(pending)+unmet,
                    peakOccupied=peak, peakOccupancy=round(100*peak/len(spots),1) if spots else None,
                    capacity=len(spots), remainingVehicles=len(available), parkingOverflow=overflow, usablePorts=len(ports))

    @staticmethod
    def run(snapshot, hub, extra=0, *, minutes=30, changes=None):
        config = SimulationService.validate(minutes, [{"kind":"demand","amount":extra}] if changes is None else changes)
        captured = time_of(snapshot["capturedAt"])
        all_hubs = ResourceService.summary(snapshot, as_of=captured)
        target = by_id(all_hubs,hub)
        baseline = SimulationService.model(snapshot,target,minutes,{},captured)
        scenario = SimulationService.model(snapshot,target,minutes,config,captured)
        others = [x for x in all_hubs if x["id"] != hub]
        donor = max(others,key=lambda x:x["available"]-x["demand"],default=None)
        recipient = max(others,key=lambda x:x["free"],default=None)
        advice = []
        if scenario["shortage"] and donor and donor["available"] > donor["demand"]:
            advice.append(dict(action="transfer_in",hub=donor["id"],
                               count=min(scenario["shortage"],donor["available"]-donor["demand"])))
        if scenario["unservedCharge"]:
            advice.append(dict(action="reschedule", count=scenario["unservedCharge"]))
        if scenario["parkingOverflow"] or scenario["peakOccupancy"] == 100:
            if recipient and recipient["free"]:
                advice.append(dict(action="redirect",hub=recipient["id"],count=recipient["free"]))
        if scenario["remainingVehicles"] > baseline["remainingVehicles"]:
            advice.append(dict(action="transfer_out",count=scenario["remainingVehicles"]-baseline["remainingVehicles"]))
        return dict(hub=hub,minutes=minutes,changes=config,baselineMetrics=baseline,scenarioMetrics=scenario,
                    base=baseline["demand"],hypothetical=scenario["demand"],available=baseline["initialVehicles"],
                    servedBase=baseline["served"],servedScenario=scenario["served"],shortageBase=baseline["shortage"],
                    shortage=scenario["shortage"],extra=config.get("demand",0),version=snapshot["version"],
                    model=SimulationService.MODEL,snapshotAt=iso(captured),recommendations=advice)
