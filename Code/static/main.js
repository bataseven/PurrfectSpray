let isHoming = true;

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
    }, 2000); // fallback just in case


    document.getElementById("video-feed").onload = hideSpinner;

    document.getElementById("follow-mode-btn").addEventListener("click", () => {
        const btn = document.getElementById("follow-mode-btn");

        if (!followMode) {
            if (!isHoming) {
                socket.emit("motor_control", { action: "stop" });
            }
            // Activating follow mode
            followMode = false; // temporarily block
            document.getElementById("video-overlay").style.display = "block";
            btn.innerHTML = '<i class="fas fa-mouse-pointer"></i> Follow Mode (ON)';
            setTimeout(() => {
                followMode = true;
            }, 500); // wait 0.5 second before enabling
        } else {
            // Turning off follow mode
            followMode = false;
            document.getElementById("video-overlay").style.display = "none";
            btn.innerHTML = '<i class="fas fa-hand-pointer"></i> Manual Click Mode';
        }
    });


    const autoBtn = document.getElementById("start-btn");

    autoBtn.addEventListener("click", () => {
        const isStarting = autoBtn.textContent.includes("Start");
        const target = document.getElementById("auto-target-select").value;
    
        if (isStarting) {
            socket.emit("motor_control", { action: "start", target: target });
        } else {
            socket.emit("motor_control", { action: "stop" });
        }
    });
    
    document.getElementById("laser-btn").addEventListener("click", () => socket.emit("toggle_laser"));
    document.getElementById("shoot-btn").addEventListener("click", () => {
        const btn = document.getElementById("shoot-btn");
        btn.disabled = true;
        btn.textContent = "FIRING...";
        btn.dataset.firedAt = Date.now();  // track time for delayed ack display
        socket.emit("shoot");
    });


    socket.on("status_update", data => {
        isHoming = !data.homing;  // true if homing in progress
    
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
    
    
    socket.on("motor_status", data => {
        const statusEl = document.getElementById("motor-status");
        const autoBtn = document.getElementById("start-btn");
        const icon = autoBtn.querySelector("i");
    
        const status = data.status || "Unknown";
        statusEl.textContent = status;
        statusEl.className = 'status-value ' + (data.auto_mode ? "Running" : "Stopped");
    
        // Update button icon/text
        if (data.auto_mode) {
            icon.className = "fas fa-stop";
            autoBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Auto Mode';
            showToast(`Tracking: ${data.target || "Target"}`);
        } else {
            icon.className = "fas fa-play";
            autoBtn.innerHTML = '<i class="fas fa-play"></i> Start Auto Mode';
            showToast("Auto Mode Stopped");
        }
    });
    
    function showToast(message) {
        const toast = document.getElementById("mode-toast");
        toast.textContent = message;
        toast.classList.add("show");
    
        setTimeout(() => {
            toast.classList.remove("show");
        }, 1500);
    }

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


    let motorThrottle = { 1: 0, 2: 0 };

    function emitMotorUpdate(motorNum, deg) {
        if (Date.now() - motorThrottle[motorNum] < 50) return; // Throttle to 20Hz
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

    function debounce(func, wait) {
        let timeout;
        return function () {
            const context = this, args = arguments;
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(context, args), wait);
        };
    }

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
            // Switch to manual mode on click
            followMode = false;
            document.getElementById("follow-mode-btn").innerHTML =
                '<i class="fas fa-hand-pointer"></i> Manual Click Mode';
            document.getElementById("video-overlay").style.display = "none";

            // Show toast
            const toast = document.getElementById("mode-toast");
            toast.classList.add("show");
            setTimeout(() => toast.classList.remove("show"), 1500);
        }

        socket.emit("click_target", { x, y });
    });


    setInterval(() => {
        if (!followMode || !isHovering || mouseX === null || mouseY === null) return;
        if (mouseX === lastSent.x && mouseY === lastSent.y) return;
        lastSent = { x: mouseX, y: mouseY };
        socket.emit("click_target", { x: mouseX, y: mouseY });
    }, 50);
});
