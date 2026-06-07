/**
 * API client – thin wrapper around fetch() for the Python FastAPI backend.
 *
 * All functions are async and throw on HTTP errors so callers can handle
 * them uniformly with try/catch.
 *
 * Base URL is always http://127.0.0.1:8000 (the local FastAPI server).
 */

const API_BASE = 'http://127.0.0.1:8000';

/**
 * Internal helper: fetch JSON from the backend.
 * Throws an Error with a human-readable message on non-2xx status codes.
 *
 * @param {string} path    - URL path, e.g. '/folders'
 * @param {RequestInit} [opts] - Optional fetch options (method, body, etc.)
 * @returns {Promise<any>} Parsed JSON response body
 */
async function _req(path, opts = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
    });

    if (!res.ok) {
        let detail = res.statusText;
        try {
            const body = await res.json();
            detail = body.detail || JSON.stringify(body);
        } catch { /* keep statusText */ }
        throw new Error(detail);
    }

    return res.json();
}

// ─────────────────────────────────────────────────────────────────────────────
// Folders
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch all study folders from the backend.
 * @returns {Promise<Array>} Array of FolderInfo objects
 */
async function apiFolderList() {
    return _req('/folders');
}

/**
 * Create a new study folder.
 * @param {string} name - Human-readable display name
 * @param {string} [folderType='Lernfach'] - 'Lernfach' or 'Praktische Aufgabe'
 * @returns {Promise<Object>} FolderInfo of the created folder
 */
async function apiFolderCreate(name, folderType = 'Lernfach') {
    return _req('/folders', {
        method: 'POST',
        body: JSON.stringify({ name, folder_type: folderType }),
    });
}

/**
 * Delete a folder and all its contents.
 * @param {string} safeName - The filesystem-safe folder name
 * @returns {Promise<Object>} Success message
 */
async function apiFolderDelete(safeName) {
    return _req(`/folders/${encodeURIComponent(safeName)}`, { method: 'DELETE' });
}

// ─────────────────────────────────────────────────────────────────────────────
// PDF upload
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Upload a PDF file to a folder's original/ directory.
 * Uses FormData (multipart) so the binary is sent correctly.
 *
 * @param {string}  safeName - Filesystem-safe folder name
 * @param {File}    file     - The PDF File object from an <input type="file">
 * @returns {Promise<Object>} Upload result with filename
 */
async function apiUploadPdf(safeName, file) {
    const form = new FormData();
    form.append('file', file);

    const res = await fetch(`${API_BASE}/folders/${encodeURIComponent(safeName)}/upload`, {
        method: 'POST',
        body: form,           // Do NOT set Content-Type; browser sets it with boundary
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
    }
    return res.json();
}

// ─────────────────────────────────────────────────────────────────────────────
// PDF → Markdown conversion
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Trigger PDF-to-Markdown conversion.
 * Pass clientId to receive progress updates via WebSocket.
 *
 * @param {string} safeName  - Filesystem-safe folder name
 * @param {string} clientId  - WebSocket client identifier for progress streaming
 * @returns {Promise<Object>} Conversion result
 */
async function apiConvertPdf(safeName, clientId) {
    const qs = clientId ? `?client_id=${encodeURIComponent(clientId)}` : '';
    return _req(`/folders/${encodeURIComponent(safeName)}/convert${qs}`, { method: 'POST' });
}

// ─────────────────────────────────────────────────────────────────────────────
// AI Summarization
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Trigger AI summarization of the converted Markdown.
 * Settings are read from the folder's settings.json on the backend.
 *
 * @param {string} safeName  - Filesystem-safe folder name
 * @param {string} clientId  - WebSocket client identifier for progress streaming
 * @returns {Promise<Object>} Summarization result
 */
async function apiSummarize(safeName, clientId) {
    const qs = clientId ? `?client_id=${encodeURIComponent(clientId)}` : '';
    return _req(`/folders/${encodeURIComponent(safeName)}/summarize${qs}`, { method: 'POST' });
}

// ─────────────────────────────────────────────────────────────────────────────
// Content retrieval
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch the converted Markdown text for preview.
 * @param {string} safeName - Filesystem-safe folder name
 * @returns {Promise<{content: string}>}
 */
async function apiGetMarkdown(safeName) {
    return _req(`/folders/${encodeURIComponent(safeName)}/markdown`);
}

/**
 * Fetch the summary Markdown text for preview.
 * @param {string} safeName - Filesystem-safe folder name
 * @returns {Promise<{content: string}>}
 */
async function apiGetSummary(safeName) {
    return _req(`/folders/${encodeURIComponent(safeName)}/summary`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Load AI settings for a specific folder.
 * @param {string} safeName - Filesystem-safe folder name
 * @returns {Promise<Object>} Settings dict
 */
async function apiGetSettings(safeName) {
    return _req(`/folders/${encodeURIComponent(safeName)}/settings`);
}

/**
 * Persist updated AI settings for a folder.
 * @param {string} safeName  - Filesystem-safe folder name
 * @param {Object} settings  - Updated settings object
 * @returns {Promise<Object>} Saved settings
 */
async function apiSaveSettings(safeName, settings) {
    return _req(`/folders/${encodeURIComponent(safeName)}/settings`, {
        method: 'PUT',
        body: JSON.stringify(settings),
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Ollama
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch the list of models installed in the local Ollama instance.
 * @returns {Promise<Array<{name: string, size_gb: number}>>}
 */
async function apiGetOllamaModels() {
    return _req('/ollama/models');
}
