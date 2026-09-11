import asyncio
import io
import json
import os
import time
import traceback
import uuid
import falcon
import falcon.asgi
from falcon.media.multipart import MultipartFormHandler
import converter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "initial_assets")
if not os.path.isdir(ASSETS_DIR):
    ASSETS_DIR = os.path.join(BASE_DIR, "initial assets")


def load_config() -> dict:
    """
    Load folder configuration from a dedicated text configuration file (config.txt)
    read at application startup. Supports 'key = value' syntax and comments (#, ;).
    """
    cfg = {
        "outputs_dir": "outputs",
        "logs_dir": "logs",
        "temp_dir": "temp",
    }
    config_file = os.path.join(BASE_DIR, "config.txt")
    if os.path.isfile(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(("#", ";", "//")):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                    elif ":" in line:
                        k, v = line.split(":", 1)
                    else:
                        continue
                    k = k.strip().lower()
                    v = v.strip().strip("\"'")
                    if k in cfg:
                        cfg[k] = v
            print(f"[CONFIG] Loaded configuration from {config_file}")
        except Exception as e:
            print(f"[WARN] Failed to read config.txt: {e}")

    # Environment variables override config.txt if explicitly provided
    cfg["outputs_dir"] = os.getenv("NOTOONS_OUTPUTS_DIR", os.getenv("OUTPUTS_DIR", cfg["outputs_dir"]))
    cfg["logs_dir"] = os.getenv("NOTOONS_LOGS_DIR", os.getenv("LOGS_DIR", cfg["logs_dir"]))
    cfg["temp_dir"] = os.getenv("NOTOONS_TEMP_DIR", os.getenv("TEMP_DIR", cfg["temp_dir"]))

    # Convert relative paths to absolute paths
    for k in ["outputs_dir", "logs_dir", "temp_dir"]:
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.abspath(os.path.join(BASE_DIR, cfg[k]))
        os.makedirs(cfg[k], exist_ok=True)

    return cfg



CONFIG = load_config()


def cleanup_temp_dir():
    """Clean up any orphan temporary files on startup."""
    temp_dir = CONFIG["temp_dir"]
    if os.path.isdir(temp_dir):
        for fname in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except Exception:
                pass


cleanup_temp_dir()

# In-memory store for jobs
JOBS: dict[str, dict] = {}
JOBS_ORDER: list[str] = []  # Ordered newest first
CACHE_TTL = 7200  # 2 hours retention


def cleanup_jobs():
    """Remove expired jobs and clean up their output files."""
    now = time.time()
    expired = [jid for jid, j in JOBS.items() if now - j.get("created_at", 0) > CACHE_TTL]
    for jid in expired:
        j = JOBS.pop(jid, None)
        if jid in JOBS_ORDER:
            JOBS_ORDER.remove(jid)
        if j:
            # Clean up output file from disk
            cbz_p = j.get("cbz_path")
            if cbz_p and os.path.isfile(cbz_p):
                try:
                    os.remove(cbz_p)
                except Exception:
                    pass
            # Clean up temp upload file if still present
            tmp_p = j.get("temp_pdf_path")
            if tmp_p and os.path.isfile(tmp_p):
                try:
                    os.remove(tmp_p)
                except Exception:
                    pass


def run_job_sync(job_id: str, temp_pdf_path: str, cbz_path: str, log_path: str, mode: str, dpi: int):
    """
    Synchronous background worker executed in a separate thread via asyncio.to_thread.
    Streams input from disk, writes output to disk, and logs to both memory and log file.
    Always cleans up temporary input files when done.
    """
    job = JOBS.get(job_id)
    if not job:
        # If job was removed before starting, delete temp file
        if os.path.isfile(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except Exception:
                pass
        return

    job["status"] = "processing"
    job["updated_at"] = time.time()

    def on_progress(cur_page, total_pages, slides_so_far):
        pct = int((cur_page / max(1, total_pages)) * 100)
        job["current_page"] = cur_page
        job["total_pages"] = total_pages
        job["progress_percent"] = pct
        job["slides_extracted"] = slides_so_far
        job["updated_at"] = time.time()

    def on_log(line):
        job["logs"].append(line)
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")
        except Exception:
            pass

    try:
        res = converter.convert_pdf(
            temp_pdf_path,
            mode=mode,
            dpi=dpi,
            output_cbz_path=cbz_path,
            logger=on_log,
            progress_callback=on_progress,
        )
        job["status"] = "completed"
        job["pages_processed"] = res["pages_processed"]
        job["slides_extracted"] = res["slides_extracted"]
        job["elapsed_ms"] = res["elapsed_ms"]
        job["cbz_size_bytes"] = res["cbz_size_bytes"]
        job["cbz_path"] = cbz_path
        job["log_path"] = log_path
        job["progress_percent"] = 100
        job["updated_at"] = time.time()
    except Exception as e:
        err_msg = str(e)
        tb = traceback.format_exc()
        job["status"] = "failed"
        job["error"] = err_msg
        job["traceback"] = tb
        on_log(f"[ERROR] {err_msg}")
        on_log(tb)
        job["updated_at"] = time.time()
        # Clean up partial/failed output file
        if os.path.isfile(cbz_path):
            try:
                os.remove(cbz_path)
            except Exception:
                pass
    finally:
        # CRITICAL: Always delete the temporary uploaded PDF once conversion finishes or fails
        if os.path.isfile(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except Exception:
                pass


async def start_job_background(job_id: str, temp_pdf_path: str, cbz_path: str, log_path: str, mode: str, dpi: int):
    await asyncio.to_thread(run_job_sync, job_id, temp_pdf_path, cbz_path, log_path, mode, dpi)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" type="image/png" href="/favicon.png">
  <title>Notoons - PDF to CBZ Converter</title>
  <style>
    :root {
      --bg: #070a12;
      --card-bg: rgba(17, 24, 39, 0.85);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-border-hover: rgba(56, 189, 248, 0.3);
      --accent: #38bdf8;
      --accent-gradient: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
      --accent-hover: #0ea5e9;
      --success: #34d399;
      --success-gradient: linear-gradient(135deg, #34d399 0%, #059669 100%);
      --warning: #fbbf24;
      --danger: #f87171;
      --text: #f8fafc;
      --muted: #94a3b8;
      --input-bg: #0b1120;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg);
      background-image: 
        radial-gradient(ellipse 80% 50% at 50% -20%, rgba(14, 165, 233, 0.15), transparent),
        radial-gradient(ellipse 60% 40% at 50% 120%, rgba(30, 58, 138, 0.12), transparent);
      background-attachment: fixed;
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 3rem 1.25rem 5rem 1.25rem;
      -webkit-font-smoothing: antialiased;
    }
    .container {
      width: 100%;
      max-width: 740px;
    }

    /* Header & Brand */
    .header {
      display: flex;
      align-items: center;
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }
    .header-logo-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      background: #fdfbf7;
      padding: 5px 8px;
      border-radius: 12px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.12);
      flex-shrink: 0;
      transition: transform 0.2s ease;
    }
    .header-logo-wrap:hover {
      transform: scale(1.02);
    }
    .header-logo {
      height: 46px;
      width: auto;
      max-width: 110px;
      display: block;
      border-radius: 4px;
      object-fit: contain;
    }
    .header-text h1 {
      font-size: 1.65rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: #ffffff;
      line-height: 1.2;
    }
    p.subtitle {
      color: var(--muted);
      font-size: 0.92rem;
      margin-top: 0.3rem;
      line-height: 1.4;
    }

    /* Config Folders Banner */
    .config-banner {
      display: flex;
      align-items: center;
      gap: 1rem;
      background: rgba(11, 17, 32, 0.5);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 8px;
      padding: 0.5rem 0.85rem;
      font-size: 0.76rem;
      color: var(--muted);
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
    }

    /* Panels */
    .panel {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 1.75rem;
      margin-bottom: 1.5rem;
      backdrop-filter: blur(16px);
      box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
    }

    /* Dropzone */
    .dropzone {
      border: 2px dashed #334155;
      border-radius: 12px;
      padding: 2.25rem 1.25rem;
      text-align: center;
      cursor: pointer;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      background: rgba(11, 17, 32, 0.6);
      position: relative;
    }
    .dropzone:hover {
      border-color: #475569;
      background: rgba(15, 23, 42, 0.8);
    }
    .dropzone.dragover {
      border-color: var(--accent);
      background: rgba(56, 189, 248, 0.08);
      box-shadow: 0 0 25px rgba(56, 189, 248, 0.15);
      transform: scale(1.005);
    }
    .dropzone-icon {
      width: 48px;
      height: 48px;
      color: var(--accent);
      margin: 0 auto 0.85rem auto;
      display: block;
      transition: transform 0.2s ease;
    }
    .dropzone:hover .dropzone-icon {
      transform: translateY(-2px);
    }
    .dropzone-title {
      font-weight: 600;
      color: var(--text);
      font-size: 1.05rem;
      margin-bottom: 0.25rem;
    }
    .dropzone-desc {
      font-size: 0.82rem;
      color: var(--muted);
    }
    input[type="file"] {
      display: none;
    }

    /* File Selected State */
    .file-pill {
      display: none;
      align-items: center;
      justify-content: center;
      gap: 0.6rem;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 0.5rem 1rem;
      border-radius: 9999px;
      margin: 0.75rem auto 0 auto;
      width: fit-content;
      max-width: 90%;
    }
    .file-pill svg {
      width: 18px;
      height: 18px;
      color: var(--accent);
      flex-shrink: 0;
    }
    .file-pill-text {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--accent);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* Controls Grid */
    .controls {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
      margin-top: 1.5rem;
    }
    .control-group label {
      display: block;
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--muted);
      margin-bottom: 0.5rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .select-wrap {
      position: relative;
    }
    select {
      width: 100%;
      background: var(--input-bg);
      border: 1px solid #243044;
      border-radius: 10px;
      color: var(--text);
      padding: 0.7rem 2.25rem 0.7rem 0.9rem;
      font-size: 0.92rem;
      font-weight: 500;
      outline: none;
      appearance: none;
      -webkit-appearance: none;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    select:hover {
      border-color: #3b4d66;
    }
    select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
    }
    .select-arrow {
      position: absolute;
      right: 0.85rem;
      top: 50%;
      transform: translateY(-50%);
      pointer-events: none;
      width: 16px;
      height: 16px;
      color: var(--muted);
    }

    /* Button */
    .btn {
      width: 100%;
      margin-top: 1.5rem;
      padding: 0.9rem;
      border-radius: 10px;
      border: none;
      background: var(--accent-gradient);
      color: #070a12;
      font-weight: 700;
      font-size: 1.02rem;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.6rem;
      box-shadow: 0 4px 15px rgba(2, 132, 199, 0.35);
    }
    .btn:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(2, 132, 199, 0.45);
    }
    .btn:active:not(:disabled) {
      transform: translateY(0);
    }
    .btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
      box-shadow: none;
      transform: none;
    }

    /* Active Job Progress Card */
    .active-job-card {
      display: none;
      background: rgba(14, 23, 42, 0.9);
      border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 14px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
      animation: fadeIn 0.3s ease;
    }
    .active-job-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }
    .active-job-title {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .active-job-badge {
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--accent);
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 0.2rem 0.6rem;
      border-radius: 9999px;
    }
    .progress-bar-wrap {
      background: #0b1120;
      border: 1px solid #1e293b;
      height: 10px;
      border-radius: 9999px;
      overflow: hidden;
      margin-bottom: 0.6rem;
    }
    .progress-bar-fill {
      height: 100%;
      background: var(--accent-gradient);
      width: 0%;
      transition: width 0.3s ease;
      border-radius: 9999px;
    }
    .active-job-meta {
      display: flex;
      justify-content: space-between;
      font-size: 0.8rem;
      color: var(--muted);
    }

    /* Jobs Queue List Section */
    .jobs-section {
      margin-top: 1rem;
    }
    .jobs-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    .jobs-title {
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .jobs-count {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.2rem 0.6rem;
      border-radius: 9999px;
      background: rgba(255, 255, 255, 0.08);
      color: var(--muted);
    }
    .jobs-list {
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }
    .job-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.1rem 1.25rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      transition: all 0.2s ease;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    }
    .job-card:hover {
      border-color: rgba(255, 255, 255, 0.15);
      background: rgba(22, 31, 50, 0.9);
    }
    .job-info {
      flex: 1;
      min-width: 0;
    }
    .job-filename {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 0.3rem;
    }
    .job-details {
      font-size: 0.78rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .job-pill {
      font-size: 0.7rem;
      font-weight: 700;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .pill-pending { background: rgba(251, 191, 36, 0.12); color: var(--warning); border: 1px solid rgba(251, 191, 36, 0.3); }
    .pill-processing { background: rgba(56, 189, 248, 0.12); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
    .pill-completed { background: rgba(52, 211, 153, 0.12); color: var(--success); border: 1px solid rgba(52, 211, 153, 0.3); }
    .pill-failed { background: rgba(248, 113, 113, 0.12); color: var(--danger); border: 1px solid rgba(248, 113, 113, 0.3); }

    .job-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-shrink: 0;
    }
    .btn-job {
      background: #1e293b;
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: var(--text);
      font-size: 0.8rem;
      font-weight: 600;
      padding: 0.45rem 0.75rem;
      border-radius: 6px;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
    }
    .btn-job:hover {
      background: #334155;
    }
    .btn-job-download {
      background: var(--success-gradient);
      color: #022c22;
      border: none;
      font-weight: 700;
    }
    .btn-job-download:hover {
      box-shadow: 0 4px 12px rgba(5, 150, 105, 0.35);
      color: #022c22;
    }
    .btn-job-delete {
      background: transparent;
      border: none;
      color: var(--muted);
      padding: 0.4rem;
      cursor: pointer;
      border-radius: 6px;
    }
    .btn-job-delete:hover {
      color: var(--danger);
      background: rgba(248, 113, 113, 0.1);
    }
    .jobs-empty {
      text-align: center;
      padding: 2rem;
      color: var(--muted);
      font-size: 0.9rem;
      background: rgba(11, 17, 32, 0.4);
      border: 1px dashed #243044;
      border-radius: 12px;
    }

    /* Terminal Log Modal */
    .log-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }
    .log-modal-content {
      background: #05080e;
      border: 1px solid #1e293b;
      border-radius: 14px;
      width: 100%;
      max-width: 740px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8);
      overflow: hidden;
    }
    .log-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.9rem 1.25rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      background: #090d16;
    }
    .log-controls {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .mac-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }
    .mac-dot.red { background: #f87171; }
    .mac-dot.yellow { background: #fbbf24; }
    .mac-dot.green { background: #34d399; }
    .log-modal-title {
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--text);
      margin-left: 0.5rem;
    }
    .log-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .btn-log-action {
      background: #1e293b;
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: var(--muted);
      font-size: 0.75rem;
      font-weight: 600;
      padding: 0.3rem 0.7rem;
      border-radius: 6px;
      cursor: pointer;
    }
    .btn-log-action:hover {
      color: var(--text);
      background: #334155;
    }
    .log-path-info {
      padding: 0.4rem 1.25rem;
      background: #0a0f1d;
      font-size: 0.75rem;
      color: var(--muted);
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }
    .log-body {
      padding: 1.25rem;
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      font-size: 0.82rem;
      line-height: 1.55;
      color: #94a3b8;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
      flex: 1;
      max-height: calc(85vh - 100px);
    }
    .log-line { margin-bottom: 3px; }
    .log-info { color: #38bdf8; }
    .log-page { color: #a5b4fc; }
    .log-success { color: #34d399; font-weight: 700; }
    .log-warn { color: #fbbf24; }
    .log-error { color: #f87171; font-weight: 700; }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @media (max-width: 600px) {
      .header { flex-direction: column; text-align: center; gap: 0.75rem; }
      .controls { grid-template-columns: 1fr; }
      .job-card { flex-direction: column; align-items: flex-start; }
      .job-actions { width: 100%; justify-content: flex-end; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-logo-wrap">
        <img class="header-logo" src="/logo.jpg" alt="notoons logo" onerror="this.parentElement.style.display='none'">
      </div>
      <div class="header-text">
        <h1>notoons</h1>
        <p class="subtitle">Fast asynchronous PDF & handout converter to CBZ.</p>
      </div>
    </div>

    <div class="panel">
      <div class="dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
        <svg class="dropzone-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
        </svg>
        <div class="dropzone-title" id="dropzoneTitle">Click to select or drag & drop PDF</div>
        <div class="dropzone-desc" id="dropzoneDesc">Supports large multi-page PDFs, handouts, and slides</div>
        <div class="file-pill" id="filePill">
          <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
          </svg>
          <span class="file-pill-text" id="filePillText"></span>
        </div>
        <input type="file" id="fileInput" accept=".pdf,application/pdf" onchange="handleFileSelected(this.files[0])" />
      </div>

      <div class="controls">
        <div class="control-group">
          <label>Extraction Mode</label>
          <div class="select-wrap">
            <select id="modeSelect">
              <option value="auto" selected>Auto (Detect Grids & Handouts)</option>
              <option value="pages">Pages (1 Page = 1 Slide)</option>
              <option value="slides">Force Grid Slicing</option>
            </select>
            <svg class="select-arrow" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </div>
        </div>
        <div class="control-group">
          <label>Rendering Resolution</label>
          <div class="select-wrap">
            <select id="dpiSelect">
              <option value="150">150 DPI (Fast & Light)</option>
              <option value="200" selected>200 DPI (Balanced Quality)</option>
              <option value="300">300 DPI (High Definition)</option>
            </select>
            <svg class="select-arrow" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </div>
        </div>
      </div>

      <button id="processBtn" class="btn" disabled onclick="submitUploadJob()">
        <svg style="width: 20px; height: 20px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
        </svg>
        Queue Conversion Job
      </button>
    </div>

    <!-- Active Job Live Progress Banner -->
    <div class="active-job-card" id="activeJobCard">
      <div class="active-job-header">
        <div class="active-job-title">
          <svg style="width: 18px; height: 18px; animation: spin 1s linear infinite; color: var(--accent);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" style="opacity: 0.25;"></circle>
            <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" style="opacity: 0.75;"></path>
          </svg>
          <span id="activeJobName">Processing...</span>
        </div>
        <span class="active-job-badge" id="activeJobPct">0%</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" id="activeProgressBar"></div>
      </div>
      <div class="active-job-meta">
        <span id="activeJobPage">Page 0 / 0</span>
        <span id="activeJobSlides">0 slides extracted</span>
      </div>
    </div>

    <!-- Jobs Queue Section -->
    <div class="jobs-section">
      <div class="jobs-header">
        <div class="jobs-title">
          <span>Conversion Jobs</span>
          <span class="jobs-count" id="jobsCountBadge">0</span>
        </div>
        <button class="btn-log-action" onclick="fetchJobs()">
          <svg style="width: 14px; height: 14px; vertical-align: middle; margin-right: 4px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          Refresh
        </button>
      </div>

      <div class="jobs-list" id="jobsList">
        <div class="jobs-empty">No jobs queued yet. Upload a PDF above to begin.</div>
      </div>
    </div>
  </div>

  <!-- Terminal Log Modal -->
  <div class="log-modal" id="logModal" onclick="closeLogModal(event)">
    <div class="log-modal-content" onclick="event.stopPropagation()">
      <div class="log-header">
        <div class="log-controls">
          <span class="mac-dot red" onclick="closeLogModal()"></span>
          <span class="mac-dot yellow"></span>
          <span class="mac-dot green"></span>
          <span class="log-modal-title" id="logModalTitle">Job Execution Log</span>
        </div>
        <div class="log-actions">
          <button class="btn-log-action" onclick="copyCurrentModalLogs()">Copy Log</button>
          <button class="btn-log-action" onclick="closeLogModal()">Close</button>
        </div>
      </div>
      <div class="log-path-info" id="logPathInfo"></div>
      <div class="log-body" id="logModalBody"></div>
    </div>
  </div>

  <script>
    let selectedFile = null;
    let pollInterval = null;
    let activeModalJobId = null;
    let cachedModalLogs = [];

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

    ['dragenter', 'dragover'].forEach(n => dropzone.addEventListener(n, e => { e.preventDefault(); dropzone.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach(n => dropzone.addEventListener(n, e => { e.preventDefault(); dropzone.classList.remove('dragover'); }));
    dropzone.addEventListener('drop', e => {
      if (e.dataTransfer.files.length) handleFileSelected(e.dataTransfer.files[0]);
    });

    function handleFileSelected(file) {
      if (!file) return;
      selectedFile = file;
      const sizeStr = file.size > 1024 * 1024 
        ? `${(file.size / (1024 * 1024)).toFixed(2)} MB`
        : `${(file.size / 1024).toFixed(1)} KB`;
      
      dropzoneTitle.textContent = "File selected (click to change)";
      dropzoneDesc.textContent = "";
      filePillText.textContent = `${file.name} (${sizeStr})`;
      filePill.style.display = "inline-flex";

      processBtn.disabled = false;
    }

    function resetDropzone() {
      selectedFile = null;
      document.getElementById('fileInput').value = '';
      dropzoneTitle.textContent = "Click to select or drag & drop PDF";
      dropzoneDesc.textContent = "Supports large multi-page PDFs, handouts, and slides";
      filePill.style.display = "none";
      processBtn.disabled = true;
    }

    async function loadConfig() {
      try {
        const res = await fetch('/config');
        const data = await res.json();
        if (data.outputs_dir) {
          document.getElementById('cfgOutputs').textContent = data.outputs_dir;
          document.getElementById('cfgLogs').textContent = data.logs_dir;
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

      processBtn.disabled = true;
      processBtn.innerHTML = `
        <svg style="width: 20px; height: 20px; animation: spin 1s linear infinite;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
      jobsCountBadge.textContent = jobs.length;
      if (jobs.length === 0) {
        jobsList.innerHTML = '<div class="jobs-empty">No jobs queued yet. Upload a PDF above to begin.</div>';
        activeJobCard.style.display = 'none';
        return;
      }

      const activeJob = jobs.find(j => j.status === 'processing' || j.status === 'pending');
      if (activeJob) {
        activeJobCard.style.display = 'block';
        activeJobName.textContent = activeJob.filename;
        const pct = activeJob.progress_percent || 0;
        activeJobPct.textContent = `${pct}%`;
        activeProgressBar.style.width = `${pct}%`;
        activeJobPage.textContent = `Page ${activeJob.current_page || 0} / ${activeJob.total_pages || '?'}`;
        activeJobSlides.textContent = `${activeJob.slides_extracted || 0} slides extracted`;
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
                ${job.status === 'completed' ? `<span>•</span><span>${job.slides_extracted} slides</span><span>•</span><span>${cbzSizeStr}</span><span>•</span><span>${durationStr}</span>` : ''}
                ${job.error ? `<span>•</span><span style="color: var(--danger);">${job.error}</span>` : ''}
              </div>
            </div>
            <div class="job-actions">
              ${job.status === 'completed' ? `
                <a href="${job.download_url}" download="${job.cbz_filename}" class="btn-job btn-job-download">
                  <svg style="width: 16px; height: 16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                  </svg>
                  Download .CBZ
                </a>
              ` : ''}
              <button class="btn-job" onclick="viewJobLogs('${job.id}')">
                <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                Logs
              </button>
              <button class="btn-job-delete" title="Delete job & output file" onclick="deleteJob('${job.id}')">
                <svg style="width: 16px; height: 16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
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
        navigator.clipboard.writeText(cachedModalLogs.join('\\n')).then(() => alert('Logs copied to clipboard!'));
      }
    }

    function closeLogModal(e) {
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

    // Initial load
    loadConfig();
    fetchJobs().then(() => {
      startPolling();
    });
  </script>
</body>
</html>
"""


class HomeResource:
    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        resp.content_type = falcon.MEDIA_HTML
        resp.text = INDEX_HTML


class ConfigResource:
    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        resp.status = falcon.HTTP_200
        resp.media = {
            "outputs_dir": CONFIG["outputs_dir"],
            "logs_dir": CONFIG["logs_dir"],
            "temp_dir": CONFIG["temp_dir"],
        }


class StaticResource:
    def __init__(self, filepath: str, content_type: str):
        self.filepath = filepath
        self.content_type = content_type
        self.data = b""
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                self.data = f.read()

    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        if not self.data:
            resp.status = falcon.HTTP_404
            return
        resp.content_type = self.content_type
        resp.data = self.data


class JobsResource:
    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        cleanup_jobs()
        summary_list = []
        for jid in JOBS_ORDER:
            j = JOBS.get(jid)
            if not j:
                continue
            summary_list.append({
                "id": j["id"],
                "filename": j["filename"],
                "cbz_filename": j["cbz_filename"],
                "status": j["status"],
                "mode": j["mode"],
                "dpi": j["dpi"],
                "file_size_bytes": j["file_size_bytes"],
                "total_pages": j["total_pages"],
                "pages_processed": j.get("pages_processed", j["total_pages"]),
                "current_page": j["current_page"],
                "progress_percent": j["progress_percent"],
                "slides_extracted": j["slides_extracted"],
                "elapsed_ms": j["elapsed_ms"],
                "cbz_size_bytes": j["cbz_size_bytes"],
                "download_url": j["download_url"],
                "error": j["error"],
                "created_at": j["created_at"],
            })
        resp.status = falcon.HTTP_200
        resp.media = {"jobs": summary_list}

    async def on_post(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        cleanup_jobs()
        try:
            form = await req.get_media()
            job_id = uuid.uuid4().hex[:10]
            temp_pdf_path = os.path.join(CONFIG["temp_dir"], f"upload_{job_id}.pdf")
            filename = "document.pdf"
            mode = "auto"
            dpi = 200
            bytes_written = 0

            async for part in form:
                if part.name == "file":
                    filename = part.filename or "document.pdf"
                    # Stream file directly to temporary disk file to prevent RAM spikes
                    with open(temp_pdf_path, "wb") as f_out:
                        while True:
                            chunk = await part.stream.read(1024 * 1024)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            bytes_written += len(chunk)
                elif part.name == "mode":
                    val = await part.get_text()
                    if val in ("auto", "slides", "pages"):
                        mode = val
                elif part.name == "dpi":
                    val = await part.get_text()
                    try:
                        dpi = int(val)
                    except ValueError:
                        pass

            if bytes_written == 0 or not os.path.isfile(temp_pdf_path):
                if os.path.isfile(temp_pdf_path):
                    os.remove(temp_pdf_path)
                resp.status = falcon.HTTP_400
                resp.media = {"status": "error", "error": "No file uploaded or file was empty."}
                return

            base_name = os.path.splitext(filename)[0]
            cbz_name = f"{base_name}.cbz"
            cbz_path = os.path.join(CONFIG["outputs_dir"], f"{job_id}_{cbz_name}")
            log_path = os.path.join(CONFIG["logs_dir"], f"{job_id}_{base_name}.log")

            job = {
                "id": job_id,
                "filename": filename,
                "cbz_filename": cbz_name,
                "cbz_path": cbz_path,
                "log_path": log_path,
                "temp_pdf_path": temp_pdf_path,
                "status": "pending",
                "mode": mode,
                "dpi": dpi,
                "file_size_bytes": bytes_written,
                "total_pages": 0,
                "pages_processed": 0,
                "current_page": 0,
                "progress_percent": 0,
                "slides_extracted": 0,
                "elapsed_ms": 0,
                "cbz_size_bytes": 0,
                "download_url": f"/download/{job_id}",
                "logs": [f"[{time.strftime('%H:%M:%S')}] [UPLOAD] Uploaded '{filename}' ({bytes_written / 1024:.1f} KB). Queued."],
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }

            JOBS[job_id] = job
            JOBS_ORDER.insert(0, job_id)

            # Write initial log to disk log file
            try:
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write(job["logs"][0] + "\n")
            except Exception:
                pass

            # Spawn background execution in thread pool without blocking ASGI loop
            asyncio.create_task(
                start_job_background(job_id, temp_pdf_path, cbz_path, log_path, mode, dpi)
            )

            resp.status = falcon.HTTP_200
            resp.media = {
                "status": "ok",
                "job_id": job_id,
                "filename": filename,
                "message": "Job enqueued successfully",
            }
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.media = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}


class JobDetailResource:
    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        j = JOBS.get(job_id)
        if not j:
            resp.status = falcon.HTTP_404
            resp.media = {"error": "Job not found"}
            return

        resp.status = falcon.HTTP_200
        resp.media = {
            "id": j["id"],
            "filename": j["filename"],
            "cbz_filename": j["cbz_filename"],
            "status": j["status"],
            "mode": j["mode"],
            "dpi": j["dpi"],
            "file_size_bytes": j["file_size_bytes"],
            "total_pages": j["total_pages"],
            "pages_processed": j.get("pages_processed", j["total_pages"]),
            "current_page": j["current_page"],
            "progress_percent": j["progress_percent"],
            "slides_extracted": j["slides_extracted"],
            "elapsed_ms": j["elapsed_ms"],
            "cbz_size_bytes": j["cbz_size_bytes"],
            "download_url": j["download_url"],
            "logs": j["logs"],
            "log_path": j.get("log_path"),
            "error": j["error"],
            "created_at": j["created_at"],
        }

    async def on_delete(self, req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        j = JOBS.pop(job_id, None)
        if job_id in JOBS_ORDER:
            JOBS_ORDER.remove(job_id)

        if j:
            cbz_p = j.get("cbz_path")
            if cbz_p and os.path.isfile(cbz_p):
                try:
                    os.remove(cbz_p)
                except Exception:
                    pass
            tmp_p = j.get("temp_pdf_path")
            if tmp_p and os.path.isfile(tmp_p):
                try:
                    os.remove(tmp_p)
                except Exception:
                    pass

        resp.status = falcon.HTTP_200
        resp.media = {"status": "ok"}


class DownloadResource:
    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        j = JOBS.get(job_id)
        if not j or j.get("status") != "completed":
            resp.status = falcon.HTTP_404
            resp.text = "File not ready or expired"
            return

        cbz_path = j.get("cbz_path")
        if not cbz_path or not os.path.isfile(cbz_path):
            resp.status = falcon.HTTP_404
            resp.text = "Output file not found on disk"
            return

        resp.content_type = "application/vnd.comicbook+zip"
        resp.append_header(
            "Content-Disposition", f'attachment; filename="{j["cbz_filename"]}"'
        )

        # Stream directly from disk in 64KB chunks: zero memory overhead
        async def file_streamer():
            with open(cbz_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        resp.stream = file_streamer()


# Initialize Falcon ASGI application
app = falcon.asgi.App()

# Configure Multipart parser to support large PDF files up to 1GB
multipart_handler = MultipartFormHandler()
multipart_handler.parse_options.max_body_part_buffer_size = 1024 * 1024 * 1024  # 1 GB
multipart_handler.parse_options.max_body_part_headers_size = 64 * 1024
app.req_options.media_handlers["multipart/form-data"] = multipart_handler

# Routes
jobs_resource = JobsResource()
app.add_route("/", HomeResource())
app.add_route("/config", ConfigResource())
app.add_route("/jobs", jobs_resource)
app.add_route("/process", jobs_resource)  # Alias for compatibility
app.add_route("/jobs/{job_id}", JobDetailResource())
app.add_route("/download/{job_id}", DownloadResource())

# Serve initial assets
logo_path = os.path.join(ASSETS_DIR, "logo.jpg")
fav_path = os.path.join(ASSETS_DIR, "favicon.png")
app.add_route("/logo.jpg", StaticResource(logo_path, "image/jpeg"))
app.add_route("/favicon.png", StaticResource(fav_path, "image/png"))
app.add_route("/favicon.ico", StaticResource(fav_path, "image/png"))
