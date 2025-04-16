from aiohttp import web
from aiohttp_middlewares import cors_middleware
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

import numpy as np
import base64
import asyncio
import cv2
import zmq
import zmq.asyncio

pcs = set()
latest_zmq_frame = None

# ZMQ subscriber
context = zmq.asyncio.Context()
sub = context.socket(zmq.SUB)
sub.connect("tcp://localhost:5555")
sub.setsockopt_string(zmq.SUBSCRIBE, "")


class LiveCameraTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()

    async def recv(self):
        global latest_zmq_frame
        await asyncio.sleep(1 / 30)  # simulate ~30fps

        if latest_zmq_frame is None:
            return VideoFrame.from_ndarray(np.zeros((480, 640, 3), dtype=np.uint8), format="bgr24")

        frame = latest_zmq_frame.copy()
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts, video_frame.time_base = await self.next_timestamp()
        print("[RTC] Sending live ZMQ frame")
        return video_frame


async def zmq_receiver():
    global latest_zmq_frame
    print("[ZMQ] Receiver started")
    while True:
        try:
            data = await sub.recv()
            jpg_data = base64.b64decode(data)
            nparr = np.frombuffer(jpg_data, np.uint8)
            latest_zmq_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        except Exception as e:
            print("[ZMQ] Error receiving frame:", e)
        await asyncio.sleep(0)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state:", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    pc.addTrack(LiveCameraTrack())

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })


# Setup aiohttp app with CORS
app = web.Application(middlewares=[cors_middleware(allow_all=True)])
app.router.add_post("/offer", offer)

# Start background task
async def on_startup(app):
    app['zmq_task'] = asyncio.create_task(zmq_receiver())

async def on_cleanup(app):
    app['zmq_task'].cancel()
    await app['zmq_task']

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
