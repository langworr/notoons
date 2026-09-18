// ==========================================
// NOTOONS - Client-Side JavaScript
// ==========================================

let selectedFile = null;
let pollInterval = null;
let activeModalJobId = null;
let cachedModalLogs = [];

document.addEventListener('DOMContentLoaded', () => {
    const dropzone = document.getElementById('dropzone');
    const dropzoneTitle = document.getElementById('dropzoneTitle');
    const dropzoneDesc = document.getElementById('dropzoneDesc');
    const filePill = document.getElementById('filePill');
    const filePillText = document.getElementById('filePillText');
    const processBtn = document.getElementById('processBtn');
    const activeJobCard = document.getElementById('activeJobCard');
    const activeJobName = document.getElementById('activeJobName');
    const activeJobPct = document.getElementById('activeJobPct');
    const activeProgressBar = document.getElementById('activeProgressBar');
    const activeJobPage = document.getElementById('activeJobPage');
    const activeJobSlides = document.getElementById('activeJobSlides');
    const jobsList = document.getElementById('jobsList');
    const jobsCountBadge = document.getElementById('jobsCountBadge');
    const logModal = document.getElementById('logModal');
    const logModalBody = document.getElementById('logModalBody');
    const logModalTitle = document.getElementById('logModalTitle');
    const logPathInfo = document.getElementById('logPathInfo');

    ['dragenter', 'dragover'].forEach(n => dropzone.addEventListener(n, e => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(n => dropzone.addEventListener(n, e => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
    }));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      if (e.dataTransfer.files.length) handleFileSelected(e.dataTransfer.files[0]);
    });

    // Initial load
    loadConfig();
    fetchJobs().then(() => {
      startPolling();
    });
});

function handleFileSelected(file) {
  if (!file) return;
  selectedFile = file;
  const sizeStr = file.size > 1024 * 1024
    ? `${(file.size / (1024 * 1024)).toFixed(2)} MB`
    : `${(file.size / 1024).toFixed(1)} KB`;

  document.getElementById('dropzoneTitle').textContent = "File selected (click to change)";
  document.getElementById('dropzoneDesc').textContent = "";
  document.getElementById('filePillText').textContent = `${file.name} (${sizeStr})`;
  document.getElementById('filePill').style.display = "inline-flex";

  document.getElementById('processBtn').disabled = false;
}

function resetDropzone() {
  selectedFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('dropzoneTitle').textContent = "Click to select or drag & drop PDF";
  document.getElementById('dropzoneDesc').textContent = "Supports large multi-page PDFs, handouts, and slides";
  document.getElementById('filePill').style.display = "none";
  document.getElementById('processBtn').disabled = true;
}

async function loadConfig() {
  try {
    const res = await fetch('/config');
    const data = await res.json();
    if (data.outputs_dir) {
      const cfgOutputs = document.getElementById('cfgOutputs');
      const cfgLogs = document.getElementById('cfgLogs');
      if (cfgOutputs) cfgOutputs.textContent = data.outputs_dir;
      if (cfgLogs) cfgLogs.textContent = data.logs_dir;
    }
  } catch (err) {
    console.error('Error fetching config:', err);
  }
}

