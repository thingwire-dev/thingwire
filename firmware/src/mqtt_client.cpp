#include "mqtt_client.h"
#include "config.h"
#include "wifi_manager.h"
#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>

#define RECONNECT_INTERVAL_MS 5000
#define TOPIC_BUF_SIZE 128

namespace MqttClient {

static WiFiClient _wifiClient;
static PubSubClient _mqtt(_wifiClient);
static CommandCallback _onCommand = nullptr;
static unsigned long _lastReconnectMs = 0;

static char _topicTelemetry[TOPIC_BUF_SIZE];
static char _topicCommand[TOPIC_BUF_SIZE];
static char _topicStatus[TOPIC_BUF_SIZE];

static void _buildTopics() {
    const char* prefix = WiFiManager::getMqttBroker();
    (void)prefix; // Topics use DEVICE_ID, not broker
    snprintf(_topicTelemetry, TOPIC_BUF_SIZE, "thingwire/%s/telemetry", DEVICE_ID);
    snprintf(_topicCommand, TOPIC_BUF_SIZE, "thingwire/%s/command", DEVICE_ID);
    snprintf(_topicStatus, TOPIC_BUF_SIZE, "thingwire/%s/status", DEVICE_ID);
}

static void _mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (_onCommand) {
        // Null-terminate the payload
        char buf[512];
        unsigned int copyLen = (length < sizeof(buf) - 1) ? length : sizeof(buf) - 1;
        memcpy(buf, payload, copyLen);
        buf[copyLen] = '\0';
        _onCommand(topic, buf);
    }
}

static bool _reconnect() {
    char clientId[32];
    snprintf(clientId, sizeof(clientId), "tw-%s", DEVICE_ID);

    // Set LWT before connecting
    _mqtt.setServer(WiFiManager::getMqttBroker(), WiFiManager::getMqttPort());

    if (_mqtt.connect(clientId, _topicStatus, 0, true, "offline")) {
        Serial.printf("[MQTT] Connected to %s:%d\n",
                      WiFiManager::getMqttBroker(), WiFiManager::getMqttPort());

        // Publish online status (retained)
        _mqtt.publish(_topicStatus, "online", true);

        // Subscribe to commands
        _mqtt.subscribe(_topicCommand);
        Serial.printf("[MQTT] Subscribed to %s\n", _topicCommand);

        return true;
    }

    Serial.printf("[MQTT] Connection failed, rc=%d\n", _mqtt.state());
    return false;
}

void init(CommandCallback onCommand) {
    _onCommand = onCommand;
    _buildTopics();
    _mqtt.setServer(WiFiManager::getMqttBroker(), WiFiManager::getMqttPort());
    _mqtt.setCallback(_mqttCallback);
    _mqtt.setBufferSize(1024);
    Serial.println("[MQTT] Client initialized");
}

void tick() {
    if (!WiFiManager::isConnected()) {
        return;
    }

    if (!_mqtt.connected()) {
        unsigned long now = millis();
        if (now - _lastReconnectMs >= RECONNECT_INTERVAL_MS) {
            _lastReconnectMs = now;
            _reconnect();
        }
        return;
    }

    _mqtt.loop();
}

bool isConnected() {
    return _mqtt.connected();
}

void publishTelemetry(const char* json) {
    if (_mqtt.connected()) {
        _mqtt.publish(_topicTelemetry, json);
    }
}

void publishRetained(const char* subtopic, const char* json) {
    if (_mqtt.connected()) {
        char topic[TOPIC_BUF_SIZE];
        snprintf(topic, TOPIC_BUF_SIZE, "thingwire/%s/%s", DEVICE_ID, subtopic);
        _mqtt.publish(topic, json, true);
    }
}

} // namespace MqttClient
