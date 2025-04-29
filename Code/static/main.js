let isHoming = true;
let autoModeActive = false;
let selectedTarget = "person"; // default selected target
let currentMode = "idle"; // "idle", "follow", "tracking"
let tipHidden = false;
let videoTip = null;
let hasControl = false;
let isTrackingActive = false;
let fadeTimeout = null;

document.addEventListener("DOMContentLoaded", function () {
    const socket = io();
    const targetPositions = { 1: 0, 2: 0 };
    let followMode = false, mouseX = null, mouseY = null, isHovering = false, lastSent = { x: null, y: null };

    let mySocketId = null;
    socket.on("connect", () => {
        mySocketId = socket.id;
    });

    videoTip = document.getElementById("video-tip");

    let webrtcConnected = false;
    let streamMonitorInterval = null;

    let lastVideoTime = 0;
    let staleCounter = 0;
    const modeIndicator = document.getElementById("video-mode-indicator");

    setInterval(() => {
        const video = document.getElementById("video-feed");
        if (!video || !video.srcObject) return;

        const currentTime = video.currentTime;
        const hasVideo = video.readyState >= 2;

        if (hasVideo && currentTime === lastVideoTime) {
            staleCounter++;
            if (staleCounter > 3) {  // ~5 seconds of no chiange
                console.warn("[RTC] Stream appears to have stalled");
                showSpinner("Reconnecting...");
                webrtcConnected = false;
                startWebRTCWithRetry();
            }
        } else {
            staleCounter = 0;
            lastVideoTime = currentTime;
        }
    }, 1500);

    let retryInProgress = false;
    async function startWebRTCWithRetry(timeoutMs = 10000) {
        if (retryInProgress) return;
        retryInProgress = true;

        const startTime = Date.now();
        const video = document.getElementById("video-feed");
        let attempts = 0;
        webrtcConnected = false;

        while (!webrtcConnected && Date.now() - startTime < timeoutMs) {
            attempts++;
            console.log(`[RTC] Attempt ${attempts} to connect...`);
            try {
                await startWebRTC();
                return;
            } catch (err) {
                console.warn("[RTC] Connection failed, retrying...", err);
                await new Promise(res => setTimeout(res, 1000));
            }
        }

        // Timeout fallback
        showSpinner("⚠️ Video stream unavailable.<br>Please try refreshing.");
        video.style.pointerEvents = "none";
    }


    async function startWebRTC() {
        return new Promise(async (resolve, reject) => {
            const pc = new RTCPeerConnection();
            const video = document.getElementById("video-feed");

            pc.addTransceiver("video", { direction: "recvonly" });

            pc.ontrack = (event) => {
                console.log("[RTC] Video track received");
                const stream = event.streams[0];
                video.srcObject = stream;

                video.onloadedmetadata = () => {
                    video.play();
                    video.style.display = "block";
                    document.getElementById("loading-spinner").style.display = "none";
                    video.style.pointerEvents = "auto";
                    webrtcConnected = true;
                    console.log("[RTC] Video playback started");
                    if (modeIndicator) modeIndicator.style.display = "block";
                    resolve();
                    if (videoTip && !tipHidden) {
                        videoTip.classList.add("show");
                        videoTip.classList.remove("hidden");
                    }
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
                const answer = await res.json();
                await pc.setRemoteDescription(new RTCSessionDescription(answer));
            } catch (err) {
                reject(err);
            }
        });
    }

    startWebRTCWithRetry();


    function showSpinner(message = "Loading...") {
        const spinner = document.getElementById("loading-spinner");
        if (spinner) {
            spinner.style.display = "block";

            const text = spinner.querySelector(".spinner-text");
            if (text) text.textContent = message;
        }

        const video = document.getElementById("video-feed");
        if (video) {
            video.style.pointerEvents = "none";
            video.style.display = "none";
        }
    }

    function hideSpinner() {
        const spinner = document.getElementById("loading-spinner");
        const video = document.getElementById("video-feed");

        if (spinner) spinner.style.display = 'none';
        if (video) video.style.display = 'inline';

        if (modeIndicator) modeIndicator.style.display = 'block';

        // show tip on initial load
        if (!tipHidden && videoTip) {
            videoTip.classList.add("show");
            videoTip.classList.remove("hidden");
        }
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

    document.getElementById("model-select").addEventListener("change", function() {
        const selectedModel = this.value;
        socket.emit('change_model', {model: selectedModel});
    });
    

    function updateModeIndicator(label, icon = "fa-crosshairs", force = false) {
        const indicator = document.getElementById("video-mode-indicator");

        // Determine new mode
        const nextMode =
            label.toLowerCase().includes("follow") ? "follow" :
                label.toLowerCase().includes("tracking") ? "tracking" :
                    "idle";

        // 🛡️ Prevent overwriting active follow mode with Idle, unless we're truly canceling it
        if (!force && nextMode === "idle" && currentMode === "follow") return;

        // Update visual + state
        indicator.innerHTML = `<i class="fas ${icon}"></i> Mode: ${label}`;
        // Only remove the pulse class if the mode is changing
        if (currentMode !== nextMode) {
            indicator.classList.remove("pulse");
            void indicator.offsetWidth;
            indicator.classList.add("pulse");
        }
        currentMode = nextMode;
    }


    // 🔁 Follow Mode Toggle
    document.getElementById("follow-mode-btn").addEventListener("click", () => {
        const btn = document.getElementById("follow-mode-btn");

        if (!followMode) {
            // Start Follow Mode
            if (!isHoming) {
                socket.emit("motor_control", { action: "stop" });
            }

            followMode = true;
            btn.innerHTML = '<i class="fas fa-ban"></i> Stop Cursor Follow';
            updateModeIndicator("Follow Cursor", "fa-mouse-pointer");

            // Optional: throttle cursor update delay for first second
            lastSent = { x: null, y: null }; // reset cursor memory
        } else {
            // Stop Follow Mode
            followMode = false;
            btn.innerHTML = '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
            updateModeIndicator("Idle", "fa-circle", true);  // 🔁 ADD THIS
        }
    });

    // 🔁 Auto Mode Button
    const autoBtn = document.getElementById("start-btn");

    autoBtn.addEventListener("click", () => {
        const isStarting = autoBtn.textContent.includes("Start");
        if (isStarting) {
            socket.emit("motor_control", { action: "start", target: selectedTarget });
        } else {
            socket.emit("motor_control", { action: "stop" });
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
        isHoming = !data.homing;

        document.getElementById("motor1-pos").textContent = data.motor1.toFixed(2) + "°";
        document.getElementById("motor2-pos").textContent = data.motor2.toFixed(2) + "°";
        document.getElementById("cpu-temp").textContent = data.cpu_temp + "°C";

        const laserStatus = document.getElementById("laser-status");
        laserStatus.textContent = data.laser ? "On" : "Off";
        laserStatus.className = 'status-value ' + (data.laser ? "On" : "Off");
        
        const homingStatus = document.getElementById("homing-status");
        if (data.homing_error) {
            homingStatus.textContent = "Error";
            homingStatus.className = "status-value Error";
        } else if (data.homing) {
            homingStatus.textContent = "Complete";
            homingStatus.className = "status-value Complete";
        } else {
            homingStatus.textContent = "In Progress";
            homingStatus.className = "status-value InProgress";
        }

        document.getElementById("sensor-status-1").textContent = data.sensor1 ? "Detected!" : "Not detected";
        document.getElementById("sensor-status-2").textContent = data.sensor2 ? "Detected!" : "Not detected";

        document.getElementById("start-btn").disabled = !data.homing;
    });

    // 🔁 Auto Mode Feedback
    socket.on("motor_status", data => {
        const statusEl = document.getElementById("motor-status");
        const autoBtn = document.getElementById("start-btn");
        const indicator = document.getElementById("auto-mode-indicator");

        const status = data.status || "Unknown";
        statusEl.textContent = status;
        statusEl.className = 'status-value ' + (data.auto_mode ? "Running" : "Stopped");

        if (data.auto_mode && !autoModeActive) {
            showToast(`Tracking: ${data.target || "Target"}`);
            autoModeActive = true;
        } else if (!data.auto_mode && autoModeActive) {
            showToast("Auto Tracking Stopped");
            autoModeActive = false;
        }
        isTrackingActive = data.auto_mode;
        if (data.auto_mode) {
            isTrackingActive = true;
            updateModeIndicator(`Tracking ${data.target || "Target"}`, "fa-bullseye");
            autoBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Auto Tracking';
            indicator.classList.remove("off");
            indicator.classList.add("on");
            indicator.innerHTML = '<i class="fas fa-bullseye"></i> Auto Tracking: On';
        } else {
            isTrackingActive = false;
            if(!followMode) {
                updateModeIndicator("Idle", "fa-circle");
            }
            autoBtn.innerHTML = '<i class="fas fa-play"></i> Start Auto Tracking';
            indicator.classList.remove("on");
            indicator.classList.add("off");
            indicator.innerHTML = '<i class="fas fa-bullseye"></i> Auto Tracking: Off';
        }
    });

    function showToast(message) {
        const toast = document.getElementById("mode-toast");
        toast.textContent = message;
        toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 1500);
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
        const rect = event.target.getBoundingClientRect();
        mouseX = Math.round(event.clientX - rect.left);
        mouseY = Math.round(event.clientY - rect.top);
    });

    let tipHidden = false;
    let fadeTimeout = null;

    function fadeOutVideoTipAfterDelay(delayMs = 1000) {
        if (!videoTip || tipHidden) return;

        tipHidden = true;

        // Cancel previous timeout if any
        if (fadeTimeout) {
            clearTimeout(fadeTimeout);
            fadeTimeout = null;
        }

        // Ensure it's visible before hiding
        videoTip.classList.add("show");
        videoTip.classList.remove("hidden");

        fadeTimeout = setTimeout(() => {
            // Only this client’s timeout can affect the DOM
            videoTip.classList.remove("show");
            videoTip.classList.add("hidden");
            fadeTimeout = null;
        }, delayMs);
    }


    document.getElementById("video-feed").addEventListener("click", function (event) {

        fadeOutVideoTipAfterDelay(1000);
        if (!isHoming) {
            socket.emit("motor_control", { action: "stop" });
        }

        const rect = this.getBoundingClientRect();

        const renderedWidth = rect.width;
        const renderedHeight = rect.height;

        const nativeWidth = 1920;
        const nativeHeight = 1080;

        const scaleX = nativeWidth / renderedWidth;
        const scaleY = nativeHeight / renderedHeight;

        const x = Math.round((event.clientX - rect.left) * scaleX);
        const y = Math.round((event.clientY - rect.top) * scaleY);

        if (followMode) {
            followMode = false;
            document.getElementById("follow-mode-btn").innerHTML =
                '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
            updateModeIndicator("Idle", "fa-circle", true);

            const toast = document.getElementById("mode-toast");
            toast.classList.add("show");
            showToast("Cursor follow disabled. You can now click to aim.");
            setTimeout(() => toast.classList.remove("show"), 1500);
        }

        // Show a toast message if the homing is failed and there already is not a toast message
        if (document.getElementById("homing-status").textContent === "Error" && !document.getElementById("mode-toast").classList.contains("show")) {
            const toast = document.getElementById("mode-toast");
            toast.classList.add("show");
            showToast("Homing failed");
            setTimeout(() => toast.classList.remove("show"), 1500);
            return;
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

        if (isTrackingActive) {
            updateModeIndicator(`Tracking ${newTarget}`, "fa-bullseye");
        }
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

        // And if the toast is not already shown
        if (!isController && hasControl && !document.getElementById("mode-toast").classList.contains("show")) {
            hasControl = false;
            showToast("Another client took control.");

            if (videoTip) {
                tipHidden = false;
                videoTip.classList.add("show");
                videoTip.classList.remove("hidden");

                if (fadeTimeout) {
                    clearTimeout(fadeTimeout);
                    fadeTimeout = null;
                }
            }
        }


        // If you lost control
        if (!isController) {
            if (followMode) {
                followMode = false;
                document.getElementById("follow-mode-btn").innerHTML =
                    '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
                updateModeIndicator("Idle", "fa-circle", true);
                showToast("Another client took control. Cursor follow stopped.");
            }

            document.getElementById("motor1-slider").disabled = true;
            document.getElementById("motor2-slider").disabled = true;

            // document.getElementById("follow-mode-btn").disabled = true;
            // document.getElementById("start-btn").disabled = true;  
        } else {
            hasControl = true;
            document.getElementById("motor1-slider").disabled = false;
            document.getElementById("motor2-slider").disabled = false;
            document.getElementById("follow-mode-btn").disabled = false;
            document.getElementById("start-btn").disabled = !isHoming; // Still respect homing
        }
    });

    socket.on("viewer_count", data => {
        const count = data.count;
        const viewerEl = document.getElementById("viewer-count");
        if (viewerEl) viewerEl.textContent = `👀 ${count} viewer${count !== 1 ? 's' : ''}`;
    });


    const nativeWidth = 1920;
    const nativeHeight = 1080;
    const video = document.getElementById("video-feed");

    function scaleCoords(x, y) {
        const rect = video.getBoundingClientRect();
        const scaleX = nativeWidth / rect.width;
        const scaleY = nativeHeight / rect.height;
        return {
            x: Math.round(x * scaleX),
            y: Math.round(y * scaleY)
        };
    }

    // 🔁 Follow Mode Stream
    setInterval(() => {
        if (!followMode || !isHovering || mouseX === null || mouseY === null) return;
        if (mouseX === lastSent.x && mouseY === lastSent.y) return;
        lastSent = { x: mouseX, y: mouseY };
        const { x, y } = scaleCoords(mouseX, mouseY);
        socket.emit("click_target", { x, y });
    }, 50);
});
