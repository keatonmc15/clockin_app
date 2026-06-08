import React, {useEffect, useMemo, useRef, useState} from 'react';
import {
  SafeAreaView,
  Text,
  View,
  TextInput,
  Alert,
  ScrollView,
  Pressable,
  Platform,
  StyleSheet,
  Image,
  AppState,
  AppStateStatus,
  Linking,
  PermissionsAndroid,
} from 'react-native';

import BackgroundGeolocation, {
  Location,
  State,
  GeofenceEvent,
} from 'react-native-background-geolocation';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {postBgEvent} from './src/bg/postBgEvent';
import HelpScreen from './screens/HelpScreen';

type OpenShift = null | {
  shift_id: number;
  store_id: number;
  store_name: string;
  clock_in_utc: string;
  clock_in_local?: string;
  closed_by_admin?: boolean;
};

type StoreItem = {code: string; name: string};
type Employee = {
  id: number;
  name: string;
  username_code?: string;
  suggested_username_code?: string;
  active: boolean;
};

type StatusResponse = {
  ok: boolean;
  employee?: Employee;
  open_shift: OpenShift;
  server_time_utc?: string;
  error?: string;
};

type LocationPermissionStatus = {
  status: 'checking' | 'granted' | 'denied' | 'incomplete';
  message: string;
  needsSettings: boolean;
};

type FreshPositionResult = {
  location: Location | null;
  errorCode?: string;
  errorMessage?: string;
};

const API_BASE = __DEV__ ? 'http://10.0.2.2:5000' : 'https://clockin-app.onrender.com';
const DEVICE_TOKEN = 'KeatonClockInMobile_Venom97Triad1997151506172024!';
const SESSION_KEY = 'clockin_mobile_employee_session_v1';
const SELECTED_STORE_KEY = 'clockin_mobile_selected_store_v1';

function headersJson() {
  return {
    'Content-Type': 'application/json',
    'X-Device-Token': DEVICE_TOKEN,
  };
}

function deviceLabel() {
  return `RN-${Platform.OS}`;
}

