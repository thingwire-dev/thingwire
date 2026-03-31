#ifndef MQTT_CLIENT_H
#define MQTT_CLIENT_H

#include <functional>

namespace MqttClient {
    /// Callback type for incoming commands (topic, payload)
    using CommandCallback = std::function<void(const char*, const char*)>;

    /// Initialize MQTT client with broker from WiFiManager
    void init(CommandCallback onCommand);

    /// Call in loop() — handles reconnect + publish queue (non-blocking)
    void tick();

    /// True when connected to MQTT broker
    bool isConnected();

    /// Publish telemetry JSON to thingwire/{device_id}/telemetry
    void publishTelemetry(const char* json);

    /// Publish a retained message (used for TD and status)
    void publishRetained(const char* subtopic, const char* json);
}

#endif // MQTT_CLIENT_H
