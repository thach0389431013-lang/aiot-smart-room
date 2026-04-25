import json
import time
import paho.mqtt.client as mqtt
import requests

BROKER = "localhost"
PORT = 1883

TOPIC_VISION = "room/vision/person"
TOPIC_MOTION = "room/motion"
TOPIC_DOOR = "room/door"
TOPIC_ALARM = "room/alarm"
TOPIC_MODE = "room/fusion/mode"

CURRENT_MODE = "mode_camera_motion"

N8N_WEBHOOK_URL = "https://nekko.app.n8n.cloud/webhook/aiot-alarm"

latest_person_ts = 0.0
latest_motion_ts = 0.0
latest_door_ts = 0.0
latest_door_state = "UNKNOWN"

PERSON_WINDOW = 3.0
MOTION_WINDOW = 3.0
DOOR_WINDOW = 5.0

ALARM_COOLDOWN = 2.0
last_alarm_publish_ts = 0.0
last_alarm_state = 0

last_n8n_alert_ts = 0
N8N_ALERT_COOLDOWN = 30


def send_alarm_to_n8n(payload):
    global last_n8n_alert_ts

    now_ts = time.time()

    if payload.get("alarm") != 1:
        return

    if now_ts - last_n8n_alert_ts < N8N_ALERT_COOLDOWN:
        return

    try:
        requests.post(N8N_WEBHOOK_URL, json=payload, timeout=3)
        last_n8n_alert_ts = now_ts
        print("[N8N] Alarm sent")
    except Exception as e:
        print("[N8N] Send error:", e)

def now():
    return time.time()


def is_recent(ts, timeout):
    return (now() - ts) <= timeout


def person_active():
    return is_recent(latest_person_ts, PERSON_WINDOW)


def motion_active():
    return is_recent(latest_motion_ts, MOTION_WINDOW)


def door_open_active():
    return latest_door_state == "OPEN" and is_recent(latest_door_ts, DOOR_WINDOW)


def evaluate_alarm():
    p = person_active()
    m = motion_active()
    d = door_open_active()

    if CURRENT_MODE == "mode_camera_motion":
        state = p and m
    elif CURRENT_MODE == "mode_full_3way":
        state = p and m and d
    elif CURRENT_MODE == "mode_motion_door":
        state = m and d
    elif CURRENT_MODE == "mode_camera_only":
        state = p
    else:
        state = False

    return state, {"person": p, "motion": m, "door": d}


def publish_alarm(client, state, reason):
    payload = {
        "alarm": 1 if state else 0,
        "mode": CURRENT_MODE,
        "reason": reason,
        "ts": now()
    }

    client.publish(TOPIC_ALARM, json.dumps(payload), qos=0, retain=True)
    print("[ALARM_PUBLISH]", payload)

    send_alarm_to_n8n(payload)


def process_alarm(client):
    global last_alarm_publish_ts, last_alarm_state

    state, reason = evaluate_alarm()
    state_int = 1 if state else 0

    if state_int != last_alarm_state:
        publish_alarm(client, state, reason)
        last_alarm_state = state_int
        last_alarm_publish_ts = now()
        return

    if state and (now() - last_alarm_publish_ts >= ALARM_COOLDOWN):
        publish_alarm(client, state, reason)
        last_alarm_publish_ts = now()


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[MQTT] Connected, reason_code={reason_code}")
    client.subscribe(TOPIC_VISION)
    client.subscribe(TOPIC_MOTION)
    client.subscribe(TOPIC_DOOR)
    client.subscribe(TOPIC_MODE)


def on_message(client, userdata, msg):
    global latest_person_ts, latest_motion_ts
    global latest_door_ts, latest_door_state
    global CURRENT_MODE

    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    print(f"[RECV] {topic}: {payload}")

    try:
        if topic == TOPIC_VISION:
            data = json.loads(payload)
            if int(data.get("person", 0)) == 1:
                latest_person_ts = now()

        elif topic == TOPIC_MOTION:
            if payload == "1":
                latest_motion_ts = now()
            elif payload == "0":
                pass
            else:
                data = json.loads(payload)
                if int(data.get("motion", 0)) == 1:
                    latest_motion_ts = now()

        elif topic == TOPIC_DOOR:
            p = payload.lower()
            if p in ("open", "opened"):
                latest_door_state = "OPEN"
                latest_door_ts = now()
            elif p in ("close", "closed"):
                latest_door_state = "CLOSED"
                latest_door_ts = now()
            else:
                data = json.loads(payload)
                door_value = str(data.get("door", "")).upper()
                if door_value in ("OPEN", "CLOSED"):
                    latest_door_state = door_value
                    latest_door_ts = now()

        elif topic == TOPIC_MODE:
            data = json.loads(payload)
            CURRENT_MODE = data.get("mode", CURRENT_MODE)
            print("[MODE_CHANGED]", CURRENT_MODE)

    except Exception as e:
        print("[PARSE_ERROR]", e)

    process_alarm(client)


def main():
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="pi-fusion-service",
            reconnect_on_failure=True,
        )
    except Exception:
        client = mqtt.Client(client_id="pi-fusion-service")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_forever()


if __name__ == "__main__":
    main()
