import React, {useState, useEffect} from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Platform,
  Alert,
  TextInput,
  ActivityIndicator,
} from 'react-native';

import DeviceInfo from 'react-native-device-info';
import BackgroundGeolocation from 'react-native-background-geolocation';

const API_BASE = 'https://clockin-app.onrender.com';
const DEVICE_TOKEN = 'KeatonClockInMobile_Venom97Triad1997151506172024!';

export default function HelpScreen({
  employee,
  store,
  isClockedIn,
  onClose,
  pin,
}) {
  const [gpsEnabled, setGpsEnabled] = useState(false);
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    BackgroundGeolocation.getState(state => {
      setGpsEnabled(!!state.enabled);
    });
  }, []);

  async function reportIssue() {
    const pinClean = (pin || '').trim();

    if (!pinClean) {
      Alert.alert('Missing PIN', 'Please log in again and try.');
      return;
    }

    if (sending) return;

    try {
      setSending(true);

      const deviceUuid = await DeviceInfo.getUniqueId();
      const appVersion = DeviceInfo.getVersion();
      const buildNumber = DeviceInfo.getBuildNumber();

      const payload = {
        employee: employee
          ? {
              id: employee.id,
              name: employee.name,
            }
          : null,

        store: store
          ? {
              code: store.code,
              name: store.name,
            }
          : null,

        isClockedIn: !!isClockedIn,

        gpsEnabled: !!gpsEnabled,

        device: {
          uuid: deviceUuid,
          brand: DeviceInfo.getBrand(),
          model: DeviceInfo.getModel(),
          systemName: DeviceInfo.getSystemName(),
          systemVersion: DeviceInfo.getSystemVersion(),
          appVersion,
          buildNumber,
        },

        platform: Platform.OS,

        timestamp_utc: new Date().toISOString(),
      };

      const res = await fetch(`${API_BASE}/api/mobile/report-issue`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Device-Token': DEVICE_TOKEN,
        },
        body: JSON.stringify({
          pin: pinClean,
          message: (message || '').trim(),
          payload,
        }),
      });

      let data = {};
      try {
        const text = await res.text();
        data = text ? JSON.parse(text) : {};
      } catch {}

      if (!res.ok) {
        Alert.alert(
          'Could not send',
          data?.error || `Server error (${res.status})`,
        );
        return;
      }

      Alert.alert(
        'Thank you!',
        'We will review the issue shortly.',
      );

      setMessage('');
    } catch (err) {
      Alert.alert(
        'Could not send',
        'Check internet connection and try again.',
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <ScrollView style={styles.container} keyboardShouldPersistTaps="handled">

      {/* Header */}
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={onClose} style={styles.backBtn}>
          <Text style={styles.backBtnText}>← Back</Text>
        </TouchableOpacity>

        <Text style={styles.title}>Help & Troubleshooting</Text>

        <View style={{width: 70}} />
      </View>

      {/* Status */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Current Status</Text>

        <Text>Employee: {employee?.name || 'Unknown'}</Text>
        <Text>Store: {store?.name || 'None'}</Text>
        <Text>Clocked In: {isClockedIn ? 'YES' : 'NO'}</Text>
        <Text>GPS Tracking: {gpsEnabled ? 'ACTIVE' : 'OFF'}</Text>
      </View>

      {/* Fix tips */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Common Fixes</Text>

        <Text style={styles.tip}>• Make sure Location is ON</Text>
        <Text style={styles.tip}>• Set permission to "Allow all the time"</Text>
        <Text style={styles.tip}>• Make sure correct store is selected</Text>
        <Text style={styles.tip}>• Restart the app</Text>
        <Text style={styles.tip}>• Restart phone if GPS is stuck</Text>
      </View>

      {/* Message */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>
          Describe the issue (optional)
        </Text>

        <TextInput
          value={message}
          onChangeText={setMessage}
          placeholder="Example: Clock-in button does nothing"
          style={styles.textArea}
          multiline
          numberOfLines={4}
          textAlignVertical="top"
        />
      </View>

      {/* Report button */}
      <TouchableOpacity
        style={[styles.reportButton, sending && {opacity: 0.6}]}
        onPress={reportIssue}
        disabled={sending}
      >
        {sending ? (
          <View style={{flexDirection: 'row', alignItems: 'center'}}>
            <ActivityIndicator />
            <Text style={styles.reportButtonText}> Sending…</Text>
          </View>
        ) : (
          <Text style={styles.reportButtonText}>Report Issue</Text>
        )}
      </TouchableOpacity>

    </ScrollView>
  );
}

const styles = StyleSheet.create({

  container: {
    flex: 1,
    padding: 20,
  },

  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 14,
  },

  backBtn: {
    paddingVertical: 6,
    paddingHorizontal: 8,
  },

  backBtnText: {
    fontWeight: 'bold',
    opacity: 0.7,
  },

  title: {
    fontSize: 24,
    fontWeight: 'bold',
  },

  section: {
    marginBottom: 18,
  },

  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 10,
  },

  tip: {
    marginBottom: 5,
  },

  textArea: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 10,
    padding: 12,
    fontSize: 15,
    minHeight: 110,
  },

  reportButton: {
    backgroundColor: '#d9534f',
    padding: 15,
    borderRadius: 8,
    alignItems: 'center',
  },

  reportButtonText: {
    color: 'white',
    fontWeight: 'bold',
    fontSize: 16,
  },

});