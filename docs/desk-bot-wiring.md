# Desk bot wiring

Target board: Seeed XIAO ESP32S3 Sense (camera, PDM mic, 8 MB PSRAM). Build with
`pio run -d firmware -e xiao_esp32s3_deskbot`. Pins live in `firmware/device-deskbot.yaml`;
regenerate `firmware/src/deskbot_gen.h` with `python3 firmware/tools/gen_deskbot_td.py`.

## Pin map

| Function | Header pin | GPIO | Notes |
| --- | --- | --- | --- |
| Pan servo signal | D0 | 1 | LEDC, 50 Hz |
| Tilt servo signal | D1 | 2 | LEDC, 50 Hz |
| PIR output | D3 | 4 | 3.3 V logic, digital input |
| OLED SDA | D4 | 5 | I2C, address 0x3C |
| OLED SCL | D5 | 6 | I2C, 400 kHz |
| Spare | D2, D6, D7 | 3, 43, 44 | D2 is a strapping pin; D6/D7 are UART0 (serial log) |

## Pins to avoid

- Camera (on the Sense board): GPIO 10, 11, 12, 13, 14, 15, 16, 17, 18, 38, 39, 40, 47, 48.
- PDM microphone: GPIO 41 (data), 42 (clock).
- microSD slot and user LED: GPIO 7, 8, 9 (D8 to D10) and 21.
- Strapping and boot: GPIO 0 (BOOT button), 3, 45, 46.

The header pins used above (GPIO 1, 2, 4, 5, 6) do not overlap any of these, so the camera and mic
stay free for later work.

## Power

- Do not power the servos from the XIAO 5V or 3V3 pin. Two hobby servos draw hundreds of mA each
  and stall peaks of 1 A or more, which browns out the board (random resets, Wi-Fi drops).
- Use a separate 5 V supply rated for at least 2 A (a USB wall adapter with a breakout, or a 5 V
  BEC). Put a 470 to 1000 uF capacitor across the servo supply near the servos.
- Tie the grounds together: servo supply GND, XIAO GND, OLED GND and PIR GND all share one common
  ground. Without it the PWM signal has no reference and the servos twitch.
- Servo signal lines go straight to D0/D1 (3.3 V logic is enough for most hobby servos).
- OLED: 3V3 from the XIAO 3V3 pin. PIR (HC-SR501 style): VCC to the 5 V supply, OUT to D3. The
  OUT level is 3.3 V, safe for the GPIO. Allow about 60 seconds of PIR warm-up after power on.

## Behavior notes for bring-up

- On boot the firmware attaches PWM and commands the park pose (pan 0, tilt 0) before Wi-Fi starts.
  The first pulse is not slewed because the real horn position is unknown, so mount the horns
  with the head near center and power the servo supply first.
- Servo pulse range is 500 to 2500 us for -90 to +90 degrees. If your servos buzz at the ends,
  tighten `servo.pulse_min_us`/`pulse_max_us` or the axis `min`/`max` in the yaml.
- Soft limits are pan -90..90 and tilt -30..30. Out-of-range commands are rejected by the device.
- Slew limit is `max_slew_deg_per_sec` per axis (default 90) and is enforced on the device.
- If no valid command arrives for `deadman_timeout_ms` (default 30 s), the head slews to park.
- Command shape on `thingwire/{device_id}/command`: `{"action_id","target":"look_at","value":{"pan":10,"tilt":0}}`
  where `target` is the action name and `value` is the action input.
- Events (`person_seen`) are published to `thingwire/{device_id}/event`.

## Wake word and person detection (stretch findings)

- The pinned PlatformIO platform (espressif32 6.x, Arduino-ESP32 2.0.17) ships ESP-DL headers for
  the S3 but no ESP-SR (no WakeNet or MultiNet) and no `ESP_SR` Arduino library.
- Arduino-ESP32 3.x adds an `ESP_SR` library for the S3 (needs the pioarduino platform fork and a
  `model` flash partition). It was not built here; confirm before relying on it.
- Recommendation: keep the milestone on Arduino with PIR. For wake word and ESP-DL person detection
  with the camera, plan a move to ESP-IDF (esp-sr and esp-dl as components), or try pioarduino
  with Arduino-ESP32 3.x in a separate env first.
