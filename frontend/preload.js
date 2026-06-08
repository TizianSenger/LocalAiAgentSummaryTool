/**
 * Electron preload script.
 *
 * This file runs in a privileged context that has access to Node.js, but we
 * deliberately expose only a minimal, typed API to the renderer process via
 * contextBridge. This prevents the renderer (which may load external content)
 * from ever touching Node.js APIs directly.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // ── Custom title bar ──────────────────────────────────────────────────
    minimize: () => ipcRenderer.send('window:minimize'),
    maximize: () => ipcRenderer.send('window:maximize'),
    close:    () => ipcRenderer.send('window:close'),

    // ── Backend lifecycle events ──────────────────────────────────────────
    /** Called once the Python backend health-check succeeds. */
    onBackendReady: cb => ipcRenderer.on('backend:ready', cb),

    /** Called if the backend fails to start within the timeout. */
    onBackendError: cb => ipcRenderer.on('backend:error', (_, msg) => cb(msg)),

    // ── Shell helpers ─────────────────────────────────────────────────────
    openExternal: url  => ipcRenderer.invoke('shell:openExternal', url),
    openPath:     path => ipcRenderer.invoke('shell:openPath', path),
});
