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
#ifndef DEVICE_ID
#define DEVICE_ID "thingwire-demo-001"
#endif
#define TELEMETRY_INTERVAL_MS 5000
#ifndef MQTT_BUFFER_SIZE
#define MQTT_BUFFER_SIZE 1024
#endif

// --- WiFi (user must edit) ---
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

// --- Desk bot profile (env xiao_esp32s3_deskbot; pins and limits live in deskbot_gen.h) ---
#define SERVO_PWM_FREQ_HZ 50            // Standard hobby servo frame rate
#define SERVO_PWM_RESOLUTION_BITS 14    // LEDC duty resolution
#define SERVO_TICK_INTERVAL_MS 10       // Slew update period
#define SERVO_SETTLE_EPSILON_DEG 0.5f   // Within this of target counts as arrived
#define DESKBOT_NOD_AMPLITUDE_DEG 12.0f // Tilt swing for one nod stroke
#define DESKBOT_SWEEP_PASSES 2          // Left-right passes per sweep, then park
#define DESKBOT_TELEMETRY_MS 200        // Desk bot telemetry period
#define DESKBOT_PERSON_HOLD_MS 5000     // PIR stays "person" this long after last motion
#define DESKBOT_PERSON_EVENT_GAP_MS 30000 // Min gap between person_seen events
#define DESKBOT_PERSON_CONFIDENCE 0.6f  // PIR is a weak signal; report modest confidence
#define DESKBOT_MQTT_BUFFER_SIZE 4096   // Desk bot TD exceeds the default 1024 bytes

#define OLED_I2C_ADDR 0x3C              // SSD1306 default address
#define OLED_WIDTH 128
#define OLED_HEIGHT 64
#define OLED_I2C_CLOCK_HZ 400000
#define OLED_FRAME_INTERVAL_MS 50       // Redraw at most 20 fps
#define OLED_BLINK_MIN_MS 2500          // Random blink gap lower bound
#define OLED_BLINK_MAX_MS 6000          // Random blink gap upper bound
#define OLED_BLINK_CLOSE_MS 90          // Time to close the lids
#define OLED_BLINK_OPEN_MS 120          // Time to reopen the lids
#define OLED_EYE_LEFT_X 38              // Left eye center
#define OLED_EYE_RIGHT_X 90             // Right eye center
#define OLED_EYE_CENTER_Y 32

#endif // CONFIG_H
