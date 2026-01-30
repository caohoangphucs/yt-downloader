const downloadBtn = document.getElementById('download-btn');
const urlInput = document.getElementById('playlist-url');
const progressContainer = document.getElementById('progress-container');
const resultContainer = document.getElementById('result-container');
const errorContainer = document.getElementById('error-container');
const progressBarFill = document.getElementById('progress-bar-fill');
const statusText = document.getElementById('status-text');
const percentageText = document.getElementById('percentage');
const currentFileText = document.getElementById('current-file');
const downloadLink = document.getElementById('download-link');
const finalTitle = document.getElementById('final-title');
const errorMessage = document.getElementById('error-message');

const previewContainer = document.getElementById('preview-container');
const previewTitle = document.getElementById('preview-title');
const previewList = document.getElementById('preview-list');

const selectAll = document.getElementById('select-all');

let previewTimeout;
urlInput.addEventListener('input', () => {
    clearTimeout(previewTimeout);
    const url = urlInput.value.trim();
    if (url.includes('youtube.com/') || url.includes('youtu.be/')) {
        previewTimeout = setTimeout(fetchPreview, 800);
    } else {
        previewContainer.classList.add('hidden');
    }
});

selectAll.addEventListener('change', () => {
    const checkboxes = previewList.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => cb.checked = selectAll.checked);
});

async function fetchPreview() {
    const url = urlInput.value.trim();
    if (!url) return;

    previewTitle.textContent = 'Loading preview...';
    previewList.innerHTML = '';
    previewContainer.classList.remove('hidden');

    try {
        const response = await fetch('/api/info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, format: 'mp4' }) // format doesn't matter for info
        });

        if (!response.ok) throw new Error();

        const data = await response.json();
        previewTitle.textContent = `${data.is_playlist ? 'Playlist' : 'Video'}: ${data.title}`;

        previewList.innerHTML = data.entries.map((entry, index) => `
            <div class="preview-item">
                <label class="checkbox-container">
                    <input type="checkbox" value="${entry.url || ''}" checked data-index="${index}">
                    <span class="checkmark"></span>
                </label>
                <div class="preview-item-info">
                    <span>${entry.title}</span>
                    <span style="color: #666">${formatDuration(entry.duration)}</span>
                </div>
            </div>
        `).join('');

        // Link individual checkboxes to select-all
        const checkboxes = previewList.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(cb => {
            cb.addEventListener('change', () => {
                const allChecked = Array.from(checkboxes).every(c => c.checked);
                selectAll.checked = allChecked;
            });
        });

    } catch (err) {
        previewContainer.classList.add('hidden');
    }
}

function formatDuration(seconds) {
    if (!seconds) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [h, m, s]
        .map(v => v < 10 ? "0" + v : v)
        .filter((v, i) => v !== "00" || i > 0)
        .join(":");
}

const cancelBtn = document.getElementById('cancel-btn');
let currentJobId = null;

downloadBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) {
        alert('Please paste a YouTube URL');
        return;
    }

    // Get selected URLs
    const selectedCheckboxes = previewList.querySelectorAll('input[type="checkbox"]:checked');
    let selected_urls = null;

    // If it's a playlist we saw in preview, collect selected URLs
    if (!previewContainer.classList.contains('hidden') && selectedCheckboxes.length > 0) {
        selected_urls = Array.from(selectedCheckboxes).map(cb => cb.value).filter(v => v);
        if (selected_urls.length === 0) {
            alert('Please select at least one video to download');
            return;
        }
    }

    const format = document.querySelector('input[name="format"]:checked').value;

    // Reset UI
    downloadBtn.disabled = true;
    downloadBtn.textContent = 'Processing...';
    progressContainer.classList.remove('hidden');
    resultContainer.classList.add('hidden');
    errorContainer.classList.add('hidden');
    progressBarFill.style.width = '0%';
    percentageText.textContent = '0%';
    statusText.textContent = 'Initializing...';
    currentFileText.textContent = '';

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, format, selected_urls })
        });

        if (!response.ok) throw new Error('Failed to start download');

        const { job_id } = await response.json();
        currentJobId = job_id;
        trackProgress(job_id);
    } catch (err) {
        showError(err.message);
    }
});

cancelBtn.addEventListener('click', async () => {
    if (!currentJobId) return;

    cancelBtn.disabled = true;
    cancelBtn.textContent = 'Cancelling...';

    try {
        await fetch(`/api/cancel/${currentJobId}`, { method: 'POST' });
    } catch (err) {
        console.error('Cancel failed', err);
    }
});

function trackProgress(jobId) {
    const eventSource = new EventSource(`/api/progress/${jobId}`);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.error) {
            showError(data.error);
            eventSource.close();
            return;
        }

        if (data.cancelled) {
            showError('Download cancelled');
            eventSource.close();
            return;
        }

        // Update progress
        const p = data.progress || 0;
        progressBarFill.style.width = `${p}%`;
        percentageText.textContent = `${Math.round(p)}%`;
        statusText.textContent = data.status;
        currentFileText.textContent = data.current_file || '';

        if (data.status === 'Completed') {
            eventSource.close();
            showResult(jobId, data.playlist_title);
        } else if (data.status === 'Error') {
            eventSource.close();
            showError(data.error);
        }
    };

    eventSource.onerror = () => {
        // Only show error if not manually cancelled
        statusText.textContent = 'Lost connection';
        eventSource.close();
    };
}

function showResult(jobId, title) {
    progressContainer.classList.add('hidden');
    resultContainer.classList.remove('hidden');

    const downloadUrl = `${window.location.origin}/download/${jobId}`;
    downloadLink.href = downloadUrl;

    finalTitle.innerHTML = `
        <div style="margin-bottom: 10px;">${title}</div>
        <div style="font-size: 0.8rem; background: rgba(0,0,0,0.3); padding: 10px; border-radius: 8px; word-break: break-all;">
            URL: <a href="${downloadUrl}" target="_blank" style="color: #00c6ff;">${downloadUrl}</a>
        </div>
    `;

    downloadBtn.disabled = false;
    downloadBtn.textContent = 'Start Download';
    currentJobId = null;
}

function showError(msg) {
    progressContainer.classList.add('hidden');
    errorContainer.classList.remove('hidden');
    errorMessage.textContent = msg.includes('Error:') ? msg : `Notification: ${msg}`;
    downloadBtn.disabled = false;
    downloadBtn.textContent = 'Start Download';
    cancelBtn.disabled = false;
    cancelBtn.textContent = 'Cancel Download';
    currentJobId = null;
}
