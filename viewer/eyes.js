"use strict";
// Draws OLED-style eyes per expression and tilts the head from MQTT telemetry.
const params = new URLSearchParams(location.search);
const deviceId = params.get("device") || "deskbot-001";
const wsUrl = params.get("ws") || "ws://" + (location.hostname || "localhost") + ":9001";
const topic = "thingwire/" + deviceId + "/telemetry";
const statusTopic = "thingwire/" + deviceId + "/status";

const canvas = document.getElementById("eyes");
const ctx = canvas.getContext("2d");
const headEl = document.getElementById("head");
const pixel = getComputedStyle(document.documentElement).getPropertyValue("--pixel").trim();

// Per expression: eye height ratio, lid slope (px, + = angry/alert), curve (+ = happy arc), pupil scale
const STYLES = {
  neutral:   { h: 1.0, lid: 0,   arc: 0,  w: 1.0 },
  happy:     { h: 0.7, lid: 0,   arc: 1,  w: 1.0 },
  curious:   { h: 1.1, lid: 0,   arc: 0,  w: 1.0, asym: true },
  sleepy:    { h: 0.25, lid: 0,  arc: 0,  w: 1.0 },
  surprised: { h: 1.4, lid: 0,   arc: 0,  w: 0.8 },
  alert:     { h: 0.9, lid: 14,  arc: 0,  w: 1.0 },
};

function drawEye(cx, cy, s, flip, big) {
  const w = 64 * s.w, h = 64 * s.h * (big ? 1.25 : 1);
  ctx.fillStyle = pixel;
  if (s.arc) {
    ctx.strokeStyle = pixel; ctx.lineWidth = 10; ctx.lineCap = "round";
    ctx.beginPath(); ctx.arc(cx, cy + 14, w / 2, Math.PI * 1.1, Math.PI * 1.9); ctx.stroke();
    return;
  }
  ctx.beginPath(); ctx.roundRect(cx - w / 2, cy - h / 2, w, h, 14); ctx.fill();
  if (s.lid) {
    ctx.fillStyle = "#000"; ctx.beginPath();
    const y = cy - h / 2, dir = flip ? -1 : 1;
    ctx.moveTo(cx - w / 2 - 2, y - 2); ctx.lineTo(cx + w / 2 + 2, y - 2);
    ctx.lineTo(cx + dir * (w / 2 + 2), y + s.lid); ctx.closePath(); ctx.fill();
  }
}

function drawFace(expression) {
  const s = STYLES[expression] || STYLES.neutral;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawEye(78, 64, s, false, false);
  drawEye(178, 64, s, true, !!s.asym);
}

function applyTelemetry(readings) {
  const pan = readings.head_pan ? readings.head_pan.value : 0;
  const tilt = readings.head_tilt ? readings.head_tilt.value : 0;
  const expr = readings.expression ? readings.expression.value : "neutral";
  const person = readings.person_detected ? readings.person_detected.value : false;
  // Head is drawn rotated: pan swings it (yaw as roll), tilt shifts it vertically.
  headEl.style.transform = "rotate(" + (pan / 3) + "deg) translateY(" + (-tilt) + "px)";
  document.getElementById("pan").textContent = pan.toFixed(1);
  document.getElementById("tilt").textContent = tilt.toFixed(1);
  document.getElementById("expr").textContent = expr;
  document.getElementById("person").textContent = person ? "yes" : "no";
  drawFace(expr);
}

drawFace("neutral");
const conn = document.getElementById("conn");
const client = mqtt.connect(wsUrl);
client.on("connect", function () {
  conn.textContent = "connected to " + wsUrl + " as " + deviceId;
  client.subscribe([topic, statusTopic]);
});
client.on("close", function () { conn.textContent = "disconnected"; });
client.on("message", function (t, payload) {
  const text = payload.toString();
  if (t === statusTopic) {
    if (text === "offline") conn.textContent = "device offline";
    return;
  }
  try { applyTelemetry(JSON.parse(text).readings || {}); } catch (e) { console.error("bad telemetry", e); }
});
