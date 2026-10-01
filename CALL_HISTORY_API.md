# 📞 Call History API — Frontend Integration Guide

Base URL: `http://<server>:8000/api/telephony/calls/`

> All endpoints require **JWT authentication**.  
> Pass the token as: `Authorization: Bearer <access_token>`

---

## 1. Incoming Call History

Fetches a paginated list of **inbound** calls along with a summary block.

### Endpoint

```
GET /api/telephony/calls/incoming-history/
```

### Query Parameters

| Parameter    | Type       | Required | Description                                                   |
|--------------|------------|----------|---------------------------------------------------------------|
| `start`      | YYYY-MM-DD | No       | Start date of the range. Defaults to **today**.               |
| `end`        | YYYY-MM-DD | No       | End date of the range. Defaults to **today**.                 |
| `status`     | string     | No       | Filter by call status. See status values below.               |
| `department` | integer    | No       | Filter by department ID.                                      |
| `operator`   | integer    | No       | Filter by operator (staff user) ID.                           |
| `patient`    | integer    | No       | Filter by patient ID.                                         |
| `search`     | string     | No       | Search by phone number (partial match on `from` or `to`).     |

### Example Request

```http
GET /api/telephony/calls/incoming-history/?start=2026-09-01&end=2026-09-29&status=missed
Authorization: Bearer eyJ...
```

---

## 2. Outgoing Call History

Fetches a paginated list of **outbound** calls along with a summary block.

### Endpoint

```
GET /api/telephony/calls/outgoing-history/
```

### Query Parameters

Same as Incoming Call History above.

### Example Request

```http
GET /api/telephony/calls/outgoing-history/?start=2026-09-01&end=2026-09-29
Authorization: Bearer eyJ...
```

---

## 3. Response Structure

Both endpoints return the same shape:

```json
{
  "count": 42,
  "next": "http://<server>:8000/api/telephony/calls/incoming-history/?page=2",
  "previous": null,
  "summary": {
    "direction": "inbound",
    "from": "2026-09-01",
    "to": "2026-09-29",
    "total_calls": 42,
    "answered": 35,
    "missed": 5,
    "rnr": 2,
    "avg_duration_seconds": 148.3
  },
  "results": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "direction": "inbound",
      "direction_display": "Inbound",
      "status": "answered",
      "status_display": "Answered",
      "from_number": "+919876543210",
      "to_number": "+912012345678",
      "patient": 101,
      "patient_name": "Ravi Kumar",
      "enquiry": null,
      "department": 3,
      "department_name": "Cardiology",
      "operator": 7,
      "operator_name": "Dr. Sneha Patil",
      "started_at": "2026-09-15T10:30:00+05:30",
      "answered_at": "2026-09-15T10:30:12+05:30",
      "ended_at": "2026-09-15T10:35:45+05:30",
      "duration_seconds": 333,
      "recording_url": "https://cdn.example.com/recordings/call-xyz.mp3",
      "consent_recorded": true,
      "ivr_path": "OPD > Marathi",
      "call_reason": "opd",
      "call_reason_display": "OPD",
      "notes": "Patient asked about appointment for next Monday.",
      "provider_name": "HoduPBX",
      "provider_call_id": "hodu-uuid-1234",
      "created_at": "2026-09-15T10:30:00+05:30"
    }
  ]
}
```

---

## 4. Field Reference

### `summary` object

| Field                  | Type    | Description                              |
|------------------------|---------|------------------------------------------|
| `direction`            | string  | `"inbound"` or `"outbound"`              |
| `from`                 | date    | Start of the queried date range          |
| `to`                   | date    | End of the queried date range            |
| `total_calls`          | integer | Total calls in the date range            |
| `answered`             | integer | Calls with status `answered`             |
| `missed`               | integer | Calls with status `missed`               |
| `rnr`                  | integer | Ring No Response calls                   |
| `avg_duration_seconds` | float   | Average talk time in seconds             |

### `results[]` — each call object

| Field                | Type            | Description                                              |
|----------------------|-----------------|----------------------------------------------------------|
| `id`                 | UUID            | Unique call ID                                           |
| `direction`          | string          | `inbound` / `outbound`                                   |
| `direction_display`  | string          | Human-readable: `"Inbound"` / `"Outbound"`               |
| `status`             | string          | See status values below                                  |
| `status_display`     | string          | Human-readable status label                              |
| `from_number`        | string          | Caller's phone number                                    |
| `to_number`          | string          | Destination phone number                                 |
| `patient`            | integer / null  | Patient ID (if linked)                                   |
| `patient_name`       | string / null   | Patient's full name                                      |
| `enquiry`            | integer / null  | Enquiry ID (pre-patient lead, if linked)                 |
| `department`         | integer / null  | Department ID                                            |
| `department_name`    | string / null   | Department name                                          |
| `operator`           | integer / null  | Operator (staff) user ID                                 |
| `operator_name`      | string / null   | Operator's full name or email                            |
| `started_at`         | datetime        | When the call started (ISO 8601 with timezone)           |
| `answered_at`        | datetime / null | When the call was answered                               |
| `ended_at`           | datetime / null | When the call ended                                      |
| `duration_seconds`   | integer         | Total talk duration in seconds                           |
| `recording_url`      | string (URL)    | Link to call recording (empty string if not available)   |
| `consent_recorded`   | boolean         | Whether patient consent was obtained for recording       |
| `ivr_path`           | string          | IVR menu breadcrumb e.g. `"OPD > Marathi"`              |
| `call_reason`        | string          | Raw reason code. See call reason values below            |
| `call_reason_display`| string          | Human-readable call reason label                         |
| `notes`              | string          | Operator notes about the call                            |
| `provider_name`      | string          | Telephony provider e.g. `"HoduPBX"`                     |
| `provider_call_id`   | string          | Provider's internal call UUID                            |
| `created_at`         | datetime        | Record creation timestamp                                |

---

## 5. Enum Values

### Call Status (`status`)

| Value       | Display Label     |
|-------------|-------------------|
| `answered`  | Answered          |
| `missed`    | Missed            |
| `rnr`       | Ring no response  |
| `busy`      | Busy              |
| `failed`    | Failed            |
| `voicemail` | Voicemail         |

### Call Reason (`call_reason`)

| Value            | Display Label   |
|------------------|-----------------|
| `opd`            | OPD             |
| `surgical`       | Surgical        |
| `second_opinion` | Second opinion  |
| `billing`        | Billing         |
| `report`         | Report          |
| `complaint`      | Complaint       |
| `other`          | Other           |

---

## 6. Pagination

| Field      | Description                              |
|------------|------------------------------------------|
| `count`    | Total number of matching records         |
| `next`     | URL to the next page (null if last page) |
| `previous` | URL to the previous page (null if first) |
| `results`  | Array of call objects for the current page |

Use the `next` / `previous` URLs directly for navigation — do **not** construct page URLs manually.

---

## 7. Quick Examples

### Get today's missed inbound calls
```
GET /api/telephony/calls/incoming-history/?status=missed
```

### Get all outbound calls for September 2026
```
GET /api/telephony/calls/outgoing-history/?start=2026-09-01&end=2026-09-30
```

### Search for calls from a specific number
```
GET /api/telephony/calls/incoming-history/?search=9876543210
```

### Filter by department and operator
```
GET /api/telephony/calls/incoming-history/?department=3&operator=7&start=2026-09-01&end=2026-09-29
```

---

## 8. Error Responses

| HTTP Status | Meaning                                                   |
|-------------|-----------------------------------------------------------|
| `401`       | Missing or invalid auth token                             |
| `403`       | Authenticated but not permitted (wrong hospital scope)    |
| `400`       | Bad query param (e.g. invalid date format)                |

---

*Generated: 2026-09-29 — HSM Hospital Backend*
