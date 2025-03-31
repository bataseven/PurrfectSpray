import cv2
import numpy as np
from picamera2 import Picamera2
from gpiozero import DigitalInputDevice
from flask import Flask, Response
import threading
import time
import io
import os

app = Flask(__name__)

# GPIO setup for hall effect sensor
HALL_SENSOR_PIN = 5
hall_sensor = DigitalInputDevice(HALL_SENSOR_PIN, pull_up=False)

# Get the directory of the current script
script_dir = os.path.dirname(os.path.realpath(__file__))

# Paths to model files
model_weights = os.path.join(script_dir, "model", "mobilenet_iter_73000.caffemodel")
model_config = os.path.join(script_dir, "model", "deploy.prototxt")
class_labels = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

# Initialize camera and model
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (640, 480)}))
net = cv2.dnn.readNetFromCaffe(model_config, model_weights)

# Frame generation flag
frame_available = threading.Event()

def generate_frames():
		"""Generate frames with bounding boxes for streaming"""
		while True:
				frame_available.wait()  # Wait until a new frame is available
				with frame_lock:
						ret, buffer = cv2.imencode('.jpg', latest_frame)
						frame = buffer.tobytes()
				yield (b'--frame\r\n'
							 b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
				frame_available.clear()  # Reset the event

@app.route('/video_feed')
def video_feed():
		"""Video streaming route"""
		return Response(generate_frames(),
									 mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
		"""Video streaming home page"""
		return """
		<html>
			<head>
				<title>Cat Detection Stream</title>
				<style>
					#video-feed {
						max-width: 100%;
						height: auto;
					}
				</style>
			</head>
			<body>
				<h1>Cat Detection Stream</h1>
				<img id="video-feed" src="/video_feed">
				<p>Hall effect sensor status: <span id="sensor-status">-</span></p>
				<script>
					// Auto-reconnect if stream is interrupted
					const videoFeed = document.getElementById('video-feed');
					const sensorStatus = document.getElementById('sensor-status');
					
					function checkStream() {
						if (videoFeed.naturalWidth === 0) {
							console.log('Stream disconnected, reconnecting...');
							videoFeed.src = '/video_feed?' + new Date().getTime();
						}
					}
					
					function updateSensorStatus() {
						fetch('/sensor_status')
							.then(response => response.text())
							.then(data => {
								sensorStatus.textContent = data;
							})
							.catch(error => {
								console.error('Error fetching sensor status:', error);
							})
							.finally(() => {
								setTimeout(updateSensorStatus, 500);
							});
					}
					
					// Check stream every 2 seconds
					setInterval(checkStream, 2000);
					updateSensorStatus();
				</script>
			</body>
		</html>
		"""

@app.route('/sensor_status')
def sensor_status():
		"""Return hall sensor status"""
		return "Detected!" if not hall_sensor.value else "Not detected"

def capture_and_process():
		"""Main capture and processing loop"""
		global latest_frame
		with open(class_labels, "r") as f:
				classes = f.read().strip().split("\n")
		
		picam2.start()
		
		try:
				while True:
						frame = picam2.capture_array()
						frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
						
						# Object detection
						blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
						net.setInput(blob)
						detections = net.forward()

						# Process detections (only cats)
						for i in range(detections.shape[2]):
								confidence = detections[0, 0, i, 2]
								if confidence > 0.5:
										class_id = int(detections[0, 0, i, 1])
										if class_id == 8:  # Cat
												box = detections[0, 0, i, 3:7] * np.array([frame.shape[1], frame.shape[0], frame.shape[1], frame.shape[0]])
												(startX, startY, endX, endY) = box.astype("int")
												cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 255, 0), 2)
												cv2.putText(frame, classes[class_id], (startX, startY - 15), 
																		cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

						with frame_lock:
								latest_frame = frame.copy()
								frame_available.set()  # Signal that a new frame is available
								
		finally:
				picam2.stop()
				hall_sensor.close()

if __name__ == '__main__':
		# Global variables
		latest_frame = None
		frame_lock = threading.Lock()
		frame_available = threading.Event()
		
		# Start the capture thread
		capture_thread = threading.Thread(target=capture_and_process)
		capture_thread.daemon = True
		capture_thread.start()
		
		# Start the Flask server
		app.run(host='0.0.0.0', port=5000, threaded=True)