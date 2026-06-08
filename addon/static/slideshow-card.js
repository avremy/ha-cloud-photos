// cloud_photos slideshow card — version: 9
// Served by HA from /local/cloud_photos/slideshow-card.js (deployed by the
// add-on at startup). Register as a Lovelace resource with cache buster ?v=1
// (the URL itself is new at cutover; the cache buster restarts at 1).
// v9: image list JSON now exposes both `images` (back-compat flat URLs) and
// `photos` (rich entries with full/thumb/mtime). We continue to read `images`
// for the slideshow — full-screen display doesn't benefit from thumbnails.
class SlideshowCard extends HTMLElement {
  set hass(hass) { this._hass = hass; }

  setConfig(config) {
    this._config = config;
    this._imageListUrl = config.image_list_url || '/local/cloud_photos/_image_list.json';
    this._interval = (config.interval || 8) * 1000;
    this._returnPath = config.return_path || '/dashboard-test/0';
    this._galleryUrl = config.gallery_url || '/slideshow/gallery';
    this._images = [];
    this._currentIndex = 0;
    this._useA = true;
    this._paused = false;
    this._controlsVisible = true;
    this._hideTimeout = null;
    this._currentUrl = '';

    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block; position: fixed !important;
          top: 0; left: 0; right: 0; bottom: 0;
          z-index: 999; background: #000;
          -webkit-user-select: none; user-select: none;
        }
        .slide {
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          background-size: contain; background-position: center;
          background-repeat: no-repeat; background-color: #000;
          opacity: 0; transition: opacity 2s ease-in-out;
        }
        .slide.active { opacity: 1; }
        .clock {
          position: absolute; bottom: 24px; right: 32px;
          color: rgba(255,255,255,0.7); font-family: -apple-system, sans-serif;
          font-size: 2.2em; font-weight: 200; letter-spacing: 2px;
          text-shadow: 0 2px 8px rgba(0,0,0,0.8);
          pointer-events: none; z-index: 10;
        }
        .clock .date {
          font-size: 0.4em; font-weight: 300; opacity: 0.8;
          margin-top: 4px; text-align: right;
        }
        .controls {
          position: absolute; top: 0; left: 0; right: 0; bottom: 0;
          z-index: 20; pointer-events: none;
          opacity: 1; transition: opacity 0.5s ease;
        }
        .controls.hidden { opacity: 0; }
        .controls.hidden .btn { pointer-events: none; }
        .btn {
          pointer-events: auto; cursor: pointer;
          background: rgba(0,0,0,0.25); border: none; color: rgba(255,255,255,0.7);
          font-family: -apple-system, sans-serif;
          transition: background 0.2s, opacity 0.2s;
          -webkit-tap-highlight-color: transparent;
          outline: none;
        }
        .btn:active { background: rgba(0,0,0,0.5); }
        .btn-left, .btn-right {
          position: absolute; top: 50%; transform: translateY(-50%);
          width: 64px; height: 120px; border-radius: 12px;
          font-size: 28px; display: flex; align-items: center; justify-content: center;
        }
        .btn-left { left: 16px; }
        .btn-right { right: 16px; }
        .btn-pause {
          position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%);
          width: 56px; height: 56px; border-radius: 50%;
          font-size: 22px; display: flex; align-items: center; justify-content: center;
        }
        .btn-back {
          position: absolute; top: 16px; left: 16px;
          width: 44px; height: 44px; border-radius: 50%;
          font-size: 18px; display: flex; align-items: center; justify-content: center;
        }
        .btn-gallery {
          position: absolute; top: 16px; right: 16px;
          width: 44px; height: 44px; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          text-decoration: none;
        }
        .btn-gallery svg { width: 22px; height: 22px; fill: currentColor; }
        .counter {
          position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%);
          color: rgba(255,255,255,0.45); font-family: -apple-system, sans-serif;
          font-size: 11px; font-weight: 300; pointer-events: none;
          text-shadow: 0 1px 4px rgba(0,0,0,0.6);
        }
        .filename {
          position: absolute; top: 16px; left: 50%; transform: translateX(-50%);
          color: rgba(255,255,255,0.5); font-family: -apple-system, monospace;
          font-size: 12px; font-weight: 300; pointer-events: none;
          text-shadow: 0 1px 4px rgba(0,0,0,0.8);
          max-width: 80%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          text-align: center;
        }
        .tap-zone {
          position: absolute; top: 0; left: 80px; right: 80px; bottom: 0;
          z-index: 15; cursor: pointer;
        }
      </style>

      <div id="container">
        <div class="slide" id="slideA"></div>
        <div class="slide" id="slideB"></div>
      </div>

      <div class="tap-zone" id="tapZone"></div>

      <div class="controls" id="controls">
        <button class="btn btn-back" id="btnBack">✕</button>
        <button class="btn btn-gallery" id="btnGallery" title="Open gallery">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16V4c0-1.1-.9-2-2-2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2m-11-4 2.03 2.71L16 11l4 5H8m-4 2v2H4c-1.1 0-2-.9-2-2V7h2v13h13v2z"/></svg>
        </button>
        <div class="filename" id="filename"></div>
        <button class="btn btn-left" id="btnLeft">‹</button>
        <button class="btn btn-right" id="btnRight">›</button>
        <button class="btn btn-pause" id="btnPause">❚❚</button>
        <div class="counter" id="counter"></div>
      </div>

      <div class="clock">
        <div id="time"></div>
        <div class="date" id="date"></div>
      </div>
    `;

    this.shadowRoot.getElementById('btnBack').addEventListener('click', (e) => { e.stopPropagation(); this._goBack(); });
    this.shadowRoot.getElementById('btnGallery').addEventListener('click', (e) => { e.stopPropagation(); this._openGallery(); });
    this.shadowRoot.getElementById('btnLeft').addEventListener('click', (e) => { e.stopPropagation(); this._prev(); this._flashControls(); });
    this.shadowRoot.getElementById('btnRight').addEventListener('click', (e) => { e.stopPropagation(); this._next(); this._flashControls(); });
    this.shadowRoot.getElementById('btnPause').addEventListener('click', (e) => { e.stopPropagation(); this._togglePause(); this._flashControls(); });
    this.shadowRoot.getElementById('tapZone').addEventListener('click', (e) => { e.stopPropagation(); this._toggleControls(); });

    this._loadImages().then(() => {
      if (this._images.length > 0) {
        this._showNext();
        this._slideTimer = setInterval(() => { if (!this._paused) this._showNext(); }, this._interval);
      }
      this._updateCounter();
    });
    this._updateClock();
    this._clockTimer = setInterval(() => this._updateClock(), 30000);
    this._scheduleHide();
  }

  _scheduleHide() {
    if (this._hideTimeout) clearTimeout(this._hideTimeout);
    this._hideTimeout = setTimeout(() => {
      this.shadowRoot.getElementById('controls').classList.add('hidden');
      this._controlsVisible = false;
    }, 5000);
  }

  _flashControls() {
    this.shadowRoot.getElementById('controls').classList.remove('hidden');
    this._controlsVisible = true;
    this._scheduleHide();
  }

  _toggleControls() {
    const el = this.shadowRoot.getElementById('controls');
    if (this._controlsVisible) {
      el.classList.add('hidden');
      this._controlsVisible = false;
      if (this._hideTimeout) clearTimeout(this._hideTimeout);
    } else {
      el.classList.remove('hidden');
      this._controlsVisible = true;
      this._scheduleHide();
    }
  }

  _goBack() {
    if (this._slideTimer) clearInterval(this._slideTimer);
    if (this._clockTimer) clearInterval(this._clockTimer);
    if (this._hideTimeout) clearTimeout(this._hideTimeout);
    history.pushState(null, '', this._returnPath);
    window.dispatchEvent(new Event('location-changed'));
  }

  _openGallery() {
    if (this._slideTimer) clearInterval(this._slideTimer);
    if (this._clockTimer) clearInterval(this._clockTimer);
    if (this._hideTimeout) clearTimeout(this._hideTimeout);
    history.pushState(null, '', this._galleryUrl);
    window.dispatchEvent(new Event('location-changed'));
  }

  _togglePause() {
    this._paused = !this._paused;
    this.shadowRoot.getElementById('btnPause').textContent = this._paused ? '▶' : '❚❚';
  }

  _next() { this._showNext(); }

  _prev() {
    if (this._images.length === 0) return;
    this._currentIndex = (this._currentIndex - 2 + this._images.length) % this._images.length;
    this._showSlide(this._images[this._currentIndex]);
    this._currentIndex++;
  }

  _showNext() {
    if (this._images.length === 0) return;
    const url = this._images[this._currentIndex % this._images.length];
    this._showSlide(url);
    this._currentIndex++;
    if (this._currentIndex % this._images.length === 0) this._loadImages();
  }

  _showSlide(url) {
    const slideA = this.shadowRoot.getElementById('slideA');
    const slideB = this.shadowRoot.getElementById('slideB');
    const active = this._useA ? slideA : slideB;
    const inactive = this._useA ? slideB : slideA;
    active.style.backgroundImage = `url("${url}")`;
    active.classList.add('active');
    inactive.classList.remove('active');
    this._useA = !this._useA;
    this._currentUrl = url;
    this._updateCounter();
    this._updateFilename();
  }

  _updateFilename() {
    const el = this.shadowRoot.getElementById('filename');
    if (el && this._currentUrl) {
      const parts = this._currentUrl.split('/');
      el.textContent = parts[parts.length - 1];
    }
  }

  _updateCounter() {
    const el = this.shadowRoot.getElementById('counter');
    if (el && this._images.length > 0) {
      const idx = ((this._currentIndex - 1) % this._images.length + this._images.length) % this._images.length + 1;
      el.textContent = idx + ' / ' + this._images.length;
    }
  }

  async _loadImages() {
    try {
      const r = await fetch(this._imageListUrl + '?t=' + Date.now());
      const data = await r.json();
      if (data.images && data.images.length > 0) {
        this._images = data.images;
        for (let i = this._images.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [this._images[i], this._images[j]] = [this._images[j], this._images[i]];
        }
      }
    } catch(e) { console.error('Slideshow: failed to load images', e); }
  }

  _updateClock() {
    const now = new Date();
    const h = now.getHours().toString().padStart(2, '0');
    const m = now.getMinutes().toString().padStart(2, '0');
    const t = this.shadowRoot.getElementById('time');
    const d = this.shadowRoot.getElementById('date');
    if (t) t.textContent = h + ':' + m;
    if (d) d.textContent = now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  }

  getCardSize() { return 0; }
  static getStubConfig() {
    return { image_list_url: '/local/cloud_photos/_image_list.json', interval: 8, return_path: '/dashboard-test/0' };
  }
}
customElements.define('slideshow-card', SlideshowCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'slideshow-card', name: 'Slideshow Card', description: 'Fullscreen photo slideshow with controls.' });
