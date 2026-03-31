#ifndef CONFIG_H
#define CONFIG_H

// --- Pin mappings ---
#define DHT_PIN 4           // DHT22 data pin
#define PIR_PIN 5           // PIR motion sensor
#define RELAY_PIN 12        // Relay control
#define LED_PIN 13          // Status LED

// --- MQTT ---
#define MQTT_BROKER "192.168.1.100"
#define MQTT_PORT 1883
#define DEVICE_ID "thingwire-demo-001"
#define TELEMETRY_INTERVAL_MS 5000

// --- WiFi (user must edit) ---
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

#endif // CONFIG_H
