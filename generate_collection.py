import json

def create_request(name, method, path_list, query=None, body=None, auth=True):
    req = {
        "name": name,
        "request": {
            "method": method,
            "header": [],
            "url": {
                "raw": "{{base_url}}/api/v1/mobile/" + "/".join(path_list) + ("/" if path_list and path_list[-1] != "" else ""),
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "mobile"] + path_list + ([""] if path_list and path_list[-1] != "" else [])
            }
        }
    }
    
    if query:
        req["request"]["url"]["query"] = [{"key": k, "value": v} for k, v in query.items()]
        req["request"]["url"]["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        
    if body:
        req["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        req["request"]["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=4)
        }
        
    if auth:
        req["request"]["auth"] = {
            "type": "bearer",
            "bearer": [
                {
                    "key": "token",
                    "value": "{{patient_token}}",
                    "type": "string"
                }
            ]
        }
    
    return req

collection = {
    "info": {
        "_postman_id": "e2f9d5f0-8b8d-5b4c-9d3e-2f1e0b9c8d7e",
        "name": "Mobile Patient API - Full",
        "description": "Complete Postman collection for all Mobile Patient API endpoints.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "item": [
        {
            "name": "Authentication",
            "item": [
                create_request("Send OTP (Login/Register)", "POST", ["auth", "otp", "send"], body={"mobile": "+1234567890"}, auth=False),
                create_request("Verify OTP", "POST", ["auth", "otp", "verify"], body={"mobile": "+1234567890", "otp": "123456"}, auth=False),
                create_request("Complete Profile (Registration Step 2)", "PATCH", ["me"], body={"first_name": "John", "last_name": "Doe", "date_of_birth": "1990-05-15", "gender": "male"}, auth=True),
                create_request("Refresh Token", "POST", ["auth", "refresh"], body={"refresh": "your_refresh_token_here"}, auth=False),
                create_request("Logout", "POST", ["auth", "logout"], auth=True),
                create_request("Register Device FCM Token", "POST", ["devices"], body={"fcm_token": "abc_123_xyz"}, auth=True)
            ]
        },
        {
            "name": "Profile & Family",
            "item": [
                create_request("Get Profile", "GET", ["me"]),
                create_request("Update Profile", "PATCH", ["me"], body={"first_name": "Jane"}),
                create_request("List Family Members", "GET", ["me", "family"]),
                create_request("Add Family Member", "POST", ["me", "family"], body={"name": "John Doe", "relation": "Spouse"}),
                create_request("Update Family Member", "PATCH", ["me", "family", "1"], body={"name": "John Doe Updated"}),
                create_request("Delete Family Member", "DELETE", ["me", "family", "1"]),
                create_request("Update Preferences", "PATCH", ["me", "preferences"], body={"language": "en", "notifications_enabled": True})
            ]
        },
        {
            "name": "Hospitals & Doctors",
            "item": [
                create_request("List Hospitals", "GET", ["hospitals"]),
                create_request("Get Hospital Details", "GET", ["hospitals", "1"]),
                create_request("List Departments", "GET", ["departments"]),
                create_request("List Doctors", "GET", ["doctors"], query={"department": "1", "q": "smith"}),
                create_request("Get Doctor Profile", "GET", ["doctors", "1"]),
                create_request("Get Doctor Slots", "GET", ["doctors", "1", "slots"], query={"date": "2026-10-01"})
            ]
        },
        {
            "name": "Appointments",
            "item": [
                create_request("Book Appointment", "POST", ["appointments"], body={"doctor_id": 1, "slot_time": "2026-10-01T10:00:00Z"}),
                create_request("List Appointments", "GET", ["appointments"], query={"status": "upcoming"}),
                create_request("Get Appointment Details", "GET", ["appointments", "1"]),
                create_request("Reschedule Appointment", "POST", ["appointments", "1", "reschedule"], body={"new_slot_time": "2026-10-02T10:00:00Z"}),
                create_request("Cancel Appointment", "POST", ["appointments", "1", "cancel"]),
                create_request("Get Appointment Queue Status", "GET", ["appointments", "1", "queue"]),
                create_request("Check-in Appointment", "POST", ["appointments", "1", "checkin"])
            ]
        },
        {
            "name": "Payments & Billing",
            "item": [
                create_request("Initiate Payment", "POST", ["payments", "initiate"], body={"appointment_id": 1, "amount": 500}),
                create_request("Verify Payment", "POST", ["payments", "verify"], body={"order_id": "ORDER_123", "payment_id": "PAY_123"}),
                create_request("Payment Webhook (Server-to-Server)", "POST", ["payments", "webhook"], body={"event": "payment.captured", "payload": {}}, auth=False),
                create_request("List Bills", "GET", ["bills"]),
                create_request("Get Bill Details", "GET", ["bills", "1"]),
                create_request("Get Bill Receipt PDF", "GET", ["bills", "1", "receipt"])
            ]
        },
        {
            "name": "Medical Records",
            "item": [
                create_request("List Prescriptions", "GET", ["prescriptions"]),
                create_request("Get Prescription Details", "GET", ["prescriptions", "1"]),
                create_request("List Reports", "GET", ["reports"], query={"type": "lab"}),
                create_request("Get Report File", "GET", ["reports", "1", "file"]),
                create_request("List Visit History", "GET", ["visits"]),
                create_request("List Uploaded Documents", "GET", ["documents"]),
                create_request("Upload Outside Report", "POST", ["documents"], body={"file": "base64_encoded_string_or_multipart"})
            ]
        },
        {
            "name": "Packages & Camps",
            "item": [
                create_request("List Health Packages", "GET", ["packages"]),
                create_request("Get Package Details", "GET", ["packages", "1"]),
                create_request("Book Package", "POST", ["packages", "1", "book"]),
                create_request("List Medical Camps", "GET", ["camps"]),
                create_request("Register for Camp", "POST", ["camps", "1", "register"])
            ]
        },
        {
            "name": "Teleconsult",
            "item": [
                create_request("Initiate Teleconsult Session", "POST", ["teleconsult", "sessions"], body={"appointment_id": 1}),
                create_request("Get Video Room Token", "GET", ["teleconsult", "sessions", "1", "token"])
            ]
        },
        {
            "name": "Support & Engagement",
            "item": [
                create_request("Raise Callback Request", "POST", ["callbacks"], body={"reason": "Need help with booking"}),
                create_request("List Notifications", "GET", ["notifications"]),
                create_request("Mark Notification Read", "PATCH", ["notifications", "1", "read"]),
                create_request("Submit Feedback", "POST", ["feedback"], body={"visit_id": 1, "rating": 5, "comment": "Great experience"}),
                create_request("Message AI Assistant", "POST", ["assistant", "message"], body={"message": "What are the hospital timings?"})
            ]
        },
        {
            "name": "Insurance (Phase 2)",
            "item": [
                create_request("Get Insurance Details", "GET", ["insurance"]),
                create_request("Add Insurance Policy", "POST", ["insurance"], body={"provider": "HealthCare Inc", "policy_number": "POL12345"}),
                create_request("Get Pre-auth Status", "GET", ["preauth", "1", "status"])
            ]
        },
        {
            "name": "Staff Endpoints (Hospital Reception)",
            "item": [
                {
                    "name": "Register Patient (Staff)",
                    "request": {
                        "method": "POST",
                        "header": [
                            {
                                "key": "Content-Type",
                                "value": "application/json"
                            }
                        ],
                        "url": {
                            "raw": "{{base_url}}/api/v1/patients/",
                            "host": ["{{base_url}}"],
                            "path": ["api", "v1", "patients", ""]
                        },
                        "body": {
                            "mode": "raw",
                            "raw": "{\n    \"first_name\": \"Jane\",\n    \"last_name\": \"Doe\",\n    \"mobile\": \"+19876543210\",\n    \"date_of_birth\": \"1995-08-20\",\n    \"gender\": \"female\"\n}"
                        },
                        "auth": {
                            "type": "bearer",
                            "bearer": [
                                {
                                    "key": "token",
                                    "value": "{{staff_token}}",
                                    "type": "string"
                                }
                            ]
                        }
                    }
                }
            ]
        }
    ],
    "variable": [
        {
            "key": "base_url",
            "value": "http://localhost:8000"
        }
    ]
}

# Add the test script to Verify OTP to auto-set the token
collection["item"][0]["item"][1]["event"] = [
    {
        "listen": "test",
        "script": {
            "exec": [
                "var jsonData = pm.response.json();",
                "if(jsonData.access) { pm.environment.set(\"patient_token\", jsonData.access); }"
            ],
            "type": "text/javascript"
        }
    }
]

with open('Mobile_Patient_API.postman_collection.json', 'w') as f:
    json.dump(collection, f, indent=4)
print("Collection updated.")
