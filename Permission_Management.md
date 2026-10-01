# Role and Permission Management Guide

This guide explains how Roles and Permissions work in the hospital backend, and how to use the provided Postman collection to test the flow.

## How Permissions Work

The permission system is built on top of Django's native Group and Permission models, but simplified using a custom `Role` model and pre-defined **Permission Templates**.

1. **Permission Templates**: Defined in `apps/accounts/permission_templates.py`. They dictate which apps each role has access to (e.g., the `doctor` template gets full CRUD access to the `patients` app).
2. **Role Creation**: When a `Role` is created with a specific template (e.g., `template="doctor"`), the system creates a backing Django `Group`. It then automatically fetches all the permissions defined by the template and adds them to the group (`sync_role`).
3. **User Assignment**: When a user is assigned a role, they are added to that role's Django `Group`, instantly granting them all the permissions attached to that group.

## Postman Collection Usage

A Postman collection named `Permission_Management.postman_collection.json` has been generated for you to test this flow end-to-end. Import the JSON file into Postman and execute the requests in order.

### Request Flow:

1. **Login as Admin**: 
   - **Endpoint**: `POST /api/v1/auth/login/`
   - **Purpose**: Authenticates as a hospital administrator to get a JWT token. The token is automatically saved as a Postman environment variable (`admin_token`) for subsequent requests.

2. **Create Role (OPD Doctor)**:
   - **Endpoint**: `POST /api/v1/roles/`
   - **Purpose**: Creates a new Role named "OPD Doctor Role" using the `"doctor"` template. The backend will automatically generate the Django Group and assign the `patients` permissions based on the template. The new role's ID is saved as `new_role_id`.

3. **Create/Update User with Role**:
   - **Endpoint**: `POST /api/v1/users/`
   - **Purpose**: Creates a new doctor user and assigns them the `new_role_id` created in the previous step.

4. **Login as New Doctor**:
   - **Endpoint**: `POST /api/v1/auth/login/`
   - **Purpose**: Authenticates as the newly created doctor. The JWT token is saved as `doctor_token`.

5. **Access Patients API as Doctor**:
   - **Endpoint**: `GET /api/v1/patients/`
   - **Purpose**: Uses the `doctor_token` to make a GET request to the patients endpoint. Since the user was assigned the doctor role (which inherits `view_patient` from the template), this request will succeed with a `200 OK` rather than a `403 Forbidden`.

## Troubleshooting

- **403 Forbidden on existing users**: If a user is getting a 403 error on an endpoint they should have access to, it usually means their Role was created without a template, or the templates were updated in the code after the role was created. 
- **Fixing desynced roles**: You can manually synchronize all role permissions from the Django shell:
  ```python
  from apps.accounts.role_sync import sync_all_roles
  sync_all_roles()
  ```
