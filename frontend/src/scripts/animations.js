/**
 * Reusable animation helpers built on top of anime.js.
 *
 * All functions follow the same pattern:
 *   - accept a DOM element (or selector string)
 *   - return the anime.js animation instance so callers can await/chain if needed
 *
 * anime.js reference: https://animejs.com/documentation/
 */

// ─────────────────────────────────────────────────────────────────────────────
// View transitions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fade + slide a view section into view.
 * Used when switching between home and folder-detail.
 *
 * @param {HTMLElement} el - The section to animate in
 */
function animateViewIn(el) {
    el.style.opacity = '0';
    el.style.transform = 'translateY(18px)';

    return anime({
        targets: el,
        opacity:          [0, 1],
        translateY:       [18, 0],
        duration:         380,
        easing:           'easeOutCubic',
    });
}

/**
 * Fade + slide a view section out of view.
 * Call this before hiding an element.
 *
 * @param {HTMLElement} el       - The section to animate out
 * @param {Function}    [onDone] - Optional callback fired after animation
 */
function animateViewOut(el, onDone) {
    return anime({
        targets:    el,
        opacity:    [1, 0],
        translateY: [0, -12],
        duration:   220,
        easing:     'easeInCubic',
        complete:   onDone,
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Folder cards
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Staggered entrance for all folder cards on the home screen.
 * Cards fly in from below with a cascading delay.
 *
 * @param {NodeList|Array} cards - Collection of card elements
 */
function animateFolderCards(cards) {
    anime({
        targets:    cards,
        opacity:    [0, 1],
        translateY: [30, 0],
        scale:      [0.95, 1],
        duration:   420,
        delay:      anime.stagger(60, { start: 80 }),
        easing:     'easeOutBack',
    });
}

/**
 * Pop-in animation for a single freshly created card.
 *
 * @param {HTMLElement} card - The card element
 */
function animateCardAppear(card) {
    card.style.opacity = '0';
    card.style.transform = 'scale(0.8)';

    return anime({
        targets:  card,
        opacity:  [0, 1],
        scale:    [0.8, 1],
        duration: 350,
        easing:   'easeOutBack',
    });
}

/**
 * Shrink + fade a card before it is removed from the DOM.
 *
 * @param {HTMLElement} card    - The card element to remove
 * @param {Function}    onDone  - Called after animation (remove element here)
 */
function animateCardRemove(card, onDone) {
    return anime({
        targets:  card,
        opacity:  [1, 0],
        scale:    [1, 0.85],
        duration: 260,
        easing:   'easeInCubic',
        complete: onDone,
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Modal
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Animate the create-folder modal into view.
 * Overlay fades in; modal card bounces up.
 *
 * @param {HTMLElement} overlay - The modal overlay wrapper
 * @param {HTMLElement} modal   - The inner modal card
 */
function animateModalOpen(overlay, modal) {
    overlay.style.opacity = '0';
    modal.style.transform = 'scale(0.88) translateY(20px)';
    modal.style.opacity   = '0';

    anime({
        targets:  overlay,
        opacity:  [0, 1],
        duration: 200,
        easing:   'linear',
    });

    return anime({
        targets:   modal,
        opacity:   [0, 1],
        scale:     [0.88, 1],
        translateY:[20, 0],
        duration:  340,
        easing:    'easeOutBack',
    });
}

/**
 * Animate the modal out of view.
 *
 * @param {HTMLElement} overlay - The modal overlay wrapper
 * @param {HTMLElement} modal   - The inner modal card
 * @param {Function}    onDone  - Called after animation (hide element here)
 */
function animateModalClose(overlay, modal, onDone) {
    anime({
        targets:  modal,
        opacity:  [1, 0],
        scale:    [1, 0.92],
        duration: 200,
        easing:   'easeInCubic',
    });

    return anime({
        targets:  overlay,
        opacity:  [1, 0],
        duration: 250,
        easing:   'linear',
        complete: onDone,
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings panel
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Slide the settings panel in from the right edge.
 *
 * @param {HTMLElement} overlay - The semi-transparent overlay
 * @param {HTMLElement} panel   - The settings drawer
 */
function animateSettingsOpen(overlay, panel) {
    anime({
        targets:  overlay,
        opacity:  [0, 1],
        duration: 250,
        easing:   'linear',
    });

    panel.classList.add('open');   // CSS transition handles the translateX
}

/**
 * Slide the settings panel back out to the right.
 *
 * @param {HTMLElement} overlay - The semi-transparent overlay
 * @param {HTMLElement} panel   - The settings drawer
 * @param {Function}    onDone  - Called after the CSS transition ends
 */
function animateSettingsClose(overlay, panel, onDone) {
    anime({
        targets:  overlay,
        opacity:  [1, 0],
        duration: 250,
        easing:   'linear',
    });

    panel.classList.remove('open');

    // Wait for the CSS transition (350 ms) before hiding
    panel.addEventListener('transitionend', onDone, { once: true });
}

// ─────────────────────────────────────────────────────────────────────────────
// Step indicators
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Animate the step circle when a step becomes complete.
 * Plays a quick bounce/scale pulse.
 *
 * @param {HTMLElement} circleEl - The .step-circle element
 */
function animateStepComplete(circleEl) {
    return anime({
        targets:  circleEl,
        scale:    [1, 1.35, 1],
        duration: 450,
        easing:   'easeOutElastic(1, .5)',
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Show the toast notification, hold for holdMs, then fade out.
 *
 * @param {HTMLElement} toast  - The toast element
 * @param {number}      holdMs - How long to keep it visible (ms)
 */
function animateToast(toast, holdMs = 2800) {
    // In case a previous animation is still running
    anime.remove(toast);

    anime({
        targets:     toast,
        opacity:     [0, 1],
        translateY:  [12, 0],
        duration:    280,
        easing:      'easeOutCubic',
        complete: () => {
            setTimeout(() => {
                anime({
                    targets:    toast,
                    opacity:    [1, 0],
                    translateY: [0, 8],
                    duration:   280,
                    easing:     'easeInCubic',
                    complete:   () => toast.classList.add('hidden'),
                });
            }, holdMs);
        },
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Loading screen exit
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fade the loading screen out and fade the main app in.
 *
 * @param {HTMLElement} loadingEl - The #loading-screen element
 * @param {HTMLElement} appEl     - The #app element
 */
function animateAppReady(loadingEl, appEl) {
    appEl.classList.remove('hidden');
    appEl.style.opacity = '0';

    anime({
        targets:  loadingEl,
        opacity:  [1, 0],
        duration: 400,
        easing:   'easeInCubic',
        complete: () => loadingEl.classList.add('hidden'),
    });

    anime({
        targets:  appEl,
        opacity:  [0, 1],
        duration: 500,
        delay:    200,
        easing:   'easeOutCubic',
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Particle burst (decorative – fires when a summary is created)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Create a burst of small circles emanating from a target element.
 * Pure CSS/anime – no canvas needed.
 *
 * @param {HTMLElement} origin - Element around which particles appear
 */
function animateParticleBurst(origin) {
    const rect = origin.getBoundingClientRect();
    const cx = rect.left + rect.width  / 2;
    const cy = rect.top  + rect.height / 2;

    const colors = ['#8b5cf6', '#a78bfa', '#22d3ee', '#4ade80', '#fbbf24'];

    for (let i = 0; i < 14; i++) {
        const dot = document.createElement('div');
        Object.assign(dot.style, {
            position:     'fixed',
            width:        '8px',
            height:       '8px',
            borderRadius: '50%',
            background:   colors[i % colors.length],
            left:         `${cx}px`,
            top:          `${cy}px`,
            pointerEvents:'none',
            zIndex:       '999',
        });
        document.body.appendChild(dot);

        const angle  = (i / 14) * Math.PI * 2;
        const radius = 60 + Math.random() * 60;

        anime({
            targets:  dot,
            left:     cx + Math.cos(angle) * radius,
            top:      cy + Math.sin(angle) * radius,
            opacity:  [1, 0],
            scale:    [1, 0.2],
            duration: 700 + Math.random() * 300,
            easing:   'easeOutCubic',
            complete: () => dot.remove(),
        });
    }
}
