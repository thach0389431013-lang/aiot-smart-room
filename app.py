from flask import Flask, render_template, jsonify, request, Response
import json
import paho.mqtt.publish as publish

from config import HTTP_HOST, HTTP_PORT, MQTT_BROKER, MQTT_PORT, TOPIC_MODE
from state_store import store
from mqtt_service import mqtt_service
from camera_service import camera_service

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        camera_service.frame_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/status")
def api_status():
    return jsonify(store.get_status())


@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": store.get_logs()})


@app.route("/api/fusion/mode", methods=["POST"])
def set_fusion_mode():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "mode_camera_motion")

    payload = json.dumps({"mode": mode})
    publish.single(TOPIC_MODE, payload=payload, hostname=MQTT_BROKER, port=MQTT_PORT)

    store.set_mode(mode)
    return jsonify({"ok": True, "mode": mode})


if __name__ == "__main__":
    mqtt_service.start()
    camera_service.start()
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, use_reloader=False)