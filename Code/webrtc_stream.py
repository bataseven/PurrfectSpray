from aiohttp import web
from aiohttp_middlewares import cors_middleware
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from dotenv import load_dotenv
load_dotenv(override=True)
import numpy as np
import base64
import asyncio
import cv2
import zmq
import zmq.asyncio
import os
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pcs = set()

FRAME_PUB_PORT = int(os.getenv("FRAME_PUB_PORT", 5555))


# ZMQ setup (with conflation)
context = zmq.asyncio.Context()
sub = context.socket(zmq.SUB)
sub.connect(f"tcp://localhost:{FRAME_PUB_PORT}")
sub.setsockopt_string(zmq.SUBSCRIBE, "")
sub.setsockopt(zmq.CONFLATE, 1)

# Shared state
latest_zmq_frame = None
frame_lock = asyncio.Lock()

class LiveCameraTrack(VideoStreamTrack):
    kind = "video"

    async def recv(self):
        # maintain a roughly 30fps pacing  
        await asyncio.sleep(1/30)

        # grab the most recent frame (or black if none yet)
        async with frame_lock:
            if latest_zmq_frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                frame = latest_zmq_frame.copy()

        # package for WebRTC
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts, video_frame.time_base = await self.next_timestamp()
        return video_frame


async def zmq_receiver():
    global latest_zmq_frame
    try:
        while True:
            data = await sub.recv()  # only ever the very latest, thanks to CONFLATE
            jpg = base64.b64decode(data)
            img = cv2.imdecode(
                np.frombuffer(jpg, np.uint8),
                cv2.IMREAD_COLOR
            )
            # store under lock
            async with frame_lock:
                latest_zmq_frame = img
    except asyncio.CancelledError:
        logger.info("zmq_receiver task cancelled")
        raise
    except Exception as e:
        logger.info("[ZMQ] Receiver error:", e)
        raise



async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("Connection state:", pc.connectionState)
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
    try:
        await app['zmq_task']
    except asyncio.CancelledError:
        pass

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