async function submitUploadJob() {
  if (!selectedFile) return;

  const fileToUpload = selectedFile;
  const mode = document.getElementById('modeSelect').value;
  const dpi = document.getElementById('dpiSelect').value;
  const processBtn = document.getElementById('processBtn');

  processBtn.disabled = true;
  processBtn.innerHTML = `
    <svg style="width: 20px; height: 20px; animation: spin 1s linear infinite;"
      fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" style="opacity: 0.25;"></circle>
      <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" style="opacity: 0.75;"></path>
    </svg>
    <span>Uploading...</span>
  `;

  const formData = new FormData();
  formData.append('file', fileToUpload);
  formData.append('mode', mode);
  formData.append('dpi', dpi);

  try {
    const res = await fetch('/jobs', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      resetDropzone();
      await fetchJobs();
      startPolling();
    } else {
      alert('Failed to enqueue job: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Upload failed: ' + err.message);
  } finally {
    processBtn.disabled = selectedFile === null;
    processBtn.innerHTML = `
      <svg style="width: 20px; height: 20px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
      </svg>
      Queue Conversion Job
    `;
  }
}

async function fetchJobs() {
  try {
    const res = await fetch('/jobs');
    const data = await res.json();
    if (data.jobs) {
      renderJobs(data.jobs);
    }
  } catch (err) {
    console.error('Error fetching jobs:', err);
  }
}

function renderJobs(jobs) {
  const jobsCountBadge = document.getElementById('jobsCountBadge');
  const jobsList = document.getElementById('jobsList');
  const activeJobCard = document.getElementById('activeJobCard');

  jobsCountBadge.textContent = jobs.length;
  if (jobs.length === 0) {
    jobsList.innerHTML = '<div class="jobs-empty">No jobs queued yet. Upload a PDF above to begin.</div>';
    activeJobCard.style.display = 'none';
    return;
  }

  const activeJob = jobs.find(j => j.status === 'processing' || j.status === 'pending');
  if (activeJob) {
    activeJobCard.style.display = 'block';
    document.getElementById('activeJobName').textContent = activeJob.filename;
    const pct = activeJob.progress_percent || 0;
    document.getElementById('activeJobPct').textContent = `${pct}%`;
    document.getElementById('activeProgressBar').style.width = `${pct}%`;
    document.getElementById('activeJobPage').textContent = `Page ${activeJob.current_page || 0} / ${activeJob.total_pages || '?'}`;
    document.getElementById('activeJobSlides').textContent = `${activeJob.slides_extracted || 0} slides extracted`;
  } else {
    activeJobCard.style.display = 'none';
  }

  if (activeModalJobId) {
    const currentJob = jobs.find(j => j.id === activeModalJobId);
    if (currentJob && (currentJob.status === 'processing' || currentJob.status === 'pending')) {
      viewJobLogs(activeModalJobId, false);
    }
  }

  let html = '';
  jobs.forEach(job => {
    let pillClass = 'pill-pending';
    let statusText = 'Pending';

    if (job.status === 'processing') {
      pillClass = 'pill-processing';
      statusText = `Processing (${job.progress_percent || 0}%)`;
    } else if (job.status === 'completed') {
      pillClass = 'pill-completed';
      statusText = 'Completed';
    } else if (job.status === 'failed') {
      pillClass = 'pill-failed';
      statusText = 'Failed';
    }

    const sizeStr = (job.file_size_bytes / (1024 * 1024)).toFixed(1) + ' MB';
    const durationStr = job.elapsed_ms ? `${(job.elapsed_ms / 1000).toFixed(1)}s` : '';
    const cbzSizeStr = job.cbz_size_bytes ? `${(job.cbz_size_bytes / 1024).toFixed(0)} KB` : '';

    html += `
      <div class="job-card">
        <div class="job-info">
          <div class="job-filename" title="${job.filename}">${job.filename}</div>
          <div class="job-details">
            <span class="job-pill ${pillClass}">${statusText}</span>
            <span>${sizeStr}</span>
            <span>•</span>
            <span>Mode: ${job.mode} (${job.dpi} DPI)</span>
            ${job.status === 'completed'
              ? `<span>•</span><span>${job.slides_extracted} slides</span>` +
                `<span>•</span><span>${cbzSizeStr}</span><span>•</span>` +
                `<span>${durationStr}</span>`
              : ''}
            ${job.error ? `<span>•</span><span style="color: var(--danger);">${job.error}</span>` : ''}
          </div>
        </div>
        <div class="job-actions">
          ${job.status === 'completed' ? `
            <a href="${job.download_url}" download="${job.cbz_filename}" class="btn-job btn-job-download">
              <svg style="width: 16px; height: 16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              Download .CBZ
            </a>
          ` : ''}
          <button class="btn-job" onclick="viewJobLogs('${job.id}')">
            <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293
                  l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
            </svg>
            Logs
          </button>
          <button class="btn-job-delete" title="Delete job & output file" onclick="deleteJob('${job.id}')">
            <svg style="width: 16px; height: 16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7
                  m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
            </svg>
          </button>
        </div>
      </div>
    `;
  });
  jobsList.innerHTML = html;

  const hasActive = jobs.some(j => j.status === 'processing' || j.status === 'pending');
  if (!hasActive && pollInterval) {
    stopPolling();
  }
}

function startPolling() {
  if (!pollInterval) {
    pollInterval = setInterval(fetchJobs, 1000);
  }
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

async function viewJobLogs(jobId, openModal = true) {
  activeModalJobId = jobId;
  const logModalTitle = document.getElementById('logModalTitle');
  const logPathInfo = document.getElementById('logPathInfo');
  const logModal = document.getElementById('logModal');

  try {
    const res = await fetch(`/jobs/${jobId}`);
    const data = await res.json();
    if (data.logs) {
      cachedModalLogs = data.logs;
      logModalTitle.textContent = `Log: ${data.filename} (${data.status})`;
      if (data.log_path) {
        logPathInfo.textContent = `Log file: ${data.log_path}`;
        logPathInfo.style.display = 'block';
      } else {
        logPathInfo.style.display = 'none';
      }
      renderModalLogs(data.logs);
      if (openModal) {
        logModal.style.display = 'flex';
      }
    }
  } catch (err) {
    console.error('Error fetching job logs:', err);
  }
}

function renderModalLogs(lines) {
  const logModalBody = document.getElementById('logModalBody');
  logModalBody.innerHTML = '';
  lines.forEach(line => {
    const div = document.createElement('div');
    div.className = 'log-line';

    if (line.includes('[ERROR]')) div.className += ' log-error';
    else if (line.includes('[SUCCESS]')) div.className += ' log-success';
    else if (line.includes('[WARN]')) div.className += ' log-warn';
    else if (line.includes('[PAGE') || line.includes('[PACK]')) div.className += ' log-page';
    else if (line.includes('[INFO]') || line.includes('[UPLOAD]')) div.className += ' log-info';

    div.textContent = line;
    logModalBody.appendChild(div);
  });
  logModalBody.scrollTop = logModalBody.scrollHeight;
}

function copyCurrentModalLogs() {
  if (cachedModalLogs.length) {
    navigator.clipboard.writeText(cachedModalLogs.join('\n')).then(() => alert('Logs copied to clipboard!'));
  }
}

function closeLogModal(e) {
  const logModal = document.getElementById('logModal');
  if (e && e.target !== logModal && !e.target.classList.contains('mac-dot')) return;
  logModal.style.display = 'none';
  activeModalJobId = null;
}

async function deleteJob(jobId) {
  if (!confirm('Remove this job and delete its generated output file?')) return;
  try {
    await fetch(`/jobs/${jobId}`, { method: 'DELETE' });
    await fetchJobs();
  } catch (err) {
    console.error('Error deleting job:', err);
  }
}

function toggleUserDropdown(event) {
  event.stopPropagation();
  const container = event.currentTarget.parentElement;
  container.classList.toggle('active');
}

document.addEventListener('click', function (event) {
  const container = document.querySelector('.user-dropdown-container');
  if (container && container.classList.contains('active')) {
    container.classList.remove('active');
  }
});