import paho.mqtt.client as mqtt
from state_store import store
from config import MQTT_BROKER, MQTT_PORT, TOPIC_MOTION, TOPIC_DOOR, TOPIC_ALARM, TOPIC_MODE

TOPICS = [
    (TOPIC_MOTION, 0),
    (TOPIC_DOOR, 0),
    (TOPIC_ALARM, 0),
    (TOPIC_MODE, 0),
]


class MQTTService:
    def __init__(self):
        self.client = None
        self.started = False

    def _build_client(self):
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="pi-dashboard-subscriber",
                reconnect_on_failure=True,
            )
        except Exception:
            client = mqtt.Client(client_id="pi-dashboard-subscriber")

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        return client

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        store.set_mqtt_connected(reason_code == 0)
        if reason_code == 0:
            for topic, qos in TOPICS:
                client.subscribe(topic, qos=qos)
            print("[MQTT] Dashboard subscriber connected")
        else:
            print(f"[MQTT] Connect failed: {reason_code}")

    def on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=0, properties=None):
        store.set_mqtt_connected(False)
        print(f"[MQTT] Disconnected: {reason_code}")

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="ignore")
        store.update_from_topic(msg.topic, payload, simulated=False)

    def start(self):
        if self.started:
            return
        self.client = self._build_client()
        self.client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()
        self.started = True


mqtt_service = MQTTService()