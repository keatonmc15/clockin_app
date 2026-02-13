{% extends "base.html" %}

{% block content %}
<h2>Mobile Events</h2>

<form method="get" style="margin: 10px 0; display:flex; gap:10px; flex-wrap:wrap;">
  <div>
    <label>Event</label><br />
    <input name="event" value="{{ event or '' }}" placeholder="location / geofence / ..." />
  </div>

  <div>
    <label>Device UUID</label><br />
    <input name="device" value="{{ device or '' }}" placeholder="uuid" style="min-width:320px;" />
  </div>

  <div>
    <label>Limit</label><br />
    <input name="limit" value="{{ limit or 200 }}" style="width:80px;" />
  </div>

  <div style="align-self:end;">
    <button type="submit">Filter</button>
    <a href="/admin/mobile-events" style="margin-left:10px;">Reset</a>
  </div>
</form>

<table border="1" cellpadding="6" cellspacing="0" style="width:100%; font-size:14px;">
  <thead>
    <tr>
      <th>Received (Local)</th>
      <th>Event At (Local)</th>
      <th>Event</th>
      <th>Device</th>
      <th>Lat</th>
      <th>Lng</th>
      <th>Acc</th>
      <th>Raw</th>
    </tr>
  </thead>
  <tbody>
    {% for e in events %}
    <tr>
      <td>{{ fmt_dt(e.received_at) }}</td>
      <td>{{ fmt_dt(e.event_at) }}</td>
      <td>{{ e.event_type }}</td>
      <td style="max-width: 360px; word-break: break-all;">{{ e.device_uuid or "" }}</td>
      <td>{{ "%.6f"|format(e.lat) if e.lat is not none else "" }}</td>
      <td>{{ "%.6f"|format(e.lng) if e.lng is not none else "" }}</td>
      <td>{{ e.accuracy if e.accuracy is not none else "" }}</td>
      <td>
        <details>
          <summary>View</summary>
          <pre style="white-space: pre-wrap; max-width: 900px;">{{ e.raw_json }}</pre>
        </details>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{% if events|length == 0 %}
<p style="margin-top: 12px;">No mobile events found yet.</p>
{% endif %}
{% endblock %}
