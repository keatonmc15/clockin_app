const API_BASE = __DEV__ ? 'http://10.0.2.2:5000' : 'https://clockin-app.onrender.com';
const DEVICE_TOKEN = "KeatonClockInMobile_Venom97Triad1997151506172024!";

type Meta = {
  device_uuid?: string;
  device_label?: string;
};

export async function postBgEvent(payload: any, meta?: Meta) {
  const body = {
    ...payload,
    ...(meta?.device_uuid ? {device_uuid: meta.device_uuid} : {}),
    ...(meta?.device_label ? {device_label: meta.device_label} : {}),
  };

  const res = await fetch(`${API_BASE}/api/mobile/bg/event`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Device-Token': DEVICE_TOKEN,
    },
    body: JSON.stringify(body),
  });

  // swallow parse errors; we just want to know if it succeeded
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.error || `bg event post failed (${res.status})`);
  }
  return data;
}
