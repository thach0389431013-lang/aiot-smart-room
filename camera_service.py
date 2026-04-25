import os
import sys
import time
import json
import threading
from typing import Dict, List

# =================================================
# PATH SETUP (QUAN TRONG NHAT DE SUA LOI utils.datasets)
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DETECTION_DIR = os.path.join(BASE_DIR, "detection_module")
TRACKING_DIR = os.path.join(BASE_DIR, "tracking_module")

for p in [BASE_DIR, DETECTION_DIR, TRACKING_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2
import torch
import numpy as np
import paho.mqtt.client as mqtt

# IMPORT KIỂU "YOLOv5 CŨ" DE KHOP VOI detection_module
from models.common import DetectMultiBackend
from utils.general import non_max_suppression, scale_coords, xyxy2xywh
from utils.plots import Annotator, colors
from tracking_module.deep_sort import DeepSort

from config import (
    MQTT_BROKER, MQTT_PORT, TOPIC_VISION,
    CAMERA_SOURCE, FRAME_W, FRAME_H,
    MODEL_WEIGHTS, DEEPSORT_CKPT,
    DET_CONF_THRES, DET_IOU_THRES, PERSON_CLASS_ID,
    MIN_STABLE_FRAMES, TRACK_TTL_SEC,
    PUBLISH_INTERVAL_SEC, FRAME_SLEEP_SEC,
)
from state_store import store


class CameraService:
    def __init__(self):
        self.running = False
        self.thread = None

        self.cap = None
        self.model = None
        self.deepsort = None
        self.mqtt_client = None
        self.mqtt_connected = False

        self.frame_lock = threading.Lock()
        self.latest_jpeg = None

        self.last_publish_time = 0.0

        # track_state[track_id] = {"frames": int, "last_seen": float, "best_conf": float}
        self.track_state: Dict[int, Dict[str, float]] = {}

        self.current_payload = {
            "person": 0,
            "conf": 0.0,
            "track_ids": [],
            "ts": time.time(),
            "source": "pi_camera_deepsort"
        }

    # ================= MQTT =================
    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.mqtt_connected = (reason_code == 0)
        print(f"[CAMERA][MQTT] Connected, reason_code={reason_code}")

    def on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=0, properties=None):
        self.mqtt_connected = False
        print(f"[CAMERA][MQTT] Disconnected, reason_code={reason_code}")

    def create_mqtt_client(self):
        client = None

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="pi-camera-publisher",
                reconnect_on_failure=True,
            )
        except Exception:
            pass

        if client is None:
            try:
                client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.API_VERSION2,
                    client_id="pi-camera-publisher",
                    reconnect_on_failure=True,
                )
            except Exception:
                pass

        if client is None:
            client = mqtt.Client(client_id="pi-camera-publisher")

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        return client

    # ================= IMAGE UTILS =================
    def letterbox(self, img, new_shape=(640, 640), color=(114, 114, 114)):
        shape = img.shape[:2]

        r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))

        dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
        dw /= 2
        dh /= 2

        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)

        top = int(round(dh - 0.1))
        bottom = int(round(dh + 0.1))
        left = int(round(dw - 0.1))
        right = int(round(dw + 0.1))

        img = cv2.copyMakeBorder(
            img, top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=color
        )
        return img

    def bbox_iou_xyxy(self, box1: np.ndarray, box2: np.ndarray) -> float:
        xA = max(box1[0], box2[0])
        yA = max(box1[1], box2[1])
        xB = min(box1[2], box2[2])
        yB = min(box1[3], box2[3])

        inter_w = max(0.0, xB - xA)
        inter_h = max(0.0, yB - yA)
        inter_area = inter_w * inter_h

        area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
        area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])

        denom = area1 + area2 - inter_area
        if denom <= 0:
            return 0.0
        return inter_area / denom

    def match_track_conf(self, track_bbox: np.ndarray, det_boxes_xyxy: List[np.ndarray], det_confs: List[float]) -> float:
        best_iou = 0.0
        best_conf = 0.0

        for det_box, det_conf in zip(det_boxes_xyxy, det_confs):
            iou = self.bbox_iou_xyxy(track_bbox, det_box)
            if iou > best_iou:
                best_iou = iou
                best_conf = det_conf

        return best_conf

    def cleanup_old_tracks(self, now_ts: float):
        remove_ids = []
        for tid, st in self.track_state.items():
            if (now_ts - st["last_seen"]) > TRACK_TTL_SEC:
                remove_ids.append(tid)

        for tid in remove_ids:
            del self.track_state[tid]

    def make_placeholder_frame(self, text="Camera unavailable"):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, text, (25, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        return frame

    # ================= CORE =================
    def process_frame(self, frame):
        img_org = frame.copy()
        annotator = Annotator(img_org, line_width=2)

        img = self.letterbox(frame, (640, 640))
        img = img[:, :, ::-1].transpose(2, 0, 1)
        img = np.ascontiguousarray(img)

        img = torch.from_numpy(img).float() / 255.0
        img = img.unsqueeze(0)

        pred = self.model(img)
        pred = non_max_suppression(
            pred,
            DET_CONF_THRES,
            DET_IOU_THRES,
            classes=PERSON_CLASS_ID
        )

        frame_has_stable_person = False
        frame_best_conf = 0.0
        stable_track_ids = []

        now_ts = time.time()

        for det in pred:
            if det is None or len(det) == 0:
                continue

            det[:, :4] = scale_coords(img.shape[2:], det[:, :4], img_org.shape).round()

            xywhs = xyxy2xywh(det[:, 0:4])
            confs = det[:, 4]
            clss = det[:, 5]

            det_boxes_xyxy = [box.cpu().numpy() for box in det[:, 0:4]]
            det_confs = [float(c) for c in confs.cpu().numpy()]

            outputs = self.deepsort.update(xywhs.cpu(), confs.cpu(), clss.cpu(), img_org)

            if len(outputs) > 0:
                for output in outputs:
                    x1, y1, x2, y2, track_id, cls = output[:6]
                    track_id = int(track_id)
                    cls = int(cls)

                    bbox = np.array([x1, y1, x2, y2], dtype=float)
                    matched_conf = self.match_track_conf(bbox, det_boxes_xyxy, det_confs)

                    if track_id not in self.track_state:
                        self.track_state[track_id] = {
                            "frames": 1,
                            "last_seen": now_ts,
                            "best_conf": matched_conf
                        }
                    else:
                        self.track_state[track_id]["frames"] += 1
                        self.track_state[track_id]["last_seen"] = now_ts
                        self.track_state[track_id]["best_conf"] = max(
                            self.track_state[track_id]["best_conf"],
                            matched_conf
                        )

                    is_stable = self.track_state[track_id]["frames"] >= MIN_STABLE_FRAMES

                    label = f"ID {track_id} person {matched_conf:.2f}"
                    if is_stable:
                        label += " STABLE"
                        frame_has_stable_person = True
                        stable_track_ids.append(track_id)
                        frame_best_conf = max(frame_best_conf, self.track_state[track_id]["best_conf"])

                    annotator.box_label(
                        [int(x1), int(y1), int(x2), int(y2)],
                        label,
                        color=colors(cls, True)
                    )

        self.cleanup_old_tracks(now_ts)

        payload = {
            "person": 1 if frame_has_stable_person else 0,
            "conf": round(frame_best_conf if frame_has_stable_person else 0.0, 2),
            "track_ids": stable_track_ids,
            "ts": now_ts,
            "source": "pi_camera_deepsort"
        }

        result = annotator.result()

        status_text = "MQTT: CONNECTED" if self.mqtt_connected else "MQTT: DISCONNECTED"
        cv2.putText(result, status_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if self.mqtt_connected else (0, 0, 255), 2)

        cv2.putText(result, f"Stable person: {payload['person']}  Conf: {payload['conf']:.2f}",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        return result, payload

    def publish_payload(self, payload):
        self.current_payload = payload

        # update state local cho web
        store.update_from_topic(TOPIC_VISION, payload, simulated=False)

        # publish MQTT cho fusion
        if self.mqtt_connected:
            info = self.mqtt_client.publish(TOPIC_VISION, json.dumps(payload), qos=0)
            print(f"[CAMERA][MQTT] Published: {payload}, mid={info.mid}")
        else:
            print("[CAMERA][MQTT] Broker not connected, skip publish")

    def run(self):
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                frame = self.make_placeholder_frame("Camera unavailable")
                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    with self.frame_lock:
                        self.latest_jpeg = buf.tobytes()
                time.sleep(0.2)
                continue

            ret, frame = self.cap.read()
            if not ret:
                frame = self.make_placeholder_frame("Failed to read frame")
                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    with self.frame_lock:
                        self.latest_jpeg = buf.tobytes()
                time.sleep(0.1)
                continue

            result, payload = self.process_frame(frame)

            now_ts = time.time()
            if (now_ts - self.last_publish_time) >= PUBLISH_INTERVAL_SEC:
                self.publish_payload(payload)
                self.last_publish_time = now_ts

            ok, buf = cv2.imencode(".jpg", result)
            if ok:
                with self.frame_lock:
                    self.latest_jpeg = buf.tobytes()

            time.sleep(FRAME_SLEEP_SEC)

    # ================= PUBLIC =================
    def start(self):
        if self.running:
            return

        print("[CAMERA] Loading DeepSort...")
        self.deepsort = DeepSort(model_path=DEEPSORT_CKPT, use_cuda=False)

        print("[CAMERA] Loading YOLO model...")
        self.model = DetectMultiBackend(weights=MODEL_WEIGHTS, device="cpu")

        print("[CAMERA] Starting MQTT client...")
        self.mqtt_client = self.create_mqtt_client()

        print("[CAMERA] Opening webcam...")
        self.cap = cv2.VideoCapture(CAMERA_SOURCE)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)

        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def frame_generator(self):
        while True:
            with self.frame_lock:
                frame = self.latest_jpeg

            if frame is None:
                placeholder = self.make_placeholder_frame("Starting camera service...")
                ok, buf = cv2.imencode(".jpg", placeholder)
                if ok:
                    frame = buf.tobytes()

            if frame is None:
                time.sleep(0.05)
                continue

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


camera_service = CameraService()
