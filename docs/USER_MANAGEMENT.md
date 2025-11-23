# User Management - Production Ready Guide

This guide explains how to manage users in IndustryFlow v2 using production-ready tools and best practices.

## Overview

IndustryFlow v2 uses a secure, environment-based user management system with:
- **No hardcoded credentials** in code or UI
- **Environment-based configuration** for admin users
- **CLI tools** for user management
- **Role-based access control** (admin, engineer, observer)
- **Tenant isolation** via company_id

## User Roles

| Role | Permissions |
|------|------------|
| **admin** | Full access to all features, can manage users and settings |
| **engineer** | Can view data, create alert rules, train models |
| **observer** | Read-only access to dashboards and data |

## Managing Users

### 1. Using the CLI Tool (Recommended)

The `manage_users.py` script provides comprehensive user management:

#### Create a New User

```bash
# Interactive (prompts for password)
python scripts/manage_users.py create \
  --email engineer@example.com \
  --company-id 550e8400-e29b-41d4-a716-446655440000 \
  --role engineer

# With password (not recommended - use interactive mode)
python scripts/manage_users.py create \
  --email engineer@example.com \
  --password SecurePass123! \
  --company-id 550e8400-e29b-41d4-a716-446655440000 \
  --role engineer
```

#### List All Users

```bash
python scripts/manage_users.py list
```

Output:
```
========================================================================================
EMAIL                          COMPANY                   ROLE         ACTIVE   SUPERUSER    VERIFIED
========================================================================================
admin@acme.com                 ACME Corp                 admin        Yes      Yes          Yes
engineer@acme.com              ACME Corp                 engineer     Yes      No           Yes
========================================================================================
Total users: 2
========================================================================================
```

#### Reset User Password

```bash
# Interactive (prompts for password)
python scripts/manage_users.py reset-password --email user@example.com

# With password (not recommended)
python scripts/manage_users.py reset-password --email user@example.com --password NewPass123!
```

#### Delete a User

```bash
# With confirmation prompt
python scripts/manage_users.py delete --email user@example.com

# Skip confirmation (dangerous!)
python scripts/manage_users.py delete --email user@example.com --yes
```

### 2. Initial User Seeding (Development Only)

For development environments, you can seed initial admin users using environment variables:

#### Step 1: Configure .env

```bash
# Add to your .env file
ADMIN_USER_1_EMAIL=admin@yourcompany.com
ADMIN_USER_1_PASSWORD=YourSecurePassword123!
ADMIN_USER_1_COMPANY_ID=550e8400-e29b-41d4-a716-446655440000
ADMIN_USER_1_ROLE=admin

# Optional: Add more users
ADMIN_USER_2_EMAIL=engineer@yourcompany.com
ADMIN_USER_2_PASSWORD=AnotherSecurePass123!
ADMIN_USER_2_COMPANY_ID=550e8400-e29b-41d4-a716-446655440000
ADMIN_USER_2_ROLE=engineer
```

#### Step 2: Run Seeding Script

```bash
python scripts/seed_users.py
```

**⚠️ Important**: Remove or leave blank these environment variables in production!

### 3. Using the API (Advanced)

#### Register via API

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "newuser@example.com",
    "password": "SecurePass123!",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "engineer"
  }'
```

#### Login

```bash
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@example.com&password=SecurePass123!"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

## Authentication Flow

### Frontend Authentication

The frontend login flow:

1. User enters email and password
2. POST to `/auth/jwt/login`
3. Receive JWT access token
4. Store token in localStorage
5. Include token in all API requests and WebSocket connections

### WebSocket Authentication

WebSocket connections require a valid JWT token:

```javascript
const token = localStorage.getItem('access_token');
const ws = new WebSocket(`ws://localhost:8000/ws/sensors?token=${token}`);
```

The token is validated on each connection:
- Signature verified using `JWT_SECRET_KEY`
- User fetched from database
- Company ID used for tenant isolation

## Troubleshooting

### WebSocket Connection Fails

**Symptom**: `JWTError: Signature verification failed`

**Cause**: Invalid or expired token in localStorage

**Solution**:
1. Open browser console (F12)
2. Run: `localStorage.clear()`
3. Refresh page and login again

### Login Fails with "Bad Credentials"

**Causes**:
- Incorrect password
- User doesn't exist
- User is not active (`is_active=false`)

**Solution**:
- Verify credentials
- Check user exists: `python scripts/manage_users.py list`
- Reset password: `python scripts/manage_users.py reset-password --email user@example.com`

### User Cannot See Data

**Cause**: Company ID mismatch or missing permissions

**Solution**:
- Verify user's `company_id` matches equipment's `company_id`
- Check user role has necessary permissions
- Verify equipment exists in database

## Security Best Practices

### ✅ DO

- Use strong passwords (min 12 characters, mix of letters, numbers, symbols)
- Rotate `JWT_SECRET_KEY` regularly in production
- Use HTTPS in production
- Enable rate limiting on authentication endpoints
- Log authentication attempts
- Regularly audit user accounts
- Remove unused accounts promptly
- Use environment variables for secrets

### ❌ DON'T

- Hardcode passwords in code or config files
- Share credentials between users
- Store passwords in plain text
- Commit `.env` files to version control
- Use weak passwords
- Leave default passwords unchanged
- Share JWT tokens between users

## Production Deployment Checklist

- [ ] Generate secure `JWT_SECRET_KEY` (min 64 characters)
- [ ] Set strong `DB_PASSWORD` and service passwords
- [ ] Remove or leave blank `ADMIN_USER_*` variables in production `.env`
- [ ] Create initial admin user using `manage_users.py`
- [ ] Enable HTTPS/TLS for all connections
- [ ] Configure CORS origins to only allow your domain
- [ ] Set up regular database backups
- [ ] Enable logging and monitoring for auth events
- [ ] Document admin access procedures
- [ ] Set up password reset flow (if needed)
- [ ] Configure session timeout appropriately
- [ ] Enable 2FA (if required)

## Migration from Development to Production

If you have development users with hardcoded passwords:

```bash
# 1. List current users
python scripts/manage_users.py list

# 2. Delete test users
python scripts/manage_users.py delete --email test@acme.com --yes

# 3. Create production admin
python scripts/manage_users.py create \
  --email admin@yourproductiondomain.com \
  --company-id YOUR_COMPANY_UUID \
  --role admin

# 4. Update JWT_SECRET_KEY in .env to a new secure value
# This will invalidate all existing tokens

# 5. Restart services
docker-compose restart
```

## Support

For issues or questions:
- Check logs: `docker logs industryflow-api-gateway`
- Review database: `python scripts/manage_users.py list`
- Verify environment: `echo $JWT_SECRET_KEY` (should be set)
- Contact system administrator

## API Reference

### Authentication Endpoints

- `POST /auth/jwt/login` - Login and receive JWT token
- `POST /auth/register` - Register new user
- `POST /auth/jwt/logout` - Logout
- `GET /users/me` - Get current user info
- `PATCH /users/me` - Update current user

### WebSocket Endpoints

- `WS /ws/sensors?token=JWT` - Real-time sensor data stream
- `WS /ws/sensors/{equipment_id}?token=JWT` - Equipment-specific stream

All WebSocket connections require valid JWT token as query parameter.
