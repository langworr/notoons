const outputPageSize = 50;
const selectedOutputFiles = new Set();
let currentOutputDirectory = 0;
let currentOutputPath = '';
let currentOutputPage = 1;
let currentOutputListing = null;
let outputDirectories = [];

document.addEventListener('DOMContentLoaded', loadOutputFiles);

async function loadOutputFiles(page = 1) {
  const fileList = document.getElementById('outputFileList');
  const query = new URLSearchParams({
    directory: String(currentOutputDirectory),
    path: currentOutputPath,
    page: String(page),
    per_page: String(outputPageSize)
  });
  fileList.textContent = 'Loading folder...';

  try {
    const response = await fetch(`/outputs/files?${query}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    currentOutputListing = data;
    currentOutputPage = data.page;
    outputDirectories = data.directories;
    renderOutputDirectorySelect();
    renderOutputBreadcrumbs(data.breadcrumbs);
    renderOutputEntries(data.entries);
    updateOutputPagination(data);
    updateDownloadButton();
  } catch (error) {
    console.error('Could not load output folder:', error);
    fileList.textContent = `Could not load output folder: ${error.message}`;
    fileList.classList.add('management-status-error');
  }
}

function renderOutputDirectorySelect() {
  const select = document.getElementById('outputDirectorySelect');
  select.replaceChildren();
  outputDirectories.forEach(directory => {
    const option = document.createElement('option');
    option.value = directory.index;
    option.textContent = directory.nickname;
    option.selected = directory.index === currentOutputDirectory;
    select.appendChild(option);
  });
}

function renderOutputBreadcrumbs(breadcrumbs) {
  const container = document.getElementById('outputBreadcrumbs');
  container.replaceChildren();
  breadcrumbs.forEach((crumb, index) => {
    if (index) {
      const separator = document.createElement('span');
      separator.textContent = '/';
      container.appendChild(separator);
    }
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'manage-breadcrumb';
    link.textContent = crumb.name;
    link.disabled = index === breadcrumbs.length - 1;
    link.addEventListener('click', () => {
      currentOutputPath = crumb.path;
      loadOutputFiles(1);
    });
    container.appendChild(link);
  });
}

function renderOutputEntries(entries) {
  const fileList = document.getElementById('outputFileList');
  fileList.replaceChildren();
  fileList.classList.remove('management-status-error');
  if (!entries.length) {
    fileList.textContent = 'This folder is empty.';
    return;
  }

  entries.forEach(entry => {
    const row = document.createElement('div');
    row.className = 'manage-file-item';
    if (entry.kind === 'directory') {
      const folderButton = document.createElement('button');
      folderButton.type = 'button';
      folderButton.className = 'manage-folder-button';
      folderButton.textContent = `[Folder] ${entry.name}`;
      folderButton.addEventListener('click', () => {
        currentOutputPath = entry.path;
        loadOutputFiles(1);
      });
      row.appendChild(folderButton);
    } else {
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selectedOutputFiles.has(entry.id);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) selectedOutputFiles.add(entry.id);
        else selectedOutputFiles.delete(entry.id);
        updateDownloadButton();
      });
      const name = document.createElement('span');
      name.textContent = entry.name;
      row.append(checkbox, name);
    }
    fileList.appendChild(row);
  });
}

function updateOutputPagination(listing) {
  document.getElementById('outputPageInfo').textContent =
    `${listing.total} item${listing.total === 1 ? '' : 's'} · Page ${listing.page} of ${listing.total_pages}`;
  document.getElementById('previousOutputPage').disabled = listing.page <= 1;
  document.getElementById('nextOutputPage').disabled =
    listing.page >= listing.total_pages;
}

function changeOutputDirectory() {
  currentOutputDirectory = Number(
    document.getElementById('outputDirectorySelect').value
  );
  currentOutputPath = '';
  loadOutputFiles(1);
}

function changeOutputPage(offset) {
  if (!currentOutputListing) return;
  const page = currentOutputPage + offset;
  if (page < 1 || page > currentOutputListing.total_pages) return;
  loadOutputFiles(page);
}

function selectCurrentPageFiles(selected) {
  if (!currentOutputListing) return;
  currentOutputListing.entries
    .filter(entry => entry.kind === 'file')
    .forEach(entry => {
      if (selected) selectedOutputFiles.add(entry.id);
      else selectedOutputFiles.delete(entry.id);
    });
  renderOutputEntries(currentOutputListing.entries);
  updateDownloadButton();
}

function clearOutputSelection() {
  selectedOutputFiles.clear();
  if (currentOutputListing) {
    renderOutputEntries(currentOutputListing.entries);
  }
  updateDownloadButton();
}

function updateDownloadButton() {
  const button = document.getElementById('downloadSelectedButton');
  button.disabled = selectedOutputFiles.size === 0;
  document.getElementById('outputSelectionCount').textContent =
    `${selectedOutputFiles.size} file${selectedOutputFiles.size === 1 ? '' : 's'} selected`;
}

async function downloadSelectedOutputFiles() {
  if (!selectedOutputFiles.size) return;

  const status = document.getElementById('managementStatus');
  const button = document.getElementById('downloadSelectedButton');
  button.disabled = true;
  status.textContent = 'Preparing selected files...';
  status.classList.remove('management-status-error');

  try {
    const selected = Array.from(selectedOutputFiles);
    const response = await fetch('/outputs/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: selected })
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'notoons_outputs.zip';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent =
      `Download ready (${selected.length} selected file${selected.length === 1 ? '' : 's'}).`;
  } catch (error) {
    console.error('Could not download selected output files:', error);
    status.textContent = `Could not download selected files: ${error.message}`;
    status.classList.add('management-status-error');
  } finally {
    updateDownloadButton();
  }
}

async function deleteManagedFiles(url, confirmation) {
  if (!confirm(confirmation)) return;

  const status = document.getElementById('managementStatus');
  status.textContent = 'Deleting files...';
  status.classList.remove('management-status-error');

  try {
    const response = await fetch(url, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    const count = data.deleted_count;
    status.textContent = `Deleted ${count} file${count === 1 ? '' : 's'}.`;
    if (url === '/outputs/default') {
      await loadOutputFiles(currentOutputPage);
    }
  } catch (error) {
    console.error('File management action failed:', error);
    status.textContent = `Could not delete files: ${error.message}`;
    status.classList.add('management-status-error');
  }
}
