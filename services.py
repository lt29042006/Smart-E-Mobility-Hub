"""Business rules for the local Smart E-Mobility Hub demonstration."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import RLock
from uuid import uuid4

VN = timezone(timedelta(hours=7))
YES, NO, UNKNOWN = "Có khả dụng", "Không khả dụng", "Không xác định"


class BusinessError(ValueError):
    pass


class ResourceUnavailable(BusinessError):
    def __init__(self, hub, message):
        super().__init__(message)
        self.hub = hub


def require_resources(result, hub):
    if result["status"] == NO:
        raise ResourceUnavailable(hub, result["reason"])
    need(result["status"] == YES, result["reason"])


def need(condition, message):
    if not condition:
        raise BusinessError(message)


def now():
    return datetime.now(VN)


def iso(value):
    return value.isoformat()


def time_of(value):
    text = str(value)
    result = datetime.fromisoformat(text[:-1]+"+00:00" if text.endswith("Z") else text)
    return result if result.tzinfo else result.replace(tzinfo=VN)


def interval_overlap(a, b, c, d):
    return time_of(a) < time_of(d) and time_of(c) < time_of(b)


def by_id(items, item_id):
    for item in items:
        if item["id"] == item_id:
            return item
    raise BusinessError("Không tìm thấy mã tài nguyên hoặc hồ sơ.")


def code(prefix, items):
    return f"{prefix}-{len(items)+1:03}"


def seed():
    hubs = [
        ("MET", "Hub Metro ĐHQG", "Ga Metro Đại học Quốc gia", 5),
        ("BKH", "Hub Bách khoa", "Cơ sở Dĩ An", 5),
        ("KTX", "Hub Ký túc xá", "Ký túc xá khu B", 4),
    ]
    stamp = iso(now())
    return dict(
        session=uuid4().hex[:8], version=1, stamp=stamp, sessionStarted=stamp,
        hubs=[dict(id=k, name=n, location=l, demand=d, stamp=stamp) for k,n,l,d in hubs],
        spaces=[
            dict(id=f"{k}-{i:02}", hub=k, port=f"C-{k}-{i:02}" if i <= 2 else "",
                 blocked=False, portFault=False, observedSeq=0, observedVehicle="")
            for k,_,_,_ in hubs for i in range(1,7)
        ],
        vehicles=[
            dict(id=id_, hub=hub, space=f"{hub}-{space:02}", battery=bat, status="Available",
                 fault=False, kind="Xe đạp điện", plug="Type 2", observedSeq=0)
            for id_,hub,space,bat in (
                ("EB-101","MET",3,83), ("EB-102","MET",4,62), ("EB-103","MET",5,48),
                ("EB-201","BKH",3,75), ("EB-202","BKH",4,18), ("EB-301","KTX",3,55),
            )
        ],
        bookings=[], parking=[], trips=[], requests=[], schedule=[], transfers=[],
        incidents=[], events=[], log=[], prefs={}, observations={}, observationHeads={},
        dailyLedger=[], dailyReports={}, dailyJobs={}, chargingSessions=[], planHistory=[], notifications=[],
        chargeConfig={"lowThreshold": 20, "autoTarget": 80, "autoHorizonMinutes": 120},
    )


class TransactionalStore:
    """One process owns the state. Transactions replace it after validation."""
    def __init__(self):
        self.state = seed()
        self.results = {}
        self.denials = {}
        self.lock = RLock()

    def _expire(self):
        cutoff = now()
        changed = False
        for booking in self.state["bookings"]:
            if booking["status"] == "Reserved" and time_of(booking["deadline"]) <= cutoff:
                booking["status"] = "Expired"
                vehicle = by_id(self.state["vehicles"], booking["vehicle"])
                if vehicle["status"] == "Reserved":
                    vehicle["status"] = "Available"
                changed = True
        for booking in self.state["parking"]:
            if booking["status"] == "Reserved" and time_of(booking["deadline"]) <= cutoff:
                booking["status"] = "Expired"
                changed = True
        if changed:
            self.state["version"] += 1

    def read(self):
        with self.lock:
            self._expire()
            return deepcopy(self.state)

    def commit(self, actor, action, key, change):
        with self.lock:
            self._expire()
            token = actor, action, key
            if key and token in self.results:
                return deepcopy(self.results[token])
            if key and token in self.denials:
                hub, reason = self.denials[token]
                raise ResourceUnavailable(hub, reason)
            draft = deepcopy(self.state)
            try:
                result = change(draft)
            except ResourceUnavailable as exc:
                if key:
                    add_daily_event(self.state, "denial", token, exc.hub)
                    self.denials[token] = (exc.hub, str(exc))
                raise
            draft["version"] += 1
            draft["log"].append(dict(actor=actor, action=action, key=key, stamp=iso(now()),
                                     previousVersion=self.state["version"], version=draft["version"]))
            self.state = draft
            if key:
                self.results[token] = deepcopy(result)
            return result

    def refresh_demo_observations(self):
        with self.lock:
            stamp = iso(now())
            self.state["stamp"] = stamp
            for hub in self.state["hubs"]:
                hub["stamp"] = stamp
            self._expire()


def add_daily_event(state, kind, identifier, hub, stamp=None, **data):
    stamp = stamp or iso(now())
    state["dailyLedger"].append(
        dict(kind=kind, id=identifier, hub=hub, stamp=stamp, **data)
    )
    day = time_of(stamp).astimezone(VN).date().isoformat()
    if state["dailyJobs"].get(day, {}).get("status") == "Completed":
        state["dailyJobs"][day] = {"status":"Pending","attempts":0}


class ResourceService:
    @staticmethod
    def free(state, slot, start, end, ignore=None, vehicle=None):
        observed = slot.get("observedVehicle")
        if slot["blocked"] or (observed and observed != vehicle) or any(
            v["space"] == slot["id"] and v["id"] != vehicle for v in state["vehicles"]
        ):
            return False
        return not any(
            r.get("space") == slot["id"] and r["id"] != ignore
            and r["status"] in ("Reserved","Occupied","Scheduled","Charging","Confirmed","InProgress")
            and interval_overlap(start,end,r["start"],r["end"])
            for collection in ("parking","schedule","transfers") for r in state[collection]
        )

    @staticmethod
    def check(state, context, hub, start, end, resource=None, km=0, ignore=None, plug="Type 2",
              vehicle=None, as_of=None):
        as_of = as_of or now()
        if hub:
            selected = by_id(state["hubs"], hub)
            if (as_of - time_of(selected["stamp"])).total_seconds() > 30:
                return dict(status=UNKNOWN, reason="Dữ liệu Hub quá 30 giây.", version=state["version"], choices=[])
        if context == "vehicle":
            charge_holds = {x["vehicle"] for x in state["schedule"] if x["status"] == "Charging"
                            or x["status"] == "Scheduled" and interval_overlap(start,end,x["start"],x["end"])}
            choices = [
                v for v in state["vehicles"] if v["hub"] == hub and v["status"] == "Available"
                and not v["fault"] and v["battery"] >= max(20, 10+2*km)
                and v["id"] not in charge_holds
                and (not resource or v["id"] == resource)
            ]
        else:
            choices = [
                s for s in state["spaces"] if s["hub"] == hub
                and (not resource or s["id"] == resource)
                and ResourceService.free(state,s,start,end,ignore,vehicle)
                and (context != "charge" or (s["port"] and not s["portFault"] and plug == "Type 2"))
            ]
        return dict(status=YES if choices else NO, choices=choices, version=state["version"],
                    reason="" if choices else "Tài nguyên phù hợp không còn khả dụng trong khung giờ.")

    @staticmethod
    def summary(state, as_of=None):
        as_of = as_of or now()
        start, end = iso(as_of), iso(as_of+timedelta(minutes=30))
        items = []
        for h in state["hubs"]:
            slots = [s for s in state["spaces"] if s["hub"] == h["id"] and not s["blocked"]]
            occupied = sum(bool(s.get("observedVehicle")) or any(v["space"] == s["id"] for v in state["vehicles"]) for s in slots)
            free = sum(ResourceService.free(state,s,start,end) for s in slots)
            charging = ResourceService.check(state,"charge",h["id"],start,end,as_of=as_of)
            available = len(ResourceService.check(state,"vehicle",h["id"],start,end,as_of=as_of)["choices"])
            warnings = []
            if slots and (len(slots)-free)/len(slots) >= .9:
                warnings.append("Gần đầy chỗ đỗ")
            if available < h["demand"]:
                warnings.append("Thiếu xe so với nhu cầu 30 phút")
            if (as_of-time_of(h["stamp"])).total_seconds() > 30:
                warnings.append("Dữ liệu cũ")
            items.append(dict(**h, capacity=len(slots), occupied=occupied, held=len(slots)-free-occupied,
                              free=free, charging=len(charging["choices"]), available=available, warnings=warnings))
        return items


class BookingService:
    @staticmethod
    def reserve(s, user, p):
        v = by_id(s["vehicles"],p["vehicle"])
        by_id(s["hubs"],p["destination"])
        km = float(p.get("km", 0))
        need(0 <= km <= 50, "Khoảng cách phải từ 0 đến 50 km.")
        need(not any(b["user"] == user and b["status"] == "Reserved" for b in s["bookings"])
             and not any(t["user"] == user and t["status"] == "Active" for t in s["trips"]),
             "Bạn đang có đơn đặt hiệu lực hoặc chuyến đang chạy.")
        check = ResourceService.check(s,"vehicle",v["hub"],iso(now()),iso(now()+timedelta(minutes=15)),v["id"],km)
        require_resources(check, v["hub"])
        record = dict(id=code("BK",s["bookings"]), user=user, vehicle=v["id"], origin=v["hub"],
                      destination=p["destination"], deadline=iso(now()+timedelta(minutes=15)), status="Reserved")
        s["bookings"].append(record)
        v["status"] = "Reserved"
        return record

    @staticmethod
    def park(s, user, p):
        start, end = time_of(p["start"]), time_of(p["end"])
        need(now()-timedelta(minutes=2) <= start < end, "Khung giờ đỗ không hợp lệ.")
        vehicle = p["vehicle"]
        need(vehicle == "PRIVATE-"+user or any(
            t["user"] == user and t["vehicle"] == vehicle and t["status"] == "Active" for t in s["trips"]
        ), "Xe không thuộc phạm vi sử dụng của tài khoản.")
        check = ResourceService.check(s,"parking",p["hub"],iso(start),iso(end))
        require_resources(check, p["hub"])
        record = dict(id=code("PK",s["parking"]), user=user, vehicle=p["vehicle"],
                      space=check["choices"][0]["id"], hub=p["hub"], start=iso(start), end=iso(end),
                      deadline=iso(min(end,start+timedelta(minutes=15))), status="Reserved")
        s["parking"].append(record)
        return record

    @staticmethod
    def pickup(s, user, p):
        b = by_id(s["bookings"],p["id"])
        need(b["user"] == user and b["status"] == "Reserved", "Đơn đặt không còn hiệu lực hoặc không thuộc tài khoản.")
        v = by_id(s["vehicles"],b["vehicle"])
        need(v["status"] == "Reserved" and v["hub"] == b["origin"] and not v["fault"], "Xe cần được kiểm tra.")
        origin_space = v["space"]
        b["status"], v["status"], v["space"], v["hub"] = "PickedUp", "InUse", "", ""
        for slot in s["spaces"]:
            if slot.get("observedVehicle") == v["id"]:
                slot["observedVehicle"] = ""
        started = now()
        trip = dict(id=code("TR",s["trips"]), user=user, vehicle=v["id"], status="Active",
                    origin=b["origin"], originSpace=origin_space, destination=b["destination"], started=iso(started),
                    expected=iso(started+timedelta(hours=2)))
        s["trips"].append(trip)
        return trip

    @staticmethod
    def give_back(s, user, p):
        t = by_id(s["trips"],p["id"])
        need(t["user"] == user and t["status"] == "Active", "Chuyến đi không hợp lệ.")
        start, end = iso(now()), iso(now()+timedelta(minutes=1))
        observed = next((x for x in s["spaces"] if x.get("observedVehicle") == t["vehicle"]),None)
        if observed:
            need(observed["hub"] == p["hub"] and (not p.get("space") or observed["id"] == p["space"]),
                 "Vị trí trả không khớp quan sát IoT.")
        space = observed["id"] if observed else p.get("space")
        reservation = next((r for r in s["parking"] if r["vehicle"] == t["vehicle"] and r["space"] == space
                            and r["status"] == "Occupied"),None)
        check = ResourceService.check(s,"parking",p["hub"],start,end,space,
                                      ignore=reservation["id"] if reservation else None,vehicle=t["vehicle"])
        require_resources(check, p["hub"])
        v = by_id(s["vehicles"],t["vehicle"])
        need(not any(x["vehicle"] == v["id"] and x["status"] == "Charging" for x in s["schedule"]),
             "Cần kết thúc phiên sạc trước khi trả xe.")
        v.update(hub=p["hub"],space=check["choices"][0]["id"],status="Available",fault=bool(p.get("fault")))
        t.update(status="Completed",returned=iso(now()),returnHub=p["hub"],space=v["space"],
                 late=now()>time_of(t["expected"]))
        add_daily_event(s, "trip", t["id"], t["returnHub"], t["returned"])
        return t


class ChargingService:
    @staticmethod
    def evaluate(s, user, p):
        start, end = time_of(p["start"]), time_of(p["end"])
        need(now()-timedelta(minutes=2) <= start < end, "Giờ có mặt và hạn cần xe không hợp lệ.")
        target = int(p["target"])
        vehicle = p["vehicle"]
        if vehicle.startswith("PRIVATE-"):
            need(vehicle == "PRIVATE-"+user, "Xe cá nhân không thuộc tài khoản.")
            battery, plug, source = int(p["battery"]), p["plug"], "Tự khai"
        else:
            v = by_id(s["vehicles"],vehicle)
            need(any(t["user"] == user and t["vehicle"] == vehicle and t["status"] == "Active" for t in s["trips"]),
                 "Bạn chưa có chuyến đang chạy với xe này.")
            need(not v["fault"], "Xe cần kiểm tra kỹ thuật.")
            battery, plug, source = v["battery"], v["plug"], "Trạng thái xe mẫu"
        need(0 <= battery < target <= 100, "Pin mục tiêu phải cao hơn pin hiện tại và tối đa 100%.")
        need(not any(r["vehicle"] == vehicle and r["status"] in ("Pending","Waiting","Scheduled")
                     for r in s["requests"]), "Xe này đã có một nhu cầu sạc đang mở.")
        assessment = ResourceService.check(s,"charge",p["hub"],iso(start),iso(end),plug=plug)
        need(assessment["status"] != UNKNOWN, assessment["reason"])
        return dict(status=assessment["status"], reason=assessment["reason"], version=assessment["version"],
                    request=dict(user=user,vehicle=vehicle,hub=p["hub"],start=iso(start),end=iso(end),
                                 target=target,battery=battery,plug=plug,source=source,batteryAt=iso(now()),
                                 duration=max(15,ceil((target-battery)/10)*5)))

    @staticmethod
    def submit(s, user, p):
        check = ChargingService.evaluate(s,user,p)
        waiting = check["status"] == NO
        if waiting and not p.get("acceptWait"):
            raise ResourceUnavailable(p["hub"], "Cần xác nhận đồng ý chờ tài nguyên.")
        record = dict(**check["request"],id=code("CR",s["requests"]),created=iso(now()),
                      status="Waiting" if waiting else "Pending")
        s["requests"].append(record)
        return record

    @staticmethod
    def cancel(s, user, p):
        request = by_id([r for r in s["requests"] if r.get("user") == user], p["id"])
        need(request["status"] in ("Pending", "Waiting", "Scheduled"),
             "Không thể hủy nhu cầu sạc ở trạng thái hiện tại.")

        cancelled_at = iso(now())
        for slot in s["schedule"]:
            if slot["request"] == request["id"] and slot["status"] == "Scheduled":
                slot.update(status="Cancelled", cancelledAt=cancelled_at)
        request.update(status="Cancelled", cancelledAt=cancelled_at)
        return request

    @staticmethod
    def auto_requests(s, hub, instant):
        config = s["chargeConfig"]
        open_states = {"Pending", "Waiting", "Scheduled", "Charging"}
        active = {r["vehicle"] for r in s["requests"] if r["status"] in open_states
                  and (r["status"]=="Charging" or time_of(r["end"]) > instant)}
        requests = []
        last = sum(r.get("auto", False) for r in s["requests"])
        for vehicle in s["vehicles"]:
            if (vehicle["hub"] != hub or not vehicle["space"] or vehicle["status"] != "Available"
                    or vehicle["fault"] or vehicle["battery"] >= config["lowThreshold"]
                    or vehicle["id"] in active):
                continue
            battery = vehicle["battery"]
            target = config["autoTarget"]
            deadline = instant + timedelta(minutes=config["autoHorizonMinutes"])
            identifier = f"AUTO-{last + len(requests) + 1:03}"
            requests.append(dict(
                id=identifier, hub=hub, vehicle=vehicle["id"], user="", battery=battery, target=target,
                plug=vehicle["plug"], source="Xe chung pin thấp", auto=True, created=iso(instant),
                start=iso(instant), end=iso(deadline), batteryAt=iso(instant),
                duration=max(15, ceil((target - battery) / 10) * 5), status="Pending",
            ))
        return requests

    @staticmethod
    def build_plan(s, hub, start, end, adjustment=None, include_auto=True):
        start, end, instant = time_of(start), time_of(end), now()
        need(timedelta(0) < end-start <= timedelta(days=7), "Khoảng lập lịch phải nằm trong 7 ngày.")
        need(end > instant, "Khung lập lịch đã kết thúc.")
        hub_info = by_id(s["hubs"], hub)
        need((instant - time_of(hub_info["stamp"])).total_seconds() <= 30, "Dữ liệu Hub quá 30 giây.")
        forecast = deepcopy(s)
        updated = None
        if adjustment:
            old_slot = by_id(forecast["schedule"], adjustment["slot"])
            need(old_slot["status"] == "Scheduled" and time_of(old_slot["end"]) > instant and old_slot["hub"] == hub,
                 "Chỉ điều chỉnh được lượt chưa bắt đầu sạc và còn hiệu lực tại Hub này.")
            request = by_id(forecast["requests"], old_slot["request"])
            new_start, deadline = time_of(adjustment["ready"]), time_of(adjustment["deadline"])
            need(time_of(request["start"]) <= new_start < deadline <= instant + timedelta(days=7),
                 "Thời điểm mới phải từ giờ sẵn sàng, trước hạn và trong 7 ngày.")
            need((deadline - max(new_start, instant)).total_seconds() >= request["duration"] * 60,
                 "Khung giờ mới không đủ thời lượng sạc.")
            request.update(end=iso(deadline), status="Pending")
            updated = dict(slot=old_slot["id"], request=request["id"], start=iso(new_start), end=request["end"])
            old_slot["status"] = "Superseded"
        new_requests = ChargingService.auto_requests(forecast, hub, instant) if include_auto else []
        forecast["requests"].extend(new_requests)
        candidates = [r for r in forecast["requests"]
                      if r["hub"] == hub and r["status"] in ("Pending","Waiting") and time_of(r["end"]) > instant]
        candidates.sort(key=lambda r: ((time_of(r["end"])-instant).total_seconds()-60*r["duration"],
                                       r["battery"],r["created"],r["id"]))
        proposal, waiting = [], []
        for r in candidates:
            point = max(start, time_of(r["start"]), instant)
            if updated and r["id"] == updated["request"]:
                point = max(point,time_of(updated["start"]))
            limit = min(end,time_of(r["end"]))
            duration = timedelta(minutes=r["duration"])
            # Advance minute by minute over valid intervals so non-round hold endpoints are usable.
            while point + duration <= limit:
                finish = point + duration
                a, b = iso(point), iso(finish)
                busy_vehicle = any(
                    x["vehicle"] == r["vehicle"] and x["status"] in ("Scheduled","Charging")
                    and interval_overlap(a,b,x["start"],x["end"]) for x in forecast["schedule"]
                )
                if not busy_vehicle:
                    check = ResourceService.check(
                        forecast,"charge",hub,a,b,plug=r["plug"],vehicle=r["vehicle"],as_of=instant,
                    )
                    if check["choices"]:
                        space = check["choices"][0]
                        slot = dict(id=code("CS",forecast["schedule"]), request=r["id"], vehicle=r["vehicle"],
                                    hub=hub, space=space["id"], port=space["port"], start=a,end=b,
                                    status="Scheduled",waitMinutes=max(0,ceil((point-time_of(r["start"])).total_seconds()/60)))
                        forecast["schedule"].append(slot)
                        proposal.append(slot)
                        break
                point += timedelta(minutes=1)
            else:
                waiting.append(dict(id=r["id"], reason="Không tìm thấy cổng và chỗ phù hợp trước hạn cần xe."))
        can_apply = bool(proposal) and (not updated or any(x["request"] == updated["request"] for x in proposal))
        return dict(proposal=proposal, waiting=waiting, version=s["version"], hub=hub, canApply=can_apply,
                    newRequests=new_requests, adjustment=updated, fromTime=iso(start), toTime=iso(end))

    @staticmethod
    def apply_plan(s, plan):
        need(s["version"] == plan["version"], "Dữ liệu đã thay đổi. Hãy tính lại phương án.")
        need(plan["canApply"], "Chưa có lịch nháp khả thi để áp dụng.")
        adjustment = plan["adjustment"]
        if adjustment:
            existing = by_id(s["schedule"], adjustment["slot"])
            need(existing["status"] == "Scheduled", "Chỉ điều chỉnh được lượt chưa bắt đầu sạc và còn hiệu lực tại Hub này.")
            existing["status"] = "Superseded"
            by_id(s["requests"], adjustment["request"]).update(end=adjustment["end"], status="Pending")
        s["requests"].extend(deepcopy(plan["newRequests"]))
        for slot in plan["proposal"]:
            r = by_id(s["requests"],slot["request"])
            check = ResourceService.check(s,"charge",slot["hub"],slot["start"],slot["end"],slot["space"],
                                          plug=r["plug"],vehicle=r["vehicle"])
            busy_vehicle = any(
                x["vehicle"] == slot["vehicle"] and x["status"] in ("Scheduled","Charging")
                and interval_overlap(slot["start"],slot["end"],x["start"],x["end"]) for x in s["schedule"]
            )
            shared = next((v for v in s["vehicles"] if v["id"] == r["vehicle"]), None)
            unavailable = shared and (shared["fault"] or shared["status"] not in ("Available","InUse"))
            need(r["status"] in ("Pending","Waiting") and check["status"] == YES and not busy_vehicle
                 and not unavailable and time_of(slot["end"]) > now(), "Lịch xung đột. Hãy tính lại.")
            r["status"] = "Scheduled"
            s["schedule"].append(deepcopy(slot))
            notify(s, "ScheduleApplied", r, slot["id"])
        for item in plan["waiting"]:
            by_id(s["requests"],item["id"]).update(status="Waiting", waitingReason=item["reason"])
        history = dict(id=code("PLAN",s["planHistory"]), hub=plan["hub"], stamp=iso(now()),
                       version=s["version"]+1, replaced=adjustment["slot"] if adjustment else None,
                       slots=deepcopy(plan["proposal"]), waiting=deepcopy(plan["waiting"]))
        s["planHistory"].append(history)
        return dict(count=len(plan["proposal"]),slots=plan["proposal"],version=history["version"], planId=history["id"])

class OperationsService:
    @staticmethod
    def report(s, user, p):
        by_id(s["hubs"],p["hub"])
        note = str(p["description"]).strip()
        need(5 <= len(note) <= 300, "Mô tả sự cố cần 5 đến 300 ký tự.")
        target = str(p.get("resource","")).strip()
        item = dict(id=code("IN",s["incidents"]),user=user,hub=p["hub"],resource=target,
                    description=note, status="New",created=iso(now()))
        s["incidents"].append(item)
        add_daily_event(s, "incident", item["id"], item["hub"], item["created"])
        return item

    @staticmethod
    def port_fault(s, slot, source="Operator", stamp=None):
        stamp = stamp or iso(now())
        slot["portFault"] = True
        for sched in s["schedule"]:
            if sched["space"] != slot["id"]:
                continue
            request = by_id(s["requests"],sched["request"])
            if sched["status"] == "Charging":
                session = next(x for x in s["chargingSessions"] if x["slot"] == sched["id"] and x["status"] == "Charging")
                session.update(status="Interrupted", endedAt=stamp, reason="PortFault", faultSource=source)
                sched["status"] = "Interrupted"
                request["status"] = "Pending"
                notify(s, "ChargingInterrupted", request, sched["id"])
            elif sched["status"] == "Scheduled" and time_of(sched["end"]) > time_of(stamp):
                sched["status"] = "Reschedule"
                request["status"] = "Pending"
                notify(s, "ScheduleInvalidated", request, sched["id"])

    @staticmethod
    def resolve(s, p):
        item = by_id(s["incidents"],p["id"])
        action = p["action"]
        allowed = {"New":("Inspecting","Rejected"),"Inspecting":("Closed","Rejected")}
        need(action in allowed.get(item["status"],()),"Bước xử lý sự cố không phù hợp.")
        item["status"] = action
        if action == "Closed" and (p.get("fault") or p.get("repair")):
            for slot in s["spaces"]:
                if slot["port"] and slot["port"] == item["resource"] and slot["hub"] == item["hub"]:
                    if p.get("fault"):
                        OperationsService.port_fault(s,slot, f"Incident:{item['id']}")
                    else:
                        slot["portFault"] = False
            for v in s["vehicles"]:
                if v["id"] == item["resource"]:
                    v["fault"] = bool(p.get("fault"))
        return item

    @staticmethod
    def transfer(s, p):
        if p.get("id"):
            order = by_id(s["transfers"],p["id"])
            v = by_id(s["vehicles"],order["vehicle"])
            if p["action"] == "depart":
                need(order["status"] == "Confirmed" and v["status"] == "TransferHeld","Chưa thể xuất phát.")
                order["originSpace"] = v["space"]
                order["departedAt"] = iso(now())
                order["status"],v["status"],v["hub"],v["space"] = "InProgress","Transferring","",""
            elif p["action"] == "receive":
                need(order["status"] == "InProgress", "Xe chưa xuất phát.")
                slot = by_id(s["spaces"],order["space"])
                # Ignore the destination hold of this order when receiving it.
                need(ResourceService.free(s,slot,iso(now()),iso(now()+timedelta(minutes=1)),order["id"],v["id"]),
                     "Chỗ đích hiện không khả dụng.")
                order["status"],v["status"],v["hub"],v["space"] = "Completed","Available",order["destination"],slot["id"]
            else:
                raise BusinessError("Bước điều chuyển không hợp lệ.")
            return order
        v = by_id(s["vehicles"],p["vehicle"])
        need(v["hub"] and v["hub"] != p["hub"],"Hub đích phải khác Hub nguồn.")
        start, end = iso(now()), iso(now()+timedelta(days=365))
        origin = ResourceService.check(s,"vehicle",v["hub"],start,end,v["id"])
        dest = ResourceService.check(s,"parking",p["hub"],start,end)
        require_resources(origin, v["hub"])
        require_resources(dest, p["hub"])
        item = dict(id=code("TX",s["transfers"]),vehicle=v["id"],origin=v["hub"],destination=p["hub"],
                    space=dest["choices"][0]["id"],start=start,end=end,status="Confirmed")
        s["transfers"].append(item)
        v["status"] = "TransferHeld"
        return item


def notify(s, kind, request, slot):
    s["notifications"].append(dict(
        kind=kind, request=request["id"], slot=slot, user=request.get("user",""), stamp=iso(now()),
    ))


class IoTService:
    KINDS = {
        "heartbeat": "hub", "battery": "battery", "port_fault": "port", "port_status": "port",
        "parking_enter": "occupancy", "parking_exit": "occupancy",
        "charging_started": "charging", "charging_ended": "charging",
    }

    @staticmethod
    def parking(s, p, slot, happened):
        vehicle = str(p["vehicle"])
        place = slot["id"]
        shared = next((v for v in s["vehicles"] if v["id"] == vehicle), None)
        known_private = any(r["vehicle"] == vehicle for r in s["parking"] + s["requests"])
        need(shared or known_private, "Không tìm thấy mã tài nguyên hoặc hồ sơ.")
        observed = slot.get("observedVehicle", "")
        if p["kind"] == "parking_exit":
            departures = [(x.get("originSpace"),x.get("started")) for x in s["trips"]
                          if x["vehicle"] == vehicle and x["status"]=="Active"] + [
                          (x.get("originSpace"),x.get("departedAt")) for x in s["transfers"]
                          if x["vehicle"] == vehicle and x["status"]=="InProgress"]
            confirmed = any(start and origin==place and abs((happened-time_of(start)).total_seconds()) <= 30
                            for origin,start in departures)
            if observed != vehicle and not confirmed:
                return "VehicleMismatch"
            if any(x["space"] == place and x["vehicle"] == vehicle and x["status"] == "Charging"
                   for x in s["schedule"]):
                return "ChargingStillActive"
            slot["observedVehicle"] = ""
            for reservation in s["parking"]:
                if reservation["space"] == place and reservation["vehicle"] == vehicle and reservation["status"] == "Occupied":
                    reservation.update(status="Completed", leftAt=iso(happened))
            return None
        if slot["blocked"] or observed not in ("",vehicle) or any(v["space"] == place and v["id"] != vehicle for v in s["vehicles"]):
            return "SpaceAlreadyOccupied"
        if any(x["id"] != place and x.get("observedVehicle") == vehicle for x in s["spaces"]):
            return "VehicleObservedElsewhere"
        reservation = next((r for r in s["parking"] if r["space"] == place and r["vehicle"] == vehicle
                            and r["status"] == "Reserved" and time_of(r["start"]) <= happened < time_of(r["deadline"])),None)
        commitments = [x for x in s["parking"] + s["schedule"] + s["transfers"]
                       if x["space"] == place and x["status"] in ("Reserved","Occupied","Scheduled","Charging","Confirmed","InProgress")
                       and time_of(x["start"]) <= happened < time_of(x["end"])]
        if any(x["vehicle"] != vehicle for x in commitments):
            return "SpaceHeldForAnotherVehicle"
        if shared and shared["status"] in ("Reserved","TransferHeld","Transferring","InUse") and not (
            shared["space"] == place or reservation or any(x["vehicle"] == vehicle for x in commitments)
        ):
            return "VehicleBusinessState"
        if shared and shared["hub"] and shared["hub"] != slot["hub"]:
            return "VehicleBusinessState"
        if shared and shared["space"] not in ("", place) and shared["hub"] == slot["hub"]:
            # Same-Hub move may be observed with a confirmed charging allocation.
            if not any(x["vehicle"] == vehicle and x.get("port") == slot["port"] for x in commitments):
                return "VehicleBusinessState"
        slot["observedVehicle"] = vehicle
        if reservation:
            reservation.update(status="Occupied", arrivedAt=iso(happened))
        return None

    @staticmethod
    def charging(s, p, slot, happened):
        vehicle = str(p["vehicle"])
        schedule = next((x for x in s["schedule"]
                         if x["space"] == slot["id"] and x["vehicle"] == vehicle
                         and (not p.get("slotId") or x["id"] == p["slotId"])
                         and x["status"] == ("Scheduled" if p["kind"] == "charging_started" else "Charging")
                         and (p["kind"] == "charging_ended" or time_of(x["start"]) <= happened < time_of(x["end"]))),None)
        if not schedule:
            return "NoMatchingSchedule"
        req = by_id(s["requests"],schedule["request"])
        if p["kind"] == "charging_started":
            shared = next((v for v in s["vehicles"] if v["id"] == vehicle),None)
            if shared and (shared["fault"] or shared["status"] not in ("InUse","Available")
                           or (shared["hub"] and shared["hub"] != slot["hub"])):
                return "VehicleBusinessState"
            if slot["portFault"] or slot["blocked"] or any(
                x["status"] == "Charging" and (x["vehicle"] == vehicle or x["space"] == slot["id"])
                for x in s["chargingSessions"]
            ):
                return "ChargingResourceBusy"
            if slot.get("observedVehicle") not in ("",vehicle) or any(
                v["space"] == slot["id"] and v["id"] != vehicle for v in s["vehicles"]
            ) or any(x["id"] != slot["id"] and x.get("observedVehicle") == vehicle for x in s["spaces"]):
                return "SpaceAlreadyOccupied"
            session = dict(id=code("CH",s["chargingSessions"]), slot=schedule["id"], vehicle=vehicle,
                           request=req["id"], hub=slot["hub"], space=slot["id"], port=slot["port"],
                           readyAt=req["start"], startedAt=iso(happened), status="Charging")
            # An actual state event can supply a measured SOC for comparison with self-reported SOC.
            if p.get("battery") is not None:
                session.update(measuredBattery=p["battery"], reportedBattery=req["battery"],
                               batteryDelta=p["battery"]-req["battery"])
            s["chargingSessions"].append(session)
            slot["observedVehicle"] = vehicle
            schedule.update(status="Charging", startedAt=iso(happened))
            req["status"] = "Charging"
            waiting = (happened - time_of(session["readyAt"])).total_seconds() / 60
            add_daily_event(s,"chargeStart",session["id"],slot["hub"],session["startedAt"], waitMinutes=max(0,waiting))
            return None
        session = next(x for x in s["chargingSessions"] if x["slot"] == schedule["id"] and x["status"] == "Charging")
        if happened < time_of(session["startedAt"]):
            return "OlderThanChargingStart"
        session.update(status="Completed",endedAt=iso(happened))
        schedule.update(status="Completed",endedAt=iso(happened))
        req["status"] = "Completed"
        if p.get("battery") is not None:
            session["finalBattery"] = p["battery"]
            shared = next((v for v in s["vehicles"] if v["id"] == vehicle),None)
            if shared:
                shared["battery"] = p["battery"]
        return None

    @staticmethod
    def ingest(s, p, source="gateway"):
        seq = p["sequence"]
        need(type(seq) is int and 1 <= seq <= 2**53-1, "Thứ tự sự kiện phải là số nguyên dương.")
        kind = p["kind"]
        need(kind in IoTService.KINDS, "Loại sự kiện không được hỗ trợ.")
        if kind in ("battery","port_fault","port_status"):
            value = p.get("value")
            need(type(value) is int and 0 <= value <= (100 if kind == "battery" else 1),
                 "Pin phải từ 0 đến 100%." if kind == "battery" else "Trạng thái cổng phải là 0 hoặc 1.")
        else:
            value = p.get("vehicle","")
            if kind != "heartbeat":
                need(isinstance(value,str) and len(value.strip()) > 0, "Thiếu mã phương tiện quan sát.")
        if kind in ("charging_started","charging_ended") and p.get("battery") is not None:
            need(type(p["battery"]) is int and 0 <= p["battery"] <= 100, "Pin phải từ 0 đến 100%.")
        if kind == "heartbeat":
            target = by_id(s["hubs"],p["resource"])
        elif kind == "battery":
            target = by_id(s["vehicles"],p["resource"])
        else:
            target = next((x for x in s["spaces"] if p["resource"] in (x["id"],x["port"]) and p["resource"]),None)
            need(target, "Không tìm thấy mã tài nguyên hoặc hồ sơ.")
            if IoTService.KINDS[kind] in ("charging","port"):
                need(target["port"], "Vị trí không có cổng sạc.")
        instant = now()
        happened = time_of(p["observedAt"]) if p.get("observedAt") else instant
        need(happened <= instant + timedelta(seconds=5), "Thời điểm IoT vượt quá giờ máy chủ.")
        dimension = IoTService.KINDS[kind]
        key = f"{source}:{target['id']}"
        observed_key = f"{target['id']}:{dimension}"
        head = s["observationHeads"].get(key)
        previous = s["observations"].get(observed_key)
        value = int(bool(value)) if kind == "port_status" else 1 if kind == "port_fault" else value
        signal = [kind, value] if dimension in ("charging","occupancy") else value
        hub_id = target["id"] if kind == "heartbeat" else target.get("hub","")
        item = dict(
            id=p["id"], source=source, kind=kind, status="Applied", sequence=seq,
            resource=p["resource"], hub=hub_id, value=value, stamp=iso(instant), observedAt=iso(happened),
        )
        if head and seq <= head["sequence"]:
            item.update(status="Ignored", reason="OldSequence")
        elif (instant-happened).total_seconds() > 30 or (previous and happened < time_of(previous["observedAt"])):
            item.update(status="Ignored", reason="OldTimestamp")
        elif previous and previous["source"] != source and previous["signal"] != signal and (
            happened-time_of(previous["observedAt"])
        ).total_seconds() <= 30:
            item.update(status="Reconcile", reason="ConflictingSource")
        else:
            reason = None
            if kind == "battery":
                if target["status"] == "Transferring":
                    reason = "VehicleBusinessState"
                else:
                    target.update(battery=value,observedSeq=seq)
            elif dimension == "port":
                if value:
                    OperationsService.port_fault(s,target,f"IoTEvent:{source}:{p['id']}",iso(happened))
                target["observedSeq"] = seq
            elif dimension == "occupancy":
                reason = IoTService.parking(s,p,target,happened)
            elif dimension == "charging":
                reason = IoTService.charging(s,p,target,happened)
            if reason:
                item.update(status="Reconcile", reason=reason)
            else:
                s["observations"][observed_key] = dict(source=source,signal=signal,observedAt=iso(happened))
                if hub_id:
                    hub = by_id(s["hubs"],hub_id)
                    hub["stamp"] = iso(max(time_of(hub["stamp"]),happened))
                s["stamp"] = iso(instant)
        if item["status"] != "Ignored" and (not head or seq > head["sequence"]):
            s["observationHeads"][key] = dict(sequence=seq)
        s["events"].append(item)
        return item
