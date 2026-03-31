#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

namespace WiFiManager {
    /// Initialize WiFi — tries saved creds, falls back to AP mode
    void init();

    /// Call in loop() — handles AP mode web server + reconnect logic (non-blocking)
    void tick();

    /// True when connected to WiFi station
    bool isConnected();

    /// Get the configured MQTT broker (from NVS or config.h default)
    const char* getMqttBroker();

    /// Get the configured MQTT port
    int getMqttPort();
}

#endif // WIFI_MANAGER_H
