/**
 * Electron main process for StudyScript AI.
 *
 * Responsibilities:
 *  1. Spawn the Python FastAPI backend as a child process
 *  2. Poll the backend's /health endpoint until it responds
 *  3. Open the BrowserWindow and load the frontend HTML
 *  4. Handle custom title-bar IPC messages (minimize / maximize / close)
 *  5. Kill the backend when the app quits
 */

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

const BACKEND_PORT = 8000;
const BACKEND_URL  = `http://127.0.0.1:${BACKEND_PORT}`;
const BACKEND_SCRIPT = path.join(__dirname, '..', 'backend', 'main.py');

let mainWindow    = null;
let pythonProcess = null;

// ---------------------------------------------------------------------------
// Backend lifecycle
// ---------------------------------------------------------------------------

/**
 * Resolve the Python executable and arguments to use.
 * Returns { exe, args } so spawn() gets them separately.
 *
 * spawn() does NOT use a shell, so 'py -3.11' as a single string fails –
 * the executable must be 'py' and '-3.11' must be a separate argument.
 *
 * Priority: py -3.11 → py -3.12 → py -3.13 → plain py → python3 → python
 */
function findPython() {
    const { execSync } = require('child_process');

    if (process.platform === 'win32') {
        // Try versioned Windows py-launcher entries
        for (const ver of ['3.11', '3.12', '3.13']) {
            try {
                execSync(`py -${ver} --version`, { stdio: 'ignore' });
                return { exe: 'py', args: [`-${ver}`] };
            } catch { /* version not installed */ }
        }
        // Fallback: unversioned py, then python
        for (const exe of ['py', 'python3', 'python']) {
            try {
                execSync(`${exe} --version`, { stdio: 'ignore' });
                return { exe, args: [] };
            } catch { }
        }
    } else {
        for (const exe of ['python3.11', 'python3.12', 'python3', 'python']) {
            try {
                execSync(`${exe} --version`, { stdio: 'ignore' });
                return { exe, args: [] };
            } catch { }
        }
    }

    return { exe: 'py', args: [] }; // last resort
}

/**
 * Start the Python FastAPI backend.
 * stdout/stderr are piped so logs appear in the Electron dev console.
 */
function startBackend() {
    const { exe, args } = findPython();

    pythonProcess = spawn(exe, [...args, BACKEND_SCRIPT], {
        cwd: path.dirname(BACKEND_SCRIPT),
        stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonProcess.stdout.on('data', d => process.stdout.write(`[backend] ${d}`));
    pythonProcess.stderr.on('data', d => process.stderr.write(`[backend] ${d}`));

    pythonProcess.on('exit', code => {
        console.log(`[backend] Prozess beendet (exit code ${code})`);
    });
}

/**
 * Poll GET /health every 500 ms until the backend responds with HTTP 200.
 * Resolves when ready; rejects after maxWaitMs (default 60 s).
 *
 * @param {number} maxWaitMs
 * @returns {Promise<void>}
 */
function waitForBackend(maxWaitMs = 60_000) {
    return new Promise((resolve, reject) => {
        const deadline = Date.now() + maxWaitMs;

        const probe = () => {
            http.get(`${BACKEND_URL}/health`, res => {
                if (res.statusCode === 200) return resolve();
                schedule();
            }).on('error', schedule);
        };

        const schedule = () => {
            if (Date.now() >= deadline) {
                reject(new Error('Backend hat nicht innerhalb des Timeouts geantwortet.'));
            } else {
                setTimeout(probe, 500);
            }
        };

        probe();
    });
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

function createWindow() {
    mainWindow = new BrowserWindow({
        width:           1440,
        height:          900,
        minWidth:        960,
        minHeight:       640,
        backgroundColor: '#0d0d1a',
        frame:           false,          // custom title bar drawn in HTML/CSS
        titleBarStyle:   'hidden',
        show:            false,          // prevent white flash while loading
        webPreferences: {
            preload:          path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration:  false,
            webSecurity:      true,
        },
    });

    mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));

    // Show window only once content is painted
    mainWindow.once('ready-to-show', () => mainWindow.show());

    mainWindow.on('closed', () => { mainWindow = null; });
}

// ---------------------------------------------------------------------------
// IPC – custom title bar controls
// ---------------------------------------------------------------------------

ipcMain.on('window:minimize',  () => mainWindow?.minimize());
ipcMain.on('window:maximize',  () => {
    mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on('window:close',     () => mainWindow?.close());

ipcMain.handle('shell:openExternal', (_, url)  => shell.openExternal(url));
ipcMain.handle('shell:openPath',     (_, path) => shell.openPath(path));

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
    startBackend();
    createWindow();

    try {
        await waitForBackend();
        mainWindow?.webContents.send('backend:ready');
    } catch (err) {
        console.error('[main] Backend-Fehler:', err.message);
        mainWindow?.webContents.send('backend:error', err.message);
    }
});

app.on('window-all-closed', () => {
    pythonProcess?.kill();
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('will-quit', () => pythonProcess?.kill());
