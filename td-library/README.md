# ThingWire TD Library

Pre-built W3C WoT Thing Descriptions for common hardware.

## Usage

These are templates. Replace `{device_id}` and `{broker}` with your actual values before use.

Use them as reference when writing custom Thing Descriptions for your own hardware.

## Available Templates

| File | Hardware | Properties | Actions |
|------|----------|-----------|---------|
| dht22-relay.json | DHT22 + PIR + Relay | temperature, humidity, motion | setRelay |
| bme280.json | BME280 sensor | temperature, pressure, humidity | — |
| soil-moisture.json | Capacitive soil sensor | moisture | — |
| pir-motion.json | PIR motion sensor | motion | — |
| rgb-led-strip.json | WS2812B LED strip | — | setColor, setBrightness |
| servo-motor.json | SG90 servo | angle | setAngle |
