# Demo: Motion Detection → Alert

AI monitors PIR motion sensor and alerts when motion is detected at unusual hours.

## What it shows

- Event-driven agent responses
- Continuous sensor monitoring
- AI reasoning about context (time of day)

## Run it

```bash
# 1. Start the MQTT broker
cd ../../gateway
docker-compose up mosquitto -d

# 2. Start the virtual device
cd ../../
python3.11 scripts/virtual_device.py --broker localhost

# 3. Run the motion alert script
python3.11 examples/demo-motion-alert/agent-script.py
```

## Try these prompts in Claude

1. "Monitor the motion sensor and alert me if there's movement."
2. "Is there any motion detected right now?"
3. "Check motion every 10 seconds and tell me if anything changes."
