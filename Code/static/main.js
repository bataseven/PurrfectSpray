let isHomingComplete = false;
let selectedTarget = "person"; // default selected target
let controlMode = "manual"; // "manual", "follow", "tracking"
// let videoTip = null;
let hasControl = false;
let fadeTimeout = null;

document.addEventListener("DOMContentLoaded", function () {
    const socket = io();
    const targetPositions = { 1: 0, 2: 0 };
    mouseX = null, mouseY = null, isHovering = false, lastSent = { x: null, y: null }, normPos = { x: null, y: null };;

    let mySocketId = null;
    socket.on("connect", () => {
        mySocketId = socket.id;
    });


    let webrtcConnected = false;
    let pc = null;
    let lastVideoTime = 0;
    let staleCounter = 0;
    const modeIndicator = document.getElementById("video-mode-indicator");
    const followBtn = document.getElementById("follow-mode-btn");
    const trackBtn = document.getElementById("start-btn");
    const videoTip = document.getElementById("video-tip");



    // keep your existing UI refs…
    const video = document.getElementById("video-feed");
    const spinner = document.getElementById("loading-spinner");

    // 1) detect a stall, tear down old tracks, then reconnect
    setInterval(() => {
        if (!video || !video.srcObject) return;
        if (video.readyState >= 2 && video.currentTime === lastVideoTime) {
            staleCounter++;
            if (staleCounter > 3) {
                console.warn("[RTC] Stream stalled — reconnecting…");
                // STOP and DROP old tracks immediately
                video.srcObject.getTracks().forEach(t => t.stop());
                video.srcObject = null;

                webrtcConnected = false;
                showSpinner("⚠️ Connection lost — reconnecting…");
                startWebRTCWithRetry();
            }
        } else {
            staleCounter = 0;
            lastVideoTime = video.currentTime;
        }
    }, 1500);

    let retryInProgress = false;

    async function startWebRTCWithRetry(timeoutMs = 10000) {
        if (retryInProgress) return;
        retryInProgress = true;
        showSpinner("Video loading…");

        const deadline = Date.now() + timeoutMs;
        let attempt = 0;

        while (!webrtcConnected && Date.now() < deadline) {
            attempt++;
            console.log(`[RTC] Attempt ${attempt} to connect…`);
            try {
                await startWebRTC();
                break;
            } catch (err) {
                console.warn("[RTC] Offer/answer failed, will retry…", err);
                // showSpinner(`Reconnect attempt ${attempt}…`);
                await new Promise(r => setTimeout(r, 1000));
            }
        }

        if (!webrtcConnected) {
            console.error("[RTC] All reconnect attempts failed");
            showSpinner("⚠️ Video unavailable.<br>Try refreshing the page.");
            video.style.pointerEvents = "none";
        }

        retryInProgress = false;
    }


    async function startWebRTC() {
        return new Promise(async (resolve, reject) => {
            if (pc) {
                try { pc.close() } catch { }
                pc = null;
            }

            pc = new RTCPeerConnection();
            webrtcConnected = false;

            pc.addTransceiver("video", { direction: "recvonly" });
            pc.ontrack = e => {
                console.log("[RTC] Video track received");
                const [stream] = e.streams;
                video.srcObject = stream;

                video.onloadedmetadata = () => {
                    video.play().catch(() => { });
                    hideSpinner();
                    video.style.pointerEvents = "auto";
                    webrtcConnected = true;
                    console.log("[RTC] Video playback started");
                    resolve();
                };
            };

            try {
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const res = await fetch("/offer", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sdp: pc.localDescription.sdp,
                        type: pc.localDescription.type
                    })
                });
                if (!res.ok) throw new Error("Offer failed");
                const { sdp, type } = await res.json();
                await pc.setRemoteDescription(new RTCSessionDescription({ sdp, type }));
            } catch (err) {
                reject(err);
            }
        });
    }


    startWebRTCWithRetry();

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            console.log("[RTC] Tab visible — reconnecting…");
            webrtcConnected = false;
            if (pc) { pc.close(); pc = null }
            startWebRTCWithRetry();
        }
    });

    function showSpinner(message = "Loading…") {
        const spinner = document.getElementById("loading-spinner");
        if (!spinner) return;
        spinner.querySelector(".spinner-text").innerHTML = message;
        spinner.style.display = "flex";
    }

    function hideSpinner() {
        const spinner = document.getElementById("loading-spinner");
        if (spinner) spinner.style.display = "none";
    }

    setTimeout(() => {
        if (document.getElementById("video-feed").complete) {
            hideSpinner();
        }
    }, 3000);

    // Show tip after video is loaded
    document.getElementById("video-feed").onload = () => {
        hideSpinner(); // your existing logic
        videoTip.classList.add("show");
    };

    // 🔁 Target Button Selection
    document.querySelectorAll(".target-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".target-btn").forEach(b => b.classList.remove("selected"));
            btn.classList.add("selected");
            selectedTarget = btn.dataset.target;
        });
    });

    document.getElementById("model-select").addEventListener("change", function () {
        const selectedModel = this.value;
        socket.emit('change_model', { model: selectedModel });
    });


    function updateModeIndicator(label, icon = "fa-crosshairs", force = false) {
        // Determine new mode
        const nextMode =
            label.toLowerCase().includes("follow") ? "follow" :
                label.toLowerCase().includes("tracking") ? "tracking" :
                    "manual";

        // 🛡️ Prevent overwriting active follow mode with manual, unless we're truly canceling it
        if (!force && nextMode === "manual" && controlMode === "follow") return;

        // Update visual + state
        modeIndicator.innerHTML = `<i class="fas ${icon}"></i> Mode: ${label}`;
        // Only remove the pulse class if the mode is changing
        if (controlMode !== nextMode) {
            modeIndicator.classList.remove("pulse");
            void modeIndicator.offsetWidth;
            modeIndicator.classList.add("pulse");
        }
        controlMode = nextMode;
    }


    // 🔁 Follow Mode Toggle
    followBtn.addEventListener("click", () => {

        if (controlMode !== "follow") {
            // Start Follow Mode
            if (!isHomingComplete) {
                socket.emit("set_motor_mode", { mode: "manual" });
            }
            socket.emit("set_motor_mode", { mode: "follow" });
            // Optional: throttle cursor update delay for first second
            lastSent = { x: null, y: null }; // reset cursor memory
        } else {
            socket.emit("set_motor_mode", { mode: "manual" });
        }
    });


    trackBtn.addEventListener("click", () => {
        const isStarting = trackBtn.textContent.includes("Start");
        if (isStarting) {
            socket.emit("set_motor_mode", { mode: "tracking", target: selectedTarget });
        } else {
            socket.emit("set_motor_mode", { mode: "manual" });
        }
    });

    // 🔁 Manual Controls
    document.getElementById("laser-btn")?.addEventListener("click", () => socket.emit("toggle_laser"));
    document.getElementById("shoot-btn")?.addEventListener("click", () => {
        const btn = document.getElementById("shoot-btn");
        btn.disabled = true;
        btn.textContent = "FIRING...";
        btn.dataset.firedAt = Date.now();
        socket.emit("shoot");
    });

    // 🔁 Status Update from Server
    socket.on("status_update", data => {

        isHomingComplete = data.gimbal_state === "ready";


        document.getElementById("motor1-pos").textContent = data.motor1.toFixed(2) + "°";
        document.getElementById("motor2-pos").textContent = data.motor2.toFixed(2) + "°";
        document.getElementById("cpu-temp").textContent = data.cpu_temp + "°C";
        const el = document.getElementById("gimbal-cpu-temp");
        el && (el.textContent = (data.gimbal_cpu_temp != null)
            ? `${data.gimbal_cpu_temp.toFixed(1)}°C`
            : "-");


        const laserStatus = document.getElementById("laser-status");
        laserStatus.textContent = data.laser ? "On" : "Off";
        laserStatus.className = 'status-value ' + (data.laser ? "On" : "Off");

        const homingStatus = document.getElementById("homing-status");
        if (data.gimbal_state === "gimbal_not_found") {
            homingStatus.textContent = "Gimbal Disconnected";
            homingStatus.className = "status-value Error";
        } else if (data.gimbal_state === "homing_error") {
            homingStatus.textContent = "Error Homing";
            homingStatus.className = "status-value Complete";
        } else if (data.gimbal_state === "unknown") {
            homingStatus.textContent = "Unknown State";
            homingStatus.className = "status-value Error";
        } else if (data.gimbal_state === "homing") {
            homingStatus.textContent = "Homing...";
            homingStatus.className = "status-value InProgress";
        } else {
            homingStatus.textContent = "Running";
            homingStatus.className = "status-value Complete";
        }

        document.getElementById("sensor-status-1").textContent = data.sensor1 ? "Detected!" : "Not detected";
        document.getElementById("sensor-status-2").textContent = data.sensor2 ? "Detected!" : "Not detected";

        trackBtn.disabled = data.gimbal_state === "gimbal_not_found" || data.gimbal_state === "homing_error" || data.gimbal_state === "unknown";
    });

    // 🔁 Auto Mode Feedback
    socket.on("motor_status", data => {
        const statusEl = document.getElementById("motor-status");
        // Uppercase all letters of the status text
        statusEl.textContent = data.control_mode.charAt(0).toUpperCase() + data.control_mode.slice(1);
        statusEl.className = 'status-value ' + data.control_mode;
        selectedTarget = data.target || selectedTarget;
        const activeSid = data.sid;
        const isController = (activeSid === mySocketId);

        if (data.control_mode !== controlMode) {
            if (controlMode === "tracking") {
                showToast("Auto tracking disabled");
            }
            else if (controlMode === "follow") {
                showToast("Cursor follow disabled");
            }
            else if (controlMode === "manual") {
            }
            setMode(data.control_mode);
        }
        if (!isController && hasControl) {
            showToast("Another client took control.");
            videoTip.classList.add("show");
            videoTip.classList.remove("hidden");
            if (fadeTimeout) {
                clearTimeout(fadeTimeout);
                fadeTimeout = null;
            }
        }
        if (!isController) {
            document.getElementById("motor1-slider").disabled = true;
            document.getElementById("motor2-slider").disabled = true;
        }
        else {
            document.getElementById("motor1-slider").disabled = false;
            document.getElementById("motor2-slider").disabled = false;
        }

        hasControl = isController;
    });


    function setMode(mode) {
        controlMode = mode;

        switch (mode) {
            case "manual":
                updateModeIndicator("manual", "fa-circle", true);
                followBtn.innerHTML = '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
                trackBtn.innerHTML = '<i class="fas fa-play"></i> Start Auto Tracking';
                modeIndicator.classList.remove("on");
                modeIndicator.classList.add("off");
                modeIndicator.innerHTML = '<i class="fas fa-circle"></i> Mode: manual';
                break;

            case "follow":
                updateModeIndicator("Follow Cursor", "fa-mouse-pointer");
                followBtn.innerHTML = '<i class="fas fa-ban"></i> Stop Cursor Follow';
                break;

            case "tracking":
                updateModeIndicator(`Tracking ${selectedTarget}`, "fa-bullseye");
                trackBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Auto Tracking';
                modeIndicator.classList.remove("off");
                modeIndicator.classList.add("on");
                modeIndicator.innerHTML = '<i class="fas fa-bullseye"></i> Mode: Tracking ' + selectedTarget;
                break;
        }
    }

    // 🔁 Laser & Water Gun
    socket.on("laser_status", data => {
        document.getElementById("laser-status").textContent = data.status;
        document.getElementById("laser-status").className = 'status-value ' + data.status;
    });

    socket.on("shoot_ack", data => {
        const btn = document.getElementById("shoot-btn");
        const MIN_DISPLAY_TIME = 500;
        const elapsed = Date.now() - btn.dataset.firedAt;
        const remaining = Math.max(MIN_DISPLAY_TIME - elapsed, 0);
        setTimeout(() => {
            btn.textContent = "Spray!";
            btn.disabled = false;
        }, remaining);
    });

    // 🔁 Motor Control via Slider
    let motorThrottle = { 1: 0, 2: 0 };

    function emitMotorUpdate(motorNum, deg) {
        if (Date.now() - motorThrottle[motorNum] < 50) return;
        motorThrottle[motorNum] = Date.now();
        socket.emit("set_motor_position", { motor: motorNum, position: deg });
    }

    document.getElementById("motor1-slider").addEventListener("input", function () {
        const deg = parseInt(this.value);
        targetPositions[1] = deg;
        document.getElementById("motor1-target").textContent = deg + "°";
        emitMotorUpdate(1, deg);
    });

    document.getElementById("motor2-slider").addEventListener("input", function () {
        const deg = parseInt(this.value);
        targetPositions[2] = deg;
        document.getElementById("motor2-target").textContent = deg + "°";
        emitMotorUpdate(2, deg);
    });

    // 🔁 Mouse Tracking for Follow Mode
    document.getElementById("video-feed").addEventListener("mouseenter", () => isHovering = true);
    document.getElementById("video-feed").addEventListener("mouseleave", () => isHovering = false);
    document.getElementById("video-feed").addEventListener("mousemove", event => {
        const renderedWidth = rect.width;
        const renderedHeight = rect.height;
        const nativeWidth = 1920;
        const nativeHeight = 1080;
        const scaleX = nativeWidth / renderedWidth;
        const scaleY = nativeHeight / renderedHeight;
        const x = Math.round((event.clientX - rect.left) * scaleX);
        const y = Math.round((event.clientY - rect.top) * scaleY);
        


        normPos = { x: x, y: y };
    });

    let fadeTimeout = null;

    function fadeOutVideoTipAfterDelay(delayMs = 1000) {
        if (fadeTimeout) {
            clearTimeout(fadeTimeout);
            fadeTimeout = null;
        }
        fadeTimeout = setTimeout(() => {
            videoTip.classList.remove("show");
            videoTip.classList.add("hidden");
        }, delayMs);
    }

    let toastTimeout = null;

    function showToast(message) {
        const toast = document.getElementById("mode-toast");
        toast.textContent = message;
        toast.classList.add("show");
        if (toastTimeout) {
            clearTimeout(toastTimeout);
            toastTimeout = null;
        }
        toastTimeout = setTimeout(() => {
            toast.classList.remove("show");
            toastTimeout = null;
        }, 1500);
    }


    let lastClickTime = 0;
    const rect = document.getElementById("video-feed").getBoundingClientRect();

    document.getElementById("video-feed").addEventListener("click", function (event) {
        fadeOutVideoTipAfterDelay(1000);

        const now = Date.now();
        const doubleClickThreshold = 1500; // ms        
        const renderedWidth = rect.width;
        const renderedHeight = rect.height; 
        const nativeWidth = 1920;
        const nativeHeight = 1080;
        const scaleX = nativeWidth / renderedWidth;
        const scaleY = nativeHeight / renderedHeight;
        const x = Math.round((event.clientX - rect.left) * scaleX);
        const y = Math.round((event.clientY - rect.top) * scaleY);

        if (document.getElementById("homing-status").textContent === "Error" &&
            !document.getElementById("mode-toast").classList.contains("show")) {
            showToast("Homing failed. Please check the robot.");
            return;
        }

        if (controlMode === "tracking") {
            if (now - lastClickTime < doubleClickThreshold) {
                // Second click: switch to manual and emit click
                socket.emit("set_motor_mode", { mode: "manual" });
                socket.emit("click_target", { x, y });
            } else {
                // First click: just show toast
                showToast("Click again to switch to manual mode.");
                lastClickTime = now;
            }
            return; // prevent default action in tracking mode unless double-click
        }

        if (!isHomingComplete) {
            socket.emit("set_motor_mode", { mode: "manual" });
        }

        if (controlMode === "follow") {
            socket.emit("set_motor_mode", { mode: "manual" });
        }

        socket.emit("click_target", { x, y });
    });


    socket.on("target_updated", data => {
        const newTarget = data.target;
        selectedTarget = newTarget;

        // Update which button is selected
        document.querySelectorAll(".target-btn").forEach(btn => {
            if (btn.dataset.target === newTarget) {
                btn.classList.add("selected");
            } else {
                btn.classList.remove("selected");

            }
        });
    });

    const homeBtn = document.getElementById("home-btn");
    homeBtn.addEventListener("click", () => {
        socket.emit("request_home");
    });
    
    socket.on("home_ack", (data) => {
    // If the overall status isn’t "started", show it as an error/message
    if (data.status !== "started") {
        showToast(data.status);
        return;
    }

    // If there’s a remote error, show that instead of the generic message
    if (data.remote && data.remote.error) {
        showToast(`Remote homing failed: ${data.remote.error}`);
        return;
    }

    // Only now show the success toast
    showToast("Homing started…");
    });

    document.querySelectorAll(".target-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".target-btn").forEach(b => b.classList.remove("selected"));
            btn.classList.add("selected");

            selectedTarget = btn.dataset.target;
            // Emit to server so it can update app_state.tracking_target
            socket.emit("update_target", { target: selectedTarget });
        });
    });

    socket.on("controller_update", data => {
        const activeSid = data.sid;
        const isController = (activeSid === mySocketId);
    });

    socket.on("viewer_count", data => {
        const count = data.count;
        const viewerEl = document.getElementById("viewer-count");
        if (viewerEl) viewerEl.textContent = `👀 ${count} viewer${count !== 1 ? 's' : ''}`;
    });



    setInterval(() => {
        if (controlMode !== "follow" || !isHovering) return;
        
        

        // skip if same as last
        if (normPos.x === lastSent.x && normPos.y === lastSent.y) return;
        lastSent = { x: normPos.x, y: normPos.y };

        socket.emit("click_target", { x: normPos.x, y: normPos.y });
    }, 50);
});
