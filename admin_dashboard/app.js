/**
 * Zakariya Automator — Admin Dashboard v2
 * Live Firebase analytics with hourly chart, error breakdown,
 * avg time per video, videos/hour, active users, and more.
 */

// ── Firebase Config ──────────────────────────────────────────────
const firebaseConfig = {
    apiKey: "AIzaSyAvybonSnS26AOCwReCrvpIgkKLCdiGsoQ",
    authDomain: "video-genertion.firebaseapp.com",
    projectId: "video-genertion",
    storageBucket: "video-genertion.firebasestorage.app",
    databaseURL: "https://video-genertion-default-rtdb.firebaseio.com",
    messagingSenderId: "167351546759",
    appId: "1:167351546759:web:8f950b30339ba4c99d1922",
    measurementId: "G-LYYW1SCN37"
};

firebase.initializeApp(firebaseConfig);
const db   = firebase.firestore();
const rtdb = firebase.database();

// ── State ────────────────────────────────────────────────────────
let allEvents      = [];
let allVideoEvents = [];   // all video_generated events
let allFailEvents  = [];   // all video_failed events
const MAX_FEED     = 40;

// ── Helpers ──────────────────────────────────────────────────────

function timeAgo(ts) {
    if (!ts) return "—";
    const now = Date.now();
    let t;
    if (typeof ts === "string")       t = new Date(ts).getTime();
    else if (typeof ts === "number")  t = ts < 1e12 ? ts * 1000 : ts;
    else return "—";
    const s = Math.floor((now - t) / 1000);
    if (s < 60)    return `${s}s ago`;
    if (s < 3600)  return `${Math.floor(s/60)}m ago`;
    if (s < 86400) return `${Math.floor(s/3600)}h ago`;
    return `${Math.floor(s/86400)}d ago`;
}

function fmtTime(ts) {
    if (!ts) return "—";
    try { return new Date(ts).toLocaleTimeString("en-US", {hour:"2-digit", minute:"2-digit"}); }
    catch { return "—"; }
}

function fmtDuration(seconds) {
    if (!seconds || isNaN(seconds)) return "—";
    if (seconds < 60)  return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s`;
    return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
}

function el(id) { return document.getElementById(id); }

function setText(id, val) {
    const e = el(id);
    if (e) e.textContent = val;
}

function escHtml(s) {
    if (!s) return "";
    return String(s)
        .replace(/&/g,"&amp;")
        .replace(/</g,"&lt;")
        .replace(/>/g,"&gt;")
        .replace(/"/g,"&quot;");
}

function getInitials(name) {
    if (!name) return "?";
    return name.split(/[\s\-()]+/).filter(Boolean).slice(0,2).map(w=>w[0]).join("").toUpperCase();
}

function setConnection(ok) {
    const badge = el("connectionStatus");
    if (!badge) return;
    badge.className = "status-badge " + (ok ? "connected" : "disconnected");
    badge.querySelector("span:last-child").textContent = ok ? "Live" : "Disconnected";
}

// ── Listener 1: Presence (RTDB) ──────────────────────────────────
function listenPresence() {
    rtdb.ref("presence").on("value", snap => {
        const data  = snap.val() || {};
        const now   = Math.floor(Date.now() / 1000);
        let online  = 0, generating = 0;
        const onlineUsers = [];

        Object.entries(data).forEach(([id, info]) => {
            const isOnline = info.online && (now - (info.last_ping || 0)) < 120;
            if (!isOnline) return;
            online++;
            if (info.status === "generating") generating++;
            onlineUsers.push({
                id, machine: info.machine || id,
                version: info.version || "?",
                status: info.status || "idle",
                prompt: info.current_prompt || "",
                lastPing: info.last_ping
            });
        });

        setText("onlineUsers", online);
        setText("onlineBadge", online);
        setText("generatingNow", `${generating} generating`);
        renderOnlineUsers(onlineUsers);
        setConnection(true);
        setText("lastUpdated", "Updated " + new Date().toLocaleTimeString());
    }, () => setConnection(false));
}

// ── Listener 2: Devices (Firestore) ─────────────────────────────
function listenDevices() {
    db.collection("devices").onSnapshot(snap => {
        const devs = [];
        const verMap = {};
        snap.forEach(doc => {
            const d = { id: doc.id, ...doc.data() };
            devs.push(d);
            const v = d.app_version || "unknown";
            verMap[v] = (verMap[v] || 0) + 1;
        });
        setText("totalDevices", devs.length);
        setText("deviceBadge", devs.length);
        renderDeviceTable(devs);
        renderVersionList(verMap, devs.length);
    });
}

// ── Listener 3: Events (Firestore) — live feed ──────────────────
function listenEvents() {
    db.collection("events")
        .orderBy("timestamp", "desc")
        .limit(MAX_FEED)
        .onSnapshot(snap => {
            allEvents = [];
            snap.forEach(doc => allEvents.push(doc.data()));
            renderEventList(allEvents);
        });
}

// ── Aggregate counts (all events, not just last 50) ──────────────
async function updateAggregates() {
    try {
        const [vSnap, fSnap] = await Promise.all([
            db.collection("events").where("type", "==", "video_generated").get(),
            db.collection("events").where("type", "==", "video_failed").get()
        ]);

        allVideoEvents = [];
        vSnap.forEach(d => allVideoEvents.push(d.data()));
        allFailEvents  = [];
        fSnap.forEach(d => allFailEvents.push(d.data()));

        const totalVid  = allVideoEvents.length;
        const totalFail = allFailEvents.length;
        const total     = totalVid + totalFail;
        const rate      = total > 0 ? Math.round((totalVid / total) * 100) : 0;

        // Today
        const todayStr = new Date().toISOString().slice(0, 10);
        const todayVid = allVideoEvents.filter(e => (e.timestamp||"").startsWith(todayStr)).length;
        const todayFail= allFailEvents.filter(e  => (e.timestamp||"").startsWith(todayStr)).length;
        const todayTotal = todayVid + todayFail;
        const todayRate  = todayTotal > 0 ? Math.round((todayVid / todayTotal) * 100) : 0;

        // Avg time (duration field in events)
        const durations = allVideoEvents
            .map(e => typeof e.duration === "number" ? e.duration : null)
            .filter(d => d !== null && d > 0);
        const avgSec = durations.length > 0
            ? durations.reduce((a, b) => a + b, 0) / durations.length
            : null;

        // Videos/hour (last 60 min)
        const oneHourAgo = Date.now() - 3600_000;
        const lastHourVids = allVideoEvents.filter(e => {
            const t = e.timestamp ? new Date(e.timestamp).getTime() : 0;
            return t > oneHourAgo;
        }).length;

        // Error breakdown
        const errMap = { "Timeout": 0, "Rate Limit": 0, "Policy": 0, "No Textbox": 0, "Captcha": 0, "Other": 0 };
        allFailEvents.forEach(e => {
            const et = (e.error_type || "").toLowerCase();
            if      (et.includes("timeout"))   errMap["Timeout"]++;
            else if (et.includes("rate") || et.includes("high_demand")) errMap["Rate Limit"]++;
            else if (et.includes("policy") || et.includes("fatal"))     errMap["Policy"]++;
            else if (et.includes("textbox"))   errMap["No Textbox"]++;
            else if (et.includes("captcha"))   errMap["Captcha"]++;
            else                               errMap["Other"]++;
        });

        // Sessions (app_started events)
        const sessSnap = await db.collection("events").where("type", "==", "app_started").get();
        const totalSessions = sessSnap.size;

        // Best session rate
        // sessions per device
        const devSessions = {};
        const devSuccess  = {};
        allVideoEvents.forEach(e => {
            if (!e.device_id) return;
            devSuccess[e.device_id] = (devSuccess[e.device_id] || 0) + 1;
        });
        let bestRate = 0;
        Object.values(devSuccess).forEach(n => {
            if (n > bestRate) bestRate = n;
        });

        // ── Update DOM ──
        setText("totalVideos", totalVid.toLocaleString());
        setText("totalAttempts", `${total.toLocaleString()} attempts`);
        setText("todayVideos", todayVid.toLocaleString());
        setText("todayRate", `${todayRate}% success`);
        setText("successRate", rate + "%");
        setText("totalFailed", `${totalFail.toLocaleString()} failed`);
        setText("avgTime", avgSec ? fmtDuration(avgSec) : "N/A");
        setText("videosPerHour", lastHourVids);

        renderErrorBreakdown(errMap, totalFail);
        renderHourlyChart();
        renderPerfMetrics({
            totalSessions, totalVid, totalFail,
            avgSec, lastHourVids, total,
            todayVid, todayFail
        });

    } catch (e) {
        console.error("Aggregate error:", e);
    }
}

// ── Render: Hourly Chart ─────────────────────────────────────────
function renderHourlyChart() {
    const barsEl   = el("hourlyChart");
    const labelsEl = el("hourlyLabels");
    if (!barsEl) return;

    // Last 24 hours bucketed
    const now = Date.now();
    const buckets = Array.from({length:24}, (_,i) => ({
        hour: new Date(now - (23-i)*3600_000).getHours(),
        start: now - (23-i)*3600_000,
        end:   now - (23-i-1)*3600_000,
        success: 0, fail: 0
    }));

    allVideoEvents.forEach(e => {
        const t = e.timestamp ? new Date(e.timestamp).getTime() : 0;
        const b = buckets.find(b => t >= b.start && t < b.end);
        if (b) b.success++;
    });
    allFailEvents.forEach(e => {
        const t = e.timestamp ? new Date(e.timestamp).getTime() : 0;
        const b = buckets.find(b => t >= b.start && t < b.end);
        if (b) b.fail++;
    });

    const maxVal = Math.max(...buckets.map(b => b.success + b.fail), 1);
    const HEIGHT = 110; // px

    barsEl.innerHTML = buckets.map(b => {
        const total     = b.success + b.fail;
        const sPct      = Math.round((b.success / maxVal) * HEIGHT);
        const fPct      = Math.round((b.fail    / maxVal) * HEIGHT);
        return `
            <div class="bar-col" title="${b.hour}:00 — ${b.success} ok, ${b.fail} fail">
                <div style="display:flex;flex-direction:column;align-items:center;width:100%;gap:1px">
                    <div class="bar-fail"  style="height:${fPct}px"></div>
                    <div class="bar-inner" style="height:${sPct}px"></div>
                </div>
            </div>`;
    }).join("");

    // Labels: every 6 hours
    labelsEl.innerHTML = buckets.map((b,i) =>
        `<span>${i % 6 === 0 ? b.hour + "h" : ""}</span>`
    ).join("");
}

// ── Render: Error Breakdown ──────────────────────────────────────
function renderErrorBreakdown(errMap, totalFail) {
    const cont = el("errorBreakdown");
    if (!cont) return;

    const colors = {
        "Timeout":    "#f59e0b",
        "Rate Limit": "#ef4444",
        "Policy":     "#ec4899",
        "No Textbox": "#a855f7",
        "Captcha":    "#06b6d4",
        "Other":      "#6b7280"
    };

    const maxErr = Math.max(...Object.values(errMap), 1);
    setText("totalErrorsBadge", totalFail);

    cont.innerHTML = Object.entries(errMap).map(([label, count]) => {
        const pct = Math.round((count / maxErr) * 100);
        const color = colors[label] || "#6b7280";
        return `
            <div class="error-row">
                <span class="error-label">${label}</span>
                <div class="error-bar-wrap">
                    <div class="error-bar" style="width:${pct}%;background:${color};opacity:0.85"></div>
                </div>
                <span class="error-count" style="color:${color}">${count}</span>
            </div>`;
    }).join("");
}

// ── Render: Performance Metrics ──────────────────────────────────
function renderPerfMetrics(m) {
    const cont = el("perfMetrics");
    if (!cont) return;

    const items = [
        { icon:"🚀", name:"Videos / Hour",    val: m.lastHourVids },
        { icon:"⏱️", name:"Total Time Spent",  val: m.avgSec ? fmtDuration(m.avgSec * m.totalVid) : "N/A" },
        { icon:"📋", name:"Total Prompts",     val: m.total.toLocaleString() },
        { icon:"🔗", name:"Total Sessions",    val: m.totalSessions.toLocaleString() },
        { icon:"📅", name:"Today Success",     val: m.todayVid },
        { icon:"🔴", name:"Total Errors",      val: m.totalFail },
    ];

    cont.innerHTML = items.map(item => `
        <div class="perf-item">
            <div class="perf-left">
                <span class="perf-icon">${item.icon}</span>
                <span class="perf-name">${item.name}</span>
            </div>
            <span class="perf-val">${item.val}</span>
        </div>`
    ).join("");
}

// ── Render: Online Users ─────────────────────────────────────────
function renderOnlineUsers(users) {
    const cont = el("onlineList");
    if (!cont) return;
    if (!users.length) {
        cont.innerHTML = '<div class="empty-state">No users online</div>';
        return;
    }
    cont.innerHTML = users.map(u => `
        <div class="user-item">
            <div class="user-info">
                <div class="user-avatar">${getInitials(u.machine)}</div>
                <div>
                    <div class="user-name">${escHtml(u.machine)}</div>
                    <div class="user-status">${u.prompt ? "🎬 " + escHtml(u.prompt.slice(0,45)) + "…" : "⏸ Idle"}</div>
                </div>
            </div>
            <div class="user-right">
                ${u.status === "generating" ? '<span class="generating-badge">● GEN</span>' : ""}
                <span class="user-version">v${escHtml(u.version)}</span>
            </div>
        </div>`
    ).join("");
}

// ── Render: Event Feed ───────────────────────────────────────────
function renderEventList(events) {
    const cont = el("eventList");
    if (!cont) return;
    if (!events.length) {
        cont.innerHTML = '<div class="empty-state">No recent events</div>';
        return;
    }
    const ICONS = {
        video_generated: ["✅","success"],
        video_failed:    ["❌","failure"],
        app_started:     ["🟢","info"],
        app_closed:      ["🔴","info"]
    };
    cont.innerHTML = events.slice(0, 35).map(e => {
        const [icon, cls] = ICONS[e.type] || ["📋","info"];
        let text;
        const dev = escHtml((e.device_id||"").slice(-8) || "?");
        switch (e.type) {
            case "video_generated":
                text = `<strong>${dev}</strong> generated a video`; break;
            case "video_failed":
                text = `<strong>${dev}</strong> failed (${escHtml(e.error_type||"unknown")})`; break;
            case "app_started":
                text = `<strong>${escHtml(e.machine_name||dev)}</strong> came online`; break;
            case "app_closed":
                text = `<strong>${dev}</strong> went offline`; break;
            default:
                text = `<strong>${dev}</strong> ${escHtml(e.type||"event")}`;
        }
        return `
            <div class="event-item ${cls}">
                <span class="event-icon">${icon}</span>
                <span class="event-text">${text}</span>
                <span class="event-time">${fmtTime(e.timestamp)}</span>
            </div>`;
    }).join("");
}

// ── Render: Versions ─────────────────────────────────────────────
function renderVersionList(verMap, total) {
    const cont = el("versionList");
    if (!cont) return;
    const entries = Object.entries(verMap).sort((a,b) => b[1]-a[1]);
    if (!entries.length) {
        cont.innerHTML = '<div class="empty-state">No version data</div>';
        return;
    }
    cont.innerHTML = entries.map(([ver, cnt]) => {
        const pct = total > 0 ? Math.round((cnt/total)*100) : 0;
        return `
            <div class="version-item">
                <span class="version-tag">v${escHtml(ver)}</span>
                <div class="version-bar-wrap">
                    <div class="version-bar" style="width:${pct}%"></div>
                </div>
                <span class="version-count">${cnt} (${pct}%)</span>
            </div>`;
    }).join("");
}

// ── Render: Device Table ─────────────────────────────────────────
function renderDeviceTable(devices) {
    const cont = el("deviceTable");
    if (!cont) return;
    if (!devices.length) {
        cont.innerHTML = '<div class="empty-state">No devices registered</div>';
        return;
    }
    devices.sort((a,b) => (b.is_online?1:0) - (a.is_online?1:0));
    cont.innerHTML = devices.map(d => {
        const online  = d.is_online;
        const machine = escHtml(d.machine_name || d.id);
        const ver     = escHtml(d.app_version || "?");
        const os      = escHtml(d.os ? `${d.os} ${d.os_version||""}` : "—");
        const lastVid = d.last_video ? timeAgo(d.last_video) : "—";
        const seen    = d.last_seen ? timeAgo(d.last_seen) : "—";
        const statusCls = online ? "chip-green" : "chip-muted";
        const statusTxt = online ? "Online" : "Offline";
        return `
            <div class="device-row">
                <div class="online-dot ${online ? "is-online" : "is-offline"}"></div>
                <div class="device-name" title="${machine}">${machine}</div>
                <div class="device-version">v${ver}</div>
                <div style="color:var(--text-secondary)">${os}</div>
                <div><span class="stat-chip ${statusCls}">${statusTxt}</span></div>
                <div class="device-last-seen">📹 ${lastVid}</div>
                <div class="device-last-seen">👁 ${seen}</div>
            </div>`;
    }).join("");
}

// ── Clear feed ───────────────────────────────────────────────────
function clearEvents() {
    allEvents = [];
    renderEventList([]);
}

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    listenPresence();
    listenDevices();
    listenEvents();
    updateAggregates();

    // Refresh aggregates every 30s
    setInterval(updateAggregates, 30_000);

    // Refresh chart every 60s
    setInterval(renderHourlyChart, 60_000);

    // Refresh time labels every 30s
    setInterval(() => {
        setText("lastUpdated", "Updated " + new Date().toLocaleTimeString());
    }, 30_000);
});
