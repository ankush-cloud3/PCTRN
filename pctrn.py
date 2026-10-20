"""
pctrn - receive files, photos and text from your phone's browser straight to this PC.
Run with:  pctrn          (no PIN)
           pctrn --pin    (4-digit PIN required on the phone)
"""
import os
import sys
import socket
import ipaddress
import datetime
import argparse
import secrets
import hmac
import subprocess
import re
import time
import threading
import _thread
import collections
import webbrowser
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string

load_dotenv()
# ---------------- configuration (see .env.example) ----------------
SAVE_DIR = os.path.expanduser(os.getenv("PCTRN_SAVE_DIR") or os.path.join("~", "pctrn_received"))
PORT = int(os.getenv("PCTRN_PORT", "5000"))
LINK_PIN = os.getenv("PCTRN_LINK_PIN", "")   # empty = opening links from received text is disabled
SESSION_MINUTES = int(os.getenv("PCTRN_SESSION_MINUTES", "20"))   # auto-shutdown; 0 = never
# -------------------------------------------------------------------

CERT_DIR = os.path.join(os.path.expanduser("~"), ".pctrn")
CERT_FILE = os.path.join(CERT_DIR, "cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "key.pem")

os.makedirs(SAVE_DIR, exist_ok=True)
PIN = None          # set only when started with --pin
FAILS = 0
MAX_FAILS = 5
MAX_TEXT = 2_000_000  # characters
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#efede6">
<title>PCTRN - PC Transfer</title>
<style>
  :root {
    --paper:#efede6; --ink:#2a2922; --muted:#6d6759; --line:#d6d0c1;
    --stone:#9b8b70; --ok:#3f7a3a; --err:#b03a2e;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color:var(--ink); min-height:100dvh;
    background:
      radial-gradient(420px 260px at 85% 8%, #dde6ea, transparent 70%),
      radial-gradient(500px 300px at 0% 100%, #dfe5d3, transparent 70%),
      var(--paper);
    padding: max(18px, env(safe-area-inset-top)) 16px max(28px, env(safe-area-inset-bottom));
  }
  .wrap { max-width:520px; margin:0 auto; }
  .logo { font-weight:900; font-size:30px; letter-spacing:-.04em; margin:4px 4px 16px; }
  .hero { background:rgba(255,255,255,.6); border:1px solid var(--line); border-radius:24px; padding:22px 20px 20px; margin-bottom:16px; }
  .hero h2 { font-size:26px; line-height:1.1; letter-spacing:-.02em; margin:0 0 8px; font-weight:800; }
  .hero p { margin:0; font-size:15px; color:var(--ink); }
  .hero small { display:block; margin-top:10px; font-size:12px; color:var(--muted); }
  .pill { display:inline-flex; align-items:center; gap:7px; font-size:12px; font-weight:600; color:var(--muted);
          padding:6px 10px; border-radius:999px; background:rgba(255,255,255,.7); border:1px solid var(--line); margin-top:12px; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--ok); }
  .pill.off .dot { background:var(--err); }

  /* stone-look tiles, after the PCTRN artwork */
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .tile {
    position:relative; display:flex; flex-direction:column; justify-content:space-between; gap:10px;
    min-height:158px; padding:14px; border:0; border-radius:22px; color:#fff; text-align:left; cursor:pointer; font:inherit;
    border:7px solid #8f7e62; box-shadow: inset 0 0 0 2px rgba(255,255,255,.14), inset 0 -8px 16px rgba(0,0,0,.18), 0 8px 16px rgba(0,0,0,.14);
    background-image: radial-gradient(circle at 20% 15%, rgba(255,255,255,.12) 0, transparent 35%), radial-gradient(circle at 80% 90%, rgba(0,0,0,.14) 0, transparent 40%);
    transition: transform .12s ease;
  }
  .tile:active { transform:scale(.97); }
  .tile.cam   { background-color:#2e4f88; }
  .tile.photo { background-color:#6c7f45; }
  .tile.docs  { background-color:#2f7f75; }
  .tile.text  { background-color:#a27a4b; }
  .tile .t { font-size:18px; font-weight:800; letter-spacing:-.01em; line-height:1.1; }
  .tile .h { font-size:12px; opacity:.9; line-height:1.3; }
  .tile .ico { width:44px; height:44px; color:#fff; opacity:.95; }
  .tile .ico svg { width:100%; height:100%; }
  .tile .btn { align-self:flex-start; font-size:10px; font-weight:800; letter-spacing:.08em; padding:6px 10px; border-radius:99px; background:rgba(255,255,255,.88); color:#2a2922; }
  .tile.wide { grid-column: 1 / -1; min-height:auto; flex-direction:row; align-items:center; }
  .tile.wide .ico { width:40px; height:40px; }
  .tile.wide .t { font-size:17px; }
  input[type=file] { display:none; }

  #note { font-size:13px; color:#8a5a12; margin:10px 4px 0; min-height:0; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); margin:22px 4px 10px; font-weight:700; }
  .item { display:flex; align-items:center; gap:12px; padding:11px 12px; margin-bottom:8px; border-radius:16px;
          background:rgba(255,255,255,.7); border:1px solid var(--line); }
  .meta { flex:1; min-width:0; }
  .nm { font-size:14px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sz { font-size:12px; color:var(--muted); margin-top:1px; }
  .bar { height:5px; border-radius:99px; background:var(--line); margin-top:7px; overflow:hidden; }
  .bar i { display:block; height:100%; width:0; border-radius:99px; background:#6c7f45; transition:width .15s linear; }
  .item.done .bar i { background:var(--ok); } .item.fail .bar i { background:var(--err); }
  .item.done .sz { color:var(--ok); } .item.fail .sz { color:var(--err); }
  .st { flex:none; width:24px; height:24px; }

  /* text sheet */
  #sheet { position:fixed; inset:0; z-index:40; background:rgba(30,28,22,.45); display:none; align-items:flex-end; }
  #sheet.on { display:flex; }
  .panel { width:100%; max-width:520px; margin:0 auto; background:var(--paper); border-radius:24px 24px 0 0;
           padding:18px 18px max(22px, env(safe-area-inset-bottom)); box-shadow:0 -8px 30px rgba(0,0,0,.2); }
  .panel h4 { margin:0 0 4px; font-size:20px; font-weight:800; letter-spacing:-.01em; }
  .panel p { margin:0 0 12px; font-size:13px; color:var(--muted); }
  #txt { width:100%; height:200px; resize:none; padding:14px; font:inherit; font-size:16px; border-radius:18px;
         border:1px solid var(--line); background:#fff; color:var(--ink); outline:none; }
  .row { display:flex; gap:10px; margin-top:12px; }
  .row button { flex:1; padding:14px; font:inherit; font-size:16px; font-weight:700; border:0; border-radius:16px; cursor:pointer; }
  #sendTxt { color:#fff; background:#a27a4b; }
  #sendTxt[disabled] { opacity:.4; }
  #closeTxt { background:rgba(0,0,0,.08); color:var(--ink); }
  #sheetMsg { font-size:13px; color:var(--ok); margin-top:10px; min-height:18px; }
  #sheetMsg.err { color:var(--err); }

  /* camera overlay */
  #cam { position:fixed; inset:0; z-index:50; background:#000; display:none; }
  #cam.on { display:block; }
  #video { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; background:#000; }
  .top { position:absolute; left:0; right:0; top:0; display:flex; justify-content:space-between; align-items:center;
         padding:max(16px, env(safe-area-inset-top)) 16px 40px; background:linear-gradient(#0009,transparent); }
  .round { width:46px; height:46px; border-radius:50%; border:0; display:grid; place-items:center; color:#fff;
           background:rgba(255,255,255,.18); }
  .round svg { width:22px; height:22px; }
  #count { color:#fff; font-size:13px; font-weight:700; padding:7px 12px; border-radius:99px; background:rgba(255,255,255,.18); }
  .bottom { position:absolute; left:0; right:0; bottom:0; padding:40px 16px max(22px, env(safe-area-inset-bottom));
            background:linear-gradient(transparent,#000a); }
  #strip { display:flex; gap:8px; overflow-x:auto; padding:0 2px 16px; }
  #strip:empty { display:none; }
  .th { position:relative; flex:none; width:58px; height:58px; border-radius:12px; overflow:hidden; border:2px solid rgba(255,255,255,.7); }
  .th img { width:100%; height:100%; object-fit:cover; display:block; }
  .ctrl { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; }
  #shutter { width:78px; height:78px; border-radius:50%; border:4px solid #fff; background:transparent; padding:5px; }
  #shutter span { display:block; width:100%; height:100%; border-radius:50%; background:#fff; }
  #sendAll { justify-self:end; border:0; border-radius:99px; padding:13px 18px; font:inherit; font-size:15px; font-weight:700;
             color:#fff; background:#6c7f45; }
  #sendAll[disabled] { opacity:.35; }
  #flash { position:absolute; inset:0; background:#fff; opacity:0; pointer-events:none; }
  #flash.go { animation:fl .22s ease-out; }
  @keyframes fl { 0%{opacity:.75} 100%{opacity:0} }

  /* PIN gate */
  #gate { margin-top:12vh; text-align:center; }
  #gate p { color:var(--muted); font-size:14px; margin:0 0 14px; }
  #gate input { width:100%; padding:16px; font-size:28px; letter-spacing:.4em; text-align:center; border-radius:18px;
                border:1px solid var(--line); background:#fff; color:var(--ink); outline:none; }
  #gate button { width:100%; margin-top:12px; padding:16px; font:inherit; font-size:17px; font-weight:700; border:0; border-radius:18px;
                 color:#fff; background:#2e4f88; }
  #gateMsg { color:var(--err); font-size:14px; margin-top:10px; min-height:18px; }
  /* session ended overlay */
#ended { position:fixed; inset:0; z-index:100; display:none; flex-direction:column; align-items:center;
         justify-content:center; text-align:center; padding:24px; background:var(--paper); }
#ended.on { display:flex; }
#ended h2 { font-size:26px; margin:0 0 8px; letter-spacing:-.02em; }
#ended p { margin:0; color:var(--muted); font-size:15px; max-width:300px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">PCTRN</div>

  {% if need_pin %}
  <div id="gate">
    <p>Enter the PIN shown on your laptop</p>
    <input id="pin" inputmode="numeric" maxlength="4" placeholder="----" autocomplete="off">
    <button id="unlock">Unlock</button>
    <div id="gateMsg"></div>
  </div>
  {% endif %}

  <div id="app" {% if need_pin %}style="display:none"{% endif %}>
    <div class="hero">
      <h2>PC Transfer</h2>
      <p>Send files and captures straight to your PC</p>
      <small>No installation needed. Quick transfer.</small>
      <span class="pill" id="pill"><span class="dot"></span><span id="pillTxt">Connected to your laptop</span></span>
    </div>

    <div class="grid">
      <button class="tile cam" id="openCam">
        <span class="t">Open Camera</span>
        <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></span>
        <span class="h">Scan QR or send instantly</span>
        <span class="btn">SNAP &amp; SEND</span>
      </button>

      <label class="tile photo">
        <span class="t">Photos &amp; Videos</span>
        <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="13" height="13" rx="2"/><circle cx="7.5" cy="7.5" r="1.3"/><path d="M16 16l-4-4-7 7"/><rect x="9" y="8" width="13" height="13" rx="2" fill="currentColor" fill-opacity=".15"/></svg></span>
        <span class="h">Photos &amp; videos from gallery</span>
        <span class="btn">BROWSE GALLERY</span>
        <input type="file" id="media" accept="image/*,video/*" multiple>
      </label>

      <label class="tile docs">
        <span class="t">Documents / any file</span>
        <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span>
        <span class="h">Transfers PDFs, docs &amp; files</span>
        <span class="btn">CHOOSE FILES</span>
        <input type="file" id="files" multiple>
      </label>

      <button class="tile text" id="openText">
        <span class="t">Text to PC</span>
        <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h10M4 18h13"/></svg></span>
        <span class="h">Copies to PC clipboard and saves a .txt</span>
        <span class="btn">WRITE TEXT</span>
      </button>
    </div>

    <div id="note"></div>
    <h3 id="lh" style="display:none">Transfers</h3>
    <div id="list"></div>
  </div>
</div>

<div id="sheet">
  <div class="panel">
    <h4>Text to PC</h4>
    <p>Goes to the PC clipboard and is saved as a .txt in the save folder.</p>
    <textarea id="txt" placeholder="Type or paste here..."></textarea>
    <div class="row">
      <button id="closeTxt">Close</button>
      <button id="sendTxt" disabled>Send to PC</button>
    </div>
    <div id="sheetMsg"></div>
  </div>
</div>

<div id="cam">
  <video id="video" autoplay playsinline muted></video>
  <div id="flash"></div>
  <div class="top">
    <button class="round" id="closeCam" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    <div id="count" style="visibility:hidden">0</div>
    <button class="round" id="flip" aria-label="Flip camera"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 0-15.5-6.2L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 15.5 6.2L21 16"/><path d="M21 21v-5h-5"/></svg></button>
  </div>
  <div class="bottom">
    <div id="strip"></div>
    <div class="ctrl">
      <span></span>
      <button id="shutter" aria-label="Capture"><span></span></button>
      <button id="sendAll" disabled>Send all</button>
    </div>
  </div>
</div>
<div id="ended">
  <h2>Session ended</h2>
  <p>pctrn was closed on your PC. Start it again and scan the new QR code.</p>
</div>
<canvas id="canvas" style="display:none"></canvas>

<script>
const $ = id => document.getElementById(id);
const video = $('video'), list = $('list');
let pin = '', stream = null, facing = 'environment', shots = [];
const OK = '<svg class="st" viewBox="0 0 24 24" fill="none" stroke="#3f7a3a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 12.5l3 3 5-6"/></svg>';
const BAD = '<svg class="st" viewBox="0 0 24 24" fill="none" stroke="#b03a2e" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>';
const fmt = n => n < 1024 ? n + ' B' : n < 1048576 ? (n/1024).toFixed(0) + ' KB' : n < 1073741824 ? (n/1048576).toFixed(1) + ' MB' : (n/1073741824).toFixed(2) + ' GB';

function setConn(on) {
  $('pill').classList.toggle('off', !on);
  $('pillTxt').textContent = on ? 'Connected to your laptop' : 'Connection lost';
}

// ---- file / photo uploads (one at a time) ----
let queue = [], busy = false;
function upload(files) {
  [...files].forEach(f => {
    $('lh').style.display = 'block';
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = '<div class="meta"><div class="nm"></div><div class="sz">Waiting</div><div class="bar"><i></i></div></div><span class="stw"></span>';
    row.querySelector('.nm').textContent = f.name;
    list.prepend(row);
    queue.push({ f, row });
  });
  next();
}
function next() {
  if (busy || !queue.length) return;
  busy = true;
  const { f, row } = queue.shift();
  const sz = row.querySelector('.sz'), fill = row.querySelector('.bar i'), stw = row.querySelector('.stw');
  const finish = (ok, msg) => {
    row.classList.add(ok ? 'done' : 'fail');
    fill.style.width = '100%';
    sz.textContent = msg;
    stw.innerHTML = ok ? OK : BAD;
    setConn(ok || msg !== 'Connection failed');
    busy = false; next();
  };
  const fd = new FormData();
  fd.append('file', f, f.name);
  fd.append('pin', pin);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');
  sz.textContent = fmt(f.size);
  xhr.upload.onprogress = e => { if (e.lengthComputable) fill.style.width = (e.loaded / e.total * 100) + '%'; };
  xhr.onload = () => finish(xhr.status === 200, xhr.status === 200 ? fmt(f.size) + ' - saved' : 'Rejected (' + xhr.status + ')');
  xhr.onerror = () => finish(false, 'Connection failed');
  xhr.send(fd);
}

// ---- text to PC ----
function openSheet() { $('sheet').classList.add('on'); $('sheetMsg').textContent = ''; setTimeout(() => $('txt').focus(), 150); }
function closeSheet() { $('sheet').classList.remove('on'); }
$('openText').onclick = openSheet;
$('closeTxt').onclick = closeSheet;
$('txt').oninput = () => { $('sendTxt').disabled = !$('txt').value.trim(); };
$('sendTxt').onclick = async () => {
  const text = $('txt').value;
  if (!text.trim()) return;
  $('sendTxt').disabled = true;
  $('sheetMsg').className = ''; $('sheetMsg').textContent = 'Sending...';
  try {
    const r = await fetch('/text', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                     body: JSON.stringify({ text, pin }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      $('txt').value = '';
      $('sheetMsg').textContent = (j.clipboard ? 'Copied to PC clipboard' : 'Saved (clipboard not available on this PC)') + ' - ' + j.saved_as;
      setConn(true);
    } else {
      $('sheetMsg').className = 'err';
      $('sheetMsg').textContent = r.status === 403 ? 'Wrong PIN' : 'Failed (' + r.status + ')';
    }
  } catch (e) {
    $('sheetMsg').className = 'err';
    $('sheetMsg').textContent = 'Connection failed';
    setConn(false);
  }
  $('sendTxt').disabled = !$('txt').value.trim();
};

// ---- camera ----
async function startCam() {
  stopStream();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: facing, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false
    });
    video.srcObject = stream;
    $('cam').classList.add('on');
    document.body.style.overflow = 'hidden';
    $('note').textContent = '';
  } catch (e) {
    $('note').textContent = 'Camera unavailable (' + e.name + '). Open the https:// link and allow camera access.';
  }
}
function stopStream() { if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; } }
function closeCam() {
  stopStream();
  $('cam').classList.remove('on');
  document.body.style.overflow = '';
  shots.forEach(s => URL.revokeObjectURL(s.url));
  shots = []; render();
}
function render() {
  const strip = $('strip');
  strip.innerHTML = '';
  shots.forEach((s, i) => {
    const d = document.createElement('div');
    d.className = 'th';
    d.innerHTML = '<img src="' + s.url + '">';
    d.onclick = () => { URL.revokeObjectURL(s.url); shots.splice(i, 1); render(); };
    strip.appendChild(d);
  });
  strip.scrollLeft = strip.scrollWidth;
  const n = shots.length;
  $('count').style.visibility = n ? 'visible' : 'hidden';
  $('count').textContent = n + (n === 1 ? ' photo' : ' photos');
  $('sendAll').disabled = !n;
  $('sendAll').textContent = n ? 'Send ' + n : 'Send all';
}
$('openCam').onclick = startCam;
$('closeCam').onclick = closeCam;
$('flip').onclick = () => { facing = facing === 'environment' ? 'user' : 'environment'; startCam(); };
$('shutter').onclick = () => {
  if (!video.videoWidth) return;
  const c = $('canvas');
  c.width = video.videoWidth; c.height = video.videoHeight;
  c.getContext('2d').drawImage(video, 0, 0);
  const fl = $('flash'); fl.classList.remove('go'); void fl.offsetWidth; fl.classList.add('go');
  if (navigator.vibrate) navigator.vibrate(15);
  c.toBlob(b => {
    const name = 'photo_' + new Date().toISOString().replace(/[:.]/g, '-') + '_' + (shots.length + 1) + '.jpg';
    shots.push({ file: new File([b], name, { type: 'image/jpeg' }), url: URL.createObjectURL(b) });
    render();
  }, 'image/jpeg', 0.92);
};
$('sendAll').onclick = () => {
  const files = shots.map(s => s.file);
  closeCam();
  upload(files);
  window.scrollTo({ top: 0, behavior: 'smooth' });
};
$('media').onchange = e => { upload(e.target.files); e.target.value = ''; };
$('files').onchange = e => { upload(e.target.files); e.target.value = ''; };

// ---- PIN gate (only exists when the PC started with --pin) ----
if ($('unlock')) {
  const go = async () => {
    const r = await fetch('/check_pin', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify({ pin: $('pin').value }) });
    if (r.ok) { pin = $('pin').value; $('gate').style.display = 'none'; $('app').style.display = 'block'; }
    else { $('gateMsg').textContent = r.status === 429 ? 'Too many tries. Restart pctrn.' : 'Wrong PIN'; }
  };
  $('unlock').onclick = go;
  $('pin').onkeydown = e => { if (e.key === 'Enter') go(); };
}
// ---- session heartbeat ----
let missed = 0, ended = false;

async function heartbeat() {
  if (ended || busy) return;            // skip while an upload is running
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 3000);
  try {
    const r = await fetch('/ping', { cache: 'no-store', signal: ctl.signal });
    if (!r.ok) throw new Error('bad status');
    missed = 0;
    setConn(true);
  } catch (e) {
    missed++;
    setConn(false);
    if (missed >= 3) endSession();
  } finally {
    clearTimeout(timer);
  }
}

function endSession() {
  ended = true;
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  $('cam').classList.remove('on');
  $('sheet').classList.remove('on');
  $('ended').classList.add('on');
}

setInterval(heartbeat, 3000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) heartbeat(); });
</script>
</body>
</html>
"""


def pin_ok(given):
    global FAILS
    if PIN is None:
        return True
    if FAILS >= MAX_FAILS:
        return False
    if hmac.compare_digest(str(given), PIN):
        return True
    FAILS += 1
    return False


def copy_to_clipboard(text):
    """Put text on the PC clipboard. Returns True on success."""
    try:
        if sys.platform == "win32":
            # clip.exe reads UTF-16 so non-English text survives
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


# ---------------- link questions ----------------
LINK_TIMEOUT = 30          # seconds for each question and for the PIN
EXPIRE_AFTER = datetime.timedelta(hours=1)
NAME_RE = re.compile(r"^(\d{8}_\d{6})_")          # prefix pctrn gives every saved file
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
CV = threading.Condition()
PENDING = collections.deque()   # (generation, url), waiting for a console answer
GEN = 0                         # bumped whenever a new file/text cancels questions
ACTIVE = [False]                # True while a question is on screen
CANCELLED = object()
TIMED_OUT = object()


def find_links(text):
    """http/https and www. links, in the order they appear, without duplicates."""
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)]}>")
        if url.lower().startswith("www."):
            url = "https://" + url
        if len(url) > 8 and url not in out:
            out.append(url)
    return out


def cancel_pending(reason):
    """Call with CV held. Drops any waiting or on-screen link questions."""
    global GEN
    if ACTIVE[0] or PENDING:
        print(f"\n[!] Link question cancelled because {reason}. Link NOT opened.")
    GEN += 1
    PENDING.clear()
    CV.notify_all()


def read_line(timeout, token, echo=True):
    """Read one line from the console. Gives up after timeout, or when cancelled."""
    deadline = time.monotonic() + timeout
    buf = []
    if os.name == "nt":
        import msvcrt
        while True:
            if GEN != token:
                print()
                return CANCELLED
            if time.monotonic() >= deadline:
                print()
                return TIMED_OUT
            if not msvcrt.kbhit():
                time.sleep(0.1)
                continue
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                return "".join(buf)
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x00", "\xe0"):      # arrow/function keys: ignore
                msvcrt.getwch()
                continue
            if ch == "\b":
                if buf:
                    buf.pop()
                    if echo:
                        print("\b \b", end="", flush=True)
                continue
            buf.append(ch)
            if echo:
                print(ch, end="", flush=True)
    import select
    while True:
        if GEN != token:
            return CANCELLED
        if time.monotonic() >= deadline:
            return TIMED_OUT
        ready, _, _ = select.select([sys.stdin], [], [], 0.1)
        if ready:
            return sys.stdin.readline().strip()


def ask_link(url, token):
    if not LINK_PIN:
        print(f"\nLink found in the text (not opened; set PCTRN_LINK_PIN to enable):\n  {url}")
        return
    print(f"\nLink found in the text:\n  {url}")
    print(f"Open it in your browser? Type y and press Enter ({LINK_TIMEOUT} sec):")
    ans = read_line(LINK_TIMEOUT, token)
    if ans is CANCELLED:
        return
    if ans is TIMED_OUT:
        print("No answer - link not opened.")
        return
    if ans.lower() != "y":
        print("Link not opened.")
        return
    print("Enter PIN to open the link:", end=" ", flush=True)
    pin = read_line(LINK_TIMEOUT, token, echo=False)
    if pin is CANCELLED:
        return
    if pin is TIMED_OUT:
        print("No PIN - link not opened.")
        return
    if pin == LINK_PIN:
        webbrowser.open(url)
        print("Opened in your browser.")
    else:
        print("Wrong PIN - link not opened.")


def start_session_timer():
    """After SESSION_MINUTES, stop pctrn exactly as Ctrl+C would."""
    def expire():
        print(f"\n[!] {SESSION_MINUTES} minutes passed - pctrn is shutting down.")
        _thread.interrupt_main()   # raises KeyboardInterrupt in the console thread
    timer = threading.Timer(SESSION_MINUTES * 60, expire)
    timer.daemon = True
    timer.start()


def link_worker():
    """Runs in the console (main thread). Sleeps until a link is queued."""
    while True:
        with CV:
            while not PENDING:
                CV.wait(1)
            token, url = PENDING.popleft()
            ACTIVE[0] = True
        try:
            ask_link(url, token)
        finally:
            with CV:
                ACTIVE[0] = False


def cleanup_old_files():
    """Once, at startup: delete pctrn-saved files in SAVE_DIR older than one hour.
    Only regular files sitting directly in SAVE_DIR whose name has pctrn's timestamp
    prefix. Never folders, subfolders, symlinks, or anything outside SAVE_DIR."""
    root = os.path.realpath(SAVE_DIR)
    now = datetime.datetime.now()
    removed = 0
    for entry in os.scandir(root):
        m = NAME_RE.match(entry.name)
        if not m or not entry.is_file(follow_symlinks=False):
            continue
        try:
            arrived = datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        if now - arrived < EXPIRE_AFTER:
            continue
        path = os.path.join(root, entry.name)
        if os.path.dirname(os.path.realpath(path)) != root:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    print(f"Cleanup: removed {removed} file(s) older than 1 hour from {root}")


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    # need_pin is decided per request from the live PIN value, so a no-PIN run never shows the gate
    return render_template_string(PAGE, need_pin=PIN is not None)

@app.route("/ping")
def ping():
    return jsonify(ok=True)

@app.route("/check_pin", methods=["POST"])
def check_pin():
    if PIN is not None and FAILS >= MAX_FAILS:
        return jsonify(error="locked"), 429
    given = (request.get_json(silent=True) or {}).get("pin", "")
    return (jsonify(ok=True), 200) if pin_ok(given) else (jsonify(error="wrong"), 403)


@app.route("/upload", methods=["POST"])
def upload():
    if not pin_ok(request.form.get("pin", "")):
        return jsonify(error="bad pin"), 403
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400
    name = os.path.basename(f.filename).replace("\\", "_").replace("/", "_")
    final = f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{name}"
    f.save(os.path.join(SAVE_DIR, final))
    print(f"  received: {final}")
    with CV:
        cancel_pending("another file arrived")
    return jsonify(ok=True, saved_as=final)


@app.route("/text", methods=["POST"])
def text_to_pc():
    data = request.get_json(silent=True) or {}
    if not pin_ok(data.get("pin", "")):
        return jsonify(error="bad pin"), 403
    text = data.get("text") or ""
    if not text.strip():
        return jsonify(error="empty"), 400
    if len(text) > MAX_TEXT:
        return jsonify(error="too long"), 413
    saved = f"{datetime.datetime.now():%Y%m%d_%H%M%S}_text.txt"
    with open(os.path.join(SAVE_DIR, saved), "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(text)
    ok = copy_to_clipboard(text)
    print(f"  text received: {saved} (clipboard: {'yes' if ok else 'no'})")
    links = find_links(text)
    with CV:
        cancel_pending("another text arrived")
        for url in links:
            PENDING.append((GEN, url))
        CV.notify_all()
    if links:
        print(f"  {len(links)} link(s) found - answer the question(s) in this window")
    return jsonify(ok=True, saved_as=saved, clipboard=ok)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # nothing is sent; just finds the Wi-Fi interface
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def port_in_use(port):
    """True if something is already listening on the port (e.g. an old pctrn window)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_cert(ip, host):
    """Create a self-signed HTTPS certificate once (needed for the live camera)."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    os.makedirs(CERT_DIR, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pctrn")])
    san = x509.SubjectAlternativeName([
        x509.DNSName(host), x509.DNSName(host + ".local"), x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address(ip)),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))
    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def main():
    global PIN
    ap = argparse.ArgumentParser(prog="pctrn")
    ap.add_argument("--pin", action="store_true", help="require a random 4-digit PIN on the phone")
    args = ap.parse_args()

    # The bug: an old "pctrn --pin" window still running on this port keeps serving the
    # PIN page to the phone. Refuse to start instead of silently sharing that old server.
    if port_in_use(PORT):
        print(f"\nPort {PORT} is already in use: an older pctrn is still running (probably with a PIN).")
        print("Close that window (or press Ctrl+C in it), then run pctrn again.")
        sys.exit(1)

    cleanup_old_files()
    if args.pin:
        PIN = f"{secrets.randbelow(10000):04d}"
    ip = local_ip()
    host = socket.gethostname()
    ensure_cert(ip, host)

    url_ip = f"https://{ip}:{PORT}"
    url_host = f"https://{host}.local:{PORT}"

    print("\n=== pctrn running ===")
    print(f"Saving to : {SAVE_DIR}")
    print(f"PIN       : {PIN if PIN else 'off (run  pctrn --pin  to enable)'}")
    print(f"Open on phone (scan QR or type):\n   {url_ip}\n   {url_host}   (name-based, survives IP changes on most phones)\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url_ip)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:
        pass
    print("\nFirst time: phone will warn 'not secure' -> Advanced -> Proceed. Press Ctrl+C to stop.\n")

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Thread(
        target=app.run,
        kwargs=dict(host="0.0.0.0", port=PORT, ssl_context=(CERT_FILE, KEY_FILE), threaded=True),
        daemon=True,
    ).start()
    print("Link questions will appear in this window. Closing pctrn stops everything.")
    if SESSION_MINUTES > 0:
        print(f"pctrn stops automatically after {SESSION_MINUTES} minutes.\n")
        start_session_timer()
    link_worker()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
