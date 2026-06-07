/**
 * StudyScript AI – Main Application Controller
 *
 * Manages all UI state, user interactions, and orchestrates calls to the
 * API client (api.js) and the animation layer (animations.js).
 *
 * State machine (simplified):
 *   loading → home → folder-detail
 *                         ↓
 *                    settings-panel (overlay)
 *
 * The WebSocket is opened once and reused for progress streaming during
 * both PDF conversion and AI summarization.
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Application state
// ─────────────────────────────────────────────────────────────────────────────

const state = {
    folders:        [],           // Array<FolderInfo> – all loaded folders
    currentFolder:  null,         // FolderInfo – folder currently open in detail view
    clientId:       _makeId(),    // Unique ID for this session's WebSocket channel
    socket:         null,         // WebSocket instance
    operationRunning: false,      // Blocks concurrent long operations
};

// ─────────────────────────────────────────────────────────────────────────────
// DOM references
// ─────────────────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const dom = {
    loadingScreen:    $('loading-screen'),
    loadingMessage:   $('loading-message'),
    app:              $('app'),
    folderGrid:       $('folder-grid'),
    folderGridEmpty:  $('folder-grid-empty'),
    sidebarFolders:   $('sidebar-folder-list'),

    // Views
    viewHome:         $('view-home'),
    viewFolder:       $('view-folder'),

    // Folder detail
    detailTypeBadge:  $('detail-type-badge'),
    detailFolderName: $('detail-folder-name'),
    uploadArea:       $('upload-area'),
    uploadIcon:       $('upload-icon'),
    uploadText:       $('upload-text'),
    uploadSubtext:    $('upload-subtext'),
    fileInput:        $('file-input'),
    btnUpload:        $('btn-upload'),
    btnConvert:       $('btn-convert'),
    btnSummarize:     $('btn-summarize'),

    // Progress
    progressContainer: $('progress-container'),
    progressLabel:     $('progress-label'),
    progressPercent:   $('progress-percent'),
    progressFill:      $('progress-fill'),
    progressMessage:   $('progress-message'),

    // Stop button
    btnStop: $('btn-stop'),

    // Live Log
    logToggle:      $('log-toggle'),
    logToggleIcon:  $('log-toggle-icon'),
    logPanelWrapper: $('log-panel-wrapper'),
    logPanel:       $('log-panel'),
    logClear:       $('log-clear'),

    // Confirm modal
    modalConfirm:   $('modal-confirm'),
    confirmIcon:    $('confirm-icon'),
    confirmTitle:   $('confirm-title'),
    confirmMessage: $('confirm-message'),
    btnConfirmOk:   $('btn-confirm-ok'),
    btnConfirmCancel: $('btn-confirm-cancel'),

    // Content tabs + panels
    contentTabs:    $('content-tabs'),
    panelMarkdown:  $('panel-markdown'),
    panelSummary:   $('panel-summary'),
    markdownViewer: $('markdown-viewer'),
    summaryViewer:  $('summary-viewer'),

    // Steps
    stepUpload:   $('step-upload'),
    stepConvert:  $('step-convert'),
    stepSummarize:$('step-summarize'),

    // Modal
    modalCreate:    $('modal-create'),
    inputFolderName:$('input-folder-name'),

    // Settings
    settingsOverlay:      $('settings-overlay'),
    settingsPanel:        $('settings-panel'),
    settingsModel:        $('settings-model'),
    settingsClaudeModel:  $('settings-claude-model'),
    settingsPrompt:       $('settings-system-prompt'),
    settingsChunkSize:    $('settings-chunk-size'),
    settingsTemperature:  $('settings-temperature'),
    tempDisplay:          $('temp-display'),
    useVision:              $('use-vision'),
    visionConfig:           $('vision-config'),
    settingsVisionModel:    $('settings-vision-model'),
    settingsClaudeVision:   $('settings-claude-vision-model'),

    // Toast
    toast:        $('toast'),
    toastMessage: $('toast-message'),
};

// ─────────────────────────────────────────────────────────────────────────────
// Initialisation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Entry point – called once the DOM is ready.
 * Waits for the Electron backend signal before loading data.
 */
function init() {
    _wireElectronEvents();
    _wireUIEvents();
    _openWebSocket();
}

/** Listen for IPC messages from the Electron main process. */
function _wireElectronEvents() {
    if (!window.electronAPI) {
        // Running in a browser (dev mode) – skip and go straight to loading folders
        _onBackendReady();
        return;
    }

    window.electronAPI.onBackendReady(() => _onBackendReady());
    window.electronAPI.onBackendError(msg => {
        dom.loadingMessage.textContent = `Fehler: ${msg}`;
    });
}

/** Called once the Python backend is confirmed ready. */
async function _onBackendReady() {
    animateAppReady(dom.loadingScreen, dom.app);
    await _loadFolders();
    animateViewIn(dom.viewHome);
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket – progress channel
// ─────────────────────────────────────────────────────────────────────────────

/** Open a WebSocket to receive live progress updates from the backend. */
function _openWebSocket() {
    try {
        state.socket = new WebSocket(`ws://127.0.0.1:8000/ws/${state.clientId}`);

        state.socket.onmessage = e => {
            const msg = JSON.parse(e.data);
            if (msg.type === 'progress') {
                _updateProgress(msg.progress, msg.message);
            }
        };

        state.socket.onclose = () => {
            // Reconnect after 2 s if the socket drops unexpectedly
            setTimeout(_openWebSocket, 2000);
        };
    } catch (err) {
        console.warn('[ws] Could not open WebSocket:', err);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Folder list
// ─────────────────────────────────────────────────────────────────────────────

/** Fetch all folders from the backend and re-render the home grid + sidebar. */
async function _loadFolders() {
    try {
        state.folders = await apiFolderList();
        _renderFolderGrid();
        _renderSidebar();
    } catch (err) {
        _showToast('Ordner konnten nicht geladen werden: ' + err.message);
    }
}

/** Render the folder card grid on the home view. */
function _renderFolderGrid() {
    // Keep existing static children (empty-state element), remove old cards
    dom.folderGrid.querySelectorAll('.folder-card').forEach(c => c.remove());

    if (state.folders.length === 0) {
        dom.folderGridEmpty.classList.remove('hidden');
        return;
    }

    dom.folderGridEmpty.classList.add('hidden');

    const cards = state.folders.map(folder => _makeFolderCard(folder));
    cards.forEach(c => dom.folderGrid.appendChild(c));
    animateFolderCards(cards);
}

/** Render the compact sidebar folder list. */
function _renderSidebar() {
    dom.sidebarFolders.innerHTML = '';

    state.folders.forEach(folder => {
        const item = document.createElement('div');
        item.className = 'sidebar-folder-item';

        // Colour dot: green = has summary, purple = has markdown, grey = empty
        const color = folder.has_summary ? '#4ade80' : folder.has_markdown ? '#8b5cf6' : '#64748b';

        item.innerHTML = `
            <span class="folder-dot" style="background:${color}"></span>
            <span>${_esc(folder.name)}</span>
        `;

        if (state.currentFolder?.safe_name === folder.safe_name) {
            item.classList.add('active');
        }

        item.addEventListener('click', () => _openFolder(folder));
        dom.sidebarFolders.appendChild(item);
    });
}

/**
 * Build a folder card DOM element.
 * @param {Object} folder - FolderInfo from the backend
 * @returns {HTMLElement}
 */
function _makeFolderCard(folder) {
    const card = document.createElement('div');
    card.className = 'folder-card';
    card.dataset.safeName = folder.safe_name;

    card.innerHTML = `
        <button class="folder-card-delete" title="Ordner löschen">🗑</button>
        <div class="folder-card-type-indicator">${_esc(folder.folder_type)}</div>
        <div class="folder-card-name">${_esc(folder.name)}</div>
        <div class="folder-card-status">
            ${_statusRow(folder.has_pdf,      'PDF hochgeladen')}
            ${_statusRow(folder.has_markdown, 'Markdown konvertiert')}
            ${_statusRow(folder.has_summary,  'Zusammenfassung erstellt')}
        </div>
        <div class="folder-card-footer">
            ${new Date(folder.created_at).toLocaleDateString('de-DE', {
                day: '2-digit', month: '2-digit', year: 'numeric'
            })}
        </div>
    `;

    card.addEventListener('click', e => {
        if (!e.target.closest('.folder-card-delete')) {
            _openFolder(folder);
        }
    });

    card.querySelector('.folder-card-delete').addEventListener('click', e => {
        e.stopPropagation();
        _confirmDeleteFolder(folder, card);
    });

    return card;
}

/** Single status row in a folder card. */
function _statusRow(done, label) {
    return `
        <div class="status-row">
            <span class="status-dot ${done ? 'done' : 'pending'}"></span>
            <span>${label}</span>
        </div>
    `;
}

// ─────────────────────────────────────────────────────────────────────────────
// Navigation
// ─────────────────────────────────────────────────────────────────────────────

/** Open the detail view for a specific folder. */
function _openFolder(folder) {
    state.currentFolder = folder;

    // Update detail header
    dom.detailTypeBadge.textContent  = folder.folder_type;
    dom.detailFolderName.textContent = folder.name;

    // Update sidebar active state
    _renderSidebar();

    // Set step states
    _updateSteps(folder);

    // Restore button states
    dom.btnConvert.disabled   = !folder.has_pdf;
    dom.btnSummarize.disabled = !folder.has_markdown;

    // Update upload area text if PDF already present
    if (folder.has_pdf) {
        dom.uploadIcon.textContent = '✅';
        dom.uploadText.textContent = folder.pdf_filename || 'PDF hochgeladen';
        dom.uploadSubtext.textContent = 'Klicke um die PDF zu ersetzen';
    } else {
        dom.uploadIcon.textContent = '📄';
        dom.uploadText.textContent = 'PDF hierher ziehen oder klicken';
        dom.uploadSubtext.textContent = 'Unterstützt akademische Skripte mit Formeln, Tabellen, Code & Bildern';
    }

    // Load content previews if they exist
    dom.contentTabs.classList.toggle('hidden', !folder.has_markdown && !folder.has_summary);
    dom.panelMarkdown.classList.add('hidden');
    dom.panelSummary.classList.add('hidden');

    if (folder.has_markdown) {
        _loadMarkdownPreview();
    }
    if (folder.has_summary) {
        _loadSummaryPreview();
        // Default to showing summary if both exist
        _switchTab('summary');
    } else if (folder.has_markdown) {
        _switchTab('markdown');
    }

    // Switch views
    dom.viewHome.classList.add('hidden');
    dom.viewFolder.classList.remove('hidden');
    animateViewIn(dom.viewFolder);
}

/** Return to the home view. */
function _goHome() {
    animateViewOut(dom.viewFolder, () => {
        dom.viewFolder.classList.add('hidden');
        dom.viewHome.classList.remove('hidden');
        animateViewIn(dom.viewHome);
        state.currentFolder = null;
        _renderSidebar();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Step indicator helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Update which step circles are marked complete / active. */
function _updateSteps(folder) {
    _setStep(dom.stepUpload,   folder.has_pdf);
    _setStep(dom.stepConvert,  folder.has_markdown);
    _setStep(dom.stepSummarize,folder.has_summary);
}

function _setStep(el, complete) {
    el.classList.toggle('complete', complete);
    el.classList.toggle('active',   !complete);
}

// ─────────────────────────────────────────────────────────────────────────────
// PDF Upload
// ─────────────────────────────────────────────────────────────────────────────

/** Trigger the hidden file input. */
function _triggerFileChooser() {
    dom.fileInput.click();
}

/** Handle a file picked from the file chooser or drag-and-drop. */
async function _handleFileSelected(file) {
    if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
        _showToast('Bitte wähle eine PDF-Datei aus.');
        return;
    }

    if (!state.currentFolder) return;

    try {
        dom.uploadIcon.textContent = '⏳';
        dom.uploadText.textContent = `Lade hoch: ${file.name}…`;

        await apiUploadPdf(state.currentFolder.safe_name, file);

        dom.uploadIcon.textContent = '✅';
        dom.uploadText.textContent = file.name;
        dom.uploadSubtext.textContent = 'Klicke um die PDF zu ersetzen';

        // Update local state and UI
        state.currentFolder.has_pdf = true;
        state.currentFolder.pdf_filename = file.name;

        dom.btnConvert.disabled = false;
        _setStep(dom.stepUpload, true);
        await _loadFolders();   // refresh sidebar dot colour

        _showToast('PDF erfolgreich hochgeladen!');
    } catch (err) {
        dom.uploadIcon.textContent = '❌';
        dom.uploadText.textContent = 'Upload fehlgeschlagen';
        _showToast('Upload-Fehler: ' + err.message);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// PDF Conversion
// ─────────────────────────────────────────────────────────────────────────────

/** Convert the uploaded PDF to Markdown. */
async function _convertPdf() {
    if (!state.currentFolder || state.operationRunning) return;

    state.operationRunning = true;
    dom.btnConvert.disabled   = true;
    dom.btnSummarize.disabled = true;

    _showProgress('Konvertiere PDF in Markdown…');

    try {
        await apiConvertPdf(state.currentFolder.safe_name, state.clientId);

        state.currentFolder.has_markdown = true;
        _setStep(dom.stepConvert, true);
        animateStepComplete(dom.stepConvert.querySelector('.step-circle'));

        dom.btnSummarize.disabled = false;
        dom.contentTabs.classList.remove('hidden');
        await _loadMarkdownPreview();
        _switchTab('markdown');

        await _loadFolders();
        _showToast('Konvertierung abgeschlossen!');
    } catch (err) {
        const cancelled = err.message?.toLowerCase().includes('abgebrochen');
        _showToast(cancelled ? 'Konvertierung abgebrochen.' : 'Konvertierungsfehler: ' + err.message);
    } finally {
        _hideProgress();
        state.operationRunning = false;
        dom.btnConvert.disabled = false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// AI Summarization
// ─────────────────────────────────────────────────────────────────────────────

/** Summarize the converted Markdown with Ollama. */
async function _summarize() {
    if (!state.currentFolder || state.operationRunning) return;

    state.operationRunning = true;
    dom.btnSummarize.disabled = true;
    dom.btnConvert.disabled   = true;

    _showProgress('Erstelle KI-Zusammenfassung…');

    try {
        await apiSummarize(state.currentFolder.safe_name, state.clientId);

        state.currentFolder.has_summary = true;
        _setStep(dom.stepSummarize, true);

        const circle = dom.stepSummarize.querySelector('.step-circle');
        animateStepComplete(circle);
        animateParticleBurst(circle);   // celebration burst!

        dom.contentTabs.classList.remove('hidden');
        await _loadSummaryPreview();
        _switchTab('summary');

        await _loadFolders();
        _showToast('Zusammenfassung erstellt! 🎉');
    } catch (err) {
        const cancelled = err.message?.toLowerCase().includes('abgebrochen');
        _showToast(cancelled ? 'Zusammenfassung abgebrochen.' : 'Zusammenfassungsfehler: ' + err.message);
    } finally {
        _hideProgress();
        state.operationRunning = false;
        dom.btnConvert.disabled   = !state.currentFolder?.has_pdf;
        dom.btnSummarize.disabled = !state.currentFolder?.has_markdown;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress bar
// ─────────────────────────────────────────────────────────────────────────────

function _showProgress(label) {
    dom.progressLabel.textContent = label;
    dom.progressPercent.textContent = '0%';
    dom.progressFill.style.width = '0%';
    dom.progressMessage.textContent = '';
    dom.progressContainer.classList.remove('hidden');
    dom.btnStop.classList.remove('hidden');
    // Reset log panel to collapsed state for each new operation
    _clearLog();
    _logVisible = false;
    dom.logPanelWrapper.classList.add('hidden');
    dom.logToggle.classList.remove('active');
    dom.logToggleIcon.textContent = '▶ Log';
}

function _hideProgress() {
    dom.progressContainer.classList.add('hidden');
    dom.btnStop.classList.add('hidden');
}

/** Receive a progress update from the WebSocket message handler. */
function _updateProgress(percent, message) {
    dom.progressPercent.textContent = `${percent}%`;
    dom.progressFill.style.width    = `${percent}%`;
    dom.progressMessage.textContent = message;
    _appendLog(percent, message);
}

// ─────────────────────────────────────────────────────────────────────────────
// Live Log
// ─────────────────────────────────────────────────────────────────────────────

let _logVisible = false;

function _initLogToggle() {
    dom.logToggle.addEventListener('click', () => {
        _logVisible = !_logVisible;
        dom.logPanelWrapper.classList.toggle('hidden', !_logVisible);
        dom.logToggle.classList.toggle('active', _logVisible);
        dom.logToggleIcon.textContent = _logVisible ? '▼ Log' : '▶ Log';
        if (_logVisible) dom.logPanel.scrollTop = dom.logPanel.scrollHeight;
    });
    dom.logClear.addEventListener('click', _clearLog);
}

function _clearLog() {
    dom.logPanel.innerHTML = '';
}

function _appendLog(percent, message) {
    const now = new Date();
    const time = now.toTimeString().slice(0, 8);

    // Classify message type for colour coding
    let cls = 'log-info';
    if (percent === 100)                   cls = 'log-success';
    else if (message.startsWith('✓'))      cls = 'log-success';
    else if (message.startsWith('↳'))      cls = 'log-detail';
    else if (message.startsWith('⚠'))      cls = 'log-warn';
    else if (message.startsWith('KI:'))    cls = 'log-ai';
    else if (message.startsWith('FEHLER')) cls = 'log-warn';

    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML =
        `<span class="log-time">${time} [${String(percent).padStart(3)}%]</span>` +
        `<span class="log-msg ${cls}">${_escapeHtml(message)}</span>`;

    dom.logPanel.appendChild(entry);

    // Auto-scroll only if already near the bottom
    const panel = dom.logPanel;
    const nearBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60;
    if (nearBottom) panel.scrollTop = panel.scrollHeight;
}

function _escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─────────────────────────────────────────────────────────────────────────────
// Vision provider tab switch
// ─────────────────────────────────────────────────────────────────────────────

function _switchAiProviderTab(provider) {
    $('ai-provider-tabs').querySelectorAll('.vision-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.provider === provider);
    });
    $('ai-content-ollama').classList.toggle('hidden', provider !== 'ollama');
    $('ai-content-claude').classList.toggle('hidden', provider !== 'claude');
}

function _activeAiProvider() {
    const active = $('ai-provider-tabs').querySelector('.vision-tab.active');
    return active ? active.dataset.provider : 'ollama';
}

function _switchVisionTab(provider) {
    $('vision-tabs').querySelectorAll('.vision-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.provider === provider);
    });
    $('vision-content-ollama').classList.toggle('hidden', provider !== 'ollama');
    $('vision-content-claude').classList.toggle('hidden', provider !== 'claude');
}

function _activeVisionProvider() {
    const active = $('vision-tabs').querySelector('.vision-tab.active');
    return active ? active.dataset.provider : 'ollama';
}

// ─────────────────────────────────────────────────────────────────────────────
// Confirm modal (delete / close / stop)
// ─────────────────────────────────────────────────────────────────────────────

let _confirmCallback = null;

function _openConfirmModal({ icon = '⚠', title, message, confirmLabel = 'Bestätigen', onConfirm }) {
    dom.confirmIcon.textContent    = icon;
    dom.confirmTitle.textContent   = title;
    dom.confirmMessage.textContent = message;
    dom.btnConfirmOk.textContent   = confirmLabel;
    _confirmCallback = onConfirm;
    dom.modalConfirm.classList.remove('hidden');
}

function _closeConfirmModal() {
    dom.modalConfirm.classList.add('hidden');
    _confirmCallback = null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Stop running operation
// ─────────────────────────────────────────────────────────────────────────────

async function _stopOperation() {
    try {
        await apiCancel(state.clientId);
        _appendLog(0, '⚠ Abbruch wurde angefordert – warte auf Chunk-Ende…');
    } catch { /* ignore */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// Close app with confirmation
// ─────────────────────────────────────────────────────────────────────────────

function _requestClose() {
    const msg = state.operationRunning
        ? 'Ein Vorgang läuft gerade. Trotzdem beenden?\nDer aktuelle Prozess wird abgebrochen.'
        : 'StudyScript AI wirklich beenden?';
    _openConfirmModal({
        icon:         '⏻',
        title:        'App beenden',
        message:      msg,
        confirmLabel: 'Beenden',
        onConfirm:    () => window.electronAPI.close(),
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Content preview
// ─────────────────────────────────────────────────────────────────────────────

async function _loadMarkdownPreview() {
    try {
        const { content } = await apiGetMarkdown(state.currentFolder.safe_name);
        dom.markdownViewer.innerHTML = marked.parse(content);
    } catch { /* not critical */ }
}

async function _loadSummaryPreview() {
    try {
        const { content } = await apiGetSummary(state.currentFolder.safe_name);
        dom.summaryViewer.innerHTML = marked.parse(content);
    } catch { /* not critical */ }
}

/** Switch between the Markdown and Summary content tabs. */
function _switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    dom.panelMarkdown.classList.toggle('hidden', tabName !== 'markdown');
    dom.panelSummary.classList.toggle('hidden',  tabName !== 'summary');

    if (tabName === 'markdown') animateViewIn(dom.panelMarkdown);
    if (tabName === 'summary')  animateViewIn(dom.panelSummary);
}

// ─────────────────────────────────────────────────────────────────────────────
// Create folder modal
// ─────────────────────────────────────────────────────────────────────────────

function _openCreateModal() {
    dom.inputFolderName.value = '';
    dom.modalCreate.classList.remove('hidden');
    animateModalOpen(dom.modalCreate, dom.modalCreate.querySelector('.modal'));
    setTimeout(() => dom.inputFolderName.focus(), 50);
}

function _closeCreateModal() {
    const modal = dom.modalCreate.querySelector('.modal');
    animateModalClose(dom.modalCreate, modal, () => {
        dom.modalCreate.classList.add('hidden');
    });
}

async function _confirmCreateFolder() {
    const name = dom.inputFolderName.value.trim();
    if (!name) {
        dom.inputFolderName.focus();
        return;
    }

    try {
        const folder = await apiFolderCreate(name, 'Lernfach');
        state.folders.unshift(folder);
        _closeCreateModal();

        // Re-render grid and animate the new card
        _renderFolderGrid();
        _renderSidebar();

        // Animate the specific new card in
        const newCard = dom.folderGrid.querySelector(`[data-safe-name="${folder.safe_name}"]`);
        if (newCard) animateCardAppear(newCard);

        _showToast(`Ordner "${name}" erstellt!`);
    } catch (err) {
        _showToast('Fehler: ' + err.message);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Delete folder
// ─────────────────────────────────────────────────────────────────────────────

function _confirmDeleteFolder(folder, cardEl) {
    _openConfirmModal({
        icon:         '🗑',
        title:        'Ordner löschen',
        message:      `"${folder.name}" und alle Inhalte (PDF, Markdown, Zusammenfassung) werden unwiderruflich gelöscht.`,
        confirmLabel: 'Löschen',
        onConfirm:    () => {
            animateCardRemove(cardEl, async () => {
                try {
                    await apiFolderDelete(folder.safe_name);
                    state.folders = state.folders.filter(f => f.safe_name !== folder.safe_name);
                    cardEl.remove();
                    _renderFolderGrid();
                    _renderSidebar();
                    if (state.currentFolder?.safe_name === folder.safe_name) _goHome();
                    _showToast(`Ordner "${folder.name}" gelöscht.`);
                } catch (err) {
                    _showToast('Löschen fehlgeschlagen: ' + err.message);
                }
            });
        },
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings panel
// ─────────────────────────────────────────────────────────────────────────────

async function _openSettings() {
    if (!state.currentFolder) return;

    dom.settingsOverlay.classList.remove('hidden');
    animateSettingsOpen(dom.settingsOverlay, dom.settingsPanel);

    // Load models and current settings in parallel
    const [models, settings] = await Promise.all([
        apiGetOllamaModels().catch(() => []),
        apiGetSettings(state.currentFolder.safe_name).catch(() => ({})),
    ]);

    // Populate model selector
    dom.settingsModel.innerHTML = models.length
        ? models.map(m => `<option value="${_esc(m.name)}">${_esc(m.name)} (${m.size_gb} GB)</option>`).join('')
        : '<option value="">Keine Modelle gefunden</option>';

    // AI provider tabs
    const aiProvider = settings.ai_provider ?? 'ollama';
    _switchAiProviderTab(aiProvider);
    if (settings.ollama_model) dom.settingsModel.value = settings.ollama_model;
    if (settings.claude_model) dom.settingsClaudeModel.value = settings.claude_model;

    dom.settingsPrompt.value       = settings.system_prompt    ?? '';
    dom.settingsChunkSize.value    = settings.chunk_size       ?? 3000;
    dom.settingsTemperature.value  = settings.temperature      ?? 0.3;
    dom.tempDisplay.textContent    = settings.temperature      ?? 0.3;

    // Length radio
    const lengthVal = settings.summary_length ?? 'medium';
    const radioEl = document.querySelector(`input[name="summary-length"][value="${lengthVal}"]`);
    if (radioEl) radioEl.checked = true;

    // Toggles
    $('include-images').checked   = settings.include_images   ?? true;
    $('include-tables').checked   = settings.include_tables   ?? true;
    $('include-formulas').checked = settings.include_formulas ?? true;
    $('include-code').checked     = settings.include_code     ?? true;

    // Vision
    const visionOn       = settings.use_vision ?? false;
    const visionProvider = settings.vision_provider ?? 'ollama';
    dom.useVision.checked = visionOn;
    dom.visionConfig.classList.toggle('hidden', !visionOn);
    dom.settingsVisionModel.value  = settings.vision_model        ?? 'llama3.2-vision:11b';
    dom.settingsClaudeVision.value = settings.claude_vision_model ?? 'claude-haiku-4-5-20251001';
    _switchVisionTab(visionProvider);
}

function _closeSettings() {
    animateSettingsClose(dom.settingsOverlay, dom.settingsPanel, () => {
        dom.settingsOverlay.classList.add('hidden');
    });
}

async function _saveSettings() {
    if (!state.currentFolder) return;

    const selectedLength = document.querySelector('input[name="summary-length"]:checked');

    const settings = {
        ai_provider:      _activeAiProvider(),
        ollama_model:     dom.settingsModel.value,
        claude_model:     dom.settingsClaudeModel.value,
        system_prompt:    dom.settingsPrompt.value,
        summary_length:   selectedLength ? selectedLength.value : 'medium',
        temperature:      parseFloat(dom.settingsTemperature.value),
        chunk_size:       parseInt(dom.settingsChunkSize.value, 10),
        include_images:   $('include-images').checked,
        include_tables:   $('include-tables').checked,
        include_formulas: $('include-formulas').checked,
        include_code:     $('include-code').checked,
        use_vision:          dom.useVision.checked,
        vision_provider:     _activeVisionProvider(),
        vision_model:        dom.settingsVisionModel.value.trim() || 'llama3.2-vision:11b',
        claude_vision_model: dom.settingsClaudeVision.value || 'claude-haiku-4-5-20251001',
    };

    try {
        await apiSaveSettings(state.currentFolder.safe_name, settings);
        _closeSettings();
        _showToast('Einstellungen gespeichert!');
    } catch (err) {
        _showToast('Speichern fehlgeschlagen: ' + err.message);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast notifications
// ─────────────────────────────────────────────────────────────────────────────

function _showToast(message) {
    dom.toastMessage.textContent = message;
    dom.toast.classList.remove('hidden');
    animateToast(dom.toast, 3000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Event wiring
// ─────────────────────────────────────────────────────────────────────────────

function _wireUIEvents() {

    // Navigation
    $('nav-home').addEventListener('click',  _goHome);
    $('btn-back').addEventListener('click',  _goHome);
    $('btn-create-folder').addEventListener('click', _openCreateModal);

    // Modal
    $('btn-modal-cancel').addEventListener('click',  _closeCreateModal);
    $('btn-modal-confirm').addEventListener('click', _confirmCreateFolder);
    dom.inputFolderName.addEventListener('keydown', e => {
        if (e.key === 'Enter') _confirmCreateFolder();
        if (e.key === 'Escape') _closeCreateModal();
    });

    // Click-outside to close modal
    dom.modalCreate.addEventListener('click', e => {
        if (e.target === dom.modalCreate) _closeCreateModal();
    });

    // Upload area
    dom.uploadArea.addEventListener('click', _triggerFileChooser);
    dom.btnUpload.addEventListener('click', e => { e.stopPropagation(); _triggerFileChooser(); });

    dom.fileInput.addEventListener('change', () => {
        if (dom.fileInput.files[0]) _handleFileSelected(dom.fileInput.files[0]);
    });

    // Drag & drop
    dom.uploadArea.addEventListener('dragover', e => {
        e.preventDefault();
        dom.uploadArea.classList.add('drag-over');
    });
    dom.uploadArea.addEventListener('dragleave', () => {
        dom.uploadArea.classList.remove('drag-over');
    });
    dom.uploadArea.addEventListener('drop', e => {
        e.preventDefault();
        dom.uploadArea.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) _handleFileSelected(file);
    });

    // Action buttons
    dom.btnConvert.addEventListener('click',   _convertPdf);
    dom.btnSummarize.addEventListener('click', _summarize);

    // Content tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => _switchTab(btn.dataset.tab));
    });

    // Settings
    $('btn-open-settings').addEventListener('click',  _openSettings);
    $('btn-close-settings').addEventListener('click', _closeSettings);
    $('btn-settings-save').addEventListener('click',  _saveSettings);
    $('btn-settings-reset').addEventListener('click', async () => {
        if (!state.currentFolder) return;
        // Reload defaults by re-opening the panel (backend will return defaults)
        _closeSettings();
        await new Promise(r => setTimeout(r, 400));
        _openSettings();
    });

    // Click-outside to close settings
    dom.settingsOverlay.addEventListener('click', e => {
        if (e.target === dom.settingsOverlay) _closeSettings();
    });

    // Temperature slider live display
    dom.settingsTemperature.addEventListener('input', () => {
        dom.tempDisplay.textContent = parseFloat(dom.settingsTemperature.value).toFixed(2);
    });

    // Vision toggle shows/hides the config block
    dom.useVision.addEventListener('change', () => {
        dom.visionConfig.classList.toggle('hidden', !dom.useVision.checked);
    });

    // AI provider tabs
    $('ai-provider-tabs').querySelectorAll('.vision-tab').forEach(tab => {
        tab.addEventListener('click', () => _switchAiProviderTab(tab.dataset.provider));
    });

    // Vision provider tabs
    $('vision-tabs').querySelectorAll('.vision-tab').forEach(tab => {
        tab.addEventListener('click', () => _switchVisionTab(tab.dataset.provider));
    });

    // Live Log toggle
    _initLogToggle();

    // Stop button
    dom.btnStop.addEventListener('click', () => {
        _openConfirmModal({
            icon:         '■',
            title:        'Vorgang abbrechen',
            message:      'Den laufenden Vorgang wirklich abbrechen? Bereits verarbeitete Abschnitte gehen verloren.',
            confirmLabel: 'Abbrechen',
            onConfirm:    _stopOperation,
        });
    });

    // Confirm modal buttons
    dom.btnConfirmOk.addEventListener('click', () => {
        const cb = _confirmCallback;
        _closeConfirmModal();
        if (cb) cb();
    });
    dom.btnConfirmCancel.addEventListener('click', _closeConfirmModal);
    dom.modalConfirm.addEventListener('click', e => {
        if (e.target === dom.modalConfirm) _closeConfirmModal();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

/** Escape HTML special characters to prevent XSS when inserting user content. */
function _esc(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Generate a short random ID for the WebSocket channel. */
function _makeId() {
    return Math.random().toString(36).slice(2, 10);
}

// ─────────────────────────────────────────────────────────────────────────────
// Bootstrap
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);