export default function App() {
  const [bgState, setBgState] = useState<State | null>(null);
  const [locationPermission, setLocationPermission] = useState<LocationPermissionStatus>({
    status: 'checking',
    message: 'Checking location permission...',
    needsSettings: false,
  });
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [locationLookupBusy, setLocationLookupBusy] = useState(false);

  const [employeeCode, setEmployeeCode] = useState('');
  const [pin, setPin] = useState('');
  const [storeCode, setStoreCode] = useState('');
  const [loggedIn, setLoggedIn] = useState(false);
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [sessionRestoring, setSessionRestoring] = useState(true);

  const [stores, setStores] = useState<StoreItem[]>([]);
  const [storeName, setStoreName] = useState<string>(''); // display name
  const [storePickerVisible, setStorePickerVisible] = useState(false);
  const [storeSearch, setStoreSearch] = useState('');
  const [showHelp, setShowHelp] = useState(false);

  // Server-truth status
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  // Hidden debug log drawer
  const [showDebug, setShowDebug] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  // Keep a device_uuid (BG provides one in events; we also store last seen server-side)
  const deviceUuidRef = useRef<string | null>(null);

  // ✅ filteredStores (ONLY DECLARED ONCE)
  const filteredStores = useMemo(() => {
    const q = storeSearch.trim().toLowerCase();
    if (!q) return stores;

    return stores.filter(s => {
      const name = (s.name || '').toLowerCase();
      const code = (s.code || '').toLowerCase();
      return name.includes(q) || code.includes(q);
    });
  }, [stores, storeSearch]);

  // -----------------------------------
  // ✅ Geofence prompt modal state
  // -----------------------------------
  type PromptKind = 'enter' | 'exit';

  const [promptVisible, setPromptVisible] = useState(false);
  const [promptKind, setPromptKind] = useState<PromptKind>('enter');
  const [promptText, setPromptText] = useState('');
  const promptTimerRef = useRef<any>(null);

  // ✅ Auto-close after EXIT grace period
  const EXIT_GRACE_MS = 8 * 60 * 1000; // 8 minutes (change to 20_000 for fast testing)
  const exitAutoCloseTimerRef = useRef<any>(null);

  function clearExitAutoCloseTimer() {
    if (exitAutoCloseTimerRef.current) {
      clearTimeout(exitAutoCloseTimerRef.current);
      exitAutoCloseTimerRef.current = null;
    }
  }

  function clearPromptTimer() {
    if (promptTimerRef.current) {
      clearTimeout(promptTimerRef.current);
      promptTimerRef.current = null;
    }
  }

  function showPrompt(kind: PromptKind, text: string) {
    clearPromptTimer();
    setPromptKind(kind);
    setPromptText(text);
    setPromptVisible(true);
  }

  function hidePrompt() {
    clearPromptTimer();
    setPromptVisible(false);
  }

  const canContinue = useMemo(() => {
    return employeeCode.trim().length > 0 && pin.trim().length > 0;
  }, [employeeCode, pin]);

  const openShift = status?.open_shift ?? null;
  const isClockedIn = !!openShift;
  const lockedStoreName = isClockedIn ? openShift?.store_name || '' : '';
  const locationReady = locationPermission.status === 'granted';
  const canClockIn =
    loggedIn && !isClockedIn && storeCode.trim().length > 0 && locationReady && !locationLookupBusy;
  const canClockOut = loggedIn && isClockedIn && locationReady && !locationLookupBusy;

  const log = (msg: string) => {
    const line = `${new Date().toLocaleTimeString()}  ${msg}`;
    setLogs(prev => [line, ...prev].slice(0, 250));
    console.log(line);
  };

  async function apiPost(path: string, body: any) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: headersJson(),
      body: JSON.stringify(body),
    });

    const text = await res.text();
    let data: any = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = {_raw: text};
    }

    return {res, data};
  }

  async function persistEmployeeSession(
    code: string,
    pinValue: string,
    employeeValue: Employee,
  ) {
    await AsyncStorage.setItem(
      SESSION_KEY,
      JSON.stringify({
        employeeCode: code,
        pin: pinValue,
        employee: employeeValue,
      }),
    );
  }

  async function persistSelectedStore(store: StoreItem) {
    await AsyncStorage.setItem(SELECTED_STORE_KEY, JSON.stringify(store));
  }

  function androidVersionNumber() {
    const raw = Platform.Version;
    return typeof raw === 'number' ? raw : parseInt(String(raw), 10) || 0;
  }

  async function checkLocationPermission(): Promise<LocationPermissionStatus> {
    if (Platform.OS !== 'android') {
      const ok = {
        status: 'granted' as const,
        message: 'Location permission is ready.',
        needsSettings: false,
      };
      setLocationPermission(ok);
      return ok;
    }

    const fine = await PermissionsAndroid.check(
      PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
    );
    const background =
      androidVersionNumber() < 29 ||
      (await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.ACCESS_BACKGROUND_LOCATION,
      ));

    let next: LocationPermissionStatus;
    if (fine && background) {
      next = {
        status: 'granted',
        message: 'Location permission is ready.',
        needsSettings: false,
      };
    } else if (!fine) {
      next = {
        status: 'denied',
        message:
          'Location is required to clock in. Choose Allow location access and keep precise location on.',
        needsSettings: true,
      };
    } else {
      next = {
        status: 'incomplete',
        message:
          'Location is partly allowed. Open settings and choose Allow all the time for ClockIn.',
        needsSettings: true,
      };
    }

    setLocationPermission(next);
    return next;
  }

  async function requestLocationPermission() {
    if (Platform.OS !== 'android') {
      await checkLocationPermission();
      return;
    }

    setPermissionBusy(true);
    try {
      const fineResult = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
        {
          title: 'Allow location for ClockIn',
          message:
            'ClockIn needs precise location to verify you are at the store before clocking in.',
          buttonPositive: 'Allow',
          buttonNegative: 'Not now',
        },
      );

      if (fineResult !== PermissionsAndroid.RESULTS.GRANTED) {
        setLocationPermission({
          status: 'denied',
          message:
            'Location was not allowed. Open settings, choose Location, then allow precise location.',
          needsSettings: true,
        });
        return;
      }

      if (androidVersionNumber() >= 29) {
        const backgroundGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.ACCESS_BACKGROUND_LOCATION,
        );

        if (!backgroundGranted) {
          const bgResult = await PermissionsAndroid.request(
            PermissionsAndroid.PERMISSIONS.ACCESS_BACKGROUND_LOCATION,
          );

          if (bgResult !== PermissionsAndroid.RESULTS.GRANTED) {
            setLocationPermission({
              status: 'incomplete',
              message:
                'Almost done. Open settings and choose Allow all the time for location.',
              needsSettings: true,
            });
            return;
          }
        }
      }

      await checkLocationPermission();
    } catch {
      setLocationPermission({
        status: 'denied',
        message: 'Could not request location permission. Open app settings and allow location.',
        needsSettings: true,
      });
    } finally {
      setPermissionBusy(false);
    }
  }

  async function openAppSettings() {
    try {
      await Linking.openSettings();
    } catch {
      Alert.alert('Open settings', 'Open Android Settings, find ClockIn, then allow location.');
    }
  }

  async function openLocationSettings() {
    try {
      if (Platform.OS === 'android' && typeof (Linking as any).sendIntent === 'function') {
        await (Linking as any).sendIntent('android.settings.LOCATION_SOURCE_SETTINGS');
        return;
      }
      await Linking.openSettings();
    } catch {
      Alert.alert('Open settings', 'Open Android Settings and turn on Location Services.');
    }
  }

  async function ensureLocationReadyForClockIn() {
    const permission = await checkLocationPermission();
    if (permission.status === 'granted') return true;

    const buttons =
      permission.needsSettings
        ? [
            {text: 'Not now', style: 'cancel' as const},
            {text: 'Allow Location', onPress: requestLocationPermission},
            {text: 'Open Settings', onPress: openAppSettings},
          ]
        : [
            {text: 'Not now', style: 'cancel' as const},
            {text: 'Allow Location', onPress: requestLocationPermission},
          ];

    Alert.alert(
      'Location required',
      permission.message,
      buttons,
    );
    return false;
  }

  // -----------------------------------
  // Status refresh (SOURCE OF TRUTH)
  // Returns the freshest status data (or null) so callers can make decisions.
  // -----------------------------------
  async function refreshStatus(
    opts?: {silent?: boolean},
    identity?: {employeeCode: string; pin: string},
  ): Promise<StatusResponse | null> {
    if (!loggedIn && !identity) return null;
    const codeClean = (identity?.employeeCode ?? employeeCode).trim().toLowerCase();
    const pinClean = (identity?.pin ?? pin).trim();
    if (!codeClean || !pinClean) return null;

    if (!opts?.silent) setStatusLoading(true);

    try {
      const {res, data} = await apiPost('/api/mobile/status', {
        username_code: codeClean,
        pin: pinClean,
        device_uuid: deviceUuidRef.current,
        device_label: deviceLabel(),
      });

      if (!res.ok) {
        const msg = data?.error || `Status failed (${res.status})`;
        log(`❌ /api/mobile/status failed: ${msg}`);
        setStatus(prev => ({
          ...(prev || {open_shift: null}),
          ok: false,
          error: msg,
        }));
        return null;
      }

      const fresh = data as StatusResponse;
      setStatus(fresh);
      if (fresh.employee) {
        setEmployee(fresh.employee);
      }

      if (fresh?.open_shift) {
        log(
          `✅ status: CLOCKED IN shift=${fresh.open_shift.shift_id} store=${fresh.open_shift.store_name}`,
        );
      } else {
        log(`✅ status: NOT CLOCKED IN`);
      }

      return fresh;
    } catch (e) {
      const msg = `Status network error`;
      log(`❌ /api/mobile/status exception: ${String(e)}`);
      setStatus(prev => ({
        ...(prev || {open_shift: null}),
        ok: false,
        error: msg,
      }));
      return null;
    } finally {
      if (!opts?.silent) setStatusLoading(false);
    }
  }

  // Poll status every 45s while logged in
  useEffect(() => {
    if (!loggedIn) return;
    refreshStatus({silent: true});
    const id = setInterval(() => refreshStatus({silent: true}), 45000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loggedIn, employeeCode, pin]);

  // Refresh status when app returns to foreground
  useEffect(() => {
    const onAppState = (s: AppStateStatus) => {
      if (s === 'active' && loggedIn) {
        refreshStatus({silent: true});
      }
      if (s === 'active') {
        checkLocationPermission();
      }
    };
    const sub = AppState.addEventListener('change', onAppState);
    return () => sub.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loggedIn, employeeCode, pin]);

  useEffect(() => {
    checkLocationPermission();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch stores (for picker; does not block login)
useEffect(() => {
  (async () => {
    try {
      const r = await fetch(`${API_BASE}/api/stores/all`, {
        method: 'GET',
        headers: headersJson(), // harmless if not required; helpful if your server expects token
      });

      const rawText = await r.text();
      let json: any = null;
      try {
        json = rawText ? JSON.parse(rawText) : null;
      } catch {
        json = null;
      }

      const list = Array.isArray(json)
        ? json
        : Array.isArray(json?.stores)
          ? json.stores
          : [];

      if (!r.ok) {
        log(`❌ stores load failed (${r.status}) body=${rawText?.slice(0, 200)}`);
        setStores([]);
        return;
      }

      if (list.length === 0) {
        log(`⚠️ stores load returned 0 items. body=${rawText?.slice(0, 200)}`);
        setStores([]);
        return;
      }

      setStores(list);
      log(`✅ stores loaded: ${list.length}`);
    } catch (e) {
      log(`❌ stores load exception: ${String(e)}`);
      setStores([]);
    }
  })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);

  useEffect(() => {
    (async () => {
      try {
        const [sessionRaw, storeRaw] = await Promise.all([
          AsyncStorage.getItem(SESSION_KEY),
          AsyncStorage.getItem(SELECTED_STORE_KEY),
        ]);

        if (storeRaw) {
          const savedStore = JSON.parse(storeRaw);
          if (savedStore?.code) {
            setStoreCode(String(savedStore.code).trim().toLowerCase());
            setStoreName(savedStore?.name || savedStore.code);
          }
        }

        if (sessionRaw) {
          const savedSession = JSON.parse(sessionRaw);
          const codeClean = String(savedSession?.employeeCode || '').trim().toLowerCase();
          const pinClean = String(savedSession?.pin || '').trim();
          if (codeClean && pinClean) {
            setEmployeeCode(codeClean);
            setPin(pinClean);
            if (savedSession?.employee) {
              setEmployee(savedSession.employee);
            }
            setLoggedIn(true);
            await refreshStatus(
              {silent: true},
              {employeeCode: codeClean, pin: pinClean},
            );
            log('✅ restored employee session');
          }
        }
      } catch (e) {
        log(`⚠️ session restore failed: ${String(e)}`);
      } finally {
        setSessionRestoring(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function syncGeofenceForStore(storeClean: string): Promise<boolean> {
    const codeClean = employeeCode.trim().toLowerCase();
    const pinClean = pin.trim();
    if (!codeClean || !pinClean || !storeClean) return false;

    try {
      const {res, data} = await apiPost('/api/mobile/geofences', {
        username_code: codeClean,
        pin: pinClean,
        qr_token: storeClean,
        device_uuid: deviceUuidRef.current,
        device_label: deviceLabel(),
      });

      if (!res.ok) {
        const msg = data?.error || `Geofence sync failed (${res.status})`;
        log(`❌ /api/mobile/geofences failed: ${msg}`);
        Alert.alert('Geofence sync failed', msg);
        return false;
      }

      const geofences = Array.isArray(data?.geofences) ? data.geofences : [];
      if (geofences.length === 0) {
        Alert.alert('Geofence sync failed', 'No geofences returned.');
        return false;
      }

      await BackgroundGeolocation.removeGeofences().catch(() => null);
      for (const gf of geofences) {
        await BackgroundGeolocation.addGeofence(gf);
      }

      log(
        `✅ geofences loaded: ${geofences
          .map((g: any) => g.identifier)
          .join(', ')}`,
      );
      return true;
    } catch (e) {
      log(`❌ geofence sync exception: ${String(e)}`);
      Alert.alert('Geofence sync failed', 'Network error. Try again.');
      return false;
    }
  }

  async function selectStore(store: StoreItem) {
    if (isClockedIn) {
      Alert.alert('Store locked', 'Clock out before changing stores.');
      return;
    }

    const clean = store.code.trim().toLowerCase();
    setStoreCode(clean);
    setStoreName(store.name);
    setStorePickerVisible(false);
    setStoreSearch('');
    await persistSelectedStore({code: clean, name: store.name});
    if (loggedIn) {
      await syncGeofenceForStore(clean);
    }
  }

  // -----------------------------------
  // Login -> employee session
  // -----------------------------------
  async function continueLogin() {
    if (!canContinue) {
      Alert.alert('Missing info', 'Enter your username/code and PIN.');
      return;
    }

    const codeClean = employeeCode.trim().toLowerCase();
    const pinClean = pin.trim();

    try {
      const {res, data} = await apiPost('/api/mobile/me', {
        username_code: codeClean,
        pin: pinClean,
        device_uuid: deviceUuidRef.current,
        device_label: deviceLabel(),
      });

      if (!res.ok) {
        const msg = data?.error || `Login failed (${res.status})`;
        log(`❌ /api/mobile/me failed: ${msg}`);
        Alert.alert('Login failed', msg);
        return;
      }

      log(`✅ /api/mobile/me OK (emp=${data?.employee?.name || 'ok'})`);
      setEmployeeCode(codeClean);
      setEmployee(data.employee);
      setLoggedIn(true);
      setStatus({ok: true, employee: data.employee, open_shift: null});
      await persistEmployeeSession(codeClean, pinClean, data.employee);
      await checkLocationPermission();
      log('✅ employee session ready');
      await refreshStatus(
        {silent: true},
        {employeeCode: codeClean, pin: pinClean},
      );
    } catch (e) {
      log(`❌ /api/mobile/me exception: ${String(e)}`);
      Alert.alert('Login failed', 'Network error. Try again.');
      return;
    }
  }

  async function logout() {
    hidePrompt();
    setStorePickerVisible(false);
    setStoreSearch('');
    setLoggedIn(false);
    setEmployee(null);
    setEmployeeCode('');
    setPin('');
    setStatus(null);
    clearExitAutoCloseTimer();
    await AsyncStorage.removeItem(SESSION_KEY);
    log('👋 logged out');
  }

  // -----------------------------------
  // Clock in/out (MOBILE endpoints)
  // -----------------------------------
  function safeJson(value: unknown) {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  function locationErrorCode(error: any) {
    if (error == null) return undefined;
    if (typeof error === 'number') return String(error);
    if (typeof error === 'string') return error;
    return String(error.code ?? error.status ?? error.name ?? '');
  }

  function locationErrorMessage(error: any) {
    const code = locationErrorCode(error);
    const rawMessage =
      typeof error === 'string'
        ? error
        : String(error?.message || error?.error || error?.reason || '').trim();
    const lower = rawMessage.toLowerCase();

    if (code === '1' || lower.includes('permission')) {
      return 'Location permission is not allowed. Open app settings and allow precise location.';
    }
    if (code === '408' || lower.includes('timeout')) {
      return 'GPS is taking too long to find your location. Step outside or closer to a window, then try again.';
    }
    if (lower.includes('unavailable') || lower.includes('unknown') || code === '0') {
      return 'Location is unavailable right now. Make sure Location Services are on, wait a moment, then try again.';
    }
    if (code === '2' || lower.includes('network')) {
      return 'Location could not connect to the location provider. Check internet and Location Services, then try again.';
    }
    if (rawMessage) return rawMessage;
    return 'Location is unavailable right now. Make sure Location Services are on, then try again.';
  }

  function showLocationError(title: string, message: string, openSettings = false) {
    Alert.alert(
      title,
      message,
      openSettings
        ? [
            {text: 'Not now', style: 'cancel'},
            {text: 'Open Settings', onPress: openLocationSettings},
          ]
        : [{text: 'OK'}],
    );
  }

  async function getFreshPosition(): Promise<FreshPositionResult> {
    try {
      const provider = await BackgroundGeolocation.getProviderState();
      log(
        `provider enabled=${provider.enabled} gps=${provider.gps} network=${provider.network} status=${provider.status}`,
      );

      if (!provider.enabled) {
        return {
          location: null,
          errorCode: 'location_services_off',
          errorMessage: 'Turn on Location Services in Android Settings, then try again.',
        };
      }
    } catch (e) {
      log(`provider state check failed: ${safeJson(e)}`);
    }

    try {
      const location = await BackgroundGeolocation.getCurrentPosition({
        timeout: 60,
        maximumAge: 15000,
        desiredAccuracy: 25,
        samples: 3,
        persist: false,
      });
      return {location};
    } catch (e) {
      log(`getCurrentPosition failed: ${safeJson(e)}`);
      return {
        location: null,
        errorCode: locationErrorCode(e),
        errorMessage: locationErrorMessage(e),
      };
    }
  }

  async function clockIn() {
    if (!loggedIn) return;
    const permissionOk = await ensureLocationReadyForClockIn();
    if (!permissionOk) return;

    const storeClean = storeCode.trim().toLowerCase();
    if (!storeClean) {
      Alert.alert('Select a store', 'Choose your current store before clocking in.');
      return;
    }

    const synced = await syncGeofenceForStore(storeClean);
    if (!synced) return;

    setLocationLookupBusy(true);
    const locationResult = await getFreshPosition();
    setLocationLookupBusy(false);
    if (!locationResult.location) {
      showLocationError(
        'Location needed',
        locationResult.errorMessage || 'Location is unavailable right now. Try again.',
        locationResult.errorCode === 'location_services_off',
      );
      return;
    }
    const loc = locationResult.location;

    const {res, data} = await apiPost('/api/mobile/clock-in', {
      username_code: employeeCode.trim().toLowerCase(),
      pin: pin.trim(),
      qr_token: storeClean,
      lat: loc.coords.latitude,
      lon: loc.coords.longitude,
      accuracy_m: loc.coords.accuracy,
      device_uuid: deviceUuidRef.current,
      device_label: deviceLabel(),
    });

    if (!res.ok) {
      const msg = data?.message || data?.error || `Clock-in failed (${res.status})`;
      log(`❌ /api/mobile/clock-in failed: ${msg}`);
      Alert.alert('Clock-in failed', msg);
      return;
    }

    log(`✅ /api/mobile/clock-in OK shift_id=${data.shift_id}`);
    hidePrompt();
    clearExitAutoCloseTimer();
    await refreshStatus();
  }

  async function clockOut() {
    if (!loggedIn) return;
    const permissionOk = await ensureLocationReadyForClockIn();
    if (!permissionOk) return;

    setLocationLookupBusy(true);
    const locationResult = await getFreshPosition();
    setLocationLookupBusy(false);
    if (!locationResult.location) {
      showLocationError(
        'Location needed',
        locationResult.errorMessage || 'Location is unavailable right now. Try again.',
        locationResult.errorCode === 'location_services_off',
      );
      return;
    }
    const loc = locationResult.location;

    const {res, data} = await apiPost('/api/mobile/clock-out', {
      username_code: employeeCode.trim().toLowerCase(),
      pin: pin.trim(),
      lat: loc.coords.latitude,
      lon: loc.coords.longitude,
      accuracy_m: loc.coords.accuracy,
      device_uuid: deviceUuidRef.current,
      device_label: deviceLabel(),
    });

    if (!res.ok) {
      const msg = data?.message || data?.error || `Clock-out failed (${res.status})`;
      log(`❌ /api/mobile/clock-out failed: ${msg}`);
      Alert.alert('Clock-out failed', msg);
      return;
    }

    log(`✅ /api/mobile/clock-out OK minutes=${data.minutes}`);
    hidePrompt();
    clearExitAutoCloseTimer();
    await refreshStatus();
  }

  async function autoExitClose() {
    if (!loggedIn) return;

    const fresh = await refreshStatus({silent: true});
    if (!fresh?.open_shift) {
      log('ℹ️ auto-exit-close skipped (no open shift)');
      return;
    }

    const locationResult = await getFreshPosition();
    if (!locationResult.location) {
      log('auto-exit-close: no location fix');
      return;
    }
    const loc = locationResult.location;

    const {res, data} = await apiPost('/api/mobile/auto-exit-close', {
      username_code: employeeCode.trim().toLowerCase(),
      pin: pin.trim(),
      lat: loc.coords.latitude,
      lon: loc.coords.longitude,
      accuracy_m: loc.coords.accuracy,
      device_uuid: deviceUuidRef.current,
      device_label: deviceLabel(),
      reason: 'Auto-close after EXIT (grace elapsed)',
    });

    if (!res.ok) {
      const msg = data?.error || `auto-exit-close failed (${res.status})`;
      log(`❌ auto-exit-close failed: ${msg}`);
      return;
    }

    log(`✅ auto-exit-close OK shift_id=${data.shift_id} minutes=${data.minutes}`);
    await refreshStatus({silent: true});
  }

  async function onPromptYes() {
    hidePrompt();

    // Safety: refresh server-truth first (prevents stale prompts)
    const fresh = await refreshStatus({silent: true});

    if (promptKind === 'enter') {
      if (!fresh?.open_shift) {
        await clockIn();
      } else {
        log('ℹ️ prompt enter ignored (already clocked in)');
      }
    } else {
      if (fresh?.open_shift) {
        await clockOut();
      } else {
        log('ℹ️ prompt exit ignored (no open shift)');
      }
    }
  }

  function onPromptNo() {
    hidePrompt();
    log(`👎 prompt dismissed (${promptKind})`);
  }

  // -----------------------------------
  // BG Geo setup
  // -----------------------------------
  useEffect(() => {
    log(`APP_MOUNT Platform=${Platform.OS}`);

    if (!locationReady) {
      setBgState(null);
      log('tracking setup deferred until location permission is allowed');
      return;
    }

    const subLocation = BackgroundGeolocation.onLocation(
      async (location: Location) => {
        if ((location as any)?.uuid && !deviceUuidRef.current) {
          deviceUuidRef.current = String((location as any).uuid);
        }

        if (showDebug) {
          log(
            `📍 ${location.coords.latitude.toFixed(5)}, ${location.coords.longitude.toFixed(
              5,
            )} (acc ${Math.round(location.coords.accuracy)}m)`,
          );
        }

        try {
          await postBgEvent({
            event: 'location',
            timestamp: location.timestamp,
            location: location as any,
          });
        } catch (e) {
          if (showDebug) log(`⚠️ postBgEvent failed: ${String(e)}`);
        }
      },
      err => showDebug && log(`❌ location error: ${JSON.stringify(err)}`),
    );

    const subGeofence = BackgroundGeolocation.onGeofence(async (event: GeofenceEvent) => {
      if (showDebug) log(`🧭 geofence ${event.identifier} ${event.action}`);

      try {
        await postBgEvent({
          event: 'geofence',
          timestamp: Date.now(),
          geofence: event as any,
        });
      } catch (e) {
        if (showDebug) log(`⚠️ postBgEvent geofence failed: ${String(e)}`);
      }

      // ✅ Single source of prompt logic + auto-exit-close scheduling
      if (!loggedIn) return;

      if (event.action === 'ENTER') {
        // entering cancels any pending exit-auto-close
        clearExitAutoCloseTimer();

        const fresh = await refreshStatus({silent: true});
        if (!promptVisible && !fresh?.open_shift) {
          const name = storeName || storeCode || 'the store';
          showPrompt('enter', `You arrived at ${name}. Clock in now?`);
          promptTimerRef.current = setTimeout(() => onPromptNo(), 60000);
        }
        return;
      }

      if (event.action === 'EXIT') {
        const fresh = await refreshStatus({silent: true});

        // No open shift = nothing to close
        if (!fresh?.open_shift) {
          clearExitAutoCloseTimer();
          return;
        }

        // Show prompt if not already showing
        if (!promptVisible) {
          showPrompt('exit', `You left ${fresh.open_shift.store_name}. Clock out now?`);
          promptTimerRef.current = setTimeout(() => onPromptNo(), 90000);
        }

        // 🚫 Prevent double scheduling
        if (exitAutoCloseTimerRef.current) {
          log('ℹ️ auto-exit-close already scheduled');
          return;
        }

        // Schedule auto-close
        exitAutoCloseTimerRef.current = setTimeout(() => {
          exitAutoCloseTimerRef.current = null; // clear reference
          autoExitClose().catch(() => null);
        }, EXIT_GRACE_MS);

        log(`⏳ auto-exit-close scheduled in ${Math.round(EXIT_GRACE_MS / 60000)} min`);
      }
    });

    const subProvider = BackgroundGeolocation.onProviderChange(p => {
      if (showDebug) log(`📡 provider enabled=${p.enabled} status=${p.status}`);
    });

    (async () => {
      const state = await BackgroundGeolocation.ready({
        desiredAccuracy: -1,
        distanceFilter: 25,
        stopOnTerminate: false,
        startOnBoot: true,
        disableLocationAuthorizationAlert: true,
        locationAuthorizationRequest: 'Always',
        debug: false,
        logLevel: 0,
        notification: {
          title: 'ClockIn',
          text: 'Location tracking enabled',
        },
        geofenceProximityRadius: 200,
      } as any);

      setBgState(state);
      log(`ready enabled=${state.enabled} moving=${state.isMoving}`);

      if (!state.enabled) {
        await BackgroundGeolocation.start();
        const s2 = await BackgroundGeolocation.getState();
        setBgState(s2);
        log(`tracking started enabled=${s2.enabled}`);
      }
    })().catch(e => log(`❌ init error: ${String(e)}`));

    return () => {
      subLocation.remove();
      subGeofence.remove();
      subProvider.remove();
    };

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loggedIn, storeName, storeCode, employeeCode, pin, showDebug, locationReady]);

  // -----------------------------------
  // UI
  // -----------------------------------
  const statusText = isClockedIn
    ? `Clocked IN (Shift #${openShift?.shift_id})`
    : 'Not clocked in';

  const statusDetail = isClockedIn ? `Store: ${openShift?.store_name || ''}` : '';

  const clockInText = isClockedIn
    ? (openShift?.clock_in_local || openShift?.clock_in_utc || '')
    : '';

  const currentEmployee = status?.employee || employee;
  const selectedStore = isClockedIn
    ? {
        code: storeCode,
        name: openShift?.store_name || storeName || storeCode,
      }
    : storeCode
      ? {code: storeCode, name: storeName || storeCode}
      : null;

  if (showHelp) {
    return (
      <HelpScreen
        employee={currentEmployee}
        store={selectedStore}
        isClockedIn={isClockedIn}
        onClose={() => setShowHelp(false)}
        usernameCode={employeeCode}
        pin={pin}
      />
    );
  }

  function renderLocationPermissionCard() {
    const ok = locationPermission.status === 'granted';
    return (
      <View style={[styles.card, ok ? styles.permissionCardOk : styles.permissionCardWarn]}>
        <Text style={styles.permissionTitle}>Location access required</Text>
        <Text style={styles.line}>
          ClockIn needs location to confirm you are at the store before clocking in.
        </Text>
        <Text style={styles.lineMuted}>
          Choose Allow location access. If Android asks, choose Allow all the time and keep precise location on.
        </Text>
        <Text style={ok ? styles.permissionGoodText : styles.permissionWarnText}>
          {locationPermission.message}
        </Text>

        {!ok ? (
          <>
            <View style={{height: 10}} />
            <Pressable
              onPress={requestLocationPermission}
              style={[styles.secondaryBtn, permissionBusy && styles.disabledBtn]}
              disabled={permissionBusy}>
              <Text style={styles.secondaryBtnText}>
                {permissionBusy ? 'Checking...' : 'Allow Location Access'}
              </Text>
            </Pressable>

            {locationPermission.needsSettings ? (
              <>
                <View style={{height: 10}} />
                <Pressable onPress={openAppSettings} style={styles.secondaryBtn}>
                  <Text style={styles.secondaryBtnText}>Open App Settings</Text>
                </Pressable>
              </>
            ) : null}
          </>
        ) : null}
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.header}>
        <Image source={require('./assets/logo.png')} style={styles.logo} resizeMode="contain" />

        <Text style={styles.title}>ClockIn</Text>
        <Text style={styles.sub}>Tracking: {bgState?.enabled ? 'On' : 'Off'}</Text>

        <Pressable onPress={() => setShowDebug(v => !v)} style={styles.debugToggle}>
          <Text style={styles.debugToggleText}>{showDebug ? 'Hide Debug' : 'Show Debug'}</Text>
        </Pressable>
      </View>

      {renderLocationPermissionCard()}

      {!loggedIn ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Employee Login</Text>

          {sessionRestoring ? (
            <Text style={styles.lineMuted}>Restoring saved session…</Text>
          ) : null}

          <Text style={styles.label}>Username / Code</Text>
          <TextInput
            value={employeeCode}
            onChangeText={setEmployeeCode}
            placeholder="employee code"
            style={styles.input}
            autoCapitalize="none"
            autoCorrect={false}
          />

          <Text style={styles.label}>PIN</Text>
          <TextInput
            value={pin}
            onChangeText={setPin}
            keyboardType="numeric"
            placeholder="1234"
            style={styles.input}
            secureTextEntry
          />

          <Pressable
            onPress={continueLogin}
            style={[styles.primaryBtn, !canContinue && styles.disabledBtn]}
            disabled={!canContinue}>
            <Text style={styles.primaryBtnText}>Log In</Text>
          </Pressable>

          <Text style={styles.hint}>
            After login, choose your current store before clocking in.
          </Text>
        </View>
      ) : (
        <>
          <View style={styles.card}>
            <View style={styles.rowBetween}>
              <Text style={styles.cardTitle}>Shift</Text>
              <Pressable onPress={logout}>
                <Text style={styles.link}>Log Out</Text>
              </Pressable>
            </View>

            <Text style={styles.line}>
              Employee: {currentEmployee?.name || employeeCode}
            </Text>
            <Text style={styles.line}>Status: {statusText}</Text>

            {statusDetail ? <Text style={styles.line}>{statusDetail}</Text> : null}
            {clockInText ? <Text style={styles.line}>Clock-in: {clockInText}</Text> : null}

            <Text style={styles.label}>Store</Text>
            <Pressable
              onPress={() => {
                if (isClockedIn) {
                  Alert.alert('Store locked', 'Clock out before changing stores.');
                  return;
                }
                setStorePickerVisible(true);
              }}
              style={[
                styles.input,
                styles.pickerInput,
                isClockedIn && styles.lockedInput,
              ]}>
              <Text style={{fontSize: 16, opacity: storeName || lockedStoreName ? 1 : 0.45}}>
                {isClockedIn
                  ? lockedStoreName || 'Store locked to open shift'
                  : storeName || 'Tap to choose store'}
              </Text>
            </Pressable>

            {isClockedIn ? (
              <Text style={styles.lineMuted}>Store is locked while clocked in.</Text>
            ) : stores.length === 0 ? (
              <Text style={styles.lineMuted}>
                Loading store list… If this never loads, check internet.
              </Text>
            ) : !storeCode ? (
              <Text style={styles.lineMuted}>Select a store before clocking in.</Text>
            ) : !locationReady ? (
              <Text style={styles.lineMuted}>Allow location access before clocking in.</Text>
            ) : null}

            {status?.error ? <Text style={styles.lineMuted}>Status error: {status.error}</Text> : null}
            {locationLookupBusy ? <Text style={styles.lineMuted}>Getting location...</Text> : null}
            {statusLoading ? <Text style={styles.lineMuted}>Refreshing…</Text> : null}

            <View style={{height: 12}} />

            {!isClockedIn ? (
              <Pressable
                onPress={clockIn}
                style={[styles.bigBtn, !canClockIn && styles.disabledBtn]}
                disabled={!canClockIn}>
                <Text style={styles.bigBtnText}>
                  {locationLookupBusy ? 'GETTING LOCATION...' : 'CLOCK IN'}
                </Text>
              </Pressable>
            ) : (
              <Pressable
                onPress={clockOut}
                style={[styles.bigBtn, styles.bigBtnWarn, !canClockOut && styles.disabledBtn]}
                disabled={!canClockOut}>
                <Text style={styles.bigBtnText}>
                  {locationLookupBusy ? 'GETTING LOCATION...' : 'CLOCK OUT'}
                </Text>
              </Pressable>
            )}

            <View style={{height: 10}} />
            <Pressable onPress={() => refreshStatus()} style={styles.secondaryBtn}>
              <Text style={styles.secondaryBtnText}>Refresh Status</Text>
            </Pressable>

            {/* ✅ Help / Troubleshooting button */}
            <View style={{height: 10}} />
            <Pressable onPress={() => setShowHelp(true)} style={styles.secondaryBtn}>
              <Text style={styles.secondaryBtnText}>Help / Troubleshooting</Text>
            </Pressable>
          </View>

          {showDebug ? (
            <View style={[styles.card, {flex: 1}]}>
              <Text style={styles.cardTitle}>Debug</Text>
              <ScrollView style={styles.logBox}>
                {logs.map((l, idx) => (
                  <Text key={idx} style={styles.logLine}>
                    {l}
                  </Text>
                ))}
              </ScrollView>
            </View>
          ) : null}
        </>
      )}

      {/* ✅ Store Picker Modal */}
      {storePickerVisible ? (
        <View style={styles.promptOverlay}>
          <View style={styles.pickerCard}>
            <View style={styles.rowBetween}>
              <Text style={styles.pickerTitle}>Select Store</Text>
              <Pressable
                onPress={() => {
                  setStorePickerVisible(false);
                  setStoreSearch('');
                }}>
                <Text style={styles.link}>Close</Text>
              </Pressable>
            </View>

            <TextInput
              value={storeSearch}
              onChangeText={setStoreSearch}
              placeholder="Search stores…"
              style={[styles.input, {marginBottom: 10}]}
              autoCapitalize="none"
            />

            <ScrollView style={{maxHeight: 360}}>
              {filteredStores.map(s => (
                <Pressable
                  key={s.code}
                  onPress={() => selectStore(s)}
                  style={styles.pickerRow}>
                  <Text style={styles.pickerRowTitle}>{s.name}</Text>
                  <Text style={styles.pickerRowSub}>{s.code}</Text>
                </Pressable>
              ))}

              {stores.length > 0 && filteredStores.length === 0 ? (
                <Text style={styles.lineMuted}>No matches.</Text>
              ) : null}

              {stores.length === 0 ? (
                <Text style={styles.lineMuted}>
                  No stores loaded yet. Check internet.
                </Text>
              ) : null}
            </ScrollView>
          </View>
        </View>
      ) : null}

      {/* ✅ Geofence Prompt Overlay */}
      {promptVisible ? (
        <View style={styles.promptOverlay}>
          <View style={styles.promptCard}>
            <Text style={styles.promptTitle}>
              {promptKind === 'enter' ? 'Clock In?' : 'Clock Out?'}
            </Text>

            <Text style={styles.promptText}>{promptText}</Text>

            <View style={styles.promptRow}>
              <Pressable onPress={onPromptNo} style={[styles.promptBtn, styles.promptBtnSecondary]}>
                <Text style={styles.promptBtnTextSecondary}>Not now</Text>
              </Pressable>

              <Pressable
                onPress={onPromptYes}
                style={[
                  styles.promptBtn,
                  styles.promptBtnPrimary,
                  locationLookupBusy && styles.disabledBtn,
                ]}
                disabled={locationLookupBusy}>
                <Text style={styles.promptBtnTextPrimary}>
                  {locationLookupBusy
                    ? 'Getting location...'
                    : promptKind === 'enter'
                      ? 'Clock In'
                      : 'Clock Out'}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, padding: 14, backgroundColor: '#fff'},

  header: {marginBottom: 12},
  logo: {
    width: 120,
    height: 120,
    alignSelf: 'center',
    marginBottom: 10,
  },
  title: {fontSize: 22, fontWeight: '800', textAlign: 'center'},
  sub: {marginTop: 2, opacity: 0.7, textAlign: 'center'},
  debugToggle: {alignSelf: 'center', marginTop: 6},
  debugToggleText: {fontWeight: '700', opacity: 0.65},

  card: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 14,
    padding: 14,
    marginBottom: 12,
  },
  permissionCardOk: {
    borderColor: '#b8e2c1',
    backgroundColor: '#f5fbf6',
  },
  permissionCardWarn: {
    borderColor: '#f0cf9f',
    backgroundColor: '#fff9f0',
  },
  permissionTitle: {fontSize: 16, fontWeight: '900', marginBottom: 8},
  permissionGoodText: {
    color: '#136b2f',
    fontSize: 14,
    fontWeight: '800',
    marginTop: 4,
  },
  permissionWarnText: {
    color: '#8a4b00',
    fontSize: 14,
    fontWeight: '800',
    marginTop: 4,
  },
  cardTitle: {fontSize: 18, fontWeight: '800', marginBottom: 10},
  label: {fontWeight: '700', marginBottom: 6},
  input: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 12,
    marginBottom: 12,
    fontSize: 16,
  },

  pickerInput: {justifyContent: 'center'},
  lockedInput: {backgroundColor: '#f4f4f4'},

  primaryBtn: {
    backgroundColor: '#111',
    paddingVertical: 14,
    borderRadius: 14,
    alignItems: 'center',
  },
  disabledBtn: {opacity: 0.4},
  primaryBtnText: {color: '#fff', fontSize: 16, fontWeight: '800'},

  hint: {marginTop: 10, opacity: 0.6, fontSize: 13, textAlign: 'center'},

  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  link: {fontWeight: '800', opacity: 0.7},

  line: {fontSize: 16, marginBottom: 6},
  lineMuted: {fontSize: 14, opacity: 0.7},

  bigBtn: {
    backgroundColor: '#0b5',
    paddingVertical: 18,
    borderRadius: 16,
    alignItems: 'center',
  },
  bigBtnWarn: {backgroundColor: '#d33'},
  bigBtnText: {color: '#fff', fontSize: 20, fontWeight: '900', letterSpacing: 1},

  secondaryBtn: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 14,
    paddingVertical: 12,
    alignItems: 'center',
  },
  secondaryBtnText: {fontWeight: '800', opacity: 0.8},

  logBox: {
    borderWidth: 1,
    borderColor: '#eee',
    borderRadius: 12,
    padding: 10,
  },
  logLine: {marginBottom: 6, opacity: 0.85},

  // ✅ Prompt / Modal overlay styles (shared)
  promptOverlay: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.35)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 16,
  },

  // ✅ Store picker modal
  pickerCard: {
    width: '100%',
    borderRadius: 16,
    backgroundColor: '#fff',
    padding: 16,
    borderWidth: 1,
    borderColor: '#eee',
  },
  pickerTitle: {fontSize: 18, fontWeight: '900'},
  pickerRow: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f1f1',
  },
  pickerRowTitle: {fontSize: 16, fontWeight: '800'},
  pickerRowSub: {marginTop: 2, opacity: 0.6},

  // ✅ Prompt modal
  promptCard: {
    width: '100%',
    borderRadius: 16,
    backgroundColor: '#fff',
    padding: 16,
    borderWidth: 1,
    borderColor: '#eee',
  },
  promptTitle: {fontSize: 18, fontWeight: '900', marginBottom: 8},
  promptText: {fontSize: 15, opacity: 0.85, marginBottom: 14},
  promptRow: {flexDirection: 'row', gap: 10, justifyContent: 'flex-end'},
  promptBtn: {paddingVertical: 12, paddingHorizontal: 14, borderRadius: 12},
  promptBtnPrimary: {backgroundColor: '#111'},
  promptBtnSecondary: {borderWidth: 1, borderColor: '#ddd'},
  promptBtnTextPrimary: {color: '#fff', fontWeight: '900'},
  promptBtnTextSecondary: {fontWeight: '900', opacity: 0.75},
});
