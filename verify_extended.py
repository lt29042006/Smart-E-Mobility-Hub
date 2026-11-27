"""Regression checks for the new features using controlled time and fixture data."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app import ACCOUNTS, ApiController
from insights import DailySummaryService, SimulationService
from services import VN, BusinessError, ResourceUnavailable, ResourceService, TransactionalStore, add_daily_event, iso

T0 = datetime(2026,11,24,9,0,tzinfo=VN)


def rejects(call, part):
    try:
        call()
    except BusinessError as exc:
        assert part in str(exc), str(exc)
    else:
        raise AssertionError(part)


def headers(controller, username):
    data = controller.login({"user":username,"password":"demo123"})
    return {"Authorization":"Bearer "+data["token"]}


def check_controller():
    with patch("services.now",return_value=T0), patch("insights.now",return_value=T0), patch("app.now",return_value=T0):
        c = ApiController(demo_af04=True)
        student, operator = headers(c,"student"), headers(c,"operator")
        def call(action, body=None, auth=operator):
            return c.route(action,body or {},auth)
        first_revision = c.store.read()["version"]
        assert call("prefs",{"theme":"dark","language":"en"},student)["language"]=="en"
        assert call("state",auth=headers(c,"student"))["pref"]=={"theme":"dark","language":"en"}
        assert call("state")["pref"]=={} and c.store.read()["version"]==first_revision
        assert call("daily",{"hub":"MET","day":"2026-11-23"})["summary"]["averageWait"]==6
        rejects(lambda:call("daily",{"hub":"MET","day":"2026-11-23"},student),"Operator")
        req = dict(vehicle="PRIVATE-student",hub="BKH",start=iso(T0+timedelta(minutes=5)),
                   end=iso(T0+timedelta(minutes=90)),battery=25,target=65,plug="Type 2")
        assert call("charge-check",req,student)["status"]=="Có khả dụng"
        assert not c.store.read()["requests"]
        student_need = call("charge",{**req,"key":"r"},student)
        assert call("charge",{**req,"key":"r"},student)==student_need
        fields = dict(hub="BKH",start=iso(T0),end=iso(T0+timedelta(hours=2)))
        draft = call("build",fields)
        assert {s["vehicle"] for s in draft["proposal"]}=={"EB-202","PRIVATE-student"}, draft
        assert not c.store.read()["schedule"] and len(c.store.read()["requests"])==1
        committed = call("apply",{"id":draft["id"],"key":"apply"})
        assert committed["count"]==2
        assert call("apply",{"id":draft["id"],"key":"apply"})==committed
        old = next(x for x in committed["slots"] if x["vehicle"]=="PRIVATE-student")
        adjusted = dict(slot=old["id"],ready=iso(T0+timedelta(minutes=22)),deadline=iso(T0+timedelta(minutes=80)))
        revising = call("build",{**fields,"adjustment":adjusted})
        assert revising["canApply"] and revising["proposal"][0]["start"]==adjusted["ready"]
        assert c.store.read()["schedule"][0]["status"]=="Scheduled"
        call("discard-plan",{"id":revising["id"]})
        rejects(lambda:call("apply",{"id":revising["id"]}),"không còn hiệu lực")
        revising = call("build",{**fields,"adjustment":adjusted})
        iot = {"X-Demo-IoT":"gateway-local"}
        def evt(identifier, seq, kind, resource, extra=None, auth=iot):
            return call("ingest",{"id":identifier,"sequence":seq,"kind":kind,"resource":resource,**(extra or {})},auth)
        fresh = evt("battery",10,"battery","EB-101",{"value":83})
        assert evt("battery",11,"battery","EB-101",{"value":99})==fresh
        older = evt("old",9,"battery","EB-101",{"value":71})
        assert older["status"]=="Ignored"
        assert evt("old-time",11,"battery","EB-101",{"value":71,"observedAt":iso(T0-timedelta(seconds=31))})["reason"]=="OldTimestamp"
        secondary = {"X-IoT-Source":"gateway-secondary","X-Demo-IoT":"gateway-secondary-local"}
        assert evt("conflict",1,"battery","EB-101",{"value":30},secondary)["reason"]=="ConflictingSource"
        assert c.store.read()["vehicles"][0]["battery"]==83
        rejects(lambda:call("apply",{"id":revising["id"]}),"Dữ liệu đã thay đổi")
        assert next(x for x in c.store.read()["schedule"] if x["id"]==old["id"])["status"]=="Scheduled"
        replacement = call("build",{**fields,"adjustment":adjusted})
        new = call("apply",{"id":replacement["id"],"key":"replacement"})["slots"][0]
        assert next(x for x in c.store.read()["schedule"] if x["id"]==old["id"])["status"]=="Superseded"
        assert c.store.read()["planHistory"][-1]["replaced"]==old["id"]
        with patch("services.now",return_value=T0+timedelta(minutes=22)):
            start_evt = evt("begin",1,"charging_started",new["port"],{"vehicle":"PRIVATE-student","battery":24,"slotId":new["id"]})
            assert start_evt["status"]=="Applied",start_evt
            assert c.store.read()["chargingSessions"][0]["batteryDelta"]==-1
            assert c.store.read()["dailyLedger"][-1]["waitMinutes"]==17
            rejects(lambda:call("build",{**fields,"adjustment":{**adjusted,"slot":new["id"]}}),"điều chỉnh")
            rejects(lambda:call("charge-cancel",{"id":student_need["id"]},student),"Không thể hủy")
            ended = evt("end",2,"charging_ended",new["port"],{"vehicle":"PRIVATE-student","battery":66})
            assert ended["status"]=="Applied"
            assert c.store.read()["chargingSessions"][0]["status"]=="Completed"
            rejects(lambda:call("charge-cancel",{"id":student_need["id"]},student),"Không thể hủy")
            assert evt("leave-charge",3,"parking_exit",new["space"],{"vehicle":"PRIVATE-student"})["status"]=="Applied"
            auto = next(x for x in committed["slots"] if x["vehicle"]=="EB-202")
            assert evt("begin-auto",1,"charging_started",auto["port"],{"vehicle":auto["vehicle"]})["status"]=="Applied"
            fault = evt("fault",2,"port_fault",auto["port"],{"value":1})
            assert fault["status"]=="Applied"
            assert evt("fault",3,"port_fault",auto["port"],{"value":1})==fault
            assert evt("port-ok",3,"port_status",auto["port"],{"value":0})["status"]=="Applied"
            state = c.store.read()
            assert next(x for x in state["spaces"] if x["id"]==auto["space"])["portFault"]
            assert state["chargingSessions"][1]["status"]=="Interrupted"
            assert len([n for n in state["notifications"] if n["kind"]=="ChargingInterrupted"])==1
        with patch("services.now",return_value=T0+timedelta(minutes=1)):
            assert evt("heartbeat",1,"heartbeat","KTX")["status"]=="Applied"
            order=call("park",dict(vehicle="PRIVATE-student",hub="KTX",start=iso(T0),end=iso(T0+timedelta(hours=1)),key="park"),student)
            arrival=evt("arrive",1,"parking_enter",order["space"],{"vehicle":"PRIVATE-student"})
            assert arrival["status"]=="Applied",arrival
            assert c.store.read()["parking"][0]["status"]=="Occupied"
            assert evt("leave",2,"parking_exit",order["space"],{"vehicle":"PRIVATE-student"})["status"]=="Applied"
            assert c.store.read()["parking"][0]["status"]=="Completed"
        before = c.store.read()
        capture = call("simulation-snapshot")
        names = ["demand","full_parking","charge_demand","port_failure","concentration"]
        for mode in names:
            result = call("simulate",{"snapshotId":capture["id"],"hub":"MET","minutes":30,
                       "changes":[{"kind":mode,"amount":1}],"key":mode})
            assert result["changes"]=={mode:1} and result["baselineMetrics"]!=result["scenarioMetrics"],mode
        combo = {"snapshotId":capture["id"],"hub":"MET","minutes":45,"changes":[{"kind":mode,"amount":1} for mode in names]}
        res = call("simulate",{**combo,"key":"combo"})
        assert call("simulate",{**combo,"key":"combo"})==res
        another=call("simulate",{**combo,"key":"combo2"})
        assert another["id"]!=res["id"] and another["scenarioMetrics"]==res["scenarioMetrics"]
        assert before==c.store.read()
        c.worker.shutdown()


def check_charging_cancellation():
    with patch("services.now", return_value=T0), patch.dict(ACCOUNTS, {"student2": ("Student", "Student 2")}):
        controller = ApiController()
        student = headers(controller, "student")
        operator = headers(controller, "operator")
        other_student = headers(controller, "student2")
        route = controller.route
        form = dict(vehicle="PRIVATE-student", hub="BKH", start=iso(T0+timedelta(minutes=5)),
                    end=iso(T0+timedelta(minutes=90)), battery=25, target=65, plug="Type 2")
        scope = dict(hub="BKH", start=iso(T0), end=iso(T0+timedelta(hours=2)))

        first = route("charge", form, student)
        draft = route("build", scope, operator)
        cancel_first = dict(id=first["id"], key="cancel-first")
        rejects(lambda: route("charge-cancel", cancel_first, other_student), "Không tìm thấy")
        rejects(lambda: route("charge-cancel", cancel_first, operator), "Student")
        cancelled = route("charge-cancel", cancel_first, student)
        assert cancelled["status"] == "Cancelled" and cancelled["cancelledAt"] == iso(T0)
        state = controller.store.read()
        assert route("charge-cancel", cancel_first, student) == cancelled
        assert controller.store.read() == state
        rejects(lambda: route("apply", {"id":draft["id"]}, operator), "Dữ liệu đã thay đổi")
        assert not controller.store.read()["schedule"]
        assert first["id"] not in {slot["request"] for slot in route("build", scope, operator)["proposal"]}

        waiting = route("charge", {**form, "plug":"Khác", "acceptWait":True}, student)
        assert waiting["status"] == "Waiting"
        assert route("charge-cancel", {"id":waiting["id"]}, student)["status"] == "Cancelled"
        replacement = route("charge", form, student)
        assert replacement["id"] != first["id"] and replacement["status"] == "Pending"
        draft = route("build", scope, operator)
        applied = route("apply", {"id":draft["id"]}, operator)
        personal = next(slot for slot in applied["slots"] if slot["request"] == replacement["id"])
        shared = next(slot for slot in applied["slots"] if slot["vehicle"] == "EB-202")
        before = controller.store.read()
        interval = (personal["start"], personal["end"], personal["space"])
        assert ResourceService.check(before, "charge", "BKH", *interval)["status"] == "Không khả dụng"
        assert route("charge-cancel", {"id":replacement["id"]}, student)["status"] == "Cancelled"
        after = controller.store.read()
        assert ResourceService.check(after, "charge", "BKH", *interval)["status"] == "Có khả dụng"
        assert after["planHistory"] == before["planHistory"] and after["spaces"] == before["spaces"]
        assert next(slot for slot in after["schedule"] if slot["id"] == personal["id"])["status"] == "Cancelled"
        assert next(slot for slot in after["schedule"] if slot["id"] == shared["id"]) == shared
        controller.worker.shutdown()


def check_daily_jobs():
    with patch("services.now",return_value=T0):
        store=TransactionalStore()
        state=store.state
        day=T0.date()
        for kind,count in (("trip",3),("incident",1),("denial",2)):
            for i in range(count):
                add_daily_event(state,kind,f"{kind}-{i}","MET",iso(T0))
        add_daily_event(state,"denial","denial-0","MET",iso(T0))
        for i,wait in enumerate((4,8)):
            add_daily_event(state,"chargeStart",f"c-{i}","MET",iso(T0),waitMinutes=wait)
        midnight=T0.replace(hour=0)+timedelta(days=1)
        add_daily_event(state,"trip","last-minute","KTX",iso(midnight-timedelta(microseconds=1)))
        add_daily_event(state,"trip","following-day","KTX",iso(midnight))
        DailySummaryService.tick(store,midnight)
        report=state["dailyReports"][day.isoformat()]
        met, _, ktx = report
        assert (met["trips"], met["denials"],met["averageWait"],met["incidents"])==(3,2,6,1)
        assert ktx["trips"]==1 and met["completeness"]=="Partial"
        DailySummaryService.tick(store,midnight)
        assert state["dailyReports"][day.isoformat()]==report
        add_daily_event(state,"trip","late-accepted","MET",iso(midnight-timedelta(seconds=2)))
        DailySummaryService.tick(store,midnight+timedelta(seconds=5))
        assert state["dailyReports"][day.isoformat()][0]["trips"]==4
        tomorrow=midnight+timedelta(days=1)
        with patch.object(DailySummaryService,"summarize",side_effect=ValueError("test failure")):
            for minute in (0,1,6,21):
                DailySummaryService.tick(store,tomorrow+timedelta(minutes=minute))
        assert state["dailyJobs"][midnight.date().isoformat()]["status"]=="Failed"
        assert midnight.date().isoformat() not in state["dailyReports"]


def check_denial_and_booking():
    with patch("services.now",return_value=T0):
        from concurrent.futures import ThreadPoolExecutor
        from services import BookingService
        store=TransactionalStore()
        def reserve(i):
            try:
                return store.commit(str(i),"reserve",str(i),lambda s:BookingService.reserve(
                    s,str(i),{"vehicle":"EB-101","destination":"BKH","km":4}))
            except ResourceUnavailable:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(reserve,range(12)))
        assert sum(bool(x) for x in results)==1
        assert len([e for e in store.read()["dailyLedger"] if e["kind"]=="denial"])==11
        losing=next(i for i,x in enumerate(results) if x is None)
        assert reserve(losing) is None
        assert len([e for e in store.read()["dailyLedger"] if e["kind"]=="denial"])==11


if __name__=="__main__":
    check_controller()
    check_charging_cancellation()
    check_daily_jobs()
    check_denial_and_booking()
    print("PASS: account preferences, charging draft/adjust/commit/cancel, conflicts and version history, IoT events, all five What-if scenarios and replay, AF04 midnight/dedup/retry, competing bookings.")
