document.addEventListener("DOMContentLoaded", function () {
    const socket = io();
    const targetPositions = { 1: 0, 2: 0 };
    let followMode = false, mouseX = null, mouseY = null, isHovering = false, lastSent = { x: null, y: null };

    function hideSpinner() {
        document.getElementById('loading-spinner').style.display = 'none';
        document.getElementById('video-feed').style.display = 'inline';
    }

    document.getElementById("video-feed").onload = hideSpinner;

    document.getElementById("follow-mode-btn").addEventListener("click", () => {
        followMode = !followMode;
        const btn = document.getElementById("follow-mode-btn");
        btn.innerHTML = followMode
            ? '<i class="fas fa-mouse-pointer"></i> Follow Mode (ON)'
            : '<i class="fas fa-hand-pointer"></i> Manual Click Mode';
        document.getElementById("video-overlay").style.display = followMode ? "block" : "none";
    });

    document.getElementById("start-btn").addEventListener("click", () => socket.emit("motor_control", { action: "start" }));
    document.getElementById("stop-btn").addEventListener("click", () => socket.emit("motor_control", { action: "stop" }));
    document.getElementById("laser-btn").addEventListener("click", () => socket.emit("toggle_laser"));
    document.getElementById("shoot-btn").addEventListener("click", () => {
        const btn = document.getElementById("shoot-btn");
        btn.disabled = true;
        btn.textContent = "FIRING...";
        socket.emit("shoot");
    });

    socket.on("status_update", data => {
        document.getElementById("motor1-pos").textContent = data.motor1.toFixed(2) + "°";
        document.getElementById("motor2-pos").textContent = data.motor2.toFixed(2) + "°";
        document.getElementById("cpu-temp").textContent = data.cpu_temp + "°C";
        document.getElementById("laser-status").textContent = data.laser ? "On" : "Off";
        document.getElementById("laser-status").className = 'status-value ' + (data.laser ? "On" : "Off");
        document.getElementById("homing-status").textContent = data.homing ? "Complete" : "In Progress";
        document.getElementById("homing-status").className = 'status-value ' + (data.homing ? "Complete" : "InProgress");
        document.getElementById("sensor-status-1").textContent = data.sensor1 ? "Detected!" : "Not detected";
        document.getElementById("sensor-status-2").textContent = data.sensor2 ? "Detected!" : "Not detected";
    });

    socket.on("motor_status", data => {
        document.getElementById("motor-status").textContent = data.status;
        document.getElementById("motor-status").className = 'status-value ' + data.status;
    });

    socket.on("laser_status", data => {
        document.getElementById("laser-status").textContent = data.status;
        document.getElementById("laser-status").className = 'status-value ' + data.status;
    });

    socket.on("shoot_ack", data => {
        const btn = document.getElementById("shoot-btn");
        btn.textContent = data.status === "fired" ? "Spray!" : "Busy";
        setTimeout(() => {
            btn.textContent = "Spray!";
            btn.disabled = false;
        }, 1000);
    });

    document.getElementById("motor1-slider").addEventListener("input", debounce(function () {
        const deg = parseInt(this.value);
        targetPositions[1] = deg;
        document.getElementById("motor1-target").textContent = deg + "°";
        socket.emit("set_motor_position", { motor: 1, position: deg });
    }, 25));
    
    document.getElementById("motor2-slider").addEventListener("input", debounce(function () {
        const deg = parseInt(this.value);
        targetPositions[2] = deg;
        document.getElementById("motor2-target").textContent = deg + "°";
        socket.emit("set_motor_position", { motor: 2, position: deg });
    }, 25));    

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
        if (followMode) return;
        const rect = this.getBoundingClientRect();
        const x = Math.round(event.clientX - rect.left);
        const y = Math.round(event.clientY - rect.top);
        socket.emit("click_target", { x, y });
    });

    setInterval(() => {
        if (!followMode || !isHovering || mouseX === null || mouseY === null) return;
        if (mouseX === lastSent.x && mouseY === lastSent.y) return;
        lastSent = { x: mouseX, y: mouseY };
        socket.emit("click_target", { x: mouseX, y: mouseY });
    }, 50);
});
