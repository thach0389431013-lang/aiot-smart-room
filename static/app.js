function formatTs(ts) {
  if (!ts) return "--";
  const date = new Date(ts * 1000);
  return date.toLocaleString();
}

function setPill(el, text, mode) {
  el.textContent = text;
  el.className = "pill " + mode;
}

function updateStatus(data) {
  const mqttBadge = document.getElementById("mqttBadge");
  mqttBadge.textContent = data.mqtt_connected ? "CONNECTED" : "DISCONNECTED";
  mqttBadge.className = "badge status " + (data.mqtt_connected ? "connected" : "disconnected");

  document.getElementById("lastUpdate").textContent = formatTs(data.last_update);

  setPill(
    document.getElementById("visionDetected"),
    data.vision.person_detected ? "YES" : "NO",
    data.vision.person_detected ? "active" : "idle"
  );
  document.getElementById("visionConf").textContent = Number(data.vision.conf || 0).toFixed(2);
  document.getElementById("visionTrackIds").textContent = JSON.stringify(data.vision.track_ids || []);
  document.getElementById("visionSource").textContent = data.vision.source || "--";
  document.getElementById("visionTs").textContent = formatTs(data.vision.ts);

  setPill(
    document.getElementById("motionDetected"),
    data.motion.detected ? "YES" : "NO",
    data.motion.detected ? "active" : "idle"
  );
  document.getElementById("motionOnline").textContent = String(data.motion.online);
  document.getElementById("motionTs").textContent = formatTs(data.motion.ts);

  const doorState = data.door.status || "UNKNOWN";
  setPill(
    document.getElementById("doorStatus"),
    doorState,
    doorState === "OPEN" ? "alarm" : (doorState === "CLOSED" ? "active" : "unknown")
  );
  document.getElementById("doorOnline").textContent = String(data.door.online);
  document.getElementById("doorTs").textContent = formatTs(data.door.ts);

  setPill(
    document.getElementById("alarmActive"),
    data.alarm.active ? "ON" : "OFF",
    data.alarm.active ? "alarm" : "idle"
  );
  document.getElementById("alarmMode").textContent = data.alarm.mode || data.fusion_mode || "--";
  document.getElementById("alarmReasonPerson").textContent = String(data.alarm.reason.person);
  document.getElementById("alarmReasonMotion").textContent = String(data.alarm.reason.motion);
  document.getElementById("alarmReasonDoor").textContent = String(data.alarm.reason.door);
  document.getElementById("alarmTs").textContent = formatTs(data.alarm.ts);

  document.getElementById("summaryCamera").textContent = data.summary.camera;
  document.getElementById("summaryMotion").textContent = data.summary.motion;
  document.getElementById("summaryDoor").textContent = data.summary.door;
  document.getElementById("summaryAlarm").textContent = data.summary.alarm;

  document.getElementById("currentMode").textContent = data.fusion_mode || "--";
  document.getElementById("fusionMode").value = data.fusion_mode || "mode_camera_motion";

  const alarmCard = document.getElementById("cardAlarm");
  if (data.alarm.active) {
    alarmCard.classList.add("alarm-on");
  } else {
    alarmCard.classList.remove("alarm-on");
  }
}

function updateLogs(logs) {
  const body = document.getElementById("logBody");
  body.innerHTML = "";

  if (!logs || logs.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="muted">No logs yet</td></tr>';
    return;
  }

  for (const log of logs) {
    const tr = document.createElement("tr");
    const modeClass = log.simulated ? "simulated" : "live-mode";
    const modeText = log.simulated ? "SIMULATED" : "LIVE";

    tr.innerHTML = `
      <td>${formatTs(log.time)}</td>
      <td>${log.topic}</td>
      <td class="payload">${JSON.stringify(log.payload)}</td>
      <td class="${modeClass}">${modeText}</td>
    `;
    body.appendChild(tr);
  }
}

async function fetchStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  updateStatus(data);
}

async function fetchLogs() {
  const res = await fetch("/api/logs");
  const data = await res.json();
  updateLogs(data.logs);
}

async function setFusionMode() {
  const mode = document.getElementById("fusionMode").value;
  await fetch("/api/fusion/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode })
  });
  await refreshAll();
}

async function refreshAll() {
  await Promise.all([fetchStatus(), fetchLogs()]);
}

setInterval(refreshAll, 1000);
refreshAll();