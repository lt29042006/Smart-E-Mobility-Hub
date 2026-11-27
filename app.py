"""Run with Python 3.10+ and open http://127.0.0.1:8080."""
import argparse
import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic

from insights import DailySummaryService, SimulationService
from services import (
    BookingService, BusinessError, ChargingService, IoTService, OperationsService,
    ResourceService, TransactionalStore, by_id, iso, need, now,
)

WEB = Path(__file__).with_name("web")
ACCOUNTS = {"student": ("Student", "Sinh viên mẫu"), "operator": ("Operator", "Vận hành mẫu")}
IOT_SOURCES = {"gateway":"gateway-local", "gateway-secondary":"gateway-secondary-local"}
SIM_SNAPSHOT_KEYS = ("version","hubs","spaces","vehicles","requests","schedule","transfers","parking")


class ApiController:
    def __init__(self, *, external_iot=False, demo_af04=False):
        self.store = TransactionalStore()
        self.external_iot = external_iot
        if demo_af04:
            DailySummaryService.demo_data(self.store.state)
            DailySummaryService.tick(self.store)
        self.guard = Lock()
        self.sessions = {}
        self.plans = {}
        self.simulations = []
        self.simulation_snapshots = OrderedDict()
        self.simulation_requests = {}
        self.simulation_capacity = BoundedSemaphore(2)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="what-if")
        self.stopping = Event()

    def tick(self):
        while not self.stopping.wait(5):
            if not self.external_iot:
                self.store.refresh_demo_observations()
            else:
                with self.store.lock:
                    self.store._expire()
            DailySummaryService.tick(self.store)

    def capture_simulation(self):
        state = self.store.read()
        snapshot = {key:state[key] for key in SIM_SNAPSHOT_KEYS}
        snapshot["capturedAt"] = iso(now())
        identifier = token_urlsafe(12)
        with self.guard:
            self.simulation_snapshots[identifier] = snapshot
            if len(self.simulation_snapshots) > 20:
                self.simulation_snapshots.popitem(last=False)
        return identifier,snapshot

    def simulate(self, user, p):
        key = str(p.get("key",""))
        request_id = (user,key) if key else None
        if request_id:
            with self.guard:
                prior = self.simulation_requests.get(request_id)
            if prior is not None:
                return self.wait_for_simulation(prior)
        snapshot_id = p.get("snapshotId")
        if snapshot_id:
            with self.guard:
                snapshot = self.simulation_snapshots.get(snapshot_id)
            need(snapshot is not None,"Bản chụp đã hết hiệu lực. Hãy chụp lại dữ liệu.")
        else:
            snapshot_id,snapshot = self.capture_simulation()
        minutes = p.get("minutes",30)
        changes = p.get("changes")
        extra = int(p.get("extra",0))
        hub = str(p["hub"])
        by_id(snapshot["hubs"],hub)
        with self.guard:
            future = self.simulation_requests.get(request_id) if request_id else None
            if future is None:
                need(self.simulation_capacity.acquire(blocking=False),"Máy mô phỏng đang bận. Hãy thử lại.")
                future = self.worker.submit(self.run_simulation,snapshot_id,snapshot,hub,extra,minutes,changes,monotonic()+10)
                future.add_done_callback(lambda _: self.simulation_capacity.release())
                if request_id:
                    self.simulation_requests[request_id] = future
        return self.wait_for_simulation(future)

    def wait_for_simulation(self, future):
        try:
            return future.result(timeout=10)
        except TimeoutError:
            future.cancel()
            raise

    def run_simulation(self, snapshot_id, snapshot, hub, extra, minutes, changes, deadline):
        if monotonic() >= deadline:
            raise TimeoutError
        result = SimulationService.run(snapshot,hub,extra,minutes=minutes,changes=changes)
        with self.guard:
            if monotonic() >= deadline:
                raise TimeoutError
            result.update(id=f"SIM-{len(self.simulations)+1:03}",snapshotId=snapshot_id)
            self.simulations.append(result)
        return result

    def login(self, p):
        user = str(p.get("user",""))
        need(user in ACCOUNTS and compare_digest(str(p.get("password","")),"demo123"),"Sai tài khoản hoặc mật khẩu demo.")
        token = token_urlsafe(24)
        with self.guard:
            self.sessions[token] = user
        return dict(token=token,user=user,role=ACCOUNTS[user][0],name=ACCOUNTS[user][1])

    def route(self, action, p, headers):
        if action == "login":
            return self.login(p)
        if action == "ingest":
            source = headers.get("X-IoT-Source","gateway")
            need(source in IOT_SOURCES and compare_digest(headers.get("X-Demo-IoT",""),IOT_SOURCES[source]),
                 "Nguồn IoT mô phỏng chưa được xác thực.")
            key = str(p.get("id","")).strip()
            need(1 <= len(key) <= 40,"Thiếu mã sự kiện.")
            return self.store.commit(source,"ingest",key,lambda s:IoTService.ingest(s,{**p,"id":key},source))
        token = headers.get("Authorization","").removeprefix("Bearer ")
        with self.guard:
            user = self.sessions.get(token)
        need(user,"Phiên chưa đăng nhập.")
        role = ACCOUNTS[user][0]
        if action == "logout":
            with self.guard:
                self.sessions.pop(token,None)
                self.plans.pop(token,None)
            return dict(ok=True)
        if action == "state":
            state = self.store.read()
            state["summary"] = ResourceService.summary(state)
            state["pref"] = state["prefs"].get(user, {})
            del state["prefs"]
            for key in ("dailyLedger","dailyReports","dailyJobs","observations","observationHeads"):
                state.pop(key,None)
            if role == "Student":
                for key in ("bookings","parking","trips","requests","incidents","notifications"):
                    state[key] = [r for r in state[key] if r.get("user") == user]
                for key in ("events","log","schedule","transfers","planHistory","chargingSessions"):
                    state.pop(key, None)
            return state
        if action == "prefs":
            with self.store.lock:
                prefs = self.store.state["prefs"]
                previous = prefs.get(user,{})
                theme = p.get("theme",previous.get("theme","light"))
                language = p.get("language",previous.get("language","vi"))
                need(theme in ("light","dark") and language in ("vi","en"),"Giao diện chưa được hỗ trợ.")
                prefs[user] = {"theme":theme,"language":language}
                return dict(prefs[user])
        student = {
            "reserve": BookingService.reserve, "park": BookingService.park,
            "pickup": BookingService.pickup, "return": BookingService.give_back,
            "charge": ChargingService.submit, "charge-cancel": ChargingService.cancel,
            "report": OperationsService.report,
        }
        if action in student or action == "charge-check":
            need(role == "Student","Chức năng dành cho Student.")
            if action == "charge-check":
                return ChargingService.evaluate(self.store.read(),user,p)
            return self.store.commit(user,action,p.get("key"),lambda s:student[action](s,user,p))
        need(role == "Operator","Chức năng dành cho Operator.")
        if action == "daily":
            day = date.fromisoformat(p["day"]).isoformat()
            with self.store.lock:
                state = self.store.state
                by_id(state["hubs"],p["hub"])
                rows = state["dailyReports"].get(day,[])
                return dict(summary=next((r for r in rows if r["hub"] == p["hub"]),None),
                            job=state["dailyJobs"].get(day,{}).get("status","Unavailable"))
        if action == "build":
            state = self.store.read()
            adjustment = p.get("adjustment")
            need(adjustment is None or isinstance(adjustment,dict),"Dữ liệu biểu mẫu không hợp lệ.")
            plan = ChargingService.build_plan(state,p["hub"],p["start"],p["end"],adjustment)
            plan_id = token_urlsafe(12)
            with self.guard:
                self.plans[token] = (plan_id,plan)
            return dict(**plan,id=plan_id)
        if action == "apply":
            with self.guard:
                draft = self.plans.get(token)
            need(draft and draft[0] == p["id"],"Phương án nháp không còn hiệu lực.")
            return self.store.commit(user,action,p.get("key"),lambda s:ChargingService.apply_plan(s,draft[1]))
        if action == "discard-plan":
            with self.guard:
                if self.plans.get(token, (None,))[0] == p.get("id"):
                    self.plans.pop(token,None)
            return dict(ok=True)
        if action == "simulation-snapshot":
            identifier,snapshot = self.capture_simulation()
            return dict(id=identifier, version=snapshot["version"], capturedAt=snapshot["capturedAt"])
        if action == "simulation-history":
            with self.guard:
                return list(reversed(self.simulations))
        if action == "simulate":
            return self.simulate(user,p)
        operator = {"transfer":OperationsService.transfer,"resolve":OperationsService.resolve}
        need(action in operator,"Chức năng không tồn tại.")
        return self.store.commit(user,action,p.get("key"),lambda s:operator[action](s,p))


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, controller, **kwargs):
        self.controller = controller
        super().__init__(*args, **kwargs)

    def reply(self, status, payload, mime):
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        paths = {"/":("index.html","text/html"),"/app.js":("app.js","text/javascript"),
                 "/app.css":("app.css","text/css"),"/i18n.js":("i18n.js","text/javascript")}
        if self.path not in paths:
            self.send_error(404)
            return
        filename, mime = paths[self.path]
        self.reply(200,(WEB/filename).read_bytes(),mime+"; charset=utf-8")

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length","0"))
            need(0 < length <= 16_384,"Kích thước dữ liệu không hợp lệ.")
            params = json.loads(self.rfile.read(length))
            need(isinstance(params,dict),"Yêu cầu phải là đối tượng JSON.")
            data = self.controller.route(self.path.removeprefix("/api/"),params,self.headers)
            response, status = {"data":data}, 200
        except BusinessError as exc:
            response, status = {"error":str(exc)}, 400
        except (ValueError, KeyError, TypeError):
            response, status = {"error":"Dữ liệu biểu mẫu không hợp lệ."}, 400
        except TimeoutError:
            response, status = {"error":"Mô phỏng quá thời hạn."}, 503
        self.reply(status,json.dumps(response,ensure_ascii=False).encode(),"application/json; charset=utf-8")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--port",type=int,default=int(__import__("os").environ.get("PORT", 8080)))
    args.add_argument("--demo-af04",action="store_true",help="Include a labeled sample AF04 day for demonstration.")
    args.add_argument("--external-iot",action="store_true",help="Only refresh Hub data when a gateway event arrives.")
    config = args.parse_args()
    controller = ApiController(external_iot=config.external_iot,demo_af04=config.demo_af04)
    host = "0.0.0.0" if __import__("os").environ.get("RENDER") else "127.0.0.1"
    with ThreadingHTTPServer((host,config.port),partial(Handler,controller=controller)) as server:
        Thread(target=controller.tick,daemon=True).start()
        print(f"Smart E-Mobility Hub • http://127.0.0.1:{config.port}",flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            controller.stopping.set()
            controller.worker.shutdown(wait=True,cancel_futures=True)


if __name__ == "__main__":
    main()
