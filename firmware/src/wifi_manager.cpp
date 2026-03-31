#include "wifi_manager.h"
#include "config.h"
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>

#define AP_SSID "ThingWire-Setup"
#define AP_IP IPAddress(192, 168, 4, 1)
#define CONNECT_TIMEOUT_MS 30000
#define RECONNECT_INTERVAL_MS 60000
#define LED_BLINK_INTERVAL_MS 500

namespace WiFiManager {

static Preferences _prefs;
static WebServer _server(80);
static bool _apMode = false;
static bool _connected = false;
static unsigned long _connectStartMs = 0;
static unsigned long _lastReconnectMs = 0;
static unsigned long _lastLedToggleMs = 0;
static bool _ledState = false;

static char _mqttBroker[64] = "";
static int _mqttPort = MQTT_PORT;

static const char HTML_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ThingWire Setup</title>
<style>body{font-family:sans-serif;max-width:400px;margin:40px auto;padding:0 20px}
input{width:100%;padding:8px;margin:4px 0 16px;box-sizing:border-box}
button{background:#2563eb;color:#fff;border:none;padding:12px;width:100%;cursor:pointer;font-size:16px}
</style></head><body>
<h2>ThingWire Setup</h2>
<form method="POST" action="/save">
<label>WiFi SSID</label><input name="ssid" required>
<label>WiFi Password</label><input name="pass" type="password">
<label>MQTT Broker</label><input name="mqtt" placeholder="192.168.1.100">
<label>MQTT Port</label><input name="port" type="number" value="1883">
<button type="submit">Save &amp; Reboot</button>
</form></body></html>
)rawliteral";

static void _handleRoot() {
    _server.send(200, "text/html", HTML_PAGE);
}

static void _handleSave() {
    String ssid = _server.arg("ssid");
    String pass = _server.arg("pass");
    String mqtt = _server.arg("mqtt");
    String port = _server.arg("port");

    _prefs.begin("thingwire", false);
    _prefs.putString("wifi_ssid", ssid);
    _prefs.putString("wifi_pass", pass);
    if (mqtt.length() > 0) {
        _prefs.putString("mqtt_host", mqtt);
    }
    if (port.length() > 0) {
        _prefs.putInt("mqtt_port", port.toInt());
    }
    _prefs.end();

    Serial.printf("[WiFi] Saved credentials for SSID: %s\n", ssid.c_str());
    _server.send(200, "text/html", "<h2>Saved! Rebooting...</h2>");
    delay(1000);
    ESP.restart();
}

static void _startAP() {
    Serial.println("[WiFi] Starting AP mode: ThingWire-Setup");
    WiFi.mode(WIFI_AP);
    WiFi.softAPConfig(AP_IP, AP_IP, IPAddress(255, 255, 255, 0));
    WiFi.softAP(AP_SSID);

    _server.on("/", _handleRoot);
    _server.on("/save", HTTP_POST, _handleSave);
    _server.begin();

    _apMode = true;
    Serial.printf("[WiFi] AP ready at %s\n", WiFi.softAPIP().toString().c_str());
}

static bool _loadCredentials(String &ssid, String &pass) {
    _prefs.begin("thingwire", true);
    ssid = _prefs.getString("wifi_ssid", "");
    pass = _prefs.getString("wifi_pass", "");

    String broker = _prefs.getString("mqtt_host", "");
    if (broker.length() > 0) {
        broker.toCharArray(_mqttBroker, sizeof(_mqttBroker));
    } else {
        strncpy(_mqttBroker, MQTT_BROKER, sizeof(_mqttBroker) - 1);
    }
    _mqttPort = _prefs.getInt("mqtt_port", MQTT_PORT);

    _prefs.end();
    return ssid.length() > 0;
}

void init() {
    String ssid, pass;
    if (_loadCredentials(ssid, pass)) {
        Serial.printf("[WiFi] Connecting to %s...\n", ssid.c_str());
        WiFi.mode(WIFI_STA);
        WiFi.begin(ssid.c_str(), pass.c_str());
        _connectStartMs = millis();
    } else {
        Serial.println("[WiFi] No saved credentials");
        _startAP();
    }
}

void tick() {
    if (_apMode) {
        _server.handleClient();
        return;
    }

    if (WiFi.status() == WL_CONNECTED) {
        if (!_connected) {
            _connected = true;
            digitalWrite(LED_PIN, HIGH);
            Serial.printf("[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
        }
        return;
    }

    // Not connected — blink LED
    _connected = false;
    unsigned long now = millis();
    if (now - _lastLedToggleMs >= LED_BLINK_INTERVAL_MS) {
        _lastLedToggleMs = now;
        _ledState = !_ledState;
        digitalWrite(LED_PIN, _ledState ? HIGH : LOW);
    }

    // Check connection timeout → fall back to AP
    if (_connectStartMs > 0 && (now - _connectStartMs >= CONNECT_TIMEOUT_MS)) {
        Serial.println("[WiFi] Connection timeout, starting AP mode");
        WiFi.disconnect();
        _connectStartMs = 0;
        _startAP();
        return;
    }

    // Periodic reconnect attempt
    if (_connectStartMs == 0 && !_apMode && (now - _lastReconnectMs >= RECONNECT_INTERVAL_MS)) {
        _lastReconnectMs = now;
        String ssid, pass;
        if (_loadCredentials(ssid, pass)) {
            Serial.printf("[WiFi] Reconnecting to %s...\n", ssid.c_str());
            WiFi.begin(ssid.c_str(), pass.c_str());
            _connectStartMs = millis();
        }
    }
}

bool isConnected() {
    return _connected && WiFi.status() == WL_CONNECTED;
}

const char* getMqttBroker() {
    return _mqttBroker;
}

int getMqttPort() {
    return _mqttPort;
}

} // namespace WiFiManager
