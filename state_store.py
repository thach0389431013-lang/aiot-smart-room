import json
import time
from collections import deque
from copy import deepcopy


class StateStore:
    def __init__(self, max_logs=50):
        self.max_logs = max_logs
        self.logs = deque(maxlen=max_logs)
        self.current_mode = "mode_camera_motion"
        self.reset_runtime_state()

    def reset_runtime_state(self):
        now = time.time()
        self.state = {
            "mqtt_connected": False,
            "last_update": now,
            "vision": {
                "person_detected": False,
                "conf": 0.0,
                "track_ids": [],
                "source": None,
                "ts": None,
                "online": False
            },
            "motion": {
                "detected": False,
                "ts": None,
                "online": False
            },
            "door": {
                "status": "UNKNOWN",
                "ts": None,
                "online": False
            },
            "alarm": {
                "active": False,
                "reason": {
                    "person": False,
                    "motion": False,
                    "door": False
                },
                "ts": None,
                "mode": self.current_mode
            },
            "summary": {
                "camera": "idle",
                "motion": "inactive",
                "door": "unknown",
                "alarm": "idle"
            },
            "fusion_mode": self.current_mode
        }
        self.logs.clear()

    def set_mqtt_connected(self, connected: bool):
        self.state["mqtt_connected"] = connected
        self.state["last_update"] = time.time()
        self._refresh_summary()

    def set_mode(self, mode: str):
        self.current_mode = mode
        self.state["fusion_mode"] = mode
        self.state["alarm"]["mode"] = mode
        self.state["last_update"] = time.time()
        self._refresh_summary()

    def _normalize_payload(self, payload):
        if isinstance(payload, (dict, list)):
            return payload

        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")

        if isinstance(payload, str):
            raw = payload.strip()
            if not raw:
                return {}

            try:
                return json.loads(raw)
            except Exception:
                return raw

        return payload

    def _append_log(self, topic, payload, simulated=False):
        self.logs.appendleft({
            "time": time.time(),
            "topic": topic,
            "payload": payload,
            "simulated": simulated
        })

    def _refresh_summary(self):
        self.state["summary"]["camera"] = "online" if self.state["vision"]["online"] else "idle"
        self.state["summary"]["motion"] = "active" if self.state["motion"]["detected"] else "inactive"
        self.state["summary"]["door"] = self.state["door"]["status"].lower()
        self.state["summary"]["alarm"] = "triggered" if self.state["alarm"]["active"] else "idle"
        self.state["fusion_mode"] = self.current_mode

    def apply_timeouts(self):
        now = time.time()

        # Camera timeout
        vision_ts = self.state["vision"]["ts"]
        if vision_ts is not None and (now - vision_ts) > 3.0:
            self.state["vision"]["person_detected"] = False
            self.state["vision"]["conf"] = 0.0
            self.state["vision"]["track_ids"] = []

        # Motion timeout
        motion_ts = self.state["motion"]["ts"]
        if motion_ts is not None and (now - motion_ts) > 3.0:
            self.state["motion"]["detected"] = False

        # Door không tự đổi OPEN/CLOSED, chỉ đánh dấu offline nếu quá lâu không có update
        door_ts = self.state["door"]["ts"]
        if door_ts is not None and (now - door_ts) > 15.0:
            self.state["door"]["online"] = False

        # Alarm timeout
        alarm_ts = self.state["alarm"]["ts"]
        if alarm_ts is not None and (now - alarm_ts) > 3.0:
            self.state["alarm"]["active"] = False
            self.state["alarm"]["reason"] = {
                "person": False,
                "motion": False,
                "door": False
            }

        self._refresh_summary()

    def update_from_topic(self, topic, payload, simulated=False):
        payload = self._normalize_payload(payload)
        self._append_log(topic, payload, simulated=simulated)

        now = time.time()
        self.state["last_update"] = now

        # ================= VISION =================
        if topic == "room/vision/person":
            if isinstance(payload, dict):
                self.state["vision"]["person_detected"] = bool(payload.get("person", 0) == 1)
                self.state["vision"]["conf"] = float(payload.get("conf", 0.0))
                self.state["vision"]["track_ids"] = payload.get("track_ids", [])
                self.state["vision"]["source"] = payload.get("source", "pi_camera_deepsort")
                self.state["vision"]["ts"] = payload.get("ts", now)
            else:
                self.state["vision"]["person_detected"] = str(payload).strip() == "1"
                self.state["vision"]["conf"] = 0.0
                self.state["vision"]["track_ids"] = []
                self.state["vision"]["source"] = "pi_camera_deepsort"
                self.state["vision"]["ts"] = now

            self.state["vision"]["online"] = True

        # ================= MOTION =================
        elif topic == "room/motion":
            detected = False
            ts = now

            if isinstance(payload, dict):
                detected = bool(payload.get("motion", 0) == 1)
                ts = payload.get("ts", now)
            else:
                detected = str(payload).strip().lower() in ("1", "true", "on", "yes")

            self.state["motion"]["detected"] = detected
            self.state["motion"]["ts"] = ts
            self.state["motion"]["online"] = True

        # ================= DOOR =================
        elif topic == "room/door":
            status = "UNKNOWN"
            ts = now

            if isinstance(payload, dict):
                status = str(payload.get("door", "UNKNOWN")).upper()
                ts = payload.get("ts", now)
            else:
                raw = str(payload).strip().lower()
                if raw in ("open", "opened"):
                    status = "OPEN"
                elif raw in ("close", "closed"):
                    status = "CLOSED"

            self.state["door"]["status"] = status
            self.state["door"]["ts"] = ts
            self.state["door"]["online"] = True

        # ================= ALARM =================
        elif topic == "room/alarm":
            if isinstance(payload, dict):
                self.state["alarm"]["active"] = bool(payload.get("alarm", 0) == 1)
                self.state["alarm"]["reason"] = payload.get("reason", {
                    "person": False,
                    "motion": False,
                    "door": False
                })
                self.state["alarm"]["ts"] = payload.get("ts", now)
                self.state["alarm"]["mode"] = payload.get("mode", self.current_mode)
                self.current_mode = self.state["alarm"]["mode"]
            else:
                self.state["alarm"]["active"] = str(payload).strip() == "1"
                self.state["alarm"]["reason"] = {
                    "person": False,
                    "motion": False,
                    "door": False
                }
                self.state["alarm"]["ts"] = now

        # ================= FUSION MODE =================
        elif topic == "room/fusion/mode":
            if isinstance(payload, dict):
                mode = payload.get("mode", self.current_mode)
            else:
                mode = str(payload).strip() or self.current_mode

            self.set_mode(mode)

        self._refresh_summary()

    def get_status(self):
        self.apply_timeouts()
        return deepcopy(self.state)

    def get_logs(self):
        return list(self.logs)


store = StateStore()