# 📞 Call Console — Frontend Integration Guide

> **Backend Status:** ✅ Fully implemented and tested
> **Frontend Status:** 🔧 Needs integration (this document)
> **Last Updated:** September 26, 2026

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Authentication](#2-authentication)
3. [WebRTC SIP Client Setup (Removes Zoiper Dependency)](#3-webrtc-sip-client-setup)
4. [API Reference](#4-api-reference)
5. [Click-to-Call Button](#5-click-to-call-button)
6. [Inbound Call Screen-Pop](#6-inbound-call-screen-pop)
7. [Active Calls Monitor](#7-active-calls-monitor)
8. [Missed Call / RNR Queue](#8-missed-call--rnr-queue)
9. [Call History & Audio Playback](#9-call-history--audio-playback)
10. [Environment Variables](#10-environment-variables)
11. [Integration Checklist](#11-integration-checklist)

---

## 1. Overview & Architecture

```
┌─────────────────────────────────────────────────────────┐
│               FRONTEND (Your Responsibility)             │
│                                                          │
│  ┌─────────────────────┐   ┌──────────────────────────┐ │
│  │  WebRTC SIP Client  │   │    Call Console UI        │ │
│  │  (SIP.js / JsSIP)   │   │  - Click-to-Call button   │ │
│  │  Runs silently in   │   │  - Inbound screen-pop     │ │
│  │  background when    │   │  - Active calls list      │ │
│  │  agent is logged in │   │  - Missed call queue      │ │
│  └─────────────────────┘   │  - Call history + audio   │ │
│                             └──────────────────────────┘ │
└────────────────────┬────────────────────────────────────┘
                     │ REST API calls
                     ▼
┌─────────────────────────────────────────────────────────┐
│              BACKEND (Already Done ✅)                    │
│  Django 5 · PostgreSQL · HoduPBX Adapter                │
│                                                          │
│  POST /api/v1/calls/click-to-call/                       │
│  GET  /api/v1/calls/                                     │
│  GET  /api/v1/calls/active-calls/                        │
│  GET  /api/v1/callback-tasks/                            │
│  GET  /api/v1/patients/lookup/?mobile=<number>           │
│  POST /api/v1/webhooks/telephony/<hospital_id>/          │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
              HoduPBX Cloud PBX
              ecallpbx.konnectcom.in
              DID: +917123101648  |  Extension: 101
```

### How Click-to-Call Works (IMPORTANT — Read This First)

```
Agent clicks "📞 Call" on patient card
         │
         ▼
Frontend → POST /api/v1/calls/click-to-call/ { "to_number": "9XXXXXXXXX" }
         │
         ▼  HTTP 201 returned IMMEDIATELY (non-blocking)
         │
         ▼  [HoduPBX in background thread on server]
         │
         ├── Step 1: HoduPBX rings Extension 101
         │           → WebRTC SIP client in browser AUTO-ANSWERS (silent)
         │
         └── Step 2: HoduPBX dials patient number
                     → Patient phone rings
                     → Bridge established ✅
```

> ⚠️ The API returns `201` instantly. The WebRTC client **must be running** in the browser to auto-answer Step 1.
> Without it, the agent must manually pick up Zoiper/a physical phone.

---

## 2. Authentication

All API calls need a JWT Bearer token:

```http
POST /api/v1/auth/login/
Content-Type: application/json

{
  "email": "agent@hospital.com",
  "password": "password"
}
```

Response:
```json
{ "access": "eyJhbGci...", "refresh": "eyJhbGci..." }
```

Store `access` token and include in all requests:
```
Authorization: Bearer eyJhbGci...
```

---

## 3. WebRTC SIP Client Setup

This removes the Zoiper dependency entirely. The browser becomes the SIP phone.

### Install

```bash
npm install sip.js
```

### SIP Client Module

```javascript
// src/telephony/sipClient.js
import { UserAgent, Registerer, SessionState } from 'sip.js';

const CONFIG = {
  wsServer:    'wss://ecallpbx.konnectcom.in:8089/ws', // confirm port with HoduPBX support
  sipUri:      'sip:1048101@ecallpbx.konnectcom.in',
  password:    'Hospital@123',
  displayName: 'Reception',
};

let ua = null;
let registerer = null;

export function initSIPClient({ onStatusChange, onCallBridged }) {
  const uri = UserAgent.makeURI(CONFIG.sipUri);
  ua = new UserAgent({
    uri,
    transportOptions: { server: CONFIG.wsServer },
    authorizationPassword: CONFIG.password,
    displayName: CONFIG.displayName,
  });

  // Handle incoming call leg (Step 1 of click2call)
  ua.delegate = {
    onInvite(invitation) {
      console.log('[SIP] Bridge call incoming — auto-answering');
      onStatusChange?.('busy');

      // Auto-answer silently (no UI ring needed — agent initiated this call)
      invitation.accept({
        sessionDescriptionHandlerOptions: {
          constraints: { audio: true, video: false },
        },
      });

      invitation.stateChange.addListener((state) => {
        if (state === SessionState.Established) {
          onCallBridged?.();
        }
        if (state === SessionState.Terminated) {
          onStatusChange?.('online');
        }
      });
    },
  };

  ua.start().then(() => {
    registerer = new Registerer(ua);
    registerer.register();
    registerer.stateChange.addListener((state) => {
      onStatusChange?.(state === 'Registered' ? 'online' : 'offline');
    });
  });
}

export function destroySIPClient() {
  registerer?.unregister();
  ua?.stop();
}
```

### React Integration

```jsx
// src/App.jsx  (or your main layout)
import { useEffect, useState } from 'react';
import { initSIPClient, destroySIPClient } from './telephony/sipClient';

export function AppLayout({ children }) {
  const [sipStatus, setSipStatus] = useState('offline');

  useEffect(() => {
    initSIPClient({
      onStatusChange: setSipStatus,
      onCallBridged: () => console.log('[SIP] Bridge connected — patient is ringing'),
    });
    return () => destroySIPClient();
  }, []);

  return (
    <div>
      <header>
        <StatusDot status={sipStatus} />
      </header>
      {children}
    </div>
  );
}

function StatusDot({ status }) {
  const map = {
    online:  { color: '#22c55e', label: '● Online' },
    offline: { color: '#9ca3af', label: '○ Offline' },
    busy:    { color: '#f59e0b', label: '◉ On Call' },
  };
  const s = map[status] || map.offline;
  return <span style={{ color: s.color, fontWeight: 600 }}>{s.label}</span>;
}
```

> ⚠️ **WebRTC requires HTTPS in production.** It will NOT work over HTTP. Your domain must have SSL.

---

## 4. API Reference

### 4.1 Click-to-Call

```http
POST /api/v1/calls/click-to-call/
Authorization: Bearer <token>
Content-Type: application/json

{
  "to_number": "9764743275",
  "patient": 42
}
```

Response `201`:
```json
{
  "id": 15,
  "status": "rnr",
  "from_number": "101",
  "to_number": "9764743275",
  "provider_call_id": "pending-101-9764743275",
  "started_at": "2026-09-26T18:02:49+05:30"
}
```

> `status` starts as `"rnr"`. Background thread updates it to `"answered"` + real UUID after WebRTC leg answers.

---

### 4.2 List Call History

```http
GET /api/v1/calls/
GET /api/v1/calls/?direction=outbound&status=answered
GET /api/v1/calls/?patient=42
```

---

### 4.3 Active Calls (Live)

```http
GET /api/v1/calls/active-calls/
```

Returns array of live call legs from HoduPBX.

---

### 4.4 Patient Lookup (Screen-Pop)

```http
GET /api/v1/patients/lookup/?mobile=9764743275
```

Returns patient record matched by phone number.

---

### 4.5 Missed Call Queue

```http
GET  /api/v1/callback-tasks/?status=pending
POST /api/v1/callback-tasks/<id>/claim/
POST /api/v1/callback-tasks/<id>/complete/
     Body: { "notes": "Appointment booked." }
```

---

## 5. Click-to-Call Button

```jsx
// src/components/CallButton.jsx
import { useState } from 'react';

export function CallButton({ toNumber, patientId, onCallStarted }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function dial() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/v1/calls/click-to-call/', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('access_token')}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ to_number: toNumber, patient: patientId }),
      });

      if (!res.ok) throw new Error((await res.json()).detail || 'Call failed');
      const call = await res.json();
      onCallStarted?.(call);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button onClick={dial} disabled={loading}>
        {loading ? '⏳ Connecting...' : `📞 Call ${toNumber}`}
      </button>
      {error && <p style={{ color: 'red' }}>{error}</p>}
    </>
  );
}
```

**Usage on patient card:**
```jsx
<CallButton
  toNumber={patient.mobile}
  patientId={patient.id}
  onCallStarted={(call) => toast(`Calling ${patient.full_name}...`)}
/>
```

---

## 6. Inbound Call Screen-Pop

When a patient calls the hospital DID, HoduPBX fires the backend webhook.
Your frontend needs to detect new inbound calls and show a pop-up with patient info.

### Poll for New Inbound Calls

```javascript
// src/telephony/useInboundCalls.js
import { useEffect, useRef } from 'react';

export function useInboundCallPoller(onNewCall) {
  const lastChecked = useRef(new Date().toISOString());

  useEffect(() => {
    const poll = setInterval(async () => {
      const res = await fetch(
        `/api/v1/calls/?direction=inbound&ordering=-started_at`,
        { headers: { Authorization: `Bearer ${getToken()}` } }
      );
      const { results } = await res.json();

      const newCalls = results.filter(c => c.started_at > lastChecked.current);
      newCalls.forEach(onNewCall);

      if (newCalls.length) lastChecked.current = new Date().toISOString();
    }, 5000); // every 5 seconds

    return () => clearInterval(poll);
  }, []);
}
```

### Screen-Pop Component

```jsx
// src/components/ScreenPop.jsx
import { useEffect, useState } from 'react';

export function ScreenPop({ call, onDismiss }) {
  const [patient, setPatient] = useState(null);

  useEffect(() => {
    fetch(`/api/v1/patients/lookup/?mobile=${call.from_number}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then(r => r.ok ? r.json() : null)
      .then(setPatient);
  }, [call.from_number]);

  return (
    <div className="screen-pop">
      <div className="screen-pop-header">
        <b>📲 Incoming Call</b>
        <span>{call.from_number}</span>
      </div>

      {patient ? (
        <div>
          <h3>{patient.full_name}</h3>
          <p>Last visit: {patient.last_visit}</p>
          <a href={`/patients/${patient.id}`}>View Full Profile →</a>
        </div>
      ) : (
        <div>
          <p>Unknown caller</p>
          <a href={`/patients/new?mobile=${call.from_number}`}>Register New Patient →</a>
        </div>
      )}

      <button onClick={onDismiss}>Dismiss</button>
    </div>
  );
}
```

---

## 7. Active Calls Monitor

```jsx
// src/components/ActiveCalls.jsx
import { useEffect, useState } from 'react';

export function ActiveCallsMonitor() {
  const [calls, setCalls] = useState([]);

  useEffect(() => {
    const fetch_ = () =>
      fetch('/api/v1/calls/active-calls/', {
        headers: { Authorization: `Bearer ${getToken()}` },
      }).then(r => r.json()).then(setCalls);

    fetch_();
    const interval = setInterval(fetch_, 10000);
    return () => clearInterval(interval);
  }, []);

  if (!calls.length) return <p>No active calls.</p>;

  return (
    <ul>
      {calls.map((c, i) => (
        <li key={i}>
          🟢 {c.caller} → {c.callee}
          <span style={{ color: '#888', marginLeft: 8 }}>{c.duration}</span>
        </li>
      ))}
    </ul>
  );
}
```

---

## 8. Missed Call / RNR Queue

```jsx
// src/components/RNRQueue.jsx
import { useEffect, useState } from 'react';
import { CallButton } from './CallButton';

export function RNRQueue() {
  const [tasks, setTasks] = useState([]);

  const load = () =>
    fetch('/api/v1/callback-tasks/?status=pending', {
      headers: { Authorization: `Bearer ${getToken()}` },
    }).then(r => r.json()).then(d => setTasks(d.results));

  useEffect(() => { load(); }, []);

  async function claim(id) {
    await fetch(`/api/v1/callback-tasks/${id}/claim/`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    load();
  }

  return (
    <table>
      <thead><tr><th>Phone</th><th>Missed At</th><th>SLA</th><th>Actions</th></tr></thead>
      <tbody>
        {tasks.map(t => (
          <tr key={t.id}>
            <td>{t.phone_number || t.call?.from_number}</td>
            <td>{new Date(t.created_at).toLocaleTimeString()}</td>
            <td style={{ color: new Date(t.sla_due_at) < new Date() ? 'red' : 'green' }}>
              {new Date(t.sla_due_at) < new Date() ? '⚠️ Overdue' : '✅ In SLA'}
            </td>
            <td>
              <CallButton toNumber={t.phone_number || t.call?.from_number} />
              <button onClick={() => claim(t.id)}>Claim</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

---

## 9. Call History & Audio Playback

```jsx
// src/components/CallHistory.jsx
export function CallHistory({ patientId }) {
  const [calls, setCalls] = useState([]);

  useEffect(() => {
    const qs = patientId ? `?patient=${patientId}` : '';
    fetch(`/api/v1/calls/${qs}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }).then(r => r.json()).then(d => setCalls(d.results));
  }, [patientId]);

  return (
    <div>
      {calls.map(call => (
        <div key={call.id} style={{ borderBottom: '1px solid #eee', padding: 8 }}>
          <span>{call.direction === 'inbound' ? '📲' : '📞'}</span>
          <b style={{ margin: '0 8px' }}>{call.from_number} → {call.to_number}</b>
          <span>{call.status}</span>
          <span style={{ color: '#888', marginLeft: 8 }}>{call.duration_seconds}s</span>

          {call.recording_url && (
            <div style={{ marginTop: 4 }}>
              <audio controls src={call.recording_url} style={{ width: '100%' }} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
```

---

## 10. Environment Variables

Add to your frontend `.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_SIP_SERVER=wss://ecallpbx.konnectcom.in:8089/ws
VITE_SIP_URI=sip:1048101@ecallpbx.konnectcom.in
VITE_SIP_PASSWORD=Hospital@123
VITE_SIP_DISPLAY_NAME=Reception
VITE_HOSPITAL_ID=973bb98e-263c-4893-9337-d80e2f9b77db
```

> **Note:** For production, store SIP credentials securely (fetch from backend on login, not hardcoded in `.env`).

---

## 11. Integration Checklist

### WebRTC / SIP
- [ ] Install `sip.js`
- [ ] Confirm WSS endpoint with HoduPBX (`sales@konnectcom.in`)
- [ ] `initSIPClient()` called on agent login
- [ ] Online/Offline/Busy status badge in header
- [ ] Auto-answer incoming bridge leg (silent)
- [ ] HTTPS configured in production

### Click-to-Call
- [ ] `<CallButton>` on patient cards
- [ ] `<CallButton>` on enquiry/lead cards
- [ ] Loading state while connecting
- [ ] Error handling (extension busy, etc.)

### Inbound Screen-Pop
- [ ] Poll `/calls/?direction=inbound` every 5s
- [ ] Auto-lookup patient by `from_number`
- [ ] `<ScreenPop>` modal with patient info
- [ ] "Register New Patient" for unknown callers

### Active Calls
- [ ] `<ActiveCallsMonitor>` on dashboard (polls every 10s)

### Missed Call Queue
- [ ] `<RNRQueue>` component
- [ ] SLA countdown timer
- [ ] Claim + Call buttons per row

### Call History
- [ ] `<CallHistory>` on patient profile
- [ ] `<audio>` player for `recording_url`

---

## Support / Contacts

| Topic | Contact |
|---|---|
| WSS WebSocket port for SIP.js | HoduPBX: `sales@konnectcom.in` |
| Backend API bugs | Django server logs (`python manage.py runserver`) |
| SIP not registering | Verify credentials in HoduPBX portal → Extension 101 |
| WebRTC no audio | Must be on HTTPS in production |
