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

pcs = set()
latest_zmq_frame = None
frame_queue = asyncio.Queue(maxsize=1)
frame_lock = asyncio.Lock()

FRAME_PUB_PORT = int(os.getenv("FRAME_PUB_PORT", 5555))

# ZMQ subscriber
context = zmq.asyncio.Context()
sub = context.socket(zmq.SUB)
sub.connect(f"tcp://localhost:{FRAME_PUB_PORT}")
sub.setsockopt_string(zmq.SUBSCRIBE, "")


class LiveCameraTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()

    async def recv(self):
        await asyncio.sleep(1/30)
        try:
            frame = frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            arr = np.zeros((480,640,3), dtype=np.uint8)
            frame = arr
        else:
            # we got a real frame; optionally re‐queue it if you want to reuse
            pass

        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts, video_frame.time_base = await self.next_timestamp()
        return video_frame

async def zmq_receiver():
    try:
        while True:
            data = await sub.recv()
            img = cv2.imdecode(
                np.frombuffer(base64.b64decode(data), np.uint8),
                cv2.IMREAD_COLOR
            )
            # drop stale frame if queue already full
            if frame_queue.full():
                _ = frame_queue.get_nowait()
            await frame_queue.put(img)
    except asyncio.CancelledError:
        print("zmq_receiver task cancelled")



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
    try:
        await app['zmq_task']
    except asyncio.CancelledError:
        pass

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
