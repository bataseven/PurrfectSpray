document.addEventListener("DOMContentLoaded", function () {
    function hideSpinner() {
        document.getElementById('loading-spinner').style.display = 'none';
        document.getElementById('video-feed').style.display = 'inline';
    }

    const videoFeed = document.getElementById("video-feed");
    videoFeed.onload = hideSpinner
    const targetPositions = { 1: 0, 2: 0 };
    let followMode = false, mouseX = null, mouseY = null, isHovering = false;
    let lastSent = { x: null, y: null };

    function toggleFollowMode() {
        followMode = !followMode;
        const btn = document.getElementById("follow-mode-btn");
        btn.innerHTML = followMode
            ? '<i class="fas fa-mouse-pointer"></i> Follow Mode (ON)'
            : '<i class="fas fa-hand-pointer"></i> Manual Click Mode';

        document.getElementById("video-overlay").style.display = followMode ? "block" : "none";
    }

    function debounce(func, wait) {
        let timeout;
        return function () {
            const context = this, args = arguments;
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(context, args), wait);
        };
    }

    function moveMotor(motorNum) {
        const position = targetPositions[motorNum];
        fetch(`/set_motor_position?motor=${motorNum}&position=${position}`)
            .then(r => r.json())
            .then(data => console.log(`Motor ${motorNum} moving to ${position} degrees`));
    }

    document.getElementById('motor1-slider').addEventListener('input', debounce(function () {
        targetPositions[1] = parseInt(this.value);
        document.getElementById('motor1-target').textContent = targetPositions[1] + '°';
        moveMotor(1);
    }, 25));

    document.getElementById('motor2-slider').addEventListener('input', debounce(function () {
        targetPositions[2] = parseInt(this.value);
        document.getElementById('motor2-target').textContent = targetPositions[2] + '°';
        moveMotor(2);
    }, 25));

    function controlMotor(action) {
        fetch('/motor_control?action=' + action)
            .then(r => r.json())
            .then(data => {
                const statusElement = document.getElementById('motor-status');
                statusElement.textContent = data.status;
                statusElement.className = 'status-value ' + data.status;
            });
    }

    videoFeed.addEventListener("mouseenter", () => isHovering = true);
    videoFeed.addEventListener("mouseleave", () => isHovering = false);

    videoFeed.addEventListener("mousemove", (event) => {
        const rect = videoFeed.getBoundingClientRect();
        mouseX = Math.round(event.clientX - rect.left);
        mouseY = Math.round(event.clientY - rect.top);
    });

    videoFeed.addEventListener("click", function (event) {
        if (followMode) return;
        const rect = this.getBoundingClientRect();
        const x = Math.round(event.clientX - rect.left);
        const y = Math.round(event.clientY - rect.top);
        fetch("/click_target", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ x, y })
        });
    });

    function toggleLaser() {
        fetch('/toggle_laser')
            .then(r => r.json())
            .then(data => {
                const statusElement = document.getElementById('laser-status');
                statusElement.textContent = data.status;
                statusElement.className = 'status-value ' + data.status;
            });
    }

    function shoot() {
        const btn = document.getElementById('shoot-btn');
        btn.disabled = true;
        btn.textContent = "FIRING...";
        fetch('/shoot')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'busy') {
                    btn.textContent = "BUSY (TRY AGAIN)";
                    setTimeout(() => {
                        btn.textContent = "Spray!";
                        btn.disabled = false;
                    }, 1000);
                } else {
                    setTimeout(() => {
                        btn.textContent = "Spray!";
                        btn.disabled = false;
                    }, 500);
                }
            })
            .catch(error => {
                btn.textContent = "ERROR!";
                console.error('Error:', error);
                setTimeout(() => {
                    btn.textContent = "Spray!";
                    btn.disabled = false;
                }, 1000);
            });
    }

    setInterval(() => {
        if (!followMode || !isHovering || mouseX === null || mouseY === null) return;
        if (mouseX === lastSent.x && mouseY === lastSent.y) return;
        lastSent = { x: mouseX, y: mouseY };
        fetch("/click_target", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ x: mouseX, y: mouseY })
        });
    }, 50);

    function updateStatus() {
        fetch('/homing_status')
            .then(r => r.json())
            .then(data => {
                const statusElement = document.getElementById('homing-status');
                statusElement.textContent = data.complete ? "Complete" : "In Progress";
                statusElement.className = 'status-value ' + (data.complete ? 'Complete' : 'InProgress');
            });

        fetch('/sensor_status/1').then(r => r.text()).then(t => {
            document.getElementById('sensor-status-1').textContent = t;
        });

        fetch('/sensor_status/2').then(r => r.text()).then(t => {
            document.getElementById('sensor-status-2').textContent = t;
        });

        fetch('/get_motor_positions')
            .then(r => r.json())
            .then(data => {
                document.getElementById('motor1-pos').textContent = data.motor1.toFixed(2) + '°';
                document.getElementById('motor2-pos').textContent = data.motor2.toFixed(2) + '°';
            });

        fetch('/get_cpu_temp')
            .then(r => r.json())
            .then(data => {
                document.getElementById('cpu-temp').textContent = data.temp + '°C';
            });

        setTimeout(updateStatus, 500);
    }

    updateStatus();
});
