# ClockIn System Overview & Technical Documentation

## Project Overview

ClockIn is a workforce timekeeping and geofencing system designed for multi-store employee management. The system combines a Flask backend, PostgreSQL database, React Native mobile app, and GPS/geofence validation to provide secure employee clock-in and clock-out functionality.

The primary purpose of the system is to:

* Track employee work hours accurately
* Prevent off-site clock-ins and clock-outs
* Manage employees across multiple store locations
* Provide payroll-ready reporting
* Log employee GPS activity during active shifts
* Allow administrators to manage employees, stores, and payroll data from a centralized dashboard

The system is designed primarily for internal business use and focuses heavily on real-world usability, geofence reliability, and simple employee workflows.

---

# Core Technologies

## Backend

* Python
* Flask
* Flask-SQLAlchemy
* Gunicorn
* PostgreSQL (production)
* SQLite (local development/testing)

## Mobile App

* React Native
* Android-first deployment
* Transistorsoft Background Geolocation SDK

## Hosting / Infrastructure

* Render web service hosting
* Render PostgreSQL database
* GitHub source control

## Supporting Tools

* Android Studio
* Android Emulator
* PowerShell
* Git / GitHub
* Codex / AI-assisted development workflows

---

# System Purpose

The ClockIn system was built to solve several workforce management problems:

1. Employees clocking in away from assigned store locations
2. Lack of GPS verification for work shifts
3. Difficulty managing multiple store locations
4. Manual payroll calculations
5. Lack of mobile-friendly timekeeping
6. Lack of shift auditing and employee activity tracking

The system emphasizes:

* GPS validation
* Simplicity for employees
* Administrative visibility
* Payroll accuracy
* Multi-store support
* Mobile-first operation

---

# Employee Workflow

## Planned Employee Workflow

### Step 1 — Employee Login

Employee opens the mobile app and logs in using:

* Employee username/code
* Employee PIN

The employee session remains stored locally on the device so employees stay logged in between app launches.

---

### Step 2 — Store Selection

Employee selects the current store/location from a dropdown list.

Rules:

* Manual store typing is not allowed
* Store must be selected before clock-in
* Selected store persists locally
* Employee can change selected store only when not clocked in
* Active shift locks the store selection

---

### Step 3 — Clock In

Employee taps Clock In.

The mobile app:

1. Retrieves current GPS location
2. Sends:

   * employee identity
   * store code
   * device UUID
   * GPS coordinates
   * GPS accuracy
3. Backend validates:

   * employee authentication
   * selected store
   * geofence radius
   * GPS accuracy
4. Backend creates shift record

If successful, employee is considered clocked in.

---

### Step 4 — Active Shift Tracking

While clocked in:

* Background geolocation remains active
* GPS pings are logged periodically
* Geofence enter/exit events are monitored
* Shift remains associated with the original clock-in store

The employee cannot change store during an active shift.

---

### Step 5 — Clock Out

Employee taps Clock Out.

Clock-out:

* uses the store associated with the active shift
* validates employee location
* records final GPS location
* closes the shift
* stops active shift tracking

---

# Geofence System

## Geofence Purpose

The geofence system prevents employees from clocking in or out from unauthorized locations.

Each store has:

* latitude
* longitude
* geofence radius in meters

Typical geofence radius:

* 150m–200m

---

## Geofence Validation

The backend calculates employee distance from store coordinates using haversine distance calculations.

Validation includes:

* employee distance from selected store
* GPS accuracy validation
* geofence enter/exit events
* nearest-store checks

---

## Background Geolocation

The mobile app uses the Transistorsoft Background Geolocation SDK.

Features include:

* background location updates
* geofence enter/exit monitoring
* periodic GPS pings
* start on boot
* background persistence
* movement tracking

---

## GPS Ping Logging

During active shifts:

* employee GPS positions are periodically stored
* pings include:

  * latitude
  * longitude
  * distance from store
  * inside/outside radius
  * timestamp

These logs help with:

* payroll auditing
* dispute resolution
* location verification
* troubleshooting

---

# Admin Dashboard

## Admin Features

The admin dashboard allows management of:

* employees
* stores
* shifts
* payroll reports
* GPS pings
* issue reports

