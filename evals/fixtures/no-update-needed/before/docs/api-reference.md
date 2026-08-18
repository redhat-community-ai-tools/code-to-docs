# API Reference

## Authentication

### POST /auth/login

Authenticates a user and returns a JWT token.

**Request body:**
```json
{"username": "admin", "password": "secret"}
```

**Response:**
```json
{"token": "eyJ..."}
```

## Users

### GET /users

Returns a list of all users.
