let isHoming = true;
let autoModeActive = false;
let selectedTarget = "person"; // default selected target

document.addEventListener("DOMContentLoaded", function () {
    const socket = io();
    const targetPositions = { 1: 0, 2: 0 };
    let followMode = false, mouseX = null, mouseY = null, isHovering = false, lastSent = { x: null, y: null };

    function hideSpinner() {
        document.getElementById("loading-spinner").style.display = 'none';
        document.getElementById("video-feed").style.display = 'inline';
    }

    setTimeout(() => {
        if (document.getElementById("video-feed").complete) {
            hideSpinner();
        }
    }, 2000);

    document.getElementById("video-feed").onload = hideSpinner;

    // 🔁 Target Button Selection
    document.querySelectorAll(".target-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".target-btn").forEach(b => b.classList.remove("selected"));
            btn.classList.add("selected");
            selectedTarget = btn.dataset.target;
        });
    });

    function updateModeIndicator(label, icon = "fa-crosshairs") {
        const indicator = document.getElementById("video-mode-indicator");
        indicator.innerHTML = `<i class="fas ${icon}"></i> Mode: ${label}`;
    }

    // 🔁 Follow Mode Toggle
    document.getElementById("follow-mode-btn").addEventListener("click", () => {
        const btn = document.getElementById("follow-mode-btn");

        if (!followMode) {
            // Start Follow Mode
            if (!isHoming) {
                socket.emit("motor_control", { action: "stop" });
            }
            followMode = false;
            document.getElementById("video-overlay").style.display = "block";
            btn.innerHTML = '<i class="fas fa-ban"></i> Stop Cursor Follow';
            updateModeIndicator("Follow Cursor", "fa-mouse-pointer");  // 🔁 ADD THIS
            setTimeout(() => {
                followMode = true;
            }, 500);
        } else {
            // Stop Follow Mode
            followMode = false;
            document.getElementById("video-overlay").style.display = "none";
            btn.innerHTML = '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
            updateModeIndicator("Idle", "fa-circle");  // 🔁 ADD THIS
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
        homingStatus.textContent = data.homing ? "Complete" : "In Progress";
        homingStatus.className = 'status-value ' + (data.homing ? "Complete" : "InProgress");

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
            showToast("Auto Mode Stopped");
            autoModeActive = false;
        }

        if (data.auto_mode) {
            updateModeIndicator(`Tracking ${data.target || "Target"}`, "fa-bullseye");  // 🔁 ADD
            autoBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Auto Mode';
            indicator.classList.remove("off");
            indicator.classList.add("on");
            indicator.innerHTML = '<i class="fas fa-bullseye"></i> Auto Mode: On';
        } else {
            updateModeIndicator("Idle", "fa-circle");  // 🔁 ADD
            autoBtn.innerHTML = '<i class="fas fa-play"></i> Start Auto Mode';
            indicator.classList.remove("on");
            indicator.classList.add("off");
            indicator.innerHTML = '<i class="fas fa-bullseye"></i> Auto Mode: Off';
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

    document.getElementById("video-feed").addEventListener("click", function (event) {
        if (!isHoming) {
            socket.emit("motor_control", { action: "stop" });
        }

        const rect = this.getBoundingClientRect();
        const x = Math.round(event.clientX - rect.left);
        const y = Math.round(event.clientY - rect.top);

        if (followMode) {
            followMode = false;
            document.getElementById("follow-mode-btn").innerHTML =
                '<i class="fas fa-mouse-pointer"></i> Start Cursor Follow';
            document.getElementById("video-overlay").style.display = "none";
            updateModeIndicator("Idle", "fa-circle");
            
            const toast = document.getElementById("mode-toast");
            toast.classList.add("show");
            showToast("Cursor follow disabled. You can now click to aim.");
            setTimeout(() => toast.classList.remove("show"), 1500);
        }

        socket.emit("click_target", { x, y });
    });

    // 🔁 Follow Mode Stream
    setInterval(() => {
        if (!followMode || !isHovering || mouseX === null || mouseY === null) return;
        if (mouseX === lastSent.x && mouseY === lastSent.y) return;
        lastSent = { x: mouseX, y: mouseY };
        socket.emit("click_target", { x: mouseX, y: mouseY });
    }, 50);
});