---

## Employee Management

Admins can:

* create employees
* edit employees
* activate/deactivate employees
* assign employee username/code
* assign employee PIN
* manage employee login credentials

Employee username/code should be unique.

---

## Store Management

Admins can:

* create stores
* edit store names
* manage store codes
* manage store GPS coordinates
* manage geofence radius

Store codes are normalized for consistency.

---

## Shift Management

Admins can:

* view active shifts
* view historical shifts
* force-close shifts
* review GPS information
* audit employee time

Force-close actions are logged for audit purposes.

---

## Payroll Reporting

Payroll reporting includes:

* Monday–Sunday payroll week
* total hours
* employee filtering
* CSV export
* exact minute tracking

The system intentionally avoids quarter-hour rounding.

---

# Authentication & Security

## Admin Authentication

Admin credentials are stored using Render environment variables.

Required environment variables:

* SECRET_KEY
* ADMIN_USERNAME
* ADMIN_PASSWORD
* MOBILE_DEVICE_TOKEN
* DATABASE_URL

The application now intentionally fails startup if these variables are missing.

---

## Employee Authentication

Planned employee authentication flow:

* employee username/code
* employee PIN
* persistent mobile login session

Future improvements may include:

* stronger device binding
* optional device approval
* additional authentication controls

---

## Mobile Authentication

Mobile API requests use:

* employee credentials
* device UUID
* mobile device token

The system logs device information for audit and troubleshooting.

---

# Database Overview

## Primary Tables

### Store

Stores contain:

* store name
* store code
* latitude
* longitude
* geofence radius

---

### Employee

Employees contain:

* employee name
* employee username/code
* employee PIN
* active/inactive status
* device information

---

### Shift

Shifts contain:

* employee
* store
* clock-in timestamp
* clock-out timestamp
* GPS coordinates
* device metadata
* admin override information

---

### LocationPing

Location pings contain:

* employee
* shift
* store
* latitude
* longitude
* distance from store
* inside/outside radius
* timestamp

---

### MobileEvent

Mobile events contain:

* geofence events
* background location events
* device information
* raw mobile payloads

---

# API Overview

## Important Mobile Endpoints

Examples include:

* /api/mobile/clock-in
* /api/mobile/clock-out
* /api/mobile/status
* /api/mobile/geofences
* /api/mobile/bg/event
* /api/mobile/bg/locations
* /api/stores/all
* /api/stores/suggest

---

# Deployment & Infrastructure

## Production Deployment

Production environment:

* Render web service
* Gunicorn app server
* PostgreSQL database
* GitHub auto-deploy workflow

---

## Local Development

Local development commonly uses:

* Flask local server
* SQLite database
* Android emulator
* PowerShell

---

# Development Workflow

## Recommended Workflow

1. Create backup/sandbox copy
2. Test changes in backup first
3. Use Codex for targeted tasks
4. Review diffs carefully
5. Test locally
6. Promote verified files to production project
7. Push to GitHub
8. Deploy to Render

---

## Codex Usage Philosophy

Codex is used as:

* repo analyzer
* debugging assistant
* code refactoring assistant
* testing helper
* workflow automation assistant

Important rules:

* use backup projects first
* avoid blind auto-deploys
* review diffs
* test locally before production

---

# Known Issues & Future Improvements

## Current Focus Areas

* improve employee authentication
* strengthen device binding
* unify geofence validation logic
* add CSRF protection to admin forms
* improve GPS edge-case handling
* improve geofence ambiguity handling

---

## Planned Features

Potential future improvements:

* iOS support
* employee password reset flow
* stronger mobile auth
* admin GPS map dashboard
* push notifications
* improved reporting
* shift approval workflows
* manager roles

---

# Project Philosophy

The ClockIn system prioritizes:

* reliability
* real-world workforce usability
* accurate payroll data
* practical GPS validation
* simple employee workflows
* centralized administrative control

The project evolved from a simple clock-in application into a production-grade workforce management platform integrating:

* mobile applications
* geofencing
* payroll tracking
* employee management
* audit logging
* GPS validation
* administrative reporting

The development process emphasizes iterative testing, sandbox validation, and practical deployment workflows.
