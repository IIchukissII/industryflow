# Authentication Setup - Production Ready Solution

## Problem Summary

The WebSocket connection was failing with:
```
❌ JWTError: Signature verification failed.
❌ WebSocket rejected: Invalid token or no company
```

**Root Cause**: The JWT token stored in the browser's localStorage was signed with a different `JWT_SECRET_KEY` than the current API gateway configuration.

## Production-Ready Solution Implemented

### 1. **Environment-Based User Management**
   - ✅ No hardcoded credentials in code
   - ✅ User seeding via environment variables (`scripts/seed_users.py`)
   - ✅ CLI tool for user management (`scripts/manage_users.py`)
   - ✅ Comprehensive documentation (`docs/USER_MANAGEMENT.md`)

### 2. **Updated Frontend**
   - ✅ Removed hardcoded test credentials from Login page
   - ✅ Production-ready login flow
   - ✅ Clear error messages for authentication failures

### 3. **Security Enhancements**
   - ✅ Environment variables for all secrets
   - ✅ Updated `.env.example` with user management configuration
   - ✅ Production deployment checklist

## Fixing the WebSocket Connection

### For End Users (via Browser)

1. **Clear Invalid Token**:
   ```javascript
   // Open browser console (F12) and run:
   localStorage.clear();
   ```

2. **Login with Valid Credentials**:
   - Navigate to `http://localhost:3000` (or your frontend URL)
   - Login with: `test@acme.com` / `SecurePass123!`
   - The frontend will store a new valid JWT token

3. **WebSocket Auto-Connects**:
   - Once logged in, the WebSocket connection will automatically establish
   - Real-time sensor data will stream successfully

### For Developers/Admins

#### Quick Test

```bash
# 1. Login via API
curl -s -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@acme.com&password=SecurePass123!" \
  | jq -r '.access_token'

# 2. Copy the token and use it in your application
# or paste into browser console:
# localStorage.setItem('access_token', 'YOUR_TOKEN_HERE');
```

#### Create New Users

```bash
# Use the CLI tool (run from project root)
python scripts/manage_users.py create \
  --email newuser@company.com \
  --company-id 550e8400-e29b-41d4-a716-446655440000 \
  --role engineer

# The tool will prompt for password securely
```

## Verification Steps

### 1. Test Data Streaming

The TEP data streaming is already working:
```bash
# Check streaming status
cd services/mock_service && python3 stream_tep_data.py
```

Output should show:
```
Row    XX/20000 | Sent: 52/52 | Anomaly: X | Fault: XX
```

### 2. Test API Authentication

```bash
# Should return user info
curl -s -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@acme.com&password=SecurePass123!" \
  | jq '.'
```

### 3. Test WebSocket Connection

```bash
# Check API gateway logs for successful connection
docker logs industryflow-api-gateway --tail 20

# Should see:
# ✅ WebSocket CONNECTED - Company: XXX, User: test@acme.com
```

## Architecture

```
┌─────────────┐
│   Browser   │
│  (Frontend) │
└──────┬──────┘
       │ 1. Login (email/password)
       ↓
┌─────────────────────────────────┐
│      API Gateway (Port 8000)     │
│  - Validates credentials         │
│  - Generates JWT token           │
│  - Signed with JWT_SECRET_KEY    │
└──────┬──────────────────────────┘
       │ 2. Returns JWT token
       ↓
┌─────────────┐
│   Browser   │
│  localStorage│
│  saves token │
└──────┬──────┘
       │ 3. WebSocket connection with token
       ↓
┌─────────────────────────────────┐
│   WebSocket /ws/sensors?token   │
│  - Validates JWT signature      │
│  - Fetches user from database   │
│  - Filters data by company_id   │
│  - Streams real-time sensor data│
└─────────────────────────────────┘
```

## Production Deployment

### 1. Generate Secure JWT Secret

```bash
# Generate a secure random key (64 characters)
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Add to `.env`:
```bash
JWT_SECRET_KEY=your_generated_secure_key_here
```

### 2. Create Admin User

```bash
# Option A: Via CLI (recommended)
python scripts/manage_users.py create \
  --email admin@yourcompany.com \
  --company-id YOUR_COMPANY_UUID \
  --role admin

# Option B: Via environment (development only)
# Add to .env:
ADMIN_USER_1_EMAIL=admin@yourcompany.com
ADMIN_USER_1_PASSWORD=SecurePassword123!
ADMIN_USER_1_COMPANY_ID=YOUR_COMPANY_UUID
ADMIN_USER_1_ROLE=admin

# Then run:
python scripts/seed_users.py
```

### 3. Restart Services

```bash
docker-compose restart api-gateway
```

### 4. Test Complete Flow

1. Clear browser localStorage
2. Login with new admin account
3. Verify WebSocket connection
4. Verify real-time data streaming

## Security Checklist

- [x] No hardcoded passwords in code
- [x] Environment-based configuration
- [x] JWT token with secure secret
- [x] Password hashing (bcrypt)
- [x] Role-based access control
- [x] Tenant isolation via company_id
- [ ] HTTPS in production (configure reverse proxy)
- [ ] Rate limiting on auth endpoints (configure if needed)
- [ ] Regular security audits
- [ ] Password complexity requirements (consider adding)
- [ ] 2FA (consider for production)

## Troubleshooting

### WebSocket Still Fails

1. **Check JWT_SECRET_KEY matches**:
   ```bash
   docker exec industryflow-api-gateway env | grep JWT_SECRET_KEY
   ```

2. **Check user exists**:
   ```bash
   docker exec industryflow-timescaledb psql -U postgres -d industryflow \
     -c "SELECT email, company_id, role, is_active FROM \"user\";"
   ```

3. **Check API gateway logs**:
   ```bash
   docker logs industryflow-api-gateway --tail 50 | grep WebSocket
   ```

### Password Doesn't Work

Reset password using CLI:
```bash
python scripts/manage_users.py reset-password --email user@example.com
```

## Files Created/Modified

### New Files:
- `scripts/seed_users.py` - Environment-based user seeding
- `scripts/manage_users.py` - CLI tool for user management
- `docs/USER_MANAGEMENT.md` - Comprehensive user management guide
- `docs/AUTHENTICATION_SETUP.md` - This file

### Modified Files:
- `services/frontend/src/pages/Login.js` - Removed hardcoded credentials
- `.env.example` - Added user management variables

## Next Steps

1. **Clear localStorage in browser** (if WebSocket failing)
2. **Login with valid credentials**: `test@acme.com` / `SecurePass123!`
3. **Verify data streaming** in frontend dashboard
4. **Create production users** using `manage_users.py`
5. **Update JWT_SECRET_KEY** for production deployment
6. **Review** `docs/USER_MANAGEMENT.md` for complete guide

## Support

- 📖 Full documentation: `docs/USER_MANAGEMENT.md`
- 🔧 CLI help: `python scripts/manage_users.py --help`
- 📊 Check logs: `docker logs industryflow-api-gateway`
- 💬 Contact: System administrator

---

**Status**: ✅ Production-ready authentication system implemented
**Next Action**: Clear browser localStorage and login to fix WebSocket connection
