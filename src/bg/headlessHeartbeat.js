import AsyncStorage from '@react-native-async-storage/async-storage';

const API_BASE = __DEV__ ? 'http://10.0.2.2:5000' : 'https://clockin-app.onrender.com';
const DEVICE_TOKEN = 'KeatonClockInMobile_Venom97Triad1997151506172024!';
const SESSION_KEY = 'clockin_mobile_employee_session_v1';

function headersJson() {
  return {
    'Content-Type': 'application/json',
    'X-Device-Token': DEVICE_TOKEN,
  };
}

function deviceLabel() {
  return 'RN-android-headless';
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: headersJson(),
    body: JSON.stringify(body),
  });

  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {_raw: text};
  }

  return {res, data};
}

async function readSavedSession() {
  const raw = await AsyncStorage.getItem(SESSION_KEY);
  if (!raw) return null;

  try {
    const saved = JSON.parse(raw);
    const employeeCode = String(saved?.employeeCode || '').trim().toLowerCase();
    const pin = String(saved?.pin || '').trim();
    if (!employeeCode || !pin) return null;
    return {employeeCode, pin};
  } catch {
    return null;
  }
}

function eventLocation(event) {
  return (
    event?.params?.location ||
    event?.location ||
    event?.params?.geofence?.location ||
    event?.geofence?.location ||
    null
  );
}

function eventGeofence(event) {
  return event?.params?.geofence || event?.geofence || null;
}

function eventAction(event) {
  return (
    event?.params?.action ||
    event?.action ||
    event?.params?.geofence_action ||
    event?.geofence_action ||
    eventGeofence(event)?.action ||
    null
  );
}

function normalizeEventName(name) {
  if (name === 'geofence') return 'geofence';
  if (name === 'motionchange') return 'motionchange';
  if (name === 'location') return 'location';
  if (name === 'heartbeat') return 'heartbeat';
  return String(name || 'background_event');
}

export async function handleHeadlessHeartbeat(event) {
  const eventName = normalizeEventName(event?.name);
  if (!event || !['heartbeat', 'location', 'geofence', 'motionchange'].includes(eventName)) return;

  try {
    console.log(`[ClockInHeadless] ${eventName} received`);

    const session = await readSavedSession();
    if (!session) {
      console.log('[ClockInHeadless] no saved session');
      return;
    }

    const status = await apiPost('/api/mobile/status', {
      username_code: session.employeeCode,
      pin: session.pin,
      device_label: deviceLabel(),
    });

    if (!status.res.ok) {
      console.log(`[ClockInHeadless] status request failed ${status.res.status}`);
      return;
    }

    const openShift = status.data?.open_shift;
    if (!openShift?.shift_id || !openShift?.store_id) {
      console.log('[ClockInHeadless] no open shift');
      return;
    }

    console.log(`[ClockInHeadless] open shift confirmed shift=${openShift.shift_id}`);

    if (eventName === 'heartbeat') {
      console.log('[ClockInHeadless] heartbeat diagnostic-only; fresh location skipped');
      return;
    }

    const location = eventLocation(event);
    const coords = location?.coords || {};
    const geofence = eventGeofence(event);
    const action = eventAction(event);
    const payload = {
      event: eventName,
      source: `headless_${eventName}`,
      timestamp: location?.timestamp || Date.now(),
      employee_code: session.employeeCode,
      shift_id: openShift.shift_id,
      store_id: openShift.store_id,
      is_moving: event?.params?.isMoving ?? event?.isMoving,
      geofence_action: action,
      lat: coords.latitude,
      lng: coords.longitude,
      accuracy_m: coords.accuracy,
      location,
      geofence,
      headless_event: event,
    };

    const posted = await apiPost('/api/mobile/bg/event', payload);
    if (!posted.res.ok) {
      console.log(`[ClockInHeadless] bg event post failed ${posted.res.status}`);
      return;
    }

    console.log(
      `[ClockInHeadless] bg event posted ping_created=${
        posted.data?.ping_created === true
      } ping_skipped=${posted.data?.ping_skipped || ''}`,
    );
  } catch (error) {
    console.log(`[ClockInHeadless] failed: ${String(error)}`);
  }
}
