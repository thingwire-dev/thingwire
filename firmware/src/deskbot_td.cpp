#include "deskbot_td.h"
#include "config.h"
#include "wifi_manager.h"
#define DESKBOT_GEN_DEFINE_TD
#include "deskbot_gen.h"
#include <Arduino.h>
#include <string.h>

namespace DeskbotTd {

/// Count non-overlapping occurrences of needle in text
static size_t _count(const char* text, const char* needle) {
    size_t n = 0;
    size_t len = strlen(needle);
    for (const char* p = strstr(text, needle); p; p = strstr(p + len, needle)) {
        n++;
    }
    return n;
}

char* generate() {
    const char* deviceId = DEVICE_ID;
    const char* broker = WiFiManager::getMqttBroker();
    const char* kId = "{device_id}";
    const char* kBroker = "{broker}";
    size_t idN = _count(DESKBOT_TD_TEMPLATE, kId);
    size_t brN = _count(DESKBOT_TD_TEMPLATE, kBroker);
    size_t cap = strlen(DESKBOT_TD_TEMPLATE) + idN * strlen(deviceId) + brN * strlen(broker) + 1;
    char* out = (char*)malloc(cap);
    if (!out) {
        Serial.println("[WoT] TD allocation failed");
        return nullptr;
    }
    char* w = out;
    const char* r = DESKBOT_TD_TEMPLATE;
    while (*r) {
        if (strncmp(r, kId, strlen(kId)) == 0) {
            w += sprintf(w, "%s", deviceId);
            r += strlen(kId);
        } else if (strncmp(r, kBroker, strlen(kBroker)) == 0) {
            w += sprintf(w, "%s", broker);
            r += strlen(kBroker);
        } else {
            *w++ = *r++;
        }
    }
    *w = '\0';
    Serial.printf("[WoT] Generated desk bot TD (%d bytes)\n", (int)(w - out));
    return out;
}

} // namespace DeskbotTd
