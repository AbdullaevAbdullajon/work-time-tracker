// The server stores elapsed time. This script only updates its display.
const loadedAt = performance.now();
const timers = document.querySelectorAll('[data-elapsed]');

function updateTimers() {
    const sinceLoad = Math.floor((performance.now() - loadedAt) / 1000);
    for (const timer of timers) {
        const elapsed = Number(timer.dataset.elapsed) + sinceLoad;
        const hours = Math.floor(elapsed / 3600);
        const minutes = Math.floor(elapsed / 60) % 60;
        const seconds = elapsed % 60;
        timer.textContent = [hours, minutes, seconds]
            .map(value => String(value).padStart(2, '0'))
            .join(':');
    }
}

if (timers.length) {
    setInterval(updateTimers, 250);
}
