#ifndef SENSOR_REGISTRY_H
#define SENSOR_REGISTRY_H

namespace SensorRegistry {
    /// Initialize DHT22 and PIR sensors
    void init();

    /// Call in loop() — reads sensors on schedule (non-blocking)
    void tick();

    /// Latest temperature in Celsius (NaN if not yet read)
    float getTemperature();

    /// Latest humidity in percent (NaN if not yet read)
    float getHumidity();

    /// Latest PIR motion state
    bool getMotion();

    /// True if DHT22 has been read at least once successfully
    bool hasValidReading();
}

#endif // SENSOR_REGISTRY_H
