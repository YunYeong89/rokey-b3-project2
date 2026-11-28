const PREFIX = "ROKEY-PICKUP:";
const RESULT_MS = 3000;        // 결과 화면을 보여 주는 시간
const SAME_QR_IGNORE_MS = 6000; // 같은 QR 이 계속 보이면 이 시간 동안은 다시 확인하지 않음
const SCAN_EVERY_MS = 150;
const FAIL_TEXT = {
  already_picked: ["🔁", "이미 수령한 주문이에요", "같은 QR은 다시 쓸 수 없어요"],
  expired:        ["⌛", "만료된 주문이에요", "직원에게 픽업 번호를 알려 주세요"],
  not_ready:      ["⏳", "아직 준비 중이에요", "로봇이 담고 있어요. 휴대폰에 픽업 번호가 뜨면 다시 보여 주세요"],
  unknown:        ["❓", "알 수 없는 QR이에요", "로키편의점 픽업 QR이 맞는지 확인해 주세요"],
  unknown_manual: ["❓", "번호가 맞지 않아요", "픽업 번호와 확인번호 4자리를 다시 확인해 주세요"],
  too_many:       ["✋", "잠시 후 다시 시도해 주세요", "실패가 너무 많아 잠깐 막혔어요"],
  network:        ["📡", "서버에 연결할 수 없어요", "직원에게 알려 주세요"],
};

const $ = s => document.querySelector(s);
const video = $("#video"), canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", {willReadFrequently: true});
let names = {}, detector = null, busy = false, last = {text: null, t: 0}, resetTimer = null;

setInterval(() => { $("#clock").textContent = new Date().toTimeString().slice(0, 5) }, 1000);
$("#clock").textContent = new Date().toTimeString().slice(0, 5);

fetch("/api/products").then(r => r.json())
  .then(d => { for (const p of d.products) names[p.id] = p })
  .catch(() => {});

/* ---------------- 카메라 */
function camFail(msg) {
  $("#camerr").classList.add("on"); $("#frame").style.display = "none";
  $("#camerrMsg").innerHTML = msg + "<br><br>오른쪽 <b>수동 입력</b>을 쓰세요.";
  $("#engine").textContent = "카메라 없음 · 수동 입력만 가능";
}

async function startCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    camFail(`보안 연결(https)이나 localhost가 아니라서 브라우저가 카메라를 막았어요.<br>
      가게 PC에서 <code>http://localhost:8000/kiosk</code> 로 열어 주세요.`);
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode: "user", width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
  } catch (e) {
    camFail(e.name === "NotAllowedError" ? "카메라 권한이 거부되었어요. 주소창의 카메라 아이콘에서 허용해 주세요."
      : e.name === "NotFoundError" ? "연결된 카메라가 없어요." : `카메라 오류: ${e.name || e.message}`);
    return;
  }
  video.srcObject = stream;
  await video.play().catch(() => {});
  if ("BarcodeDetector" in window) {
    try {
      const formats = await BarcodeDetector.getSupportedFormats();
      if (formats.includes("qr_code")) detector = new BarcodeDetector({formats: ["qr_code"]});
    } catch {}
  }
  if (!detector && typeof jsQR !== "function") { camFail("QR 인식 기능을 불러오지 못했어요 (static/vendor/jsQR.js)."); return }
  $("#engine").textContent = `QR 인식: ${detector ? "BarcodeDetector (브라우저 기본)" : "jsQR"}`;
  loop();
}

async function readQR() {
  if (video.readyState < 2 || !video.videoWidth) return null;
  if (detector) {
    const r = await detector.detect(video);
    return r.length ? r[0].rawValue : null;
  }
  const w = Math.min(640, video.videoWidth), h = Math.round(video.videoHeight * w / video.videoWidth);
  canvas.width = w; canvas.height = h;
  ctx.drawImage(video, 0, 0, w, h);
  const code = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, {inversionAttempts: "dontInvert"});
  return code ? code.data : null;
}

async function loop() {
  if (!busy) {
    try { const text = await readQR(); if (text) onQR(text) } catch {}
  }
  setTimeout(loop, SCAN_EVERY_MS);
}

function onQR(text) {
  const now = Date.now();
  // 같은 QR 이 연속으로 잡혀도 한 번만 확인 (계속 보이는 동안은 시간 연장)
  if (text === last.text && now - last.t < SAME_QR_IGNORE_MS) { last.t = now; return }
  last = {text, t: now};
  if (!text.startsWith(PREFIX)) { showFail("unknown"); return }
  verify("/api/pickup/verify", {token: text.slice(PREFIX.length)});
}

/* ---------------- 수동 입력 */
$("#check").addEventListener("input", e => { e.target.value = e.target.value.replace(/\D/g, "").slice(0, 4) });
$("#manual").addEventListener("submit", e => {
  e.preventDefault();
  if (busy) return;
  const code = $("#code").value.trim(), check = $("#check").value.trim();
  if (!code) { $("#code").focus(); return }
  if (check.length !== 4) { $("#check").focus(); return }
  verify("/api/pickup/manual", {pickup_code: code, check}, true);
});

/* ---------------- 서버 확인 + 결과 화면 */
async function verify(url, body, manual = false) {
  if (busy) return;
  busy = true;
  showResult("wait", `<div class="r-icon">🔎</div><div class="r-title">확인 중…</div>`, false);
  let r, d = {};
  try {
    r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    d = await r.json().catch(() => ({}));
  } catch { showFail("network"); return }
  if (r.ok && d.ok) showOk(d.order);
  else showFail(d.reason === "unknown" && manual ? "unknown_manual" : d.reason || "network");
}

const label = (id, n) => { const p = names[id]; return `${p ? p.emoji + " " + p.name : id} ${n}개` };

function showOk(o) {
  const picked = Object.entries(o.picked || {}), staff = Object.entries(o.staff_items || {});
  const staffTxt = staff.map(([k, n]) => label(k, n)).join(", ");
  showResult("ok", `
    <div class="r-icon">✅</div>
    <div class="r-code">${o.pickup_code}</div>
    <div class="r-title">${picked.length ? `바구니 ${o.pickup_code}을 가져가세요` : "수령 확인됐어요"}</div>
    ${picked.length ? `<div class="r-items">${picked.map(([k, n]) => `<span>${label(k, n)}</span>`).join("")}</div>` : ""}
    ${staff.length ? `<div class="r-staff">🧑‍💼 직원에게 ${staffTxt}를 받아 가세요</div>` : ""}`);
}

function showFail(reason) {
  const [icon, title, msg] = FAIL_TEXT[reason] || FAIL_TEXT.unknown;
  showResult("fail", `<div class="r-icon">${icon}</div><div class="r-title">${title}</div><div class="r-msg">${msg}</div>`);
}

function showResult(kind, html, autoClose = true) {
  busy = true;
  const el = $("#result");
  el.className = `result on ${kind}`;
  el.innerHTML = html + (autoClose ? `<div class="r-bar run" style="animation-duration:${RESULT_MS}ms"></div>` : "");
  clearTimeout(resetTimer);
  if (autoClose) resetTimer = setTimeout(reset, RESULT_MS);
}

function reset() {  // 다시 스캔 대기
  $("#result").className = "result";
  $("#code").value = ""; $("#check").value = "";
  busy = false;
}

startCamera();
