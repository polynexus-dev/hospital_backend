# Mobile Patient API Documentation

This document outlines the API endpoints designed for the patient-facing mobile application. These endpoints are located in the `apps.mobile_patient` application and are exposed under the `/api/v1/mobile/` prefix.

## Architectural Notes

- **Data Sourcing**: The patient data consumed by these endpoints maps directly to the central hospital models (e.g. `apps.patients.models.Patient`, `apps.appointments.models.Appointment`, etc.). There is no separate patient database for the mobile app.
- **Authentication**: Authentication relies on an OTP flow (login via mobile number) which issues standard JWT tokens.
- **Scoping / Multi-tenancy**: *Every* data query in this app (below the auth layer) **MUST** be scoped to the authenticated user. A mobile user can only ever retrieve, modify, or create data for themselves and their registered family members.

## API Endpoints

### Authentication

- `POST /auth/otp/send/` - Sends an OTP to the provided mobile number.
- `POST /auth/otp/verify/` - Verifies the OTP and returns `access` and `refresh` tokens.
- `POST /auth/refresh/` - Refreshes an expired access token using a refresh token.
- `POST /auth/logout/` - Logs out the user and invalidates their tokens.
- `POST /devices/` - Registers the device's push (FCM) token for notifications.

### Profile & Family

- `GET /me/` - Retrieves the authenticated user's profile.
- `PATCH /me/` - Updates the authenticated user's profile.
- `GET /me/family/` - Lists all registered family members linked to the user.
- `POST /me/family/` - Adds a new family member to the user's account.
- `PATCH /me/family/{id}/` - Updates a specific family member's details.
- `DELETE /me/family/{id}/` - Removes a family member.
- `PATCH /me/preferences/` - Updates user preferences, including language and notification settings.

### Hospitals, Departments & Doctors

- `GET /hospitals/` - Lists available hospitals.
- `GET /hospitals/{id}/` - Retrieves detailed information about a specific hospital (address, contacts).
- `GET /departments/` - Lists available medical departments.
- `GET /doctors/` - Lists doctors (supports filtering by `department` and query `q`).
- `GET /doctors/{id}/` - Retrieves a doctor's profile, including fees and spoken languages.
- `GET /doctors/{id}/slots/` - Retrieves available booking slots for a specific doctor on a given `date`.

### Appointments

- `POST /appointments/` - Books an appointment (includes temporary hold logic).
- `GET /appointments/` - Lists the user's appointments (supports `status=upcoming|past` filter).
- `GET /appointments/{id}/` - Retrieves details of a specific appointment.
- `POST /appointments/{id}/reschedule/` - Reschedules an existing appointment.
- `POST /appointments/{id}/cancel/` - Cancels an appointment.
- `GET /appointments/{id}/queue/` - Retrieves the live queue token and wait status for an appointment.
- `POST /appointments/{id}/checkin/` - Checks in the patient for an appointment.

### Payments & Billing

- `POST /payments/initiate/` - Creates an order on the payment gateway.
- `POST /payments/verify/` - Confirms a completed payment.
- `POST /payments/webhook/` - Server-to-server endpoint for payment gateway webhooks (no auth required).
- `GET /bills/` - Lists the user's bills.
- `GET /bills/{id}/` - Retrieves details of a specific bill.
- `GET /bills/{id}/receipt/` - Generates and retrieves the PDF receipt for a bill.

### Medical Records

- `GET /prescriptions/` - Lists the user's prescriptions.
- `GET /prescriptions/{id}/` - Retrieves a specific prescription.
- `GET /reports/` - Lists medical reports (supports `type=lab|radiology` filter).
- `GET /reports/{id}/file/` - Retrieves the actual file/document for a report.
- `GET /visits/` - Lists past visit history and summaries.
- `GET /documents/` - Lists uploaded patient documents.
- `POST /documents/` - Uploads a new outside report or document.

### Packages & Camps

- `GET /packages/` - Lists available health packages.
- `GET /packages/{id}/` - Retrieves details for a specific health package.
- `POST /packages/{id}/book/` - Books a health package.
- `GET /camps/` - Lists upcoming medical camps.
- `POST /camps/{id}/register/` - Registers the user for a medical camp.

### Teleconsultation

- `POST /teleconsult/sessions/` - Initiates a teleconsultation session.
- `GET /teleconsult/sessions/{id}/token/` - Retrieves the video room token for an active session.

### Support & Engagement

- `POST /callbacks/` - Raises a callback request in the hospital CRM.
- `GET /notifications/` - Lists in-app notifications.
- `PATCH /notifications/{id}/read/` - Marks a specific notification as read.
- `POST /feedback/` - Submits a rating and comment for a past visit.
- `POST /assistant/message/` - Sends a message to the AI assistant.

### Insurance (Phase 2)

- `GET /insurance/` - Retrieves saved insurance policy details.
- `POST /insurance/` - Adds new insurance policy details.
- `GET /preauth/{id}/status/` - Retrieves the status of an insurance pre-authorization request.

## Postman Collection Flow Guide

A full Postman collection (`Mobile_Patient_API.postman_collection.json`) has been provided to help you test this API from scratch. Follow this step-by-step guide to get started:

### Step 1: Import the Collection
1. Open Postman.
2. Click **Import** and select the `Mobile_Patient_API.postman_collection.json` file.
3. The collection **"Mobile Patient API - Full"** will appear in your workspace.

### Step 2: Send OTP (Login/Register)
The API uses a passwordless OTP login system.
1. Open the **Authentication** folder and click on the **Send OTP (Login/Register)** request.
2. Check the `Body` tab (it defaults to `{"mobile": "+1234567890"}`).
3. Click **Send**. You should receive a mock success message indicating the OTP was sent.

### Step 3: Verify OTP (Obtain Token)
This step simulates entering the OTP received via SMS.
1. Click on the **Verify OTP** request.
2. Ensure the `Body` contains the same mobile number and a mock OTP (e.g., `123456`).
3. Click **Send**. 
4. **Important**: You will see `access` and `refresh` tokens in the response. **You do not need to copy them.** A Postman test script on this request automatically extracts the `access` token and saves it to a variable named `{{patient_token}}`.

### Step 4: Test Protected Endpoints
Now that you are authenticated, you can freely test any other endpoint.
1. Open the **Profile & Family** folder and click on **Get Profile**.
2. Click the **Authorization** tab. Notice it is already set to `Bearer Token` with the value `{{patient_token}}`.
3. Click **Send**. The request will succeed because the token is automatically injected.
4. Try booking an appointment by navigating to **Appointments > Book Appointment** and clicking **Send**.

### Managing State
- If your token expires, run the **Refresh Token** request.
- To test as a different user, run **Send OTP** and **Verify OTP** with a different mobile number. The `{{patient_token}}` variable will be automatically overwritten with the new user's session.
