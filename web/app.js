"use strict";

const root = document.querySelector("#app");
const auth = { session: null, state: null };
let page = "search", flash = "", error = false, draft = null, evaluation = null, simulation = null;
let selectedHub = "", minBattery = 20;
let daily = null, editingSlot = "", snapshot = null, simulationInput = null, simulationHistory = [];
const guestKey = "semh-display";
let guestPrefs = {theme:"light",language:"vi"};
try {
  const stored = JSON.parse(localStorage.getItem(guestKey));
  if(stored?.language==="en") guestPrefs.language = "en";
  if(stored?.theme==="dark") guestPrefs.theme = "dark";
} catch (_) {}
const displayPrefs = () => ({...guestPrefs,...auth.state?.pref});
const t = value => displayPrefs().language==="en" && Object.hasOwn(EN,value) ? EN[value] : value;
const locale = () => displayPrefs().language==="en" ? "en-GB" : "vi-VN";
const saveGuest = prefs => {
  guestPrefs = prefs;
  try { localStorage.setItem(guestKey, JSON.stringify(prefs)); } catch (_) {}
};
const labels = {
  Available:"Có thể sử dụng", Reserved:"Đã giữ", Expired:"Hết hạn", PickedUp:"Đã nhận xe",
  Active:"Đang đi", Completed:"Hoàn tất", Pending:"Chờ lập lịch", Waiting:"Chờ tài nguyên", Cancelled:"Đã hủy",
  Scheduled:"Đã lên lịch", Confirmed:"Đã xác nhận", InProgress:"Đang thực hiện",
  New:"Mới", Inspecting:"Đang xử lý", Closed:"Đã đóng", Rejected:"Bác bỏ",
  Reschedule:"Chờ lập lại", InUse:"Đang sử dụng", TransferHeld:"Giữ điều chuyển", Transferring:"Đang điều chuyển",
  Occupied:"Đã vào chỗ", Charging:"Đang sạc", Interrupted:"Gián đoạn", Applied:"Đã áp dụng",
  Ignored:"Đã bỏ qua", Reconcile:"Cần đối soát", Complete:"Đủ dữ liệu", Partial:"Dữ liệu không đầy đủ", Demo:"Dữ liệu minh họa"
};
const strings = {
  search:["Tìm Hub và phương tiện","Xem pin, chỗ đỗ và cổng sạc trước khi đặt xe."],
  trips:["Chuyến đi của tôi","Theo dõi đơn đặt, nhận xe và xác nhận trả xe."],
  parking:["Đặt chỗ đỗ","Chọn Hub và thời gian cần đỗ."],
  charging:["Đăng ký nhu cầu sạc","Kiểm tra sơ bộ cổng sạc và chỗ đỗ tương ứng trước khi gửi yêu cầu."],
  report:["Báo cáo sự cố","Gửi mô tả để bộ phận vận hành kiểm tra."],
  monitor:["Giám sát mạng lưới Hub","Theo dõi xe, chỗ đỗ và nhu cầu 30 phút tại từng Hub."],
  incidents:["Xử lý sự cố","Kiểm tra và cập nhật hồ sơ sự cố do Student gửi."],
  transfers:["Điều chuyển phương tiện","Lập lệnh trước khi xác nhận xe rời và đến Hub."],
  schedule:["Lập lịch sạc thông minh","Tính phương án theo thời gian có mặt, hạn cần xe và pin."],
  whatif:["Mô phỏng What-if","So sánh vận hành cơ sở và tình huống giả định trên cùng bản chụp."],
  preferences:["Tùy chỉnh giao diện","Lưu ngôn ngữ và chế độ hiển thị theo tài khoản."]
};
const nav = {
  Student:[["search","Tìm Hub"],["trips","Chuyến đi"],["parking","Chỗ đỗ"],["charging","Nhu cầu sạc"],["report","Báo sự cố"]],
  Operator:[["monitor","Giám sát"],["incidents","Sự cố"],["transfers","Điều chuyển"],["schedule","Lập lịch sạc"],["whatif","What-if"]]
};
const safe = value => String(value ?? "").replace(/[&<>"']/g, mark => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[mark]);
const status = value => t(labels[value] || value);
const badge = (value, warning=false) => `<span class="pill${warning?" warn":""}">${safe(t(value))}</span>`;
const btn = (label,action,value="",secondary=false) => `<button type="button" class="button${secondary?" secondary":""}" data-action="${safe(action)}" data-value="${safe(value)}">${safe(t(label))}</button>`;
const submit = label => `<button class="button" type="submit">${safe(t(label))}</button>`;
const field = (label, widget) => `<label class="field">${safe(t(label))}${widget}</label>`;
const input = (name,value="",type="text",extra="") => `<input name="${name}" value="${safe(value)}" type="${type}" ${extra} required>`;
const optional = name => `<input name="${name}" maxlength="35">`;
const select = (name,opts,current="") => `<select name="${name}">${opts.map(([k,l])=>`<option value="${safe(k)}" ${k===current?"selected":""}>${safe(t(l))}</option>`).join("")}</select>`;
const hubOpts = () => auth.state.hubs.map(h=>[h.id,h.name]);
const hubName = id => auth.state.hubs.find(h=>h.id===id)?.name || id;
const vehicleOpts = () => auth.state.vehicles.filter(v=>v.status==="Available" && !v.fault).map(v=>[v.id,`${v.id} · ${hubName(v.hub)}`]);
const time = value => new Intl.DateTimeFormat(locale(),{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Ho_Chi_Minh"}).format(new Date(value));
const dateTime = value => new Intl.DateTimeFormat(locale(),{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Ho_Chi_Minh"}).format(new Date(value));
const localInput = (value, roundUp=false) => {
  const epoch = new Date(value).getTime();
  return new Date((roundUp ? Math.ceil(epoch/60000)*60000 : epoch)+420*60000).toISOString().slice(0,16);
};
const formTime = offset => localInput(Date.now()+offset*60000);
const localDate = offset => formTime(offset).slice(0,10);
const moneyless = (s,value) => `<div><strong>${safe(value)}</strong><span>${safe(t(s))}</span></div>`;
const empty = (message="Chưa có dữ liệu.") => `<p class="empty">${safe(t(message))}</p>`;
const card = (title, body) => `<section class="card"><h2>${safe(t(title))}</h2>${body}</section>`;
const actions = html => `<div class="buttons">${html}</div>`;
const form = (action,fields,buttons) => `<form data-form="${action}"><div class="fields">${fields}</div>${actions(buttons)}</form>`;

async function api(action,payload={}) {
  const headers = {"Content-Type":"application/json"};
  if(auth.session) headers.Authorization = "Bearer "+auth.session.token;
  const response = await fetch("/api/"+action,{method:"POST",headers,body:JSON.stringify(payload)});
  const body = await response.json();
  if(!response.ok) throw new Error(body.error);
  return body.data;
}
function notice(message,bad=false) { flash=message; error=bad; }
function clear() { flash=""; error=false; }
function banner() { return flash ? `<p role="status" class="notice${error?" negative":""}">${safe(t(flash))}</p>` : ""; }
function key() { return crypto.randomUUID(); }
function bar() {
  const prefs = displayPrefs();
  return `<header class="top"><div class="wrap bar"><div class="brand"><span class="mark" aria-hidden="true">⚡</span>Smart E-Mobility Hub</div>
  <div class="identity">${btn(prefs.language==="vi"?"English":"Tiếng Việt","quick-language","",true)}
  ${auth.session ? `<span>${safe(t(auth.session.name))} · ${auth.session.role}</span>${btn("Đăng xuất","logout","",true)}`
    : btn(prefs.theme==="light"?"Tối":"Sáng","quick-theme","",true)}</div></div></header>`;
}
function login() {
  root.innerHTML = bar()+`<section class="auth"><div class="card"><h1>${t("Đăng nhập")}</h1><p class="muted">${t("Hệ thống điều phối phương tiện điện · Khu đô thị ĐHQG-HCM")}</p>
  <form data-form="login">${banner()}${field("Tài khoản",input("user","","text",'autocomplete="username"'))}
  ${field("Mật khẩu",input("password","","password",'autocomplete="current-password"'))}${submit("Đăng nhập")}</form>
  <details><summary>${t("Tài khoản dữ liệu mẫu")}</summary><p class="bodycopy">Student: student / demo123<br>Operator: operator / demo123</p></details></div></section>`;
}
async function refresh() { auth.state = await api("state"); }
function hubs() {
  return `<div class="grid three">${auth.state.summary.map(h=>`<section class="card"><h3>${safe(h.name)}</h3>
  <p class="muted">${safe(h.location)}</p><div class="summary">${moneyless("Xe phù hợp",h.available)}${moneyless("Chỗ trống",h.free)}${moneyless("Cổng khả dụng",h.charging)}</div>
  <div class="pills">${h.warnings.map(x=>badge(x,true)).join("") || badge("Hoạt động bình thường")}</div>
    <p class="mini">${t("Đang đỗ")} ${h.occupied}/${h.capacity} · ${t("Giữ trước")} ${h.held} · ${t("Cập nhật")} ${time(h.stamp)}</p></section>`).join("")}</div>`;
}
function search() {
  const filter = form("filter",field("Hub xuất phát",select("hub",[["","Toàn mạng"],...hubOpts()],selectedHub))
      + field("Mức pin tối thiểu (%)",input("battery",minBattery,"number",'min="20" max="100"')),submit("Lọc phương tiện"));
  const vehicles = auth.state.vehicles.filter(v=>v.status==="Available" && !v.fault && v.battery>=minBattery && (!selectedHub || v.hub===selectedHub)
    && !auth.state.summary.find(h=>h.id===v.hub)?.warnings.includes("Dữ liệu cũ"));
  const list = vehicles.map(v=>`<div class="row"><div><strong>${safe(v.id)}</strong><p class="muted">${safe(t(v.kind))} · ${safe(hubName(v.hub))}</p></div>
  ${badge(v.battery+"% "+t("pin"))}${btn("Chọn xe","choose",v.id,true)}</div>`).join("");
  return hubs()+card("Tìm theo hành trình",filter)+card("Xe dùng chung phù hợp",list||empty());
}
function reserve() {
  const v = auth.state.vehicles.find(x=>x.id===selectedHub);
  if(!v) return empty("Xe đã rời danh sách.");
  return card(v.id+" · "+hubName(v.hub),`<p class="bodycopy">${t("Pin")} ${v.battery}% · ${t("Xe được giữ trong 15 phút sau xác nhận. Điểm đến là dự kiến.")}</p>`
  +form("reserve",`<input type="hidden" name="vehicle" value="${safe(v.id)}">`+
    field("Hub đến dự kiến",select("destination",hubOpts(),"BKH"))+
    field("Quãng đường ước tính (km)",input("km",4,"number",'min="0" max="50" step="0.1"')),
    submit("Xác nhận đặt xe")+btn("Quay lại","tab","search",true)));
}
function trips() {
  const active = auth.state.trips.find(t=>t.status==="Active");
  return card("Đơn đặt xe",auth.state.bookings.map(b=>`<div class="row"><div><strong>${b.id} · ${b.vehicle}</strong>
    <p class="muted">${safe(hubName(b.origin))} → ${safe(hubName(b.destination))} · ${t("Hạn nhận")} ${time(b.deadline)}</p></div>
    ${badge(status(b.status))}${b.status==="Reserved"?btn("Xác nhận nhận xe","pickup",b.id):""}</div>`).join("")||empty("Chưa đặt xe."))
    + (active?card(t("Chuyến đang chạy")+" · "+active.id,`<p class="bodycopy">${safe(active.vehicle)} · ${t("Bắt đầu")} ${time(active.started)} · ${t("Hạn dự kiến")} ${time(active.expected)}</p>`
    +form("return",`<input type="hidden" name="id" value="${active.id}">`+field("Hub trả",select("hub",hubOpts(),active.destination))
    +field("Tình trạng xe",select("fault",[["0","Bình thường"],["1","Cần kiểm tra"]])),submit("Xác nhận trả xe"))):"")
    + card("Lịch sử chuyến", auth.state.trips.filter(x=>x.status==="Completed").map(x=>`<div class="row"><div><strong>${x.id} · ${x.vehicle}</strong>
    <p class="muted">${safe(hubName(x.returnHub))} · ${t("Vị trí")} ${safe(x.space)} · ${t("Trả lúc")} ${time(x.returned)}</p></div>${badge(x.late?"Trả muộn":"Hoàn tất")}</div>`).join("")||empty("Chưa có chuyến đã trả xe."));
}
function bookingOptions() {
  return [["PRIVATE-"+auth.session.user,"Xe điện cá nhân"],...auth.state.trips.filter(x=>x.status==="Active").map(x=>[x.vehicle,x.vehicle+" · "+t("Chuyến đang đi")])];
}
function parking() {
  return card("Thông tin đặt chỗ",form("park",field("Phương tiện",select("vehicle",bookingOptions()))+field("Hub",select("hub",hubOpts()))
    +field("Từ",input("start",formTime(5),"datetime-local"))+field("Đến",input("end",formTime(65),"datetime-local")),
    submit("Xác nhận đặt chỗ")))
    +card("Đơn đặt chỗ của tôi",auth.state.parking.map(r=>`<div class="row"><div><strong>${r.id}</strong> · ${safe(hubName(r.hub))} · ${safe(r.space)}
    <p class="muted">${time(r.start)}–${time(r.end)}</p></div>${badge(status(r.status))}</div>`).join("")||empty());
}
function charging() {
  const p = evaluation?.input || {};
  const editor = form("charge-check",field("Phương tiện",select("vehicle",bookingOptions(),p.vehicle))
    +field("Hub sạc",select("hub",hubOpts(),p.hub || "BKH"))
    +field("Đầu sạc xe cá nhân",select("plug",[["Type 2","Type 2"],["Khác","Khác"]],p.plug))
    +field("Giờ có mặt",input("start",p.start || formTime(5),"datetime-local"))
    +field("Hạn cần xe",input("end",p.end || formTime(70),"datetime-local"))
    +field("Pin hiện tại của xe cá nhân (%)",input("battery",p.battery ?? 25,"number",'min="0" max="100"'))
    +field("Pin mục tiêu (%)",input("target",p.target ?? 65,"number",'min="1" max="100"')),
    submit("Kiểm tra khả dụng"));
  const check = evaluation ? card("Kết quả kiểm tra",badge(evaluation.result.status,evaluation.result.status!=="Có khả dụng")
    +`<p class="bodycopy">${safe(evaluation.result.reason ? t(evaluation.result.reason) :
      t("Tìm thấy cổng sạc và vị trí đỗ phù hợp. Ước tính")+" "+evaluation.result.request.duration+" "+t("phút")+".")}</p>`
    +actions(btn(evaluation.result.status==="Có khả dụng"?"Xác nhận gửi nhu cầu":"Đồng ý chờ tài nguyên","charge")
    +btn("Hủy yêu cầu nháp","cancel-charge","",true))) : "";
  const req = auth.state.requests.map(r=>{
    const canCancel = ["Pending","Waiting","Scheduled"].includes(r.status);
    return `<div class="row"><div><strong>${safe(r.id)} · ${safe(hubName(r.hub))}</strong>
      <p class="muted">${t("Pin")} ${r.battery}% → ${r.target}% · ${safe(t(r.source))} · ${t("Hạn cần xe")} ${time(r.end)}</p></div>
      <div class="pills charging-request-actions">${badge(status(r.status))}
        ${canCancel?btn("Hủy nhu cầu sạc","charge-cancel",r.id,true):""}</div></div>`;
  }).join("");
  const notices = {ScheduleApplied:"Đã có lịch sạc",ScheduleInvalidated:"Lịch cần lập lại",ChargingInterrupted:"Phiên sạc bị gián đoạn"};
  return check+card("Thông tin nhu cầu",editor)+card("Nhu cầu của tôi",req||empty())
    +card("Thông báo sạc",auth.state.notifications.slice(-15).reverse().map(x=>
      `<p class="bodycopy">${safe(x.request)} · ${t(notices[x.kind])} · ${time(x.stamp)}</p>`).join("")||empty());
}
function report() {
  return card("Thông tin sự cố",form("report",field("Hub",select("hub",hubOpts()))+field("Mã tài nguyên",optional("resource"))
  +`<div class="wide">${field("Mô tả",'<textarea required name="description" rows="2" maxlength="300"></textarea>')}</div>`,submit("Gửi báo cáo")))
  +card("Báo cáo của tôi",auth.state.incidents.map(r=>`<div class="row"><div><strong>${r.id}</strong> · ${safe(r.description)}</div>${badge(status(r.status))}</div>`).join("")||empty());
}
function monitor() {
  const summary = daily?.result.summary;
  const inputDay = daily?.day || localDate(-1440);
  const dailyForm = form("daily", field("Hub",select("hub",hubOpts(),daily?.hub || "MET"))
    +field("Ngày (giờ Việt Nam)",input("day",inputDay,"date",`max="${localDate(-1440)}"`)),submit("Xem tổng hợp"));
  const data = !daily ? "" : summary
    ? `<p class="bodycopy">${safe(summary.day)} · ${safe(hubName(summary.hub))} · ${badge(status(summary.completeness),summary.completeness!=="Complete")}</p>`
      +table(["Chuyến hoàn tất","Từ chối thiếu tài nguyên","Chờ sạc trung bình","Sự cố mới"],[[
        summary.trips,summary.denials,summary.averageWait===null?"N/A":`${summary.averageWait} ${t("phút")}`,summary.incidents
      ]])
    : empty(daily.result.job==="Failed" ? "Tổng hợp thất bại." : daily.result.job==="Retrying" ? "Đang chờ thử lại." : "Ngày đã chọn chưa có bản tổng hợp.");
  const kinds = {heartbeat:"Nhịp Hub",battery:"Mức pin",port_fault:"Hỏng cổng",port_status:"Trạng thái cổng",
    parking_enter:"Xe vào chỗ",parking_exit:"Xe rời chỗ",charging_started:"Bắt đầu sạc",charging_ended:"Kết thúc sạc"};
  const events = auth.state.events.slice(-30).reverse().map(e=>[
    safe(e.id),safe(e.source),safe(t(kinds[e.kind] || e.kind)),safe(e.resource),time(e.observedAt),
    badge(status(e.status),e.status==="Reconcile"),safe(t(e.reason || ""))
  ]);
  return hubs()+card("Trạng thái mạng lưới",
    `<p class="bodycopy">${t("Phiên dữ liệu")} ${safe(auth.state.session)} · ${t("Phiên bản")} ${auth.state.version} · ${t("Bản ghi nhận")} ${time(auth.state.stamp)}</p>`
    +`<p class="bodycopy">${auth.state.vehicles.length} ${t("phương tiện")} · ${auth.state.requests.length} ${t("nhu cầu sạc")} · ${auth.state.transfers.length} ${t("lệnh điều chuyển")}</p>`)
    +card("Tổng hợp vận hành theo ngày",dailyForm+data)
    +card("Quan sát IoT",table(["Mã","Nguồn","Sự kiện","Tài nguyên","Thời điểm","Kết quả","Lý do"],events));
}

function table(headers,rows) {
  if(!rows.length) return empty();
  return `<div class="scroll"><table><thead><tr>${headers.map(h=>`<th scope="col">${safe(t(h))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r=>`<tr>${r.map(value=>`<td>${value}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function schedule() {
  const h = draft?.hub || "BKH";
  const config = auth.state.chargeConfig;
  const editor = form("build",field("Hub",select("hub",hubOpts(),h))
    +field("Bắt đầu",input("start",draft ? localInput(draft.fromTime) : formTime(0),"datetime-local"))
    +field("Kết thúc",input("end",draft ? localInput(draft.toTime) : formTime(120),"datetime-local")),submit("Tính phương án"));
  const format = x=>[safe(x.request),safe(x.vehicle),safe(x.port),safe(x.space),time(x.start)+"–"+time(x.end),safe(x.waitMinutes)+" "+t("phút")];
  const headers = ["Nhu cầu","Phương tiện","Cổng sạc","Chỗ đỗ","Khung giờ","Chờ"];
  const preview = draft ? card(t("Phương án nháp")+" · "+hubName(draft.hub),
    `<p class="bodycopy">${draft.proposal.length} ${t("nhu cầu có lịch")} · ${draft.waiting.length} ${t("nhu cầu còn chờ")} · ${t("Phiên bản")} ${draft.version}</p>`
    +(draft.adjustment ? `<p class="bodycopy">${t("Thay lịch")} ${safe(draft.adjustment.slot)}</p>` : "")
    +table(headers,draft.proposal.map(format))
    +draft.waiting.map(x=>`<p class="bodycopy">${safe(x.id)} · ${safe(t(x.reason))}</p>`).join("")
    +actions((draft.canApply?btn("Áp dụng lịch","apply"):"")+btn("Bỏ phương án","cancel-plan","",true))) : "";
  const selected = auth.state.schedule.find(x=>x.id===editingSlot && x.status==="Scheduled");
  let adjustment = "";
  if(selected) {
    const request = auth.state.requests.find(x=>x.id===selected.request);
    adjustment = card(t("Điều chỉnh lịch")+" · "+selected.id,form("adjust-plan",
      `<input type="hidden" name="slot" value="${safe(selected.id)}">`
      +field("Thời điểm sạc mới",input("ready",localInput(selected.start,true),"datetime-local"))
      +field("Hạn cần xe",input("deadline",localInput(request.end,true),"datetime-local")),
      submit("Tính lại phương án")+btn("Đóng","close-adjustment","",true)));
  }
  const rows = auth.state.schedule.filter(x=>x.status!=="Superseded").map(x=>[
    ...format(x),badge(status(x.status)), x.status==="Scheduled" && new Date(x.end)>new Date()
      ? btn("Điều chỉnh","edit-slot",x.id,true) : ""
  ]);
  const history = [...auth.state.planHistory].reverse().map(x=>[
    safe(x.id),time(x.stamp),x.version,safe(hubName(x.hub)),safe(x.replaced || "—"),x.slots.length
  ]);
  return preview+adjustment+card("Phạm vi lập lịch",editor+
    `<p class="mini">${t("Tự xét xe chung đang đỗ có pin dưới")} ${config.lowThreshold}% · ${t("Pin mục tiêu")} ${config.autoTarget}%</p>`)
    +card("Nhu cầu đã tiếp nhận",table(["Mã","Hub","Xe","Pin hiện tại","Hạn cần xe","Trạng thái"],auth.state.requests.map(r=>[
      safe(r.id),safe(hubName(r.hub)),safe(r.vehicle),r.battery+"%",time(r.end),badge(status(r.status))
    ])))
    +card("Lịch đã áp dụng",table([...headers,"Trạng thái","Thao tác"],rows))
    +card("Phiên sạc thực tế",table(["Mã","Xe","Cổng sạc","Bắt đầu","Kết thúc","Trạng thái","Pin đo / tự khai"],
      [...auth.state.chargingSessions].reverse().map(x=>[
        safe(x.id),safe(x.vehicle),safe(x.port),time(x.startedAt),x.endedAt?time(x.endedAt):"—",badge(status(x.status)),
        x.measuredBattery===undefined?"—":`${x.measuredBattery}% / ${x.reportedBattery}%`
      ])))
    +card("Lịch sử phiên bản lịch",table(["Mã","Thời điểm","Phiên bản","Hub","Lịch bị thay","Số lượt"],history));
}
function incidents() {
  return card("Hồ sơ sự cố",auth.state.incidents.map(x=>`<div class="row"><div><strong>${x.id} · ${safe(hubName(x.hub))}</strong>
    <p class="bodycopy">${safe(x.description)} · ${safe(x.resource)}</p>${badge(status(x.status))}</div>${x.status==="New"?btn("Bắt đầu xử lý","resolve",x.id+"|Inspecting"):
    x.status==="Inspecting"?btn("Đóng hồ sơ","resolve",x.id+"|Closed")+btn("Xác nhận tài nguyên hỏng","resolve",x.id+"|Closed|fault",true)
      +(x.resource?btn("Xác nhận phục hồi","resolve",x.id+"|Closed|repair",true):""):""}</div>`).join("")||empty("Chưa có báo cáo."));
}
function transfers() {
  return card("Tạo lệnh",form("transfer",field("Xe nguồn",select("vehicle",vehicleOpts()))+
    field("Hub đích",select("hub",hubOpts(),"KTX")), submit("Xác nhận lệnh")))
    +card("Lệnh điều chuyển",auth.state.transfers.map(x=>`<div class="row"><div><strong>${x.id} · ${x.vehicle}</strong>
    <p class="muted">${safe(hubName(x.origin))} → ${safe(hubName(x.destination))} · ${t("Chỗ đích")} ${safe(x.space)}</p></div>${badge(status(x.status))}
    ${x.status==="Confirmed"?btn("Xác nhận xuất phát","transfer-step",x.id+"|depart"):x.status==="InProgress"?btn("Xác nhận tiếp nhận","transfer-step",x.id+"|receive"):""}</div>`).join("")||empty());
}
const scenarioOptions = [
  ["demand","Tăng lượt đến",100],["full_parking","Hub đầy chỗ đỗ",1],
  ["charge_demand","Tăng nhu cầu sạc",100],["port_failure","Hỏng cổng sạc",20],
  ["concentration","Tập trung quá nhiều xe",100]
];
function whatif() {
  const s = simulation;
  const current = simulationInput || {};
  const editor = form("simulate",field("Hub",select("hub",hubOpts(),current.hub || "MET"))
    +field("Thời lượng (phút)",input("minutes",current.minutes ?? 30,"number",'min="1" max="60"'))
    +scenarioOptions.map(([kind,label,max])=>field(label,input(kind,current[kind] ?? (kind==="demand"?12:0),"number",`min="0" max="${max}"`))).join(""),
    submit("Chạy mô phỏng")+btn("Chụp lại dữ liệu","new-snapshot","",true));
  const metrics = [
    ["Nhu cầu","demand"],["Xe khả dụng ban đầu","initialVehicles"],["Nhu cầu được đáp ứng","served"],
    ["Tỷ lệ đáp ứng (%)","demandRate"],["Lượt chưa đáp ứng","shortage"],["Chờ sạc trung bình (phút)","chargeWait"],
    ["Nhu cầu sạc chưa phục vụ","unservedCharge"],["Đỉnh chiếm chỗ (%)","peakOccupancy"],["Xe còn lại","remainingVehicles"],
    ["Xe không còn chỗ","parkingOverflow"],["Cổng hoạt động","usablePorts"]
  ];
  const messages = {
    transfer_in:r=>`${t("Cân nhắc điều chuyển vào")} ${r.count} ${t("xe từ")} ${hubName(r.hub)}.`,
    transfer_out:r=>`${t("Cân nhắc điều chuyển ra")} ${r.count} ${t("xe")}.`,
    reschedule:r=>`${t("Xem xét xếp lại")} ${r.count} ${t("nhu cầu sạc")}.`,
    redirect:r=>`${t("Hướng nhu cầu đỗ tới")} ${hubName(r.hub)} (${r.count} ${t("chỗ trống")}).`
  };
  return card("Cấu hình kịch bản", editor
    +`<p class="mini">${snapshot ? t("Bản chụp phiên bản")+" "+snapshot.version+" · "+dateTime(snapshot.capturedAt) : t("Chưa có bản chụp.")}</p>`)
    +(s?card(t("Kết quả")+" · "+s.id+" · "+hubName(s.hub),
      `<p class="mini">${t("Bản chụp phiên bản")} ${s.version} · ${dateTime(s.snapshotAt)} · ${safe(s.model)} · ${s.minutes} ${t("phút")}</p>`
      +table(["Chỉ số","Cơ sở","Giả định"],metrics.map(([label,key])=>[safe(t(label)),s.baselineMetrics[key]??"N/A",s.scenarioMetrics[key]??"N/A"]))
      +`<p class="bodycopy"><strong>${t("Khuyến nghị")}</strong></p>`
      + (s.recommendations.length ? s.recommendations.map(r=>`<p class="bodycopy">${safe(messages[r.action](r))}</p>`).join("")
        : `<p class="bodycopy">${t("Tiếp tục giám sát hoặc phối hợp nhiều Hub để xử lý phần thiếu hụt.")}</p>`)
      +`<p class="muted">${t("Kết quả ước tính dùng bước một phút. Khuyến nghị cần được xác nhận trong nghiệp vụ vận hành.")}</p>`):"")
    +card("Lịch sử mô phỏng",table(["Mã","Hub","Bản chụp","Thao tác"],simulationHistory.map(x=>[
      safe(x.id),safe(hubName(x.hub)),dateTime(x.snapshotAt),btn("Xem kết quả","show-simulation",x.id,true)
    ])));
}
function preferences() {
  const current = displayPrefs();
  return card("Tùy chỉnh giao diện",form("prefs",
    field("Chế độ",select("theme",[["light","Sáng"],["dark","Tối"]],current.theme))
    +field("Ngôn ngữ",select("language",[["vi","Tiếng Việt"],["en","English"]],current.language)),submit("Lưu giao diện")));
}
function render() {
  const {theme,language} = displayPrefs();
  document.documentElement.dataset.theme = theme;
  document.documentElement.lang = language;
  if(!auth.session || !auth.state) {login();return;}
  const tabs = [...nav[auth.session.role],["preferences","Giao diện"]].map(([id,label])=>
    `<button type="button" data-action="tab" data-value="${id}" class="${(id===page||(id==="search"&&page==="reserve"))?"active":""}">${t(label)}</button>`).join("");
  const [title,subtitle] = page==="reserve"?["Đặt trước xe dùng chung","Xác nhận thông tin hành trình và hạn nhận xe."]:strings[page];
  const views = {search,reserve,trips,parking,charging,report,monitor,incidents,transfers,schedule,whatif,preferences};
  root.innerHTML = bar()+`<nav class="nav" aria-label="${t("Chức năng")}"><div class="wrap tabs">${tabs}</div></nav>
  <div class="wrap content"><header class="title"><div><h1>${safe(t(title))}</h1><p>${safe(t(subtitle))}</p></div>
    ${page==="monitor"?btn("Làm mới","refresh","",true):""}</header>${banner()}${views[page]()}</div>`;
}
async function run(work, el, preserve=false) {
  const forms = preserve ? [...root.querySelectorAll("form")].map(f=>[f.dataset.form,[...new FormData(f)]]) : [];
  if(el) el.disabled = true;
  try { await work(); }
  catch(exc) { notice(exc.message,true); }
  finally {
    render();
    for (const [action,values] of forms) {
      const formElement = [...root.querySelectorAll("form")].find(f=>f.dataset.form===action);
      if (!formElement) continue;
      for (const [name,value] of values) {
        const control = formElement.elements.namedItem(name);
        if(control) control.value = value;
      }
    }
  }
}
root.addEventListener("submit", event => {
  const el = event.target;
  if(!el.dataset.form) return;
  event.preventDefault();
  const p = Object.fromEntries(new FormData(el));
  const action = el.dataset.form;
  run(async()=> {
    clear();
    if(action==="login") {
      auth.session = await api("login",p);
      await refresh();
      page = auth.session.role==="Student"?"search":"monitor";
      return;
    }
    if(action==="filter") { selectedHub=p.hub; minBattery=Number(p.battery); return; }
    if(action==="charge-check") { evaluation={input:p,result:await api(action,p)};return; }
    if(action==="build") { draft=await api(action,p);editingSlot="";return; }
    if(action==="adjust-plan") {
      const slot=auth.state.schedule.find(x=>x.id===p.slot);
      draft=await api("build",{hub:slot.hub,start:p.ready,end:p.deadline,
        adjustment:{slot:p.slot,ready:p.ready,deadline:p.deadline}});
      editingSlot="";
      return;
    }
    if(action==="daily") { daily={...p,result:await api(action,p)};return; }
    if(action==="simulate") {
      if(!snapshot) snapshot=await api("simulation-snapshot");
      simulationInput=p;
      const changes=scenarioOptions.map(([kind])=>({kind,amount:Number(p[kind])})).filter(x=>x.amount!==0);
      simulation=await api(action,{snapshotId:snapshot.id,hub:p.hub,minutes:Number(p.minutes),changes,key:key()});
      simulationHistory=await api("simulation-history");
      return;
    }
    if(action==="return") p.fault = p.fault==="1";
    const item = await api(action,{...p,key:key()});
    const messages = {
      reserve:`${t("Đã giữ xe")} ${item.vehicle} · ${t("Đơn")} ${item.id}`,
      return:`${t("Đã trả xe")} · ${t("Chuyến")} ${item.id} · ${t("Hoàn tất")}`,
      park:`${t("Đã giữ chỗ")} ${item.space} · ${t("Đơn")} ${item.id}`,
      report:`${t("Đã gửi báo cáo")} ${item.id}`,
      transfer:`${t("Đã xác nhận lệnh")} ${item.id}`, prefs:"Đã lưu tùy chọn giao diện."
    };
    if(action==="reserve") {page="trips";selectedHub="";}
    await refresh();
    notice(messages[action]);
  },event.submitter);
});
root.addEventListener("click",event=>{
  const el = event.target.closest("button[data-action]");
  if(!el) return;
  const {action,value} = el.dataset;
  if(action==="charge-cancel" && !window.confirm(`${t("Hủy nhu cầu sạc")} ${value}?`)) return;
  const preserve = ["quick-language","quick-theme","new-snapshot","charge-cancel"].includes(action);
  run(async()=>{
    clear();
    if(action==="quick-language" || action==="quick-theme") {
      const options=displayPrefs();
      if(action==="quick-language") options.language=options.language==="vi"?"en":"vi";
      else options.theme=options.theme==="light"?"dark":"light";
      if(auth.session) auth.state.pref=await api("prefs",options);
      else saveGuest(options);
      return;
    }
    if(action==="tab"){
      page=value;selectedHub="";await refresh();
      if(value==="whatif"){
        if(!snapshot) snapshot=await api("simulation-snapshot");
        simulationHistory=await api("simulation-history");
      }
      return;
    }
    if(action==="choose"){selectedHub=value;page="reserve";return;}
    if(action==="cancel-charge"){evaluation=null;return;}
    if(action==="cancel-plan"){await api("discard-plan",{id:draft.id});draft=null;return;}
    if(action==="edit-slot"){editingSlot=value;return;}
    if(action==="close-adjustment"){editingSlot="";return;}
    if(action==="new-snapshot"){snapshot=await api("simulation-snapshot");return;}
    if(action==="show-simulation"){simulation=simulationHistory.find(x=>x.id===value);return;}
    if(action==="refresh"){
      await refresh();
      if(daily) daily.result=await api("daily",{hub:daily.hub,day:daily.day});
      return;
    }
    if(action==="logout"){
      await api("logout");auth.session=null;auth.state=null;
      selectedHub="";evaluation=null;draft=null;simulation=null;daily=null;editingSlot="";
      snapshot=null;simulationInput=null;simulationHistory=[];return;
    }
    if(action==="pickup"){
      const item=await api(action,{id:value,key:key()});
      notice(`${t("Đã nhận xe")} ${item.vehicle} · ${t("Chuyến đang chạy")} ${item.id}`);
    } else if(action==="charge"){
      const item=await api(action,{...evaluation.input,acceptWait:evaluation.result.status!=="Có khả dụng",key:key()});
      evaluation=null;notice(`${t("Đã ghi nhận")} ${item.id} · ${status(item.status)}`);
    } else if(action==="charge-cancel"){
      try {
        const item=await api(action,{id:value,key:key()});
        notice(`${t("Đã hủy nhu cầu sạc")} ${item.id}`);
      } finally {
        await refresh();
      }
      return;
    } else if(action==="apply"){
      const applied=await api(action,{id:draft.id,key:key()});draft=null;
      notice(`${t("Đã áp dụng lịch cho")} ${applied.count} ${t("nhu cầu")} · ${t("Phiên bản")} ${applied.version}`);
    } else if(action==="transfer-step"){
      const [id,step]=value.split("|");
      const item=await api("transfer",{id,action:step,key:key()});notice(`${item.id} · ${status(item.status)}`);
    } else if(action==="resolve"){
      const [id,step,disposition]=value.split("|");
      const item=await api(action,{id,action:step,fault:disposition==="fault",repair:disposition==="repair",key:key()});
      notice(`${item.id} · ${status(item.status)}`);
    }
    await refresh();
  },el,preserve);
});
render();
