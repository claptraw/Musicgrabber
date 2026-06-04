        // =============================================================================
        // Release Notes
        // =============================================================================
        // RELEASE_NOTES is defined in release-notes.js, loaded before this file.
        // To update release notes for a new version, edit static/release-notes.js only.

        const releaseNotesVersions = Object.keys(RELEASE_NOTES).sort((a, b) => {
            const aParts = a.split('.').map(n => parseInt(n, 10) || 0);
            const bParts = b.split('.').map(n => parseInt(n, 10) || 0);
            const maxLen = Math.max(aParts.length, bParts.length);
            for (let i = 0; i < maxLen; i++) {
                const av = aParts[i] || 0;
                const bv = bParts[i] || 0;
                if (av !== bv) return bv - av;
            }
            return 0;
        });
        let currentReleaseNotesIndex = -1;

        function updateReleaseNotesPager() {
            const olderBtn = document.getElementById('releaseNotesOlderBtn');
            const newerBtn = document.getElementById('releaseNotesNewerBtn');
            if (!olderBtn || !newerBtn) return;

            const atNewest = currentReleaseNotesIndex <= 0;
            const atOldest = currentReleaseNotesIndex >= releaseNotesVersions.length - 1;

            olderBtn.disabled = atOldest;
            newerBtn.disabled = atNewest;
        }

        function renderReleaseNotes(index) {
            if (index < 0 || index >= releaseNotesVersions.length) return;

            const version = releaseNotesVersions[index];
            const notes = RELEASE_NOTES[version];
            if (!notes) return;

            currentReleaseNotesIndex = index;

            const overlay = document.getElementById('releaseNotesOverlay');
            const titleEl = document.getElementById('releaseNotesTitle');
            const bodyEl = document.getElementById('releaseNotesBody');

            titleEl.textContent = notes.title;

            bodyEl.innerHTML = notes.sections.map(section => {
                if (section.warning) {
                    return `<div style="margin-bottom:16px; padding:12px 14px; background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.4); border-radius:6px;">` +
                        `<div style="font-size:12px; font-weight:700; color:#f59e0b; margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;">&#9888; ${escapeHtml(section.heading)}</div>` +
                        `<p style="margin:0; color:var(--text-secondary);">${escapeHtml(section.warning)}</p>` +
                        (section.items ? `<ul style="margin:8px 0 0; padding-left:18px; color:var(--text-secondary);">${section.items.map(item => `<li style="margin-bottom:4px;">${escapeHtml(item)}</li>`).join('')}</ul>` : '') +
                        `</div>`;
                }
                let html = `<div style="margin-bottom:16px;">`;
                html += `<div style="font-size:12px; font-weight:600; color:var(--text-primary); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;">${escapeHtml(section.heading)}</div>`;
                if (section.body) {
                    html += `<p style="margin:0; color:var(--text-secondary);">${escapeHtml(section.body)}</p>`;
                }
                if (section.items) {
                    html += `<ul style="margin:0; padding-left:18px; color:var(--text-secondary);">`;
                    html += section.items.map(item => `<li style="margin-bottom:4px;">${escapeHtml(item)}</li>`).join('');
                    html += `</ul>`;
                }
                html += `</div>`;
                return html;
            }).join('');

            updateReleaseNotesPager();
            overlay.style.display = 'flex';
        }

        function showReleaseNotes(version) {
            // Strip pre-release suffixes (-dev, -beta, -rc1, etc.) so dev builds
            // show the same notes as the release they're building toward.
            const baseVersion = (version || '').replace(/[-+].+$/, '');
            const targetIndex = releaseNotesVersions.indexOf(baseVersion);
            if (targetIndex === -1) return;
            renderReleaseNotes(targetIndex);
        }

        function showOlderReleaseNotes() {
            if (currentReleaseNotesIndex < releaseNotesVersions.length - 1) {
                renderReleaseNotes(currentReleaseNotesIndex + 1);
            }
        }

        function showNewerReleaseNotes() {
            if (currentReleaseNotesIndex > 0) {
                renderReleaseNotes(currentReleaseNotesIndex - 1);
            }
        }

        function closeReleaseNotes() {
            const overlay = document.getElementById('releaseNotesOverlay');
            overlay.style.display = 'none';
            // Store base version (strip -dev etc.) so the modal doesn't reappear for
            // the same release regardless of whether it was seen on a dev or release build.
            if (serverConfig.version) {
                localStorage.setItem('seen_version', serverConfig.version.replace(/[-+].+$/, ''));
            }
        }

        // Close on backdrop click
        document.getElementById('releaseNotesOverlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeReleaseNotes();
        });

        // =============================================================================
        // Session Authentication
        // =============================================================================

        let serverConfig = {}; // Populated from /api/config at startup

        function normaliseRootPath(path) {
            path = String(path || '').trim();
            if (!path || path === '/') return '';
            return '/' + path.replace(/^\/+|\/+$/g, '');
        }

        const APP_ROOT_PATH = (() => {
            const injected = typeof window.__MUSICGRABBER_ROOT_PATH__ === 'string'
                ? window.__MUSICGRABBER_ROOT_PATH__
                : '';
            const meta = document.querySelector('meta[name="musicgrabber-root-path"]')?.content || '';
            if (injected || meta) return normaliseRootPath(injected || meta);
            const path = window.location.pathname || '/';
            return normaliseRootPath(path.replace(/\/+$/, ''));
        })();

        function withRootPath(path) {
            const value = String(path || '');
            if (!value) return APP_ROOT_PATH || '/';
            if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//')) return value;
            if (APP_ROOT_PATH && (value === APP_ROOT_PATH || value.startsWith(`${APP_ROOT_PATH}/`) || value.startsWith(`${APP_ROOT_PATH}?`))) {
                return value;
            }
            if (value.startsWith('/')) return `${APP_ROOT_PATH}${value}`;
            if (value.startsWith('?')) return `${APP_ROOT_PATH || ''}/${value}`;
            return APP_ROOT_PATH ? `${APP_ROOT_PATH}/${value.replace(/^\/+/, '')}` : `/${value.replace(/^\/+/, '')}`;
        }

        const missingTrackVersionsState = {
            playlistId: '',
            playlistName: '',
            artist: '',
            title: '',
            rowId: '',
            results: [],
        };

        function getSessionToken() {
            return localStorage.getItem('sessionToken') || '';
        }

        function setSessionToken(token) {
            if (token) localStorage.setItem('sessionToken', token);
            else localStorage.removeItem('sessionToken');
        }

        function getCurrentUser() {
            try {
                return JSON.parse(localStorage.getItem('currentUser') || 'null');
            } catch { return null; }
        }

        function setCurrentUser(user) {
            if (user) localStorage.setItem('currentUser', JSON.stringify(user));
            else localStorage.removeItem('currentUser');
        }

        function isAdmin() {
            const user = getCurrentUser();
            // In single-user mode (no users_exist), user is null but we treat as admin.
            // In session mode, check the role.
            return !user || user.role === 'admin';
        }

        function isPeon() {
            const user = getCurrentUser();
            return !!(user && user.role === 'peon');
        }

        // Namespaced localStorage key — keeps per-user preferences separate on shared browsers.
        // Session-global keys (theme, seen_version, sessionToken, etc.) are NOT namespaced.
        function userStorageKey(key) {
            const user = getCurrentUser();
            return user && user.id ? `mg_${user.id}_${key}` : `mg_${key}`;
        }

        function buildJobDownloadPath(jobId) {
            return withRootPath(`/api/jobs/${encodeURIComponent(String(jobId || ''))}/download`);
        }

        async function getJobDownloadUrl(jobId) {
            if (!serverConfig?.users_exist) return buildJobDownloadPath(jobId);
            const resp = await apiFetch('/api/auth/download-token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_id: String(jobId || '') }),
            });
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                throw new Error(data.detail || 'Unable to issue download token');
            }
            const data = await resp.json();
            return data.url || buildJobDownloadPath(jobId);
        }

        async function saveJobToDevice(jobId) {
            try {
                const url = await getJobDownloadUrl(jobId);
                const a = document.createElement('a');
                a.href = withRootPath(url);
                a.download = '';
                a.rel = 'noopener';
                a.style.display = 'none';
                document.body.appendChild(a);
                a.click();
                a.remove();
            } catch (error) {
                showToast('Failed to start download', true);
            }
        }

        async function apiFetch(url, options = {}) {
            const token = getSessionToken();
            const headers = { ...options.headers };
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }
            const response = await fetch(withRootPath(url), { ...options, headers });

            if (response.status === 401) {
                // Session expired or invalid — clear local state and show login screen
                if (serverConfig && serverConfig.users_exist) {
                    setSessionToken(null);
                    setCurrentUser(null);
                    showLoginScreen();
                }
                throw new Error('Authentication required');
            }

            if (response.status === 429) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || 'Rate limit exceeded');
            }

            return response;
        }

        // =============================================================================
        // Login / Logout / Password Change
        // =============================================================================

        function showLoginScreen() {
            document.getElementById('loginScreen').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('loginError').style.display = 'none';
            document.getElementById('loginPassword').value = '';
            setTimeout(() => document.getElementById('loginUsername').focus(), 50);
        }

        function hideLoginScreen() {
            document.getElementById('loginScreen').style.display = 'none';
            document.getElementById('app').style.display = '';
        }

        async function doLogin() {
            const username = document.getElementById('loginUsername').value.trim();
            const password = document.getElementById('loginPassword').value;
            const errorDiv = document.getElementById('loginError');
            const btn = document.getElementById('loginBtn');

            if (!username || !password) {
                errorDiv.textContent = 'Please enter username and password.';
                errorDiv.style.display = 'block';
                return;
            }

            btn.disabled = true;
            errorDiv.style.display = 'none';

            try {
                const resp = await fetch(withRootPath('/api/auth/login'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password }),
                });

                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    errorDiv.textContent = data.detail || 'Invalid username or password.';
                    errorDiv.style.display = 'block';
                    return;
                }

                const data = await resp.json();
                setSessionToken(data.token);
                setCurrentUser(data.user);
                hideLoginScreen();

                if (data.user.force_password_change) {
                    showChangePasswordScreen(true);
                    return;
                }

                // Reinitialise the app now that we're logged in
                location.reload();
            } catch (e) {
                errorDiv.textContent = 'Login failed. Please try again.';
                errorDiv.style.display = 'block';
            } finally {
                btn.disabled = false;
            }
        }

        async function doLogout() {
            try {
                const token = getSessionToken();
                if (token) {
                    await fetch(withRootPath('/api/auth/logout'), {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}` },
                    });
                }
            } catch {}
            setSessionToken(null);
            setCurrentUser(null);
            location.reload();
        }

        function showChangePasswordScreen(forced = false) {
            document.getElementById('changePasswordScreen').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('changePasswordForced').style.display = forced ? 'block' : 'none';
            document.getElementById('changePasswordError').style.display = 'none';
        }

        function hideChangePasswordScreen() {
            document.getElementById('changePasswordScreen').style.display = 'none';
            document.getElementById('app').style.display = '';
        }

        async function doChangePassword() {
            const currentPw = document.getElementById('changePasswordCurrent').value;
            const newPw = document.getElementById('changePasswordNew').value;
            const confirmPw = document.getElementById('changePasswordConfirm').value;
            const errorDiv = document.getElementById('changePasswordError');

            if (newPw !== confirmPw) {
                errorDiv.textContent = 'Passwords do not match.';
                errorDiv.style.display = 'block';
                return;
            }
            if (newPw.length < 8) {
                errorDiv.textContent = 'Password must be at least 8 characters.';
                errorDiv.style.display = 'block';
                return;
            }

            try {
                const resp = await apiFetch('/api/auth/password', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
                });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    errorDiv.textContent = data.detail || 'Failed to change password.';
                    errorDiv.style.display = 'block';
                    return;
                }
                // Update stored user to clear force_password_change
                const user = getCurrentUser();
                if (user) {
                    user.force_password_change = false;
                    setCurrentUser(user);
                }
                hideChangePasswordScreen();
                location.reload();
            } catch (e) {
                errorDiv.textContent = 'Error changing password.';
                errorDiv.style.display = 'block';
            }
        }

        // =============================================================================
        // Theme Toggle - Day and Night Modes
        // =============================================================================

        function getTheme() {
            return localStorage.getItem('theme') || 'dark';
        }

        function setTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('theme', theme);
            const btn = document.getElementById('themeToggle');
            if (btn) btn.innerHTML = theme === 'dark' ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
        }

        // Apply saved theme immediately
        setTheme(getTheme());

        document.getElementById('themeToggle').addEventListener('click', () => {
            setTheme(getTheme() === 'dark' ? 'light' : 'dark');
        });

        // =============================================================================
        // DOM Elements
        // =============================================================================

        const searchInput = document.getElementById('searchInput');
        const searchBtn = document.getElementById('searchBtn');
        const searchClearBtn = document.getElementById('searchClearBtn');

        function setSearchValue(val) {
            searchInput.value = val;
            searchClearBtn.style.display = val ? '' : 'none';
        }
        const searchHistory = document.getElementById('searchHistory');
        const relatedSuggestions = document.getElementById('relatedSuggestions');
        const resultsTab = document.getElementById('resultsTab');
        const bulkTabContainer = document.getElementById('bulkTabContainer');
        const albumsTabContainer = document.getElementById('albumsTabContainer');
        const bulkInput = document.getElementById('bulkInput');
        const bulkImportBtn = document.getElementById('bulkImportBtn');
        const bulkResults = document.getElementById('bulkResults');
        const fileUpload = document.getElementById('fileUpload');
        const fileName = document.getElementById('fileName');
        const spotifyUrlInput = document.getElementById('spotifyUrlInput');
        const playlistServicesHint = document.getElementById('playlistServicesHint');
        const playlistServicesTooltip = document.getElementById('playlistServicesTooltip');
        const fetchSpotifyBtn = document.getElementById('fetchSpotifyBtn');
        const spotifyError = document.getElementById('spotifyError');
        const queueTabContainer = document.getElementById('queueTabContainer');
        const queueTab = document.getElementById('queueTab');
        const clearQueueBtn = document.getElementById('clearQueueBtn');
        const resetStatsBtn = document.getElementById('resetStatsBtn');
        const watchedTabContainer = document.getElementById('watchedTabContainer');
        const watchedList = document.getElementById('watchedList');
        const watchedUrlInput = document.getElementById('watchedUrlInput');
        const addWatchedBtn = document.getElementById('addWatchedBtn');
        const watchedIntervalSelect = document.getElementById('watchedIntervalSelect');
        const watchedError = document.getElementById('watchedError');
        const refreshAllWatchedBtn = document.getElementById('refreshAllWatchedBtn');
        const watchedScheduleInfo = document.getElementById('watchedScheduleInfo');
        const watchedConvertToFlac = document.getElementById('watchedConvertToFlac');
        const toast = document.getElementById('toast');
        const tabs = document.querySelectorAll('.tab');
        const lineCounter = document.getElementById('lineCounter');
        const statsTabContainer = document.getElementById('statsTabContainer');
        const statsContent = document.getElementById('statsContent');
        const settingsTabContainer = document.getElementById('settingsTabContainer');
        const createPlaylistCheckbox = document.getElementById('createPlaylistCheckbox');
        const playlistNameInput = document.getElementById('playlistNameInput');
        const convertToFlacCheckbox = document.getElementById('convertToFlac');
        let watchedFlacTouched = false;

        // =============================================================================
        // Destination Picker (unified "Add to..." for Playlist and Album)
        // =============================================================================

        let _playlists = [];
        let _destinationMode = null; // null | 'playlist' | 'album'
        let _albumDirArtist = null;
        let _albumDirAlbum = null;
        let _albumInfo = null; // response from /api/albums/dirs/{artist}/{album}/info

        async function loadPlaylists() {
            try {
                const resp = await apiFetch('/api/playlists');
                if (!resp.ok) {
                    console.warn('GET /api/playlists returned', resp.status);
                    return;
                }
                const data = await resp.json();
                _playlists = data.playlists || [];
                _populatePlaylistSelector();
            } catch (e) {
                console.warn('loadPlaylists failed:', e);
            }
        }

        function _populatePlaylistSelector() {
            const sel = document.getElementById('playlistSelectorInput');
            if (!sel) return;
            const current = sel.value;
            while (sel.options.length > 1) sel.remove(1);
            for (const pl of _playlists) {
                const opt = document.createElement('option');
                opt.value = pl.name;
                opt.textContent = pl.is_watched ? `${pl.name} (watched)` : pl.name;
                opt.dataset.isWatched = pl.is_watched ? '1' : '0';
                opt.dataset.syncMode = pl.sync_mode || 'append';
                sel.appendChild(opt);
            }
            const newOpt = document.createElement('option');
            newOpt.value = '__new__';
            newOpt.textContent = '+ New playlist...';
            sel.appendChild(newOpt);
            if (current && current !== '__new__' && [...sel.options].some(o => o.value === current)) {
                sel.value = current;
            }
        }

        function getSelectedPlaylist() {
            if (_destinationMode !== 'playlist') return null;
            const panel = document.getElementById('playlistSelector');
            const sel = document.getElementById('playlistSelectorInput');
            if (!sel || !sel.value || sel.value === '__new__' || !panel || panel.style.display === 'none') return null;
            const opt = sel.options[sel.selectedIndex];
            return { name: sel.value, is_watched: opt && opt.dataset.isWatched === '1' };
        }

        function _updatePlaylistSelectorWarning() {
            const sel = document.getElementById('playlistSelectorInput');
            const warn = document.getElementById('playlistSelectorWarn');
            if (!warn || !sel) return;
            const opt = sel.options[sel.selectedIndex];
            const isMirror = opt && opt.dataset.isWatched === '1' && opt.dataset.syncMode === 'mirror';
            warn.style.display = isMirror ? 'inline' : 'none';
        }

        function getSelectedAlbumRoute() {
            if (_destinationMode !== 'album') return null;
            if (!_albumDirArtist || !_albumDirAlbum) return null;

            const base = {
                album_artist: _albumDirArtist,
                album_name: _albumDirAlbum,
                release_mbid: _albumInfo ? _albumInfo.release_mbid : null,
                track_total: _albumInfo ? ((_albumInfo.tracks || []).length || null) : null,
            };

            if (!_albumInfo || !_albumInfo.release_mbid) {
                // Folder routing only — no MBID so no track metadata enrichment
                return { ...base, mode: 'no_mbid' };
            }

            const modeSel = document.getElementById('albumRouteMode');
            const trackSel = document.getElementById('albumRouteTrackSelect');
            const mode = modeSel ? modeSel.value : 'auto';

            if (mode !== 'manual') {
                return { ...base, mode: 'auto' };
            }
            if (!trackSel || trackSel.value === '') return null;

            const idx = Number(trackSel.value);
            const track = (_albumInfo.tracks || [])[idx];
            if (!track) return null;
            let trackNum = null;
            if (track.position && /^\d+$/.test(String(track.position).trim())) {
                trackNum = Number(String(track.position).trim());
            } else {
                trackNum = idx + 1;
            }
            return { ...base, mode: 'manual', track_title: track.title, track_number: trackNum };
        }

        function _resetDestination() {
            _destinationMode = null;
            _albumDirArtist = null;
            _albumDirAlbum = null;
            _albumInfo = null;
            const toggle = document.getElementById('destinationPickerToggle');
            const modePanel = document.getElementById('destinationModePanel');
            const playlistPanel = document.getElementById('playlistSelector');
            const albumPanel = document.getElementById('albumRoutePanel');
            if (toggle) { toggle.textContent = '+ Add to...'; toggle.classList.remove('active'); }
            if (modePanel) modePanel.style.display = 'none';
            if (playlistPanel) playlistPanel.style.display = 'none';
            if (albumPanel) albumPanel.style.display = 'none';
            const sel = document.getElementById('playlistSelectorInput');
            if (sel) sel.value = '';
            _updatePlaylistSelectorWarning();
        }

        async function _loadAlbumArtists() {
            const sel = document.getElementById('albumArtistSelect');
            if (!sel) return;
            sel.innerHTML = '<option value="">Loading...</option>';
            try {
                const resp = await apiFetch('/api/albums/dirs');
                if (!resp.ok) { sel.innerHTML = '<option value="">Error loading artists</option>'; return; }
                const data = await resp.json();
                sel.innerHTML = '<option value="">Select artist...</option>';
                for (const a of (data.artists || [])) {
                    const opt = document.createElement('option');
                    opt.value = a;
                    opt.textContent = a;
                    sel.appendChild(opt);
                }
                if (!(data.artists || []).length) {
                    sel.innerHTML = '<option value="">No albums on disk yet</option>';
                }
            } catch (e) {
                sel.innerHTML = '<option value="">Error loading artists</option>';
            }
        }

        async function _loadAlbumFolders(artist) {
            const sel = document.getElementById('albumFolderSelect');
            const statusEl = document.getElementById('albumRouteStatus');
            const modeSel = document.getElementById('albumRouteMode');
            const trackSel = document.getElementById('albumRouteTrackSelect');
            const warnEl = document.getElementById('albumRouteWarn');
            _albumInfo = null;
            _albumDirAlbum = null;
            if (statusEl) statusEl.style.display = 'none';
            if (modeSel) modeSel.style.display = 'none';
            if (trackSel) trackSel.style.display = 'none';
            if (warnEl) warnEl.style.display = 'none';
            if (!sel) return;
            sel.innerHTML = '<option value="">Loading...</option>';
            sel.style.display = '';
            try {
                const resp = await apiFetch(`/api/albums/dirs/${encodeURIComponent(artist)}`);
                if (!resp.ok) { sel.innerHTML = '<option value="">Error loading albums</option>'; return; }
                const data = await resp.json();
                sel.innerHTML = '<option value="">Select album...</option>';
                for (const al of (data.albums || [])) {
                    const opt = document.createElement('option');
                    opt.value = al;
                    opt.textContent = al;
                    sel.appendChild(opt);
                }
            } catch (e) {
                sel.innerHTML = '<option value="">Error loading albums</option>';
            }
        }

        async function _loadAlbumInfo(artist, album) {
            const statusEl = document.getElementById('albumRouteStatus');
            const modeSel = document.getElementById('albumRouteMode');
            const trackSel = document.getElementById('albumRouteTrackSelect');
            const warnEl = document.getElementById('albumRouteWarn');
            _albumDirArtist = artist;
            _albumDirAlbum = album;
            _albumInfo = null;
            if (statusEl) { statusEl.textContent = 'Loading...'; statusEl.style.display = ''; }
            if (modeSel) modeSel.style.display = 'none';
            if (trackSel) trackSel.style.display = 'none';
            if (warnEl) warnEl.style.display = 'none';
            try {
                const resp = await apiFetch(
                    `/api/albums/dirs/${encodeURIComponent(artist)}/${encodeURIComponent(album)}/info`
                );
                const data = await resp.json();
                if (data.found && data.release_mbid) {
                    _albumInfo = data;
                    if (statusEl) statusEl.textContent = `${data.artist} / ${data.album}`;
                    const tracks = data.tracks || [];
                    if (trackSel) {
                        trackSel.innerHTML = tracks.map((t, idx) => {
                            const num = t.position ? `${t.position}. ` : `${idx + 1}. `;
                            return `<option value="${idx}">${escapeHtml(num + (t.title || 'Unknown'))}</option>`;
                        }).join('');
                    }
                    if (modeSel) { modeSel.style.display = ''; modeSel.value = 'auto'; }
                    if (trackSel) trackSel.style.display = 'none';
                } else {
                    if (statusEl) statusEl.textContent = `${artist} / ${album}`;
                    if (warnEl) {
                        warnEl.textContent = 'Album info not found - track metadata will not be embedded.';
                        warnEl.style.display = '';
                    }
                }
            } catch (e) {
                if (statusEl) statusEl.textContent = 'Error loading album info.';
            }
        }

        function _initPlaylistPanelInternals() {
            const sel = document.getElementById('playlistSelectorInput');
            const clearBtn = document.getElementById('playlistSelectorClear');
            const newNameInput = document.getElementById('playlistNewNameInput');
            const newNameConfirm = document.getElementById('playlistNewNameConfirm');
            if (!sel) return;

            function _showNewPlaylistInput() {
                if (newNameInput) { newNameInput.style.display = ''; newNameInput.value = ''; newNameInput.focus(); }
                if (newNameConfirm) newNameConfirm.style.display = '';
            }
            function _hideNewPlaylistInput() {
                if (newNameInput) newNameInput.style.display = 'none';
                if (newNameConfirm) newNameConfirm.style.display = 'none';
            }
            function _confirmNewPlaylist() {
                const name = (newNameInput ? newNameInput.value : '').trim();
                if (!name) { newNameInput && newNameInput.focus(); return; }
                const existing = [...sel.options].find(o => o.value.toLowerCase() === name.toLowerCase() && o.value !== '__new__');
                if (existing) { sel.value = existing.value; _hideNewPlaylistInput(); _updatePlaylistSelectorWarning(); return; }
                const newOpt = document.createElement('option');
                newOpt.value = name;
                newOpt.textContent = name;
                newOpt.dataset.isWatched = '0';
                const sentinel = [...sel.options].find(o => o.value === '__new__');
                sel.insertBefore(newOpt, sentinel || null);
                sel.value = name;
                _hideNewPlaylistInput();
                _updatePlaylistSelectorWarning();
            }

            sel.addEventListener('change', () => {
                if (sel.value === '__new__') _showNewPlaylistInput();
                else { _hideNewPlaylistInput(); _updatePlaylistSelectorWarning(); }
            });
            if (newNameConfirm) newNameConfirm.addEventListener('click', _confirmNewPlaylist);
            if (newNameInput) {
                newNameInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') { e.preventDefault(); _confirmNewPlaylist(); }
                    if (e.key === 'Escape') { sel.value = ''; _hideNewPlaylistInput(); _updatePlaylistSelectorWarning(); }
                });
            }
            if (clearBtn) clearBtn.addEventListener('click', _resetDestination);
        }

        function _initAlbumPanelInternals() {
            const artistSel = document.getElementById('albumArtistSelect');
            const albumSel = document.getElementById('albumFolderSelect');
            const modeSel = document.getElementById('albumRouteMode');
            const trackSel = document.getElementById('albumRouteTrackSelect');
            const clearBtn = document.getElementById('albumRouteClear');

            if (artistSel) {
                artistSel.addEventListener('change', () => {
                    const artist = artistSel.value;
                    if (artist) _loadAlbumFolders(artist);
                    else {
                        if (albumSel) { albumSel.innerHTML = '<option value="">Select album...</option>'; albumSel.style.display = 'none'; }
                        const statusEl = document.getElementById('albumRouteStatus');
                        if (statusEl) statusEl.style.display = 'none';
                        if (modeSel) modeSel.style.display = 'none';
                        if (trackSel) trackSel.style.display = 'none';
                        _albumDirArtist = null; _albumDirAlbum = null; _albumInfo = null;
                    }
                });
            }
            if (albumSel) {
                albumSel.addEventListener('change', () => {
                    const album = albumSel.value;
                    if (album && artistSel && artistSel.value) _loadAlbumInfo(artistSel.value, album);
                });
            }
            if (modeSel) {
                modeSel.addEventListener('change', () => {
                    if (trackSel) trackSel.style.display = modeSel.value === 'manual' ? '' : 'none';
                });
            }
            if (clearBtn) clearBtn.addEventListener('click', _resetDestination);
        }

        function initDestinationPicker() {
            const toggle = document.getElementById('destinationPickerToggle');
            const modePanel = document.getElementById('destinationModePanel');
            const playlistPanel = document.getElementById('playlistSelector');
            const albumPanel = document.getElementById('albumRoutePanel');
            if (!toggle) return;

            loadPlaylists();

            toggle.addEventListener('click', () => {
                if (_destinationMode !== null) { _resetDestination(); return; }
                if (modePanel) modePanel.style.display = 'flex';
                toggle.classList.add('active');
            });

            const destModePlaylist = document.getElementById('destModePlaylist');
            if (destModePlaylist) {
                destModePlaylist.addEventListener('click', () => {
                    _destinationMode = 'playlist';
                    if (modePanel) modePanel.style.display = 'none';
                    if (playlistPanel) playlistPanel.style.display = 'flex';
                    toggle.textContent = 'Adding to playlist';
                    loadPlaylists();
                    const sel = document.getElementById('playlistSelectorInput');
                    if (sel) sel.focus();
                });
            }

            const destModeAlbum = document.getElementById('destModeAlbum');
            if (destModeAlbum) {
                destModeAlbum.addEventListener('click', () => {
                    _destinationMode = 'album';
                    if (modePanel) modePanel.style.display = 'none';
                    if (albumPanel) albumPanel.style.display = 'flex';
                    toggle.textContent = 'Adding to album';
                    _loadAlbumArtists();
                });
            }

            const pickerClear = document.getElementById('destinationPickerClear');
            if (pickerClear) pickerClear.addEventListener('click', _resetDestination);

            _initPlaylistPanelInternals();
            _initAlbumPanelInternals();
        }

        // audioFormat tracks which format to use when conversion is on ("flac", "alac", "opus", or "mp3")
        let audioFormat = 'flac';

        function setAudioFormat(format) {
            audioFormat = ['flac', 'alac', 'opus', 'mp3'].includes(format) ? format : 'flac';

            const btnFlac = document.getElementById('formatBtnFlac');
            const btnAlac = document.getElementById('formatBtnAlac');
            const btnOpus = document.getElementById('formatBtnOpus');
            const btnMp3  = document.getElementById('formatBtnMp3');
            if (btnFlac) btnFlac.classList.toggle('active', audioFormat === 'flac');
            if (btnAlac) btnAlac.classList.toggle('active', audioFormat === 'alac');
            if (btnOpus) btnOpus.classList.toggle('active', audioFormat === 'opus');
            if (btnMp3)  btnMp3.classList.toggle('active',  audioFormat === 'mp3');

            const labels = { flac: 'FLAC', alac: 'ALAC', opus: 'Opus', mp3: 'MP3' };
            const label = labels[audioFormat];
            const headerLabel = document.getElementById('headerFormatLabel');
            if (headerLabel) headerLabel.textContent = label;

            const watchedLabel = document.getElementById('watchedFormatLabel');
            if (watchedLabel) watchedLabel.textContent = `Convert to ${label}`;
            const artistFlacLabel = document.getElementById('artistFlacLabel');
            if (artistFlacLabel) artistFlacLabel.textContent = `Convert to ${label}`;

            const alacNote = document.getElementById('alacFormatNote');
            if (alacNote) alacNote.style.display = audioFormat === 'alac' ? 'block' : 'none';
            const mp3Note = document.getElementById('mp3FormatNote');
            if (mp3Note) mp3Note.style.display = audioFormat === 'mp3' ? 'block' : 'none';

            // Show quality sub-rows only for the relevant format
            const mp3QualityRow = document.getElementById('mp3QualityRow');
            if (mp3QualityRow) mp3QualityRow.style.display = audioFormat === 'mp3' ? '' : 'none';
            const opusQualityRow = document.getElementById('opusQualityRow');
            if (opusQualityRow) opusQualityRow.style.display = audioFormat === 'opus' ? '' : 'none';
            const alacQualityRow = document.getElementById('alacQualityRow');
            if (alacQualityRow) alacQualityRow.style.display = audioFormat === 'alac' ? '' : 'none';

            // Keep hidden input in sync so settings save picks it up
            const hiddenInput = document.getElementById('settingAudioFormat');
            if (hiddenInput) hiddenInput.value = audioFormat;

            localStorage.setItem(userStorageKey('audioFormat'), audioFormat);
        }

        function setMp3Bitrate(val) {
            const valid = ['v2', 'v0', '320k', '256k', '192k', '128k'];
            if (!valid.includes(val)) val = 'v2';
            const ids = { v2: 'mp3QualityBtnV2', v0: 'mp3QualityBtnV0', '320k': 'mp3QualityBtn320',
                          '256k': 'mp3QualityBtn256', '192k': 'mp3QualityBtn192', '128k': 'mp3QualityBtn128' };
            for (const [k, id] of Object.entries(ids)) {
                const btn = document.getElementById(id);
                if (btn) btn.classList.toggle('active', k === val);
            }
            const input = document.getElementById('settingMp3Bitrate');
            if (input) input.value = val;
            localStorage.setItem(userStorageKey('mp3Bitrate'), val);
        }

        function setOpusBitrate(val) {
            const valid = ['320k', '256k', '192k', '128k', '96k'];
            if (!valid.includes(val)) val = '320k';
            const ids = { '320k': 'opusQualityBtn320', '256k': 'opusQualityBtn256',
                          '192k': 'opusQualityBtn192', '128k': 'opusQualityBtn128', '96k': 'opusQualityBtn96' };
            for (const [k, id] of Object.entries(ids)) {
                const btn = document.getElementById(id);
                if (btn) btn.classList.toggle('active', k === val);
            }
            const input = document.getElementById('settingOpusBitrate');
            if (input) input.value = val;
            localStorage.setItem(userStorageKey('opusBitrate'), val);
        }

        function setAlacBitrate(val) {
            const valid = ['lossless', '320k', '256k', '192k', '128k'];
            if (!valid.includes(val)) val = 'lossless';
            const ids = { 'lossless': 'alacQualityBtnLossless', '320k': 'alacQualityBtn320',
                          '256k': 'alacQualityBtn256', '192k': 'alacQualityBtn192', '128k': 'alacQualityBtn128' };
            for (const [k, id] of Object.entries(ids)) {
                const btn = document.getElementById(id);
                if (btn) btn.classList.toggle('active', k === val);
            }
            const input = document.getElementById('settingAlacBitrate');
            if (input) input.value = val;
            localStorage.setItem(userStorageKey('alacBitrate'), val);
        }

        const versionLabel = document.getElementById('versionLabel');
        const PLAYLIST_SERVICES = ["Spotify", "YouTube", "Apple Music", "Amazon Music", "SoundCloud", "ListenBrainz"];

        function renderPlaylistServicesText() {
            const text = PLAYLIST_SERVICES.join(", ");
            if (playlistServicesHint) {
                playlistServicesHint.textContent = `Supported services: ${text}`;
            }
            if (playlistServicesTooltip) {
                playlistServicesTooltip.title = `Supported services: ${text}`;
            }
        }

        renderPlaylistServicesText();

        // Load config from server then handle auth and app initialisation
        (async function initApp() {
            let config = null;
            try {
                const resp = await fetch(withRootPath('/api/config'));
                if (resp.ok) config = await resp.json();
            } catch {}

            if (config) {
                serverConfig = config;

                // Set version label
                if (config.version) {
                    versionLabel.textContent = `v${config.version}`;
                    // Show release notes once when the version changes (new install or update).
                    // Compare base versions (strip -dev etc.) so dev builds trigger correctly.
                    const baseVersion = config.version.replace(/[-+].+$/, '');
                    const seenVersion = localStorage.getItem('seen_version');
                    if (seenVersion !== baseVersion && RELEASE_NOTES[baseVersion]) {
                        showReleaseNotes(config.version);
                    }
                }

                // Set convert on/off from server if not saved locally
                if (localStorage.getItem(userStorageKey('convertToFlac')) === null && typeof config.default_convert_to_flac === 'boolean') {
                    convertToFlacCheckbox.checked = config.default_convert_to_flac;
                    if (watchedConvertToFlac && !watchedFlacTouched) {
                        watchedConvertToFlac.checked = config.default_convert_to_flac;
                    }
                }

                // Set audio format picker from server if not saved locally
                if (localStorage.getItem(userStorageKey('audioFormat')) === null && config.audio_format) {
                    setAudioFormat(config.audio_format);
                }

                // Multi-user: check if we need a login screen
                if (config.users_exist) {
                    const token = getSessionToken();
                    if (!token) {
                        showLoginScreen();
                        return;
                    }
                    // Validate existing session
                    try {
                        const meResp = await apiFetch('/api/auth/me');
                        if (!meResp.ok) {
                            setSessionToken(null);
                            setCurrentUser(null);
                            showLoginScreen();
                            return;
                        }
                        const user = await meResp.json();
                        setCurrentUser(user);
                        if (user.force_password_change) {
                            showChangePasswordScreen(true); // forced
                            return;
                        }
                    } catch {
                        showLoginScreen();
                        return;
                    }
                }

                // Show warning if music directory isn't mounted
                if (config.volume_mounted === false) {
                    showVolumeMountWarning();
                }

                // Show Playlists folder toggle in watched playlist add form if configured
                if (config.playlists_subdir) {
                    const toggle = document.getElementById('watchedPlaylistsDirToggle');
                    if (toggle) toggle.style.display = 'flex';
                    const watchedUsePlaylistsDir = document.getElementById('watchedUsePlaylistsDir');
                    if (watchedUsePlaylistsDir) watchedUsePlaylistsDir.checked = true;
                }
            }

            // Single-user mode OR successful session — apply role-based UI
            applyUserRoleToUI();
            populateSourceChips();
        })();

        // Restore convert on/off from localStorage (namespaced per user)
        const savedFlacPref = localStorage.getItem(userStorageKey('convertToFlac'));
        if (savedFlacPref !== null) {
            convertToFlacCheckbox.checked = savedFlacPref === 'true';
        }

        // Restore audio format picker from localStorage (namespaced per user)
        const savedAudioFormat = localStorage.getItem(userStorageKey('audioFormat'));
        if (savedAudioFormat) {
            setAudioFormat(savedAudioFormat);
        }

        if (watchedConvertToFlac) {
            watchedConvertToFlac.addEventListener('change', () => {
                watchedFlacTouched = true;
            });
        }

        // Save convert preference when header toggle is flipped
        convertToFlacCheckbox.addEventListener('change', () => {
            localStorage.setItem(userStorageKey('convertToFlac'), convertToFlacCheckbox.checked);
            if (watchedConvertToFlac && !watchedFlacTouched) {
                watchedConvertToFlac.checked = convertToFlacCheckbox.checked;
            }
        });

        let currentTab = 'results';
        let downloadingIds = new Set();
        let lastResults = [];
        let currentSearchToken = 0;
        let pendingSlskdToken = 0;
        let currentArtworkUrl = null;
        let currentArtworkArtist = null;
        let currentArtworkTitle = null;
        let currentSearchLogToken = null;
        let currentSource = 'all'; // Always search all sources
        const expandedJobIds = new Set();
        const watchedRefreshPending = new Map();
        let activeTagEditor = null;

        // Source selector - restore saved preference and wire up clicks
        (function initSourceSelector() {
            const buttons = document.querySelectorAll('#sourceSelector .source-option');
            buttons.forEach(btn => {
                if (btn.dataset.source === currentSource) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
                btn.addEventListener('click', () => {
                    buttons.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    currentSource = btn.dataset.source;
                    localStorage.setItem(userStorageKey('searchSource'), currentSource);
                });
            });
        })();
        const MAX_QUEUE_SIZE = 100;
        const QUEUE_PAGE_SIZE = 10;
        let queuePage = 1;
        let allQueueJobs = [];
        let currentBulkImportId = null;
        let bulkImportPollInterval = null;
        let queuePollInterval = null;
        let watchedRefreshPollInterval = null;
        let watchedLoadInFlight = false;
        const tagEditorOverlay = document.getElementById('tagEditorOverlay');
        const tagEditorArtist = document.getElementById('tagEditorArtist');
        const tagEditorTitle = document.getElementById('tagEditorTitle');
        const tagEditorAlbum = document.getElementById('tagEditorAlbum');
        const tagEditorAlbumArtist = document.getElementById('tagEditorAlbumArtist');
        const tagEditorYear = document.getElementById('tagEditorYear');
        const tagEditorTrackNumber = document.getElementById('tagEditorTrackNumber');
        const tagEditorTrackTotal = document.getElementById('tagEditorTrackTotal');
        const tagEditorMeta = document.getElementById('tagEditorMeta');
        const tagEditorPreview = document.getElementById('tagEditorPreview');
        const tagEditorError = document.getElementById('tagEditorError');
        const tagEditorGuessBtn = document.getElementById('tagEditorGuessBtn');
        const tagEditorResetBtn = document.getElementById('tagEditorResetBtn');
        const tagEditorSaveBtn = document.getElementById('tagEditorSaveBtn');

        // Preview state
        const previewAudio = document.getElementById('previewAudio');
        let hoverTimeout = null;
        let currentPreviewId = null;
        let previewCache = new Map(); // Cache preview URLs
        const MAX_PREVIEW_CACHE = 100;
        const HOVER_DELAY = 2000; // 2 seconds before preview starts
        let _previewFadeInterval = null;
        const PREVIEW_FADE_DURATION = 5000; // ms to ramp from 0 to target volume

        const previewVolume = 0.8; // Fixed preview volume - fade-in handles the ramp

        // Preview functions
        function startHoverTimer(videoId, element, result) {
            clearHoverTimer();
            hoverTimeout = setTimeout(() => {
                startPreview(videoId, element, result);
            }, HOVER_DELAY);
        }

        function clearHoverTimer() {
            if (hoverTimeout) {
                clearTimeout(hoverTimeout);
                hoverTimeout = null;
            }
        }

        async function startPreview(videoId, element, result) {
            // Don't preview if already downloading
            if (downloadingIds.has(videoId)) return;

            // Stop any current preview
            stopPreview();

            element.classList.add('loading-preview');

            try {
                let audioUrl = previewCache.get(videoId);

                if (!audioUrl) {
                    // Build preview URL with source params
                    const previewSource = (result && result.source) || 'youtube';
                    const params = new URLSearchParams({ source: previewSource });
                    if ((previewSource === 'soundcloud' || previewSource === 'mp3phoenix' || previewSource === 'zvu4no' || previewSource === 'freemp3cloud' || previewSource === 'monochrome') && result.source_url) {
                        params.set('url', result.source_url);
                    }
                    const response = await apiFetch(`/api/preview/${encodeURIComponent(videoId)}?${params}`);
                    if (!response.ok) throw new Error('Failed to get preview');

                    const data = await response.json();
                    audioUrl = data.url;
                    previewCache.set(videoId, audioUrl);
                    if (previewCache.size > MAX_PREVIEW_CACHE) {
                        const oldestKey = previewCache.keys().next().value;
                        previewCache.delete(oldestKey);
                    }
                }

                element.classList.remove('loading-preview');
                element.classList.add('previewing');
                currentPreviewId = videoId;

                previewAudio.src = audioUrl;
                previewAudio.volume = 0;
                previewAudio.play().catch(() => {
                    // Autoplay blocked or other error
                    stopPreview();
                });

                // Fade in to target volume over PREVIEW_FADE_DURATION
                if (_previewFadeInterval) clearInterval(_previewFadeInterval);
                const fadeSteps = 30;
                const fadeStepMs = PREVIEW_FADE_DURATION / fadeSteps;
                let step = 0;
                _previewFadeInterval = setInterval(() => {
                    step++;
                    previewAudio.volume = Math.min(previewVolume, (step / fadeSteps) * previewVolume);
                    if (step >= fadeSteps) clearInterval(_previewFadeInterval);
                }, fadeStepMs);


            } catch (error) {
                element.classList.remove('loading-preview');
                // Silently fail - preview is a nice-to-have
            }
        }

        function stopPreview() {
            clearHoverTimer();
            if (_previewFadeInterval) { clearInterval(_previewFadeInterval); _previewFadeInterval = null; }
            previewAudio.pause();
            previewAudio.src = '';
            currentPreviewId = null;

            // Remove previewing class from all items
            document.querySelectorAll('.result-item.previewing').forEach(item => {
                item.classList.remove('previewing');
            });
            document.querySelectorAll('.result-item.loading-preview').forEach(item => {
                item.classList.remove('loading-preview');
            });
        }

        // Search history management
        function getSearchHistory() {
            const history = localStorage.getItem(userStorageKey('searchHistory'));
            return history ? JSON.parse(history) : [];
        }

        function saveSearchHistory(query) {
            let history = getSearchHistory();
            // Remove duplicates
            history = history.filter(q => q !== query);
            // Add to front
            history.unshift(query);
            // Keep last 10
            history = history.slice(0, 10);
            localStorage.setItem(userStorageKey('searchHistory'), JSON.stringify(history));
        }

        function showSearchHistory() {
            const history = getSearchHistory();
            if (history.length === 0) {
                searchHistory.classList.remove('show');
                return;
            }

            searchHistory.innerHTML = history.map(q => `
                <div class="history-item" data-query="${escapeHtml(q)}">
                    ${escapeHtml(q)}
                </div>
            `).join('');

            searchHistory.querySelectorAll('.history-item').forEach(item => {
                item.addEventListener('click', () => {
                    setSearchValue(item.dataset.query);
                    searchHistory.classList.remove('show');
                    search();
                });
            });

            searchHistory.classList.add('show');
        }

        function hideSearchHistory() {
            setTimeout(() => {
                searchHistory.classList.remove('show');
            }, 200);
        }

        // Tab switching
        const allTabPanels = [resultsTab, bulkTabContainer, albumsTabContainer, watchedTabContainer, queueTabContainer, statsTabContainer, settingsTabContainer];
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentTab = tab.dataset.tab;

                // Remove visible from all panels, then re-add on the next frame so the
                // transition actually fires (display:none -> display:block needs a tick to paint)
                allTabPanels.forEach(p => p.classList.remove('tab-visible'));
                const panelMap = {
                    results: resultsTab,
                    bulk: bulkTabContainer,
                    albums: albumsTabContainer,
                    watched: watchedTabContainer,
                    queue: queueTabContainer,
                    stats: statsTabContainer,
                    settings: settingsTabContainer
                };
                const target = panelMap[currentTab];
                if (target) target.classList.add('tab-visible');

                // Stop preview when leaving results tab
                if (currentTab !== 'results') {
                    stopPreview();
                }

                if (currentTab === 'queue') {
                    loadJobs();
                    loadDownloadable();
                    loadTrash();
                    if (queuePollInterval) clearInterval(queuePollInterval);
                    queuePollInterval = setInterval(() => loadJobs(false), 3000);
                } else {
                    if (queuePollInterval) {
                        clearInterval(queuePollInterval);
                        queuePollInterval = null;
                    }
                    stopLibraryPlayback();
                }

                if (currentTab === 'watched') {
                    loadWatchedPlaylists();
                    loadWatchedArtists();
                    loadWatchedUpgrades();
                    populateSourceChips();
                } else if (watchedRefreshPollInterval) {
                    clearInterval(watchedRefreshPollInterval);
                    watchedRefreshPollInterval = null;
                }

                if (currentTab === 'bulk') {
                    populateSourceChips();
                }

                if (currentTab === 'stats') {
                    loadStats();
                }

                if (currentTab === 'settings') {
                    loadSettings();
                    loadBlacklist();
                    if (isAdmin()) loadUsers();
                }

                // Show floating save bar only on settings tab
                const floatingBar = document.getElementById('settingsFloatingBar');
                if (floatingBar) floatingBar.style.display = currentTab === 'settings' ? 'flex' : 'none';
            });
        });

        // Search
        let lastSourceHealthToastAt = 0;

        function showUnavailableSourcesToast(sources) {
            if (!Array.isArray(sources) || sources.length === 0) return;
            const now = Date.now();
            if (now - lastSourceHealthToastAt < 30000) return;
            lastSourceHealthToastAt = now;
            const first = sources[0];
            const retryMs = first.retry_at ? Math.max(0, first.retry_at * 1000 - now) : 0;
            const retryText = retryMs ? `, retrying in ~${Math.max(1, Math.ceil(retryMs / 60000))} min` : '';
            const moreText = sources.length > 1 ? ` (+${sources.length - 1} more)` : '';
            showToast(`${first.label || first.id} unavailable${moreText}${retryText}`);
        }

        async function search() {
            if (searchBtn.disabled) return;
            const query = searchInput.value.trim();
            if (!query) return;
            const searchToken = ++currentSearchToken;
            currentSearchLogToken = null;

            // Save to history
            saveSearchHistory(query);
            hideSearchHistory();

            searchBtn.disabled = true;
            resultsTab.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            relatedSuggestions.style.display = 'none';
            exploreBar.style.display = 'none';
            const _destRow1 = document.getElementById('destinationPickerRow');
            if (_destRow1) _destRow1.style.display = 'none';
            _exploreOriginalResults = null;
            _exploreResolvedResults = null;
            currentArtworkUrl = null;
            currentArtworkArtist = null;
            currentArtworkTitle = null;
            stopPreview(); // Stop any playing preview

            try {
                const response = await apiFetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query, limit: 15, source: currentSource })
                });

                if (!response.ok) throw new Error('Search failed');

                const data = await response.json();
                if (searchToken !== currentSearchToken) {
                    return;
                }
                lastResults = data.results;
                currentSearchLogToken = data.search_token || null;
                showUnavailableSourcesToast(data.unavailable_sources);
                renderResults(data.results);
                showRelatedSuggestions(data.results, data.album_suggestion);

                // Fire artwork fetch for "Artist - Title" queries
                const artworkParsed = parseArtistTitle(query);
                if (artworkParsed) {
                    fetchAndApplyArtwork(artworkParsed.artist, artworkParsed.title, searchToken);
                }

                // If slskd is enabled and we're searching YouTube or All, fetch slskd results too
                if (data.slskd_enabled && (currentSource === 'youtube' || currentSource === 'all')) {
                    pendingSlskdToken = searchToken;
                    // Show a small indicator so the user knows SLK is still working
                    const slskdIndicator = document.createElement('div');
                    slskdIndicator.id = 'slskd-searching';
                    slskdIndicator.className = 'slskd-searching-indicator';
                    slskdIndicator.innerHTML = '<span class="watched-refresh-spinner"></span> Searching Soulseek&hellip;';
                    resultsTab.appendChild(slskdIndicator);
                    fetchSlskdResults(query, searchToken);
                }
            } catch (error) {
                resultsTab.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon"><i class="fa-solid fa-circle-exclamation"></i></div>
                        <p>Search failed. Try again.</p>
                    </div>
                `;
                showToast('Search failed', true);
            } finally {
                searchBtn.disabled = false;
            }
        }

        async function fetchSlskdResults(query, searchToken) {
            try {
                const response = await apiFetch('/api/search/slskd', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query, limit: 15 })
                });

                if (!response.ok) {
                    document.getElementById('slskd-searching')?.remove();
                    return;
                }

                const data = await response.json();
                if (searchToken !== currentSearchToken || pendingSlskdToken !== searchToken) {
                    return;
                }
                if (data.results && data.results.length > 0) {
                    mergeSlskdResults(data.results);
                } else {
                    document.getElementById('slskd-searching')?.remove();
                }
            } catch (error) {
                console.log('slskd search failed:', error);
                document.getElementById('slskd-searching')?.remove();
            } finally {
                if (pendingSlskdToken === searchToken) {
                    pendingSlskdToken = 0;
                }
            }
        }

        function mergeSlskdResults(slskdResults) {
            // Interleave slskd results with existing YouTube results
            // Insert 1 slskd result after every 2 YouTube results
            const merged = [];
            let ytIndex = 0;
            let slskdIndex = 0;

            while (ytIndex < lastResults.length || slskdIndex < slskdResults.length) {
                // Add 2 YouTube results
                for (let i = 0; i < 2 && ytIndex < lastResults.length; i++) {
                    merged.push(lastResults[ytIndex++]);
                }
                // Add 1 slskd result
                if (slskdIndex < slskdResults.length) {
                    merged.push(slskdResults[slskdIndex++]);
                }
            }

            lastResults = merged;
            renderResults(merged);
            // Re-apply artwork after the re-render wipes the thumbnails
            if (currentArtworkUrl && currentArtworkArtist && currentArtworkTitle) {
                applyArtworkToResults(currentArtworkArtist, currentArtworkTitle, currentArtworkUrl);
            }
        }

        function parseArtistTitle(query) {
            const match = query.match(/^(.+?)\s+-\s+(.+)$/);
            if (!match) return null;
            return { artist: match[1].trim(), title: match[2].trim() };
        }

        async function fetchAndApplyArtwork(artist, title, searchToken) {
            try {
                const resp = await apiFetch(
                    `/api/search/artwork?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`
                );
                if (!resp.ok || searchToken !== currentSearchToken) return;
                const data = await resp.json();
                if (!data.url) return;
                currentArtworkUrl = data.url;
                currentArtworkArtist = artist;
                currentArtworkTitle = title;
                applyArtworkToResults(artist, title, data.url);
            } catch (e) {
                // Artwork is purely cosmetic, never fail the search over it
            }
        }

        function applyArtworkToResults(queryArtist, queryTitle, artworkUrl) {
            const norm = s => s.toLowerCase().replace(/[^a-z0-9\s]/g, '').replace(/\s+/g, ' ').trim();
            const normArtist = norm(queryArtist);
            const normTitle = norm(queryTitle);

            resultsTab.querySelectorAll('.result-item').forEach((item, index) => {
                const result = lastResults[index];
                if (!result) return;

                // Only fill in blank placeholders, leave source thumbnails alone
                const thumb = item.querySelector('.result-thumb');
                if (!thumb || thumb.tagName === 'IMG') return;

                const resultArtist = norm(result.artist || result.channel || '');
                const resultTitle = norm(result.title || '');
                const artistMatch = resultArtist.includes(normArtist) || normArtist.includes(resultArtist);
                const titleMatch = resultTitle.includes(normTitle) || normTitle.includes(resultTitle);

                if (artistMatch && titleMatch) {
                    const img = document.createElement('img');
                    img.className = 'result-thumb has-artwork';
                    img.src = artworkUrl;
                    img.alt = '';
                    img.loading = 'lazy';
                    thumb.replaceWith(img);
                }
            });
        }

        // Show related search suggestions based on artists, plus album suggestion if available
        function showRelatedSuggestions(results, albumSuggestion) {
            if (!results || results.length === 0) return;

            // Extract unique artists/channels
            const artists = [...new Set(results.map(r => r.channel))].slice(0, 5);

            if (artists.length === 0 && !albumSuggestion) return;

            // Album suggestion section (shown first when the backend found a matching album)
            let albumHtml = '';
            if (albumSuggestion) {
                albumHtml = `
                    <div class="related-title">Artist and Album</div>
                    <div class="suggestion-chips">
                        <button class="suggestion-chip album-suggestion-chip"
                            data-artist="${escapeAttr(albumSuggestion.artist_name)}"
                            data-artist-mbid="${escapeAttr(albumSuggestion.artist_mbid)}"
                            data-album="${escapeAttr(albumSuggestion.album_title)}"
                            data-release-mbid="${escapeAttr(albumSuggestion.release_mbid)}">
                            <i class="fa-solid fa-compact-disc"></i> ${escapeHtml(albumSuggestion.artist_name)} \u2013 ${escapeHtml(albumSuggestion.album_title)}
                        </button>
                    </div>
                `;
            }

            let artistHtml = '';
            if (artists.length > 0) {
                artistHtml = `
                    <div class="related-title">Related searches</div>
                    <div class="suggestion-chips">
                        ${artists.map(artist => `
                            <button class="suggestion-chip" data-query="${escapeHtml(artist)}">
                                ${escapeHtml(artist)}
                            </button>
                        `).join('')}
                    </div>
                `;
            }

            relatedSuggestions.innerHTML = `<div class="related-suggestions">${albumHtml}${artistHtml}</div>`;

            // Wire artist chip click handlers
            relatedSuggestions.querySelectorAll('.suggestion-chip:not(.album-suggestion-chip)').forEach(chip => {
                chip.addEventListener('click', () => {
                    setSearchValue(chip.dataset.query);
                    search();
                });
            });

            // Wire album suggestion chip click handler
            const albumChip = relatedSuggestions.querySelector('.album-suggestion-chip');
            if (albumChip) {
                albumChip.addEventListener('click', () => {
                    openAlbumFromSearch({
                        artistName: albumChip.dataset.artist,
                        albumTitle: albumChip.dataset.album,
                        artistMbid: albumChip.dataset.artistMbid,
                        releaseMbid: albumChip.dataset.releaseMbid,
                    });
                });
            }

            relatedSuggestions.style.display = 'block';
        }

        // -----------------------------------------------------------------------
        // Explore similar artists via ListenBrainz
        // -----------------------------------------------------------------------

        let _exploreOriginalResults = null;  // stash so Back restores them
        let _exploreResolvedResults = null;  // the actual search results from the explore run

        const exploreBar = document.getElementById('exploreBar');

        async function exploreSimilar(artist) {
            if (!artist) return;
            stopPreview();

            // Stash current results so "Back" can restore them
            _exploreOriginalResults = lastResults ? [...lastResults] : [];

            // Show the explore bar with a loading state
            _showExploreBar(artist, null);

            // Show a spinner in results while we fetch
            resultsTab.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            relatedSuggestions.style.display = 'none';

            let tracks;
            const MAX_RETRIES = 3;
            const RETRY_DELAY_MS = 1500;
            let lastError;
            for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
                if (attempt > 0) await new Promise(r => setTimeout(r, RETRY_DELAY_MS));
                try {
                    const resp = await apiFetch('/api/explore/similar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ artist, mode: 'easy', limit: 25 })
                    });
                    if (!resp.ok) throw new Error('Could not reach similar-artists service');
                    const data = await resp.json();
                    tracks = data.artists || [];
                    lastError = null;
                    break;
                } catch (e) {
                    lastError = e;
                }
            }
            if (lastError) {
                resultsTab.innerHTML = `<div class="empty-state"><p>Could not load similar artists: ${escapeHtml(lastError.message)}</p></div>`;
                return;
            }

            if (!tracks.length) {
                resultsTab.innerHTML = `<div class="empty-state"><p>No similar artists found for "${escapeHtml(artist)}"</p></div>`;
                return;
            }

            // Update bar now we know the count
            _showExploreBar(artist, tracks);

            // Search each similar artist in parallel (batches of 5) and render as results arrive
            resultsTab.innerHTML = '';
            const exploreResults = [];

            async function searchOne(track) {
                // track is {artist} - search by artist name to get their top result
                const query = track.artist;
                try {
                    const resp = await apiFetch('/api/search', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ query, limit: 1, source: currentSource })
                    });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    return data.results && data.results[0] ? data.results[0] : null;
                } catch { return null; }
            }

            // Batch into groups of 5 to avoid hammering the backend
            for (let i = 0; i < tracks.length; i += 5) {
                const batch = tracks.slice(i, i + 5);
                const batchResults = await Promise.all(batch.map(searchOne));
                batchResults.forEach(r => {
                    if (r) {
                        exploreResults.push(r);
                        // Render incrementally - append each card as it arrives
                        const tmp = document.createElement('div');
                        tmp.innerHTML = _renderOneResult(r, exploreResults.length - 1);
                        const item = tmp.firstElementChild;
                        resultsTab.appendChild(item);
                        _attachResultHandlers(item, r, exploreResults.length - 1);
                    }
                });
            }

            // Update lastResults so individual downloads work, and stash for Download All
            lastResults = exploreResults;
            _exploreResolvedResults = exploreResults;

            // All batches done - enable the Download All button now it has real data
            const dlBtn = document.getElementById('exploreDownloadAllBtn');
            if (dlBtn) { dlBtn.disabled = false; dlBtn.textContent = 'Download All'; }

            if (!exploreResults.length) {
                resultsTab.innerHTML = `<div class="empty-state"><p>Couldn't find playable tracks for similar artists</p></div>`;
            }
        }

        function _showExploreBar(artist, tracks) {
            const trackCount = tracks ? tracks.length : null;
            const countText = trackCount ? ` (${trackCount} similar artists)` : '';
            exploreBar.innerHTML = `
                <div class="explore-bar">
                    <span class="explore-bar-label">~ Similar to <strong>${escapeHtml(artist)}</strong>${escapeHtml(countText)}</span>
                    <div class="explore-bar-actions">
                        ${tracks ? `
                            <label class="explore-playlist-label">
                                <input type="checkbox" id="explorePlaylistCheckbox" checked>
                                <span>Save as playlist</span>
                            </label>
                            <button class="explore-download-all-btn" id="exploreDownloadAllBtn" disabled>Loading...</button>
                        ` : ''}
                        <button class="explore-back-btn" id="exploreBackBtn">&#x2715; Back</button>
                    </div>
                </div>
            `;
            exploreBar.style.display = 'block';

            document.getElementById('exploreBackBtn').addEventListener('click', () => {
                exploreBar.style.display = 'none';
                stopPreview();
                if (_exploreOriginalResults) {
                    renderResults(_exploreOriginalResults);
                    showRelatedSuggestions(_exploreOriginalResults);
                }
                _exploreOriginalResults = null;
                _exploreResolvedResults = null;
            });

            const dlAllBtn = document.getElementById('exploreDownloadAllBtn');
            if (dlAllBtn && tracks) {
                dlAllBtn.addEventListener('click', () => _exploreDownloadAll(artist, tracks));
            }
        }

        async function _exploreDownloadAll(artist, tracks) {
            const dlAllBtn = document.getElementById('exploreDownloadAllBtn');
            if (dlAllBtn) { dlAllBtn.disabled = true; dlAllBtn.textContent = 'Queuing...'; }

            // Use the dedicated explore results (not lastResults, which may still hold the original search).
            const resolvedResults = _exploreResolvedResults && _exploreResolvedResults.length ? _exploreResolvedResults : null;
            let songs;
            if (resolvedResults) {
                songs = resolvedResults.map(r => {
                    const a = (r.artist || r.channel || '').trim();
                    const t = (r.title || '').trim();
                    return a && t ? `${a} - ${t}` : (a || t);
                }).filter(Boolean).join('\n');
            } else {
                songs = tracks.map(t => `${t.artist} - top track`).join('\n');
            }
            const now = new Date();
            const dateSuffix = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
            const playlistName = `Similar to ${artist} (${dateSuffix})`;
            const makePlaylist = document.getElementById('explorePlaylistCheckbox')?.checked ?? true;

            const requestBody = { songs, convert_to_flac: true };
            if (makePlaylist) {
                requestBody.create_playlist = true;
                requestBody.playlist_name = playlistName;
                requestBody.use_playlists_dir = true;
            }

            try {
                const resp = await apiFetch('/api/bulk-import-async', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                if (!resp.ok) throw new Error('Bulk import failed');
                if (dlAllBtn) { dlAllBtn.textContent = 'Queued!'; }
                // Switch to the Queue tab so they can watch progress
                setTimeout(() => {
                    document.querySelector('[data-tab="queue"]')?.click();
                }, 800);
            } catch (e) {
                if (dlAllBtn) { dlAllBtn.disabled = false; dlAllBtn.textContent = 'Download All'; }
                showToast(`Could not queue downloads: ${e.message}`, true);
            }
        }

        // Render a single result card as an HTML string (mirrors the main renderResults template)
        function _renderOneResult(r, index) {
            const safeSource = escapeHtml(r.source || '');
            const safeVideoId = escapeHtml(r.video_id || '');
            return `
                <div class="result-item ${downloadingIds.has(r.video_id) ? 'downloading' : ''} ${r.source === 'soulseek' ? 'soulseek' : ''}"
                     data-video-id="${safeVideoId}"
                     data-index="${index}"
                     data-title="${escapeHtml(r.title)}"
                     data-tooltip="${r.source !== 'soulseek' ? 'Hover to preview, click to download' : 'Click to download'}">
                    ${r.source !== 'soulseek' ? '<div class="preview-indicator">▶</div>' : ''}
                    ${r.thumbnail ? `<img class="result-thumb" src="${escapeHtml(r.thumbnail)}" alt="" loading="lazy">` : '<div class="result-thumb"></div>'}
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(r.title)}</div>
                        <div class="result-meta">${formatResultMeta(r)}</div>
                        <div class="result-badges">
                            <span class="source-badge ${safeSource}">${getSourceBadge(r.source)}</span>
                            ${r.quality ? `<span class="quality-badge ${getQualityBadgeClass(r.quality)}">${formatQualityLabel(r.quality)}</span>` : ''}
                            ${r.duration ? `<span class="result-duration">${r.duration}</span>` : ''}
                        </div>
                    </div>
                    ${r.source !== 'soulseek' ? `<div class="mobile-actions"><button class="preview-btn" data-video-id="${safeVideoId}" data-index="${index}" title="Preview">Preview &#9654;</button></div>` : ''}
                </div>
            `;
        }

        // Attach download/preview handlers to a single result card element
        function _attachResultHandlers(item, result, index) {
            const videoId = item.dataset.videoId;

            item.addEventListener('click', () => {
                if (!downloadingIds.has(videoId)) {
                    stopPreview();
                    downloadTrack(result, item);
                }
            });

            item.addEventListener('mouseenter', () => {
                if (!downloadingIds.has(videoId) && result.source !== 'soulseek') {
                    startHoverTimer(videoId, item, result);
                }
            });

            item.addEventListener('mouseleave', () => stopPreview());

            const previewBtn = item.querySelector('.preview-btn');
            if (previewBtn) {
                previewBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (downloadingIds.has(videoId)) return;
                    if (currentPreviewId === videoId) {
                        stopPreview();
                    } else {
                        startPreview(videoId, item, result);
                    }
                });
            }
        }

        function _renderMissingTrackCandidate(result, index) {
            return `
                <div class="missing-track-candidate">
                    ${_renderOneResult(result, index)}
                    <div class="missing-track-candidate-actions">
                        <button type="button"
                            class="missing-track-candidate-btn"
                            data-action="queue-missing-track-candidate"
                            data-index="${index}">Use This</button>
                    </div>
                </div>
            `;
        }

        function _attachMissingTrackCandidateHandlers(wrapper, result, index) {
            const item = wrapper.querySelector('.result-item');
            if (item) {
                const videoId = item.dataset.videoId;
                item.addEventListener('mouseenter', () => {
                    if (result.source !== 'soulseek') {
                        startHoverTimer(videoId, item, result);
                    }
                });
                item.addEventListener('mouseleave', () => stopPreview());

                const previewBtn = item.querySelector('.preview-btn');
                if (previewBtn) {
                    previewBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        if (currentPreviewId === videoId) {
                            stopPreview();
                        } else {
                            startPreview(videoId, item, result);
                        }
                    });
                }
            }

            const btn = wrapper.querySelector('[data-action="queue-missing-track-candidate"]');
            if (btn) {
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    queueMissingTrackCandidate(index, btn);
                });
            }
        }

        function getSourceBadge(source) {
            const badges = { youtube: 'YT', mp3phoenix: 'PX', soundcloud: 'SC', zvu4no: 'ZV', freemp3cloud: 'FMC', soulseek: 'SLK', monochrome: 'MONO' };
            return badges[source] || source.toUpperCase().slice(0, 3);
        }

        function getSourceLabel(source) {
            const labels = { youtube: 'YouTube', mp3phoenix: 'MP3Phoenix', soundcloud: 'SoundCloud', zvu4no: 'zvu4no', freemp3cloud: 'FreeMp3Cloud', soulseek: 'Soulseek', monochrome: 'Monochrome' };
            return labels[source] || source;
        }

        function getQualityBadgeClass(quality) {
            if (!quality) return '';
            const q = quality.toUpperCase();
            if (q === 'HI_RES_LOSSLESS') return 'hires';
            if (q === 'LOSSLESS' || q.includes('FLAC')) return 'flac';
            if (q === 'HIGH' || q.includes('320') || q.includes('256')) return 'mp3-high';
            return '';
        }

        function formatQualityLabel(quality) {
            if (!quality) return '';
            const labels = {
                'HI_RES_LOSSLESS': 'Hi-Res',
                'LOSSLESS': 'Lossless',
                'HIGH': 'HQ',
            };
            return labels[quality] || quality;
        }

        function formatResultMeta(result) {
            const parts = [];
            if (result.artist) {
                if (result.channel && result.channel !== result.artist) {
                    parts.push(`${escapeHtml(result.artist)} • ${escapeHtml(result.channel)}`);
                } else {
                    parts.push(escapeHtml(result.artist));
                }
            } else {
                parts.push(escapeHtml(result.channel || ''));
            }
            // Show album if known - clickable to open in Albums tab
            if (result.album) {
                const albumArtist = escapeAttr(result.artist || result.channel || '');
                const albumTitle = escapeAttr(result.album);
                parts.push(`<span class="album-link" data-artist="${albumArtist}" data-album="${albumTitle}" title="View album in Albums tab">${escapeHtml(result.album)}</span>`);
            }
            return parts.join(' • ');
        }

        function renderResults(results) {
            // Store results for later access (needed for slskd fields on download)
            lastResults = results;

            if (!results.length) {
                resultsTab.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon"><i class="fa-solid fa-magnifying-glass"></i></div>
                        <p>No results found</p>
                    </div>
                `;
                return;
            }

            // Show the destination picker row now that there are results to act on
            const _destRow2 = document.getElementById('destinationPickerRow');
            if (_destRow2) _destRow2.style.display = '';

            resultsTab.innerHTML = results.map((r, index) => {
                const safeSource = escapeHtml(r.source || '');
                const safeVideoId = escapeHtml(r.video_id || '');
                return `
                <div class="result-item ${downloadingIds.has(r.video_id) ? 'downloading' : ''} ${r.source === 'soulseek' ? 'soulseek' : ''}"
                     data-video-id="${safeVideoId}"
                     data-index="${index}"
                     data-title="${escapeHtml(r.title)}"
                     data-tooltip="${r.source !== 'soulseek' ? 'Hover to preview, click to download' : 'Click to download'}">
                    ${r.source !== 'soulseek' ? '<div class="preview-indicator">▶</div>' : ''}
                    ${r.thumbnail ? `<img class="result-thumb" src="${escapeHtml(r.thumbnail)}" alt="" loading="lazy">` : '<div class="result-thumb"></div>'}
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(r.title)}</div>
                        <div class="result-meta">${formatResultMeta(r)}</div>
                        <div class="result-badges">
                            <span class="source-badge ${safeSource}">${getSourceBadge(r.source)}</span>
                            ${r.quality ? `<span class="quality-badge ${getQualityBadgeClass(r.quality)}">${formatQualityLabel(r.quality)}</span>` : ''}
                            ${r.duration ? `<span class="result-duration">${r.duration}</span>` : ''}
                        </div>
                    </div>
                    <div class="mobile-actions">
                        ${r.source !== 'soulseek' ? `<button class="preview-btn" data-video-id="${safeVideoId}" data-index="${index}" title="Preview">Preview &#9654;</button>` : ''}
                        <button class="explore-btn" data-artist="${escapeAttr(r.artist || r.channel)}" title="Find similar artists via ListenBrainz">~ Similar</button>
                    </div>
                </div>
                `;
            }).join('');

            // Add click and hover handlers
            resultsTab.querySelectorAll('.result-item').forEach(item => {
                const videoId = item.dataset.videoId;
                const index = parseInt(item.dataset.index);
                const result = lastResults[index];

                // Click to download
                item.addEventListener('click', () => {
                    if (!downloadingIds.has(videoId)) {
                        stopPreview();
                        downloadTrack(result, item);
                    }
                });

                // Hover to preview (desktop only, not Soulseek)
                item.addEventListener('mouseenter', () => {
                    if (!downloadingIds.has(videoId) && result.source !== 'soulseek') {
                        startHoverTimer(videoId, item, result);
                    }
                });

                item.addEventListener('mouseleave', () => {
                    stopPreview();
                });

                // Mobile preview button - tap to preview without triggering download
                const previewBtn = item.querySelector('.preview-btn');
                if (previewBtn) {
                    previewBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        if (downloadingIds.has(videoId)) return;
                        if (currentPreviewId === videoId) {
                            stopPreview();
                        } else {
                            startPreview(videoId, item, result);
                        }
                    });
                }

                // Similar artists button - explore via ListenBrainz
                const exploreBtn = item.querySelector('.explore-btn');
                if (exploreBtn) {
                    exploreBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        exploreSimilar(exploreBtn.dataset.artist);
                    });
                }

                // Album link - click to open in Albums tab
                const albumLink = item.querySelector('.album-link');
                if (albumLink) {
                    albumLink.addEventListener('click', (e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        openAlbumFromSearch({
                            artistName: albumLink.dataset.artist,
                            albumTitle: albumLink.dataset.album
                        });
                    });
                }
            });
        }

        async function loadMissingTrackVersions() {
            const body = document.getElementById('missingTrackVersionsBody');
            const meta = document.getElementById('missingTrackVersionsMeta');
            if (!body || !meta) return;

            meta.textContent = `${missingTrackVersionsState.playlistName || 'Watched playlist'} • ${missingTrackVersionsState.artist} - ${missingTrackVersionsState.title}`;
            body.innerHTML = '<div class="missing-track-modal-empty">Searching for alternatives...</div>';

            try {
                const params = new URLSearchParams({
                    artist: missingTrackVersionsState.artist,
                    title: missingTrackVersionsState.title,
                    limit: '4',
                });
                const resp = await apiFetch(`/api/watched-playlists/${encodeURIComponent(missingTrackVersionsState.playlistId)}/track-candidates?${params.toString()}`);
                if (!resp.ok) throw new Error('Search failed');
                const data = await resp.json();
                const results = Array.isArray(data.results) ? data.results : [];
                missingTrackVersionsState.results = results;

                if (!results.length) {
                    body.innerHTML = '<div class="missing-track-modal-empty">No alternatives found. Retry will still run the automatic watched-playlist search, or Search will open the full Results tab.</div>';
                    return;
                }

                body.innerHTML = results.map((result, index) => _renderMissingTrackCandidate(result, index)).join('');
                body.querySelectorAll('.missing-track-candidate').forEach((wrapper, index) => {
                    _attachMissingTrackCandidateHandlers(wrapper, results[index], index);
                });
            } catch (error) {
                body.innerHTML = '<div class="missing-track-modal-empty">Could not load alternatives right now.</div>';
            }
        }

        async function openMissingTrackVersionsModal(playlistId, playlistName, artist, title, rowId) {
            missingTrackVersionsState.playlistId = playlistId;
            missingTrackVersionsState.playlistName = playlistName;
            missingTrackVersionsState.artist = artist;
            missingTrackVersionsState.title = title;
            missingTrackVersionsState.rowId = rowId || '';
            missingTrackVersionsState.results = [];
            document.getElementById('missingTrackVersionsOverlay').style.display = 'flex';
            await loadMissingTrackVersions();
        }

        function closeMissingTrackVersionsModal() {
            stopPreview();
            missingTrackVersionsState.playlistId = '';
            missingTrackVersionsState.playlistName = '';
            missingTrackVersionsState.artist = '';
            missingTrackVersionsState.title = '';
            missingTrackVersionsState.rowId = '';
            missingTrackVersionsState.results = [];
            document.getElementById('missingTrackVersionsOverlay').style.display = 'none';
        }

        async function queueMissingTrackCandidate(index, btn) {
            const result = missingTrackVersionsState.results[index];
            if (!result) return;

            const originalText = btn ? btn.textContent : '';
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Queueing...';
            }

            try {
                const payload = {
                    artist: missingTrackVersionsState.artist,
                    title: missingTrackVersionsState.title,
                    video_id: result.video_id || '',
                    source: result.source || 'youtube',
                    source_url: result.source_url || null,
                    slskd_username: result.slskd_username || null,
                    slskd_filename: result.slskd_filename || null,
                    slskd_size: result.slskd_size || result.size || null,
                };
                const resp = await apiFetch(`/api/watched-playlists/${encodeURIComponent(missingTrackVersionsState.playlistId)}/queue-track-candidate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Failed to queue candidate');
                }

                const row = missingTrackVersionsState.rowId ? document.getElementById(missingTrackVersionsState.rowId) : null;
                if (row) {
                    const retryBtn = row.querySelector('[data-action="retry-missing-track"]');
                    if (retryBtn) {
                        retryBtn.disabled = true;
                        retryBtn.textContent = 'Queued';
                    }
                }

                showToast(`Queued ${missingTrackVersionsState.artist} - ${missingTrackVersionsState.title}`);
                closeMissingTrackVersionsModal();
                if (document.querySelector('.tab.active[data-tab="queue"]')) {
                    loadJobs(false);
                }
            } catch (error) {
                showToast(error?.message || 'Failed to queue candidate', true);
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = originalText || 'Use This';
                }
            }
        }

        async function downloadTrack(result, element) {
            // Check queue size limit
            const queueSize = await getQueueSize();
            if (queueSize >= MAX_QUEUE_SIZE) {
                showToast(`Queue limit reached (max ${MAX_QUEUE_SIZE})`, true);
                return;
            }

            downloadingIds.add(result.video_id);
            element.classList.add('downloading');

            showToast(`Processing (${getSourceLabel(result.source)})...`, false);

            try {
                let queuedTitle = result.title;
                const payload = {
                    video_id: result.video_id,
                    title: queuedTitle,
                    convert_to_flac: convertToFlacCheckbox.checked,
                    source: result.source || 'youtube',
                    search_token: currentSearchLogToken
                };
                if (result.artist || result.channel) {
                    payload.artist = result.artist || result.channel;
                }

                // URL-based sources need the full URL for downloading
                if ((result.source === 'soundcloud' || result.source === 'mp3phoenix' || result.source === 'zvu4no' || result.source === 'freemp3cloud' || result.source === 'monochrome') && result.source_url) {
                    payload.source_url = result.source_url;
                }

                // Add slskd-specific fields if this is a Soulseek result
                if (result.source === 'soulseek') {
                    payload.slskd_username = result.slskd_username;
                    payload.slskd_filename = result.slskd_filename;
                    payload.slskd_size = result.slskd_size || result.size || null;
                    if (result.artist) {
                        payload.artist = result.artist;
                    }
                }

                const albumRoute = getSelectedAlbumRoute();
                if (_destinationMode === 'album' && !albumRoute) {
                    throw new Error('Select an artist and album folder before downloading.');
                }
                if (albumRoute) {
                    if (albumRoute.mode === 'no_mbid') {
                        // Folder routing only — no .albuminfo found so no track metadata enrichment
                        payload.album_artist = albumRoute.album_artist;
                        payload.album_name = albumRoute.album_name;
                    } else {
                        let matchedTrack = null;
                        if (albumRoute.mode === 'manual') {
                            matchedTrack = {
                                track_title: albumRoute.track_title,
                                track_number: albumRoute.track_number,
                                track_total: albumRoute.track_total,
                            };
                        } else {
                            const matchResp = await apiFetch(`/api/albums/release/${encodeURIComponent(albumRoute.release_mbid)}/match-track`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    artist: result.artist || result.channel || '',
                                    title: result.title || '',
                                    album_artist: albumRoute.album_artist || '',
                                })
                            });
                            if (!matchResp.ok) {
                                const err = await matchResp.json().catch(() => ({}));
                                throw new Error(err.detail || 'No album-track match found');
                            }
                            matchedTrack = await matchResp.json();
                        }

                        queuedTitle = matchedTrack.track_title || queuedTitle;
                        payload.title = queuedTitle;
                        payload.album_release_mbid = albumRoute.release_mbid;
                        payload.album_artist = albumRoute.album_artist;
                        payload.album_name = albumRoute.album_name;
                        payload.album_track_title = matchedTrack.track_title || queuedTitle;
                        payload.album_track_number = matchedTrack.track_number || null;
                        payload.album_track_total = matchedTrack.track_total || albumRoute.track_total || null;
                    }
                }

                // Playlist routing - attach target playlist if one is selected
                const selectedPlaylist = getSelectedPlaylist();
                if (selectedPlaylist && albumRoute) {
                    throw new Error('Choose either Add to playlist or Add to album, not both');
                }
                if (selectedPlaylist) {
                    payload.playlist_name = selectedPlaylist.name;
                    payload.use_playlists_dir = true;
                }

                const response = await apiFetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) throw new Error('Download failed');

                const data = await response.json();

                // Update toast with success message
                const formatMsg = convertToFlacCheckbox.checked ? '' : ' (original format)';
                const qualityMsg = result.quality ? ` [${formatQualityLabel(result.quality)}]` : '';
                const pl = getSelectedPlaylist();
                const playlistMsg = pl ? ` → ${pl.name}` : '';
                const al = getSelectedAlbumRoute();
                const albumMsg = al ? ` → ${al.album_name}` : '';
                showToast(`Added to queue${qualityMsg}${formatMsg}${playlistMsg}${albumMsg}`);
            } catch (error) {
                downloadingIds.delete(result.video_id);
                element.classList.remove('downloading');
                showToast(error?.message ? `Failed to queue: ${error.message}` : 'Failed to queue', true);
            }
        }

        async function getQueueSize() {
            // Use cached queue data if available, otherwise fetch
            const jobs = allQueueJobs.length > 0 ? allQueueJobs : await (async () => {
                try {
                    const response = await apiFetch('/api/jobs?limit=250');
                    if (!response.ok) return [];
                    const data = await response.json();
                    return data.jobs;
                } catch { return []; }
            })();
            return jobs.filter(j => j.status === 'queued' || j.status === 'downloading').length;
        }

        async function loadJobs(showLoading = true) {
            if (showLoading) {
                queueTab.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            }

            try {
                const response = await apiFetch('/api/jobs?limit=250');
                if (!response.ok) throw new Error('Failed to load jobs');

                const data = await response.json();
                allQueueJobs = data.jobs;
                // Clamp page in case jobs were cleared
                const totalPages = Math.max(1, Math.ceil(allQueueJobs.length / QUEUE_PAGE_SIZE));
                if (queuePage > totalPages) queuePage = totalPages;
                renderQueuePage();
            } catch (error) {
                if (showLoading) {
                    queueTab.innerHTML = `
                        <div class="empty-state">
                            <div class="empty-state-icon"><i class="fa-solid fa-circle-exclamation"></i></div>
                            <p>Failed to load queue</p>
                        </div>
                    `;
                }
            }
        }

        function renderQueuePage() {
            const totalPages = Math.max(1, Math.ceil(allQueueJobs.length / QUEUE_PAGE_SIZE));
            const start = (queuePage - 1) * QUEUE_PAGE_SIZE;
            const pageJobs = allQueueJobs.slice(start, start + QUEUE_PAGE_SIZE);
            renderJobs(pageJobs);

            const pagerEl = document.getElementById('queuePager');
            const infoEl = document.getElementById('queuePagerInfo');
            const prevBtn = document.getElementById('queuePrevBtn');
            const nextBtn = document.getElementById('queueNextBtn');
            if (allQueueJobs.length > 1) {
                pagerEl.style.display = 'flex';
                infoEl.textContent = `Page ${queuePage} of ${totalPages} (${allQueueJobs.length} jobs)`;
                prevBtn.disabled = queuePage <= 1;
                nextBtn.disabled = queuePage >= totalPages;
            } else {
                pagerEl.style.display = 'none';
            }
        }

        function changeQueuePage(delta) {
            const totalPages = Math.max(1, Math.ceil(allQueueJobs.length / QUEUE_PAGE_SIZE));
            queuePage = Math.max(1, Math.min(totalPages, queuePage + delta));
            renderQueuePage();
        }

        function renderJobs(jobs) {
            if (!jobs.length) {
                expandedJobIds.clear();
                queueTab.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon"><i class="fa-solid fa-inbox"></i></div>
                        <p>No downloads yet</p>
                    </div>
                `;
                return;
            }

            const visibleJobIds = new Set(jobs.map(j => j.id));
            for (const id of Array.from(expandedJobIds)) {
                if (!visibleJobIds.has(id)) expandedJobIds.delete(id);
            }

            queueTab.innerHTML = jobs.map(job => {
                const hasDetails = job.status === 'completed' || job.status === 'completed_with_errors' || job.status === 'failed';
                const isExpanded = hasDetails && expandedJobIds.has(job.id);
                const sourceLabel = getSourceLabel(job.source || 'youtube');
                const sourceUrl = job.source_url || '';
                const isClickableUrl = sourceUrl.startsWith('https://');
                const fileDeleted = Number(job.file_deleted || 0) === 1;
                return `
                <div class="job-item ${hasDetails ? 'has-details' : ''} ${isExpanded ? 'expanded' : ''}" data-job-id="${escapeHtml(job.id || '')}" ${hasDetails ? 'onclick="toggleJobDetails(this)"' : ''}>
                    <div class="job-status ${job.status}"></div>
                    <div class="job-info">
                        <div class="job-title">${escapeHtml(job.artist ? `${job.artist} - ${job.title}` : job.title)}${job.status === 'completed_with_errors' ? '<span class="job-warning-badge">ISSUES</span>' : ''}</div>
                        <div class="job-meta">${formatJobStatus(job.status)}${job.status === 'downloading' && job.progress_stage ? ` <span class="job-progress-stage">${escapeHtml(job.progress_stage)}</span>` : ''} • ${formatTime(job.created_at)}</div>
                        ${job.error ? `<div class="job-error">${job.error.startsWith('Already exists') ? 'Already in library' : escapeHtml(job.error)}</div>` : ''}
                        ${hasDetails ? `
                        <div class="job-details" style="display:${isExpanded ? 'block' : 'none'};">
                            ${job.audio_quality ? `<div class="job-details-row"><span class="job-details-label">Quality:</span> ${escapeHtml(job.audio_quality)}</div>` : ''}
                            ${job.error && job.error.startsWith('Already exists') ? `<div class="job-details-row"><span class="job-details-label">Path:</span> <span class="job-details-url">${escapeHtml(job.error.replace(/^Already exists(?: in [^:]+)?:\s*/, '').replace(/ \(added to playlist\)$/, ''))}</span></div>` : ''}
                            <div class="job-details-row"><span class="job-details-label">Source:</span> ${escapeHtml(sourceLabel)}</div>
                            ${job.metadata_source ? `<div class="job-details-row"><span class="job-details-label">Metadata:</span> ${escapeHtml(formatMetadataSource(job.metadata_source))}</div>` : ''}
                            ${sourceUrl ? `<div class="job-details-row"><span class="job-details-label">URL:</span> ${isClickableUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(sourceUrl)}</a>` : `<span class="job-details-url">${escapeHtml(sourceUrl)}</span>`}</div>` : ''}
                            <div class="job-details-row"><span class="job-details-label">Queued:</span> ${formatTimeFull(job.created_at)}</div>
                            ${job.completed_at ? `<div class="job-details-row"><span class="job-details-label">Completed:</span> ${formatTimeFull(job.completed_at)}</div>` : ''}
                            ${job.completed_at && job.created_at ? `<div class="job-details-row"><span class="job-details-label">Duration:</span> ${formatDuration(job.created_at, job.completed_at)}</div>` : ''}
                            ${['failed', 'completed_with_errors'].includes(job.status) || (job.error || '').includes('mismatch') ? `
                            <div id="score-rationale-link-${escapeHtml(job.id || '')}" class="job-details-row" style="margin-top:4px;">
                                <a href="#" onclick="event.preventDefault(); event.stopPropagation(); loadScoreRationale('${escapeAttr(job.id || '')}')" style="font-size:12px; color:var(--accent); text-decoration:none;">Why this result?</a>
                                <button class="score-rationale-copy" onclick="event.stopPropagation(); copyScoreRationale('${escapeAttr(job.id || '')}')" style="margin-left:8px;" title="Copy scoring rationale to clipboard"><i class="fa-solid fa-clipboard"></i> Copy</button>
                            </div>
                            <div id="score-rationale-${escapeHtml(job.id || '')}" style="display:none;"></div>
                            ` : ''}
                            <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
                                ${job.status !== 'failed' && !fileDeleted ? `<button class="library-play-btn" onclick="event.stopPropagation(); playJobAudio('${escapeAttr(job.id || '')}', this)" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;"><i class="fa-solid fa-play"></i></button>` : ''}
                                <button onclick="event.stopPropagation(); redownloadJob('${escapeAttr(job.id || '')}')" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Re-download</button>
                                <button class="report-btn" data-job-id="${escapeHtml(job.id || '')}" data-video-id="${escapeHtml(job.video_id || '')}" data-uploader="${escapeHtml(job.uploader || '')}" data-source="${escapeHtml(job.source || 'youtube')}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--warning); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Report</button>
                                ${(job.error || '').includes('mismatch') ? `<button class="force-accept-btn" data-job-id="${escapeHtml(job.id || '')}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Force Download</button>` : ''}
                                ${(job.status === 'completed' || job.status === 'completed_with_errors') && !fileDeleted ? `<button class="edit-tags-btn" data-job-id="${escapeHtml(job.id || '')}" data-artist="${escapeAttr(job.artist || '')}" data-title="${escapeAttr(job.title || '')}" data-album="${escapeAttr(job.album_name || '')}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Edit Tags</button>` : ''}
                                ${job.status !== 'failed' ? (fileDeleted
                                    ? `<button disabled style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-secondary); color: var(--text-muted); border: 1px solid var(--border); border-radius: 6px; cursor: not-allowed; opacity: 0.8;">Trashed</button>`
                                    : `<button class="delete-file-btn" data-job-id="${escapeHtml(job.id || '')}" data-track-name="${escapeHtml(job.artist ? job.artist + ' - ' + job.title : job.title)}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--error); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;"><i class="fa-solid fa-trash-can"></i> Trash</button>
                                      <button onclick="event.stopPropagation(); saveJobToDevice('${escapeAttr(job.id || '')}')" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;"><i class="fa-solid fa-download"></i> Save to device</button>`) : ''}
                            </div>
                        </div>` : ''}
                    </div>
                    <span class="source-badge job-source-badge ${job.source || 'youtube'}">${getSourceBadge(job.source || 'youtube')}</span>
                </div>`;
            }).join('');

            queueTab.querySelectorAll('.delete-file-btn').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    deleteJobFile(btn.dataset.jobId || '', btn.dataset.trackName || '');
                });
            });

            queueTab.querySelectorAll('.report-btn').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    openReportDialog(
                        btn.dataset.jobId || '',
                        btn.dataset.videoId || '',
                        btn.dataset.uploader || '',
                        btn.dataset.source || 'youtube'
                    );
                });
            });

            queueTab.querySelectorAll('.force-accept-btn').forEach((btn) => {
                btn.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    btn.disabled = true;
                    btn.textContent = 'Queued...';
                    try {
                        const res = await apiFetch(`/api/jobs/${btn.dataset.jobId}/force-accept`, { method: 'POST' });
                        if (!res.ok) {
                            const err = await res.json().catch(() => ({}));
                            throw new Error(err.detail || 'Failed');
                        }
                        showToast('Re-queued with mismatch check disabled');
                        loadJobs();
                    } catch (e) {
                        showToast(`Force download failed: ${e.message}`, true);
                        btn.disabled = false;
                        btn.textContent = 'Force Download';
                    }
                });
            });

            queueTab.querySelectorAll('.edit-tags-btn').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    openTagEditor({
                        jobId: btn.dataset.jobId || '',
                        artist: btn.dataset.artist || '',
                        title: btn.dataset.title || '',
                        album: btn.dataset.album || '',
                    });
                });
            });

            // Polling is now handled by queuePollInterval (setInterval) managed by tab switch.
        }

        function formatJobStatus(status) {
            if (status === 'completed_with_errors') {
                return 'completed (with errors)';
            }
            return status;
        }

        function formatMetadataSource(metadataSource) {
            const source = (metadataSource || '').toLowerCase();
            const labels = {
                'acoustid_fingerprint': 'AcoustID fingerprint',
                'musicbrainz_text': 'MusicBrainz text match',
                'youtube_guessed': 'YouTube embedded/guessed',
                'soundcloud_guessed': 'SoundCloud embedded/guessed',
                'mp3phoenix_guessed': 'MP3Phoenix embedded/guessed',
                'zvu4no_guessed': 'zvu4no embedded/guessed',
                'freemp3cloud_guessed': 'FreeMp3Cloud embedded/guessed',
                'soulseek_guessed': 'Soulseek embedded/guessed',
            };
            return labels[source] || metadataSource;
        }

        function formatDateYmd(isoString) {
            if (!isoString) return '';
            const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(isoString) ? isoString : `${isoString.replace(' ', 'T')}Z`;
            const date = new Date(normalized);
            if (Number.isNaN(date.getTime())) return '';
            const y = date.getFullYear();
            const m = String(date.getMonth() + 1).padStart(2, '0');
            const d = String(date.getDate()).padStart(2, '0');
            return `${y}-${m}-${d}`;
        }

        function formatTime(isoString) {
            return formatDateYmd(isoString);
        }

        function toggleJobDetails(el) {
            const details = el.querySelector('.job-details');
            if (details) {
                const isOpen = details.style.display !== 'none';
                details.style.display = isOpen ? 'none' : 'block';
                el.classList.toggle('expanded', !isOpen);
                const jobId = el.dataset.jobId;
                if (jobId) {
                    if (isOpen) expandedJobIds.delete(jobId);
                    else expandedJobIds.add(jobId);
                }
            }
        }

        // =============================================================================
        // Score Rationale (why the scorer picked a particular result)
        // =============================================================================

        const SCORE_LABELS = {
            "base": null,
            "official_title": "Official title tag",
            "official_channel": "Official channel",
            "official_video": "Official music video",
            "official_audio": "Official audio",
            "topic_channel": "Topic channel",
            "live_requested": "Live version (requested)",
            "live_variant": "Live/session penalty",
            "live_channel": "Live channel penalty",
            "junk_variant": "Junk variant (karaoke/nightcore/etc.)",
            "bootleg_edit": "Bootleg/fan edit",
            "cover_or_variant": "Cover/remix/instrumental",
            "lyric_video": "Lyric video",
            "unofficial_title": "Unofficial/fan upload",
            "unofficial_channel": "Fan/tribute channel",
            "copyright_dodge": "Copyright-dodging upload",
            "channel_in_title": "Channel name in title",
            "query_full_coverage": "All search terms matched",
            "query_good_coverage": "Most search terms matched",
            "query_poor_coverage": "Few search terms matched",
            "title_match": "Exact title match",
            "title_overlap_full": "Strong title overlap",
            "title_overlap_strong": "Good title overlap",
            "title_mismatch": "Title mismatch",
            "artist_in_title": "Artist in title",
            "artist_in_channel": "Artist matches channel",
            "artist_match": "Artist matched",
            "artist_overlap": "Partial artist match",
            "artist_mismatch": "Artist mismatch",
            "artist_title_phrase": "Artist + title phrase match",
            "plain_title": "Plain studio title",
            "variant_suffix": "Variant suffix penalty",
            "reaction_or_compilation": "Reaction/compilation",
            "compilation_album": "Compilation album",
            "extended_version": "Extended version",
            "non_song_result": "Non-song result",
            "reverb": "Reverb edit",
            "duration_too_short": "Way too short",
            "duration_short": "Very short",
            "duration_brief": "Slightly short",
            "duration_sweet_spot": "Good duration",
            "duration_long": "Suspiciously long",
            "duration_very_long": "Way too long",
            "low_views": "Low views",
            "good_views": "Decent view count",
            "huge_views": "Very popular",
            "mb_duration_exact": "MusicBrainz duration match",
            "mb_duration_close": "Close to MusicBrainz duration",
            "mb_duration_off": "Duration doesn't match MusicBrainz",
            "mb_duration_way_off": "Duration way off MusicBrainz",
            "mb_search_duration": "MusicBrainz duration scoring",
            "source_quality": "Source quality bonus",
            "free_slot": "Free download slot",
            "fast_uploader": "Fast uploader",
            "popularity": "Popularity bonus",
        };

        function parseBreakdownEntry(entry) {
            // "live_variant=-180" → { key: "live_variant", delta: -180, label: "Live/session penalty" }
            const m = (entry || '').match(/^([a-z_]+)=([+-]?\d+)$/);
            if (!m) return null;
            const key = m[1];
            if (SCORE_LABELS[key] === null) return null;  // hidden (e.g. "base")
            return {
                key,
                delta: parseInt(m[2], 10),
                label: SCORE_LABELS[key] || key.replace(/_/g, ' '),
            };
        }

        function humaniseBreakdown(breakdown, topN) {
            const parsed = (breakdown || [])
                .map(parseBreakdownEntry)
                .filter(Boolean)
                .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
            const items = topN ? parsed.slice(0, topN) : parsed;
            return items.map(p => {
                const cls = p.delta >= 0 ? 'score-reason-positive' : 'score-reason-negative';
                const sign = p.delta >= 0 ? '+' : '';
                return `<span class="${cls}">${escapeHtml(p.label)} (${sign}${p.delta})</span>`;
            }).join(', ');
        }

        function renderRationalePanel(data) {
            const d = data.decision || {};
            const selected = d.selected;
            const runnersUp = d.runners_up || [];
            if (!selected) return '';

            let html = '<div class="score-rationale">';
            html += '<div class="score-rationale-header">';
            if (data.query) {
                html += `<span class="score-rationale-query">Search: ${escapeHtml(data.query)}</span>`;
            }
            html += '</div>';

            // Winner
            html += '<div class="score-rationale-candidate score-rationale-picked">';
            html += '<div class="score-rationale-candidate-header">';
            html += `<span class="score-rationale-label">Picked</span>`;
            html += `<span class="source-badge ${escapeHtml(selected.source || 'youtube')}" style="font-size:10px;padding:1px 6px;">${getSourceBadge(selected.source || 'youtube')}</span>`;
            html += `<span class="score-rationale-title">${escapeHtml(selected.channel || '')}${selected.channel ? ' \u2013 ' : ''}${escapeHtml(selected.title || '')}</span>`;
            html += `<span class="score-rationale-score">score ${selected.score != null ? selected.score : '?'}</span>`;
            html += '</div>';
            const selectedReasons = humaniseBreakdown(selected.breakdown, 5);
            if (selectedReasons) {
                html += `<div class="score-rationale-reasons">${selectedReasons}</div>`;
            }
            html += '</div>';

            // Runners-up
            runnersUp.forEach((ru, i) => {
                html += '<div class="score-rationale-candidate score-rationale-runnerup">';
                html += '<div class="score-rationale-candidate-header">';
                html += `<span class="score-rationale-label">#${i + 2}</span>`;
                html += `<span class="source-badge ${escapeHtml(ru.source || 'youtube')}" style="font-size:10px;padding:1px 6px;">${getSourceBadge(ru.source || 'youtube')}</span>`;
                html += `<span class="score-rationale-title">${escapeHtml(ru.channel || '')}${ru.channel ? ' \u2013 ' : ''}${escapeHtml(ru.title || '')}</span>`;
                html += `<span class="score-rationale-score">score ${ru.score != null ? ru.score : '?'}</span>`;
                html += '</div>';
                const ruReasons = humaniseBreakdown(ru.breakdown, 3);
                if (ruReasons) {
                    html += `<div class="score-rationale-reasons">${ruReasons}</div>`;
                }
                html += '</div>';
            });

            // Raw scoring expander
            const allBreakdowns = [];
            allBreakdowns.push(`Picked: ${(selected.breakdown || []).join(', ')}`);
            runnersUp.forEach((ru, i) => {
                allBreakdowns.push(`#${i + 2}: ${(ru.breakdown || []).join(', ')}`);
            });
            html += '<details class="score-rationale-raw">';
            html += '<summary>Raw scoring</summary>';
            html += `<pre>${escapeHtml(allBreakdowns.join('\n'))}</pre>`;
            html += '</details>';

            html += '</div>';
            return html;
        }

        async function loadScoreRationale(jobId) {
            const container = document.getElementById(`score-rationale-${jobId}`);
            const link = document.getElementById(`score-rationale-link-${jobId}`);
            if (!container) return;

            // Already loaded?
            if (container.dataset.loaded === 'true') {
                container.style.display = container.style.display === 'none' ? 'block' : 'none';
                return;
            }

            try {
                const res = await apiFetch(`/api/jobs/${jobId}/score-rationale`);
                if (!res.ok) {
                    // No data for this job; hide the link entirely
                    if (link) link.style.display = 'none';
                    return;
                }
                const data = await res.json();
                container.innerHTML = renderRationalePanel(data);
                container.dataset.loaded = 'true';
                container.style.display = 'block';
            } catch {
                if (link) link.style.display = 'none';
            }
        }

        function copyScoreRationale(jobId) {
            const container = document.getElementById(`score-rationale-${jobId}`);
            if (!container || container.dataset.loaded !== 'true') return;

            // Re-fetch the data to build a clean text version
            apiFetch(`/api/jobs/${jobId}/score-rationale`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (!data) return;
                    const d = data.decision || {};
                    const sel = d.selected;
                    if (!sel) return;

                    let text = '';
                    if (data.query) text += `Search: ${data.query}\n`;
                    text += `\nPicked: [${sel.source}] ${sel.channel} - ${sel.title}\n`;
                    text += `Score: ${sel.score}\n`;
                    text += `Breakdown: ${(sel.breakdown || []).join(', ')}\n`;

                    const runners = d.runners_up || [];
                    if (runners.length) {
                        text += `\nRunners-up:\n`;
                        runners.forEach((ru, i) => {
                            text += `  #${i + 2}: [${ru.source}] ${ru.channel} - ${ru.title} (score ${ru.score})\n`;
                            text += `  Breakdown: ${(ru.breakdown || []).join(', ')}\n`;
                        });
                    }

                    navigator.clipboard.writeText(text.trim()).then(() => {
                        showToast('Score rationale copied');
                    });
                })
                .catch(() => {});
        }

        function escapeHtmlText(value) {
            return escapeHtml(value == null ? '' : String(value));
        }

        function renderTagEditorPreview() {
            if (!activeTagEditor) return;
            const artist = (tagEditorArtist.value || '').trim();
            const title = (tagEditorTitle.value || '').trim();
            const album = (tagEditorAlbum.value || '').trim();
            const albumArtist = (tagEditorAlbumArtist.value || '').trim();
            const year = (tagEditorYear.value || '').trim();
            const trackNumber = (tagEditorTrackNumber.value || '').trim();
            const trackTotal = (tagEditorTrackTotal.value || '').trim();
            const filename = [artist, title].filter(Boolean).join(' - ') || 'Untitled track';
            tagEditorPreview.innerHTML = `
                <div><strong>Filename preview:</strong> ${escapeHtmlText(filename)}${escapeHtmlText(activeTagEditor.extension || '')}</div>
                <div><strong>Album tag:</strong> ${album ? escapeHtmlText(album) : '<em>empty</em>'}</div>
                <div><strong>Album artist:</strong> ${albumArtist ? escapeHtmlText(albumArtist) : '<em>empty</em>'}</div>
                <div><strong>Track:</strong> ${trackNumber ? `${escapeHtmlText(trackNumber)}${trackTotal ? ` / ${escapeHtmlText(trackTotal)}` : ''}` : '<em>empty</em>'}</div>
                <div><strong>Year:</strong> ${year ? escapeHtmlText(year) : '<em>empty</em>'}</div>
            `;
        }

        function resetTagEditorDraft() {
            if (!activeTagEditor) return;
            tagEditorArtist.value = activeTagEditor.original.artist || '';
            tagEditorTitle.value = activeTagEditor.original.title || '';
            tagEditorAlbum.value = activeTagEditor.original.album || '';
            tagEditorAlbumArtist.value = activeTagEditor.original.album_artist || '';
            tagEditorYear.value = activeTagEditor.original.year || '';
            tagEditorTrackNumber.value = activeTagEditor.original.track_number || '';
            tagEditorTrackTotal.value = activeTagEditor.original.track_total || '';
            tagEditorError.style.display = 'none';
            tagEditorError.textContent = '';
            renderTagEditorPreview();
        }

        function openTagEditor({ jobId, artist, title, album, albumArtist = '', year = '', trackNumber = '', trackTotal = '' }) {
            const job = allQueueJobs.find(j => String(j.id) === String(jobId));
            activeTagEditor = {
                jobId: String(jobId || ''),
                extension: job?.file_path ? (job.file_path.match(/\.[^.\/\\]+$/)?.[0] || '') : '',
                sourceLabel: getSourceLabel(job?.source || 'youtube'),
                guessArtistSeed: (artist || '').trim(),
                guessTitleSeed: (title || '').trim(),
                guessOffset: 0,
                original: {
                    artist: artist || '',
                    title: title || '',
                    album: album || '',
                    album_artist: albumArtist || '',
                    year: year || '',
                    track_number: trackNumber || '',
                    track_total: trackTotal || '',
                },
            };
            tagEditorMeta.textContent = [activeTagEditor.sourceLabel, activeTagEditor.jobId].filter(Boolean).join(' • ');
            resetTagEditorDraft();
            tagEditorOverlay.style.display = 'flex';
            setTimeout(() => tagEditorArtist.focus(), 0);
        }

        function closeTagEditor() {
            activeTagEditor = null;
            tagEditorOverlay.style.display = 'none';
            tagEditorError.style.display = 'none';
            tagEditorError.textContent = '';
            tagEditorGuessBtn.disabled = false;
            tagEditorGuessBtn.textContent = 'Guess from MusicBrainz';
            tagEditorSaveBtn.disabled = false;
            tagEditorSaveBtn.textContent = 'Save Tags';
        }

        async function saveTagEditor() {
            if (!activeTagEditor) return;

            const artist = (tagEditorArtist.value || '').trim();
            const title = (tagEditorTitle.value || '').trim();
            const album = (tagEditorAlbum.value || '').trim();
            const albumArtist = (tagEditorAlbumArtist.value || '').trim();
            const year = (tagEditorYear.value || '').trim();
            const rawTrackNumber = tagEditorTrackNumber.value ? parseInt(tagEditorTrackNumber.value, 10) : null;
            const rawTrackTotal = tagEditorTrackTotal.value ? parseInt(tagEditorTrackTotal.value, 10) : null;
            const trackNumber = Number.isFinite(rawTrackNumber) ? rawTrackNumber : null;
            const trackTotal = Number.isFinite(rawTrackTotal) ? rawTrackTotal : null;

            if (!artist || !title) {
                tagEditorError.textContent = 'Artist and title are required.';
                tagEditorError.style.display = 'block';
                return;
            }
            if (year && !/^\d{4}$/.test(year)) {
                tagEditorError.textContent = 'Year must be four digits.';
                tagEditorError.style.display = 'block';
                return;
            }
            if (trackNumber && trackTotal && trackNumber > trackTotal) {
                tagEditorError.textContent = 'Track number cannot be greater than total tracks.';
                tagEditorError.style.display = 'block';
                return;
            }

            const originalText = tagEditorSaveBtn.textContent;
            tagEditorSaveBtn.disabled = true;
            tagEditorSaveBtn.textContent = 'Saving...';
            tagEditorError.style.display = 'none';

            try {
                const res = await apiFetch(`/api/jobs/${activeTagEditor.jobId}/tags`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        artist,
                        title,
                        album,
                        album_artist: albumArtist,
                        year,
                        track_number: trackNumber,
                        track_total: trackTotal,
                    }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Failed to save tags');
                }

                const card = queueTab.querySelector(`.job-item[data-job-id="${activeTagEditor.jobId}"]`);
                if (card) {
                    const titleEl = card.querySelector('.job-title');
                    if (titleEl) titleEl.firstChild.textContent = artist ? `${artist} - ${title}` : title;
                    const editBtn = card.querySelector('.edit-tags-btn');
                    if (editBtn) {
                        editBtn.dataset.artist = artist;
                        editBtn.dataset.title = title;
                        editBtn.dataset.album = album;
                    }
                }
                const queueJob = allQueueJobs.find(j => String(j.id) === String(activeTagEditor.jobId));
                if (queueJob) {
                    queueJob.artist = artist;
                    queueJob.title = title;
                    queueJob.album_name = album;
                }

                showToast('Tags updated');
                closeTagEditor();
            } catch (e) {
                tagEditorError.textContent = e.message || 'Failed to save tags.';
                tagEditorError.style.display = 'block';
                tagEditorSaveBtn.disabled = false;
                tagEditorSaveBtn.textContent = originalText;
            }
        }

        async function guessTagEditorFromMusicBrainz() {
            if (!activeTagEditor) return;
            const artist = (tagEditorArtist.value || '').trim();
            const title = (tagEditorTitle.value || '').trim();
            if (!artist || !title) {
                tagEditorError.textContent = 'Enter artist and title before guessing.';
                tagEditorError.style.display = 'block';
                return;
            }

            const originalText = tagEditorGuessBtn.textContent;
            tagEditorGuessBtn.disabled = true;
            tagEditorGuessBtn.textContent = 'Guessing...';
            tagEditorError.style.display = 'none';

            try {
                if (artist !== activeTagEditor.guessArtistSeed || title !== activeTagEditor.guessTitleSeed) {
                    activeTagEditor.guessArtistSeed = artist;
                    activeTagEditor.guessTitleSeed = title;
                    activeTagEditor.guessOffset = 0;
                }
                const res = await apiFetch(`/api/jobs/${activeTagEditor.jobId}/musicbrainz-guess?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}&offset=${activeTagEditor.guessOffset}`);
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'No more suitable MusicBrainz matches found');
                }
                const guess = await res.json();
                tagEditorArtist.value = guess.artist || artist;
                tagEditorTitle.value = guess.title || title;
                tagEditorAlbum.value = guess.album || '';
                tagEditorAlbumArtist.value = guess.album_artist || '';
                tagEditorYear.value = guess.year || '';
                tagEditorTrackNumber.value = guess.track_number || '';
                tagEditorTrackTotal.value = guess.track_total || '';
                activeTagEditor.guessOffset = (guess.candidate_index || 0) + 1;
                renderTagEditorPreview();
                if ((guess.candidate_count || 0) > 1) {
                    tagEditorGuessBtn.textContent = activeTagEditor.guessOffset < guess.candidate_count ? 'Guess Again' : 'No More Guesses';
                } else {
                    tagEditorGuessBtn.textContent = 'Guess Again';
                }
                showToast(`MusicBrainz metadata applied${guess.candidate_count ? ` (${(guess.candidate_index || 0) + 1}/${guess.candidate_count})` : ''}`);
            } catch (e) {
                tagEditorError.textContent = e.message || 'MusicBrainz lookup failed.';
                tagEditorError.style.display = 'block';
                tagEditorGuessBtn.textContent = 'Guess Again';
            } finally {
                tagEditorGuessBtn.disabled = false;
                if (tagEditorGuessBtn.textContent === 'Guessing...') {
                    tagEditorGuessBtn.textContent = originalText;
                }
            }
        }

        async function redownloadJob(jobId) {
            try {
                showToast('Re-downloading...');
                const response = await apiFetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Re-download failed');
                }
                showToast('Queued for re-download');
                loadJobs();
            } catch (error) {
                showToast(error.message || 'Re-download failed', true);
            }
        }

        async function deleteJobFile(jobId, trackName) {
            if (!confirm(`Move "${trackName}" to trash?`)) return;

            try {
                const response = await apiFetch(`/api/jobs/${jobId}/file`, { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Delete failed');
                }
                const data = await response.json();
                if (data.trashed) {
                    showToast(`Moved to trash (${data.deleted.length} file(s))`);
                } else if (data.deleted.length > 0) {
                    showToast(`Deleted ${data.deleted.length} file(s)`);
                } else {
                    showToast('File was already missing, marked as deleted');
                }
                loadJobs();
                loadTrash();
            } catch (error) {
                showToast(error.message || 'Delete failed', true);
                loadJobs();
            }
        }

        // =============================================================================
        // Trash Bin
        // =============================================================================

        async function loadTrash() {
            const section = document.getElementById('trashSection');
            const list = document.getElementById('trashList');
            try {
                const response = await apiFetch('/api/trash');
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to load trash');
                }

                if (!data.files.length) {
                    section.style.display = 'none';
                    list.innerHTML = '';
                    return;
                }

                section.style.display = 'block';
                const sizeLabel = formatFileSize(data.total_size);

                list.innerHTML = `
                    <div style="margin-bottom: 8px; font-size: 12px; color: var(--text-muted);">
                        ${data.files.length} file(s), ${sizeLabel} total
                    </div>
                    ${data.files.map(f => `
                        <div class="job-item" style="cursor: default;">
                            <div class="job-status completed"></div>
                            <div class="job-info">
                                <div class="job-title">${escapeHtml(f.name)}</div>
                                <div class="job-meta">${escapeHtml(f.path)} &middot; ${formatFileSize(f.size)}</div>
                            </div>
                            <div style="display: flex; gap: 6px; align-items: center; flex-shrink: 0;">
                                <button class="library-play-btn" onclick="playTrashAudio('${escapeAttr(f.path)}', this)" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;"><i class="fa-solid fa-play"></i></button>
                                <button onclick="restoreTrashFile('${escapeAttr(f.path)}')" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Restore</button>
                                <button onclick="deleteTrashFile('${escapeAttr(f.path)}', '${escapeAttr(f.name)}')" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--error); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Delete</button>
                            </div>
                        </div>
                    `).join('')}
                `;
            } catch (e) {
                console.error('Failed to load trash', e);
                section.style.display = 'block';
                list.innerHTML = `
                    <div class="job-item" style="cursor: default;">
                        <div class="job-info">
                            <div class="job-title">Trash unavailable</div>
                            <div class="job-meta">${escapeHtml(e?.message || 'Failed to load trash')}</div>
                        </div>
                    </div>
                `;
            }
        }

        function formatFileSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
            return (bytes / 1073741824).toFixed(2) + ' GB';
        }

        async function restoreTrashFile(path) {
            try {
                const response = await apiFetch(`/api/trash/restore?path=${encodeURIComponent(path)}`, { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Restore failed');
                }
                const data = await response.json();
                showToast(`Restored: ${data.restored}`);
                loadTrash();
            } catch (error) {
                showToast(error.message || 'Restore failed', true);
            }
        }

        async function deleteTrashFile(path, name) {
            if (!confirm(`Permanently delete "${name}"? This cannot be undone.`)) return;
            try {
                const response = await apiFetch(`/api/trash/file?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Delete failed');
                }
                showToast('Permanently deleted');
                loadTrash();
            } catch (error) {
                showToast(error.message || 'Delete failed', true);
            }
        }

        async function emptyTrash() {
            if (!confirm('Permanently delete everything in the trash? This cannot be undone.')) return;
            try {
                const response = await apiFetch('/api/trash', { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Failed to empty trash');
                }
                const data = await response.json();
                showToast(`Trash emptied (${data.deleted} file(s) removed)`);
                loadTrash();
            } catch (error) {
                showToast(error.message || 'Failed to empty trash', true);
            }
        }

        // =============================================================================
        // Library / Trash Playback
        // =============================================================================

        let _libraryPlayingId = null;

        function _startLibraryStream(id, url, btn) {
            stopPreview();
            stopLibraryPlayback();
            _libraryPlayingId = id;
            if (btn) btn.innerHTML = '<i class="fa-solid fa-stop"></i>';
            previewAudio.oncanplay = () => {
                previewAudio.oncanplay = null;
                previewAudio.play().catch(() => {});
            };
            previewAudio.onended = () => stopLibraryPlayback();
            previewAudio.onerror = (e) => {
                // Ignore aborted loads (caused by stopLibraryPlayback clearing src)
                if (previewAudio.error && previewAudio.error.code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED) {
                    stopLibraryPlayback();
                    showToast('Could not play file', true);
                }
            };
            previewAudio.volume = previewVolume;
            previewAudio.src = url;
            previewAudio.load();
        }

        function playJobAudio(jobId, btn) {
            if (_libraryPlayingId === jobId) { stopLibraryPlayback(); return; }
            _startLibraryStream(jobId, `/api/jobs/${jobId}/stream`, btn);
        }

        function playTrashAudio(path, btn) {
            if (_libraryPlayingId === 'trash:' + path) { stopLibraryPlayback(); return; }
            _startLibraryStream('trash:' + path, `/api/trash/stream?path=${encodeURIComponent(path)}`, btn);
        }

        function stopLibraryPlayback() {
            if (!_libraryPlayingId) return;
            previewAudio.oncanplay = null;
            previewAudio.onended = null;
            previewAudio.onerror = null;
            previewAudio.pause();
            previewAudio.src = '';
            // Reset all play buttons back to the play icon
            document.querySelectorAll('.library-play-btn').forEach(b => {
                b.innerHTML = '<i class="fa-solid fa-play"></i>';
            });
            _libraryPlayingId = null;
        }

        // =============================================================================
        // Report / Blacklist
        // =============================================================================

        function openReportDialog(jobId, videoId, uploader, source) {
            document.getElementById('reportJobId').value = jobId;
            document.getElementById('reportVideoId').value = videoId;
            document.getElementById('reportSource').value = source;
            document.getElementById('reportUploader').value = uploader;
            document.getElementById('reportReason').value = 'wrong_track';
            document.getElementById('reportNote').value = '';
            document.getElementById('reportBlockUploader').checked = false;

            const uploaderRow = document.getElementById('reportUploaderRow');
            const uploaderName = document.getElementById('reportUploaderName');
            if (uploader) {
                uploaderName.textContent = uploader;
                uploaderRow.style.display = 'block';
            } else {
                uploaderRow.style.display = 'none';
            }

            const overlay = document.getElementById('reportOverlay');
            overlay.style.display = 'flex';
        }

        function closeReportDialog() {
            document.getElementById('reportOverlay').style.display = 'none';
        }

        tagEditorOverlay.addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeTagEditor();
        });
        tagEditorGuessBtn.addEventListener('click', guessTagEditorFromMusicBrainz);
        tagEditorResetBtn.addEventListener('click', resetTagEditorDraft);
        tagEditorSaveBtn.addEventListener('click', saveTagEditor);
        [tagEditorArtist, tagEditorTitle, tagEditorAlbum, tagEditorAlbumArtist, tagEditorYear, tagEditorTrackNumber, tagEditorTrackTotal].forEach((input) => {
            input.addEventListener('input', () => {
                if (tagEditorError.style.display !== 'none') {
                    tagEditorError.style.display = 'none';
                    tagEditorError.textContent = '';
                }
                renderTagEditorPreview();
            });
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    saveTagEditor();
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    closeTagEditor();
                }
            });
        });

        // Close on overlay click (not the dialog itself)
        document.getElementById('reportOverlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeReportDialog();
        });
        document.getElementById('missingTrackVersionsOverlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeMissingTrackVersionsModal();
        });
        document.getElementById('missingTrackVersionsRefreshBtn').addEventListener('click', () => {
            loadMissingTrackVersions();
        });

        async function submitReport() {
            const btn = document.getElementById('reportSubmitBtn');
            btn.disabled = true;
            btn.textContent = 'Reporting...';

            try {
                const payload = {
                    job_id: document.getElementById('reportJobId').value || null,
                    video_id: document.getElementById('reportVideoId').value || null,
                    uploader: document.getElementById('reportUploader').value || null,
                    source: document.getElementById('reportSource').value || 'youtube',
                    reason: document.getElementById('reportReason').value,
                    note: document.getElementById('reportNote').value || null,
                    block_uploader: document.getElementById('reportBlockUploader').checked
                };

                const response = await apiFetch('/api/blacklist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Report failed');
                }

                showToast('Track reported and blacklisted');
                closeReportDialog();
            } catch (error) {
                showToast(error.message || 'Report failed', true);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Report';
            }
        }

        const _reasonLabels = {
            wrong_track: 'Wrong track',
            poor_quality: 'Poor quality',
            slowed_pitched: 'Slowed/pitched',
            contentid: 'ContentID dodge',
            other: 'Other'
        };

        async function loadBlacklist() {
            const container = document.getElementById('blacklistContent');
            if (!container) return;

            try {
                const response = await apiFetch('/api/blacklist?limit=200');
                if (!response.ok) throw new Error('Failed to load blacklist');

                const data = await response.json();
                const entries = data.entries || [];

                if (entries.length === 0) {
                    container.innerHTML = '<p style="color:var(--text-secondary); font-style:italic;">No blacklisted items yet. Use the Report button on queue items to flag bad tracks.</p>';
                    return;
                }

                container.innerHTML = `
                    <table style="width:100%; border-collapse:collapse; font-size:12px;">
                        <thead>
                            <tr style="text-align:left; color:var(--text-secondary); border-bottom:1px solid var(--border);">
                                <th style="padding:6px 8px;">Type</th>
                                <th style="padding:6px 8px;">Value</th>
                                <th style="padding:6px 8px;">Source</th>
                                <th style="padding:6px 8px;">Reason</th>
                                <th style="padding:6px 8px;">Date</th>
                                <th style="padding:6px 8px;"></th>
                            </tr>
                        </thead>
                        <tbody>
                            ${entries.map(e => {
                                const isUploader = !e.video_id && e.uploader;
                                const typeLabel = isUploader ? 'Uploader' : 'Video';
                                const value = isUploader ? e.uploader : (e.video_id || '?');
                                const reasonLabel = _reasonLabels[e.reason] || e.reason || '';
                                const dateStr = formatDateYmd(e.created_at);
                                return `<tr style="border-bottom:1px solid var(--border);">
                                    <td style="padding:6px 8px;"><span style="display:inline-block; padding:2px 6px; font-size:10px; font-weight:600; border-radius:4px; background:${isUploader ? 'var(--warning)' : 'var(--error)'}; color:#000;">${typeLabel}</span></td>
                                    <td style="padding:6px 8px; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(value)}">${escapeHtml(value)}</td>
                                    <td style="padding:6px 8px;">${escapeHtml(e.source || '')}</td>
                                    <td style="padding:6px 8px;">${escapeHtml(reasonLabel)}</td>
                                    <td style="padding:6px 8px;">${dateStr}</td>
                                    <td style="padding:6px 8px;"><button onclick="removeBlacklistEntry(${e.id})" style="padding:3px 8px; font-size:11px; font-family:inherit; font-weight:600; background:var(--bg-tertiary); color:var(--error); border:1px solid var(--border); border-radius:4px; cursor:pointer;">Remove</button></td>
                                </tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                    <p style="margin-top:8px; color:var(--text-secondary); font-size:11px;">${entries.length} entr${entries.length === 1 ? 'y' : 'ies'}</p>
                `;
            } catch (error) {
                container.innerHTML = '<p style="color:var(--error);">Failed to load blacklist</p>';
            }
        }

        async function removeBlacklistEntry(entryId) {
            try {
                const response = await apiFetch(`/api/blacklist/${entryId}`, { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Remove failed');
                }
                showToast('Blacklist entry removed');
                loadBlacklist();
            } catch (error) {
                showToast(error.message || 'Remove failed', true);
            }
        }

        function formatTimeFull(isoString) {
            return formatDateYmd(isoString);
        }

        function formatDuration(startIso, endIso) {
            if (!startIso || !endIso) return '';
            const start = new Date(startIso);
            const end = new Date(endIso);
            const diffMs = end - start;
            if (diffMs < 0) return '';
            const secs = Math.floor(diffMs / 1000);
            if (secs < 60) return `${secs}s`;
            const mins = Math.floor(secs / 60);
            const remainSecs = secs % 60;
            if (mins < 60) return `${mins}m ${remainSecs}s`;
            const hrs = Math.floor(mins / 60);
            const remainMins = mins % 60;
            return `${hrs}h ${remainMins}m`;
        }

        function showToast(message, isError = false) {
            toast.textContent = message;
            toast.className = 'toast' + (isError ? ' error' : '');
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2500);
        }

        function showVolumeMountWarning() {
            // Don't show if user dismissed it
            if (localStorage.getItem('dismissedVolumeWarning') === 'true') return;

            const banner = document.createElement('div');
            banner.className = 'warning-banner';
            banner.innerHTML = `
                <div class="warning-content">
                    <strong>Warning:</strong> Music directory doesn't appear to be mounted.
                    Downloads will be lost when the container restarts.
                    <a href="https://gitlab.com/g33kphr33k/musicgrabber#troubleshooting" target="_blank" rel="noopener">See setup guide</a>
                </div>
                <button class="warning-dismiss" onclick="dismissVolumeWarning(this.parentElement)">&times;</button>
            `;
            document.body.insertBefore(banner, document.body.firstChild);
        }

        function dismissVolumeWarning(banner) {
            localStorage.setItem('dismissedVolumeWarning', 'true');
            banner.remove();
        }

        async function bulkImport() {
            const songs = bulkInput.value.trim();
            if (!songs) {
                showToast('Please enter some songs', true);
                return;
            }

            // Validate playlist name if checkbox is checked
            const createPlaylist = createPlaylistCheckbox.checked;
            const playlistName = playlistNameInput.value.trim();

            if (createPlaylist && !playlistName) {
                showToast('Please enter a playlist name', true);
                playlistNameInput.focus();
                return;
            }

            bulkImportBtn.disabled = true;
            bulkImportBtn.textContent = 'Starting...';
            bulkResults.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

            try {
                const requestBody = {
                    songs,
                    convert_to_flac: convertToFlacCheckbox.checked
                };
                if (createPlaylist) {
                    requestBody.create_playlist = true;
                    requestBody.playlist_name = playlistName;
                    if (playlistNameInput.dataset.sourceUrl) {
                        requestBody.playlist_source_url = playlistNameInput.dataset.sourceUrl;
                    }
                    const usePlaylistsDirCheckbox = document.getElementById('usePlaylistsDirCheckbox');
                    if ((usePlaylistsDirCheckbox && usePlaylistsDirCheckbox.checked) || (!usePlaylistsDirCheckbox && serverConfig.playlists_subdir)) {
                        requestBody.use_playlists_dir = true;
                    }
                }
                const bulkPriority = document.getElementById('bulkPrioritySource');
                if (bulkPriority && bulkPriority.value) {
                    requestBody.priority_source = bulkPriority.value;
                    requestBody.preferred_sources = bulkPriority.value;
                }

                // Start the async import
                const response = await apiFetch('/api/bulk-import-async', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Bulk import failed');
                }

                const data = await response.json();
                currentBulkImportId = data.import_id;

                // Clear the textarea
                bulkInput.value = '';
                playlistNameInput.value = '';
                delete playlistNameInput.dataset.sourceUrl;
                createPlaylistCheckbox.checked = false;
                playlistNameInput.style.display = 'none';
                const usePlaylistsDirCheckboxCleared = document.getElementById('usePlaylistsDirCheckbox');
                if (usePlaylistsDirCheckboxCleared) usePlaylistsDirCheckboxCleared.checked = false;
                const usePlaylistsDirRowCleared = document.getElementById('usePlaylistsDirRow');
                if (usePlaylistsDirRowCleared) usePlaylistsDirRowCleared.style.display = 'none';
                updateLineCounter();

                // Show initial progress
                showBulkImportProgress({
                    status: 'pending',
                    total_tracks: data.total_tracks,
                    searched: 0,
                    queued: 0,
                    failed: 0
                });

                // Start polling for progress
                startBulkImportPolling(data.import_id);

            } catch (error) {
                bulkResults.innerHTML = '';
                showToast(error.message || 'Bulk import failed', true);
                bulkImportBtn.disabled = false;
                bulkImportBtn.textContent = 'Import & Download All';
            }
        }

        function showBulkImportProgress(data) {
            const searchDone = data.status === 'completed' || data.status === 'error';
            // Show search progress while searching, download progress once searches are done
            const percent = data.total_tracks > 0
                ? (searchDone
                    ? Math.round(((data.completed + data.failed + (data.skipped || 0)) / data.total_tracks) * 100)
                    : Math.round((data.searched / data.total_tracks) * 100))
                : 0;
            const isComplete = data.complete;

            let statusText = 'Processing...';
            let statusColor = 'var(--accent)';
            if (data.rate_limited) {
                statusText = 'Rate limited - waiting...';
                statusColor = 'var(--warning)';
            } else if (data.complete) {
                statusText = 'Complete';
                statusColor = 'var(--accent)';
            } else if (data.status === 'completed' && data.queued > 0) {
                statusText = `Downloading... (${data.queued} remaining)`;
                statusColor = 'var(--accent)';
            } else if (data.status === 'error') {
                statusText = 'Error';
                statusColor = 'var(--error)';
            }

            let html = `
                <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <div style="font-size: 14px; font-weight: 600;">Import Progress</div>
                        <div style="font-size: 12px; color: ${statusColor};">${statusText}</div>
                    </div>

                    <!-- Progress bar -->
                    <div style="background: var(--bg-tertiary); border-radius: 4px; height: 8px; margin-bottom: 12px; overflow: hidden;">
                        <div style="background: ${data.rate_limited ? 'var(--warning)' : 'var(--accent)'}; height: 100%; width: ${percent}%; transition: width 0.3s ease;"></div>
                    </div>

                    <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; text-align: center;">
                        <div>
                            <div style="font-size: 16px; font-weight: 600;">${data.searched}</div>
                            <div style="font-size: 10px; color: var(--text-secondary);">Searched</div>
                        </div>
                        <div>
                            <div style="font-size: 16px; font-weight: 600; color: var(--warning);">${data.queued}</div>
                            <div style="font-size: 10px; color: var(--text-secondary);">Queued</div>
                        </div>
                        <div>
                            <div style="font-size: 16px; font-weight: 600; color: var(--accent);">${data.completed || 0}</div>
                            <div style="font-size: 10px; color: var(--text-secondary);">Done</div>
                        </div>
                        <div>
                            <div style="font-size: 16px; font-weight: 600; color: var(--error);">${data.failed}</div>
                            <div style="font-size: 10px; color: var(--text-secondary);">Failed</div>
                        </div>
                        <div>
                            <div style="font-size: 16px; font-weight: 600;">${data.total_tracks}</div>
                            <div style="font-size: 10px; color: var(--text-secondary);">Total</div>
                        </div>
                    </div>
                </div>
            `;

            // Show recent tracks
            if (data.recent_tracks && data.recent_tracks.length > 0) {
                html += `
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px;">
                        <div style="font-size: 13px; font-weight: 600; margin-bottom: 8px; color: var(--text-secondary);">Recent Activity</div>
                        ${data.recent_tracks.slice(0, 5).map(track => {
                            let icon = '...';
                            let color = 'var(--text-secondary)';
                            if (track.status === 'queued') {
                                icon = '+';
                                color = 'var(--accent)';
                            } else if (track.status === 'failed') {
                                icon = 'x';
                                color = 'var(--error)';
                            } else if (track.status === 'searching') {
                                icon = '?';
                            }
                            return `
                                <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; display: flex; align-items: center; gap: 8px;">
                                    <span style="color: ${color}; width: 16px;">${icon}</span>
                                    <span>${escapeHtml(track.artist)} - ${escapeHtml(track.song)}</span>
                                    ${track.error ? `<span style="color: var(--error); font-size: 11px;">(${escapeHtml(track.error)})</span>` : ''}
                                </div>
                            `;
                        }).join('')}
                    </div>
                `;
            }

            // Show error if any
            if (data.error) {
                html += `
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--error); border-radius: 12px; margin-top: 12px;">
                        <div style="font-size: 13px; color: var(--error);">${escapeHtml(data.error)}</div>
                    </div>
                `;
            }

            bulkResults.innerHTML = html;

            // Update button state
            if (isComplete) {
                bulkImportBtn.disabled = false;
                bulkImportBtn.textContent = 'Import & Download All';
                if (data.status === 'completed') {
                    showToast(`Import complete: ${data.queued} queued, ${data.failed} failed`);
                }
            } else {
                bulkImportBtn.disabled = true;
                bulkImportBtn.textContent = `Processing ${data.searched}/${data.total_tracks}...`;
            }
        }

        function startBulkImportPolling(importId) {
            // Clear any existing polling
            if (bulkImportPollInterval) {
                clearInterval(bulkImportPollInterval);
            }

            // Poll every 2 seconds
            bulkImportPollInterval = setInterval(async () => {
                try {
                    const response = await apiFetch(`/api/bulk-import/${importId}/status`);
                    if (!response.ok) {
                        throw new Error('Failed to get status');
                    }

                    const data = await response.json();
                    showBulkImportProgress(data);

                    // Stop polling when complete
                    if (data.complete) {
                        clearInterval(bulkImportPollInterval);
                        bulkImportPollInterval = null;
                        currentBulkImportId = null;
                    }
                } catch (error) {
                    console.error('Polling error:', error);
                    // Keep polling, might be a transient error
                }
            }, 2000);
        }

        // =============================================================
        // Albums tab
        // =============================================================

        let albumSelectedArtist = null;  // {mbid, name}
        let albumSelectedRelease = null; // {release_mbid, title, year}
        let albumPollInterval = null;
        let albumSelectedM3uName = null;

        function setAlbumResetVisible(visible) {
            const resetBtn = document.getElementById('albumResetBtn');
            if (!resetBtn) return;
            resetBtn.classList.toggle('show', !!visible);
        }

        function resetAlbumFormAndScrollTop() {
            const input = document.getElementById('albumArtistInput');
            const resultsEl = document.getElementById('albumArtistResults');
            const listSection = document.getElementById('albumListSection');
            const listEl = document.getElementById('albumList');
            const listHeading = document.getElementById('albumListHeading');
            const tracklistSection = document.getElementById('albumTracklistSection');
            const tracklistEl = document.getElementById('albumTracklist');
            const tracklistHeading = document.getElementById('albumTracklistHeading');
            const warningEl = document.getElementById('albumExistingWarning');
            const legendEl = document.getElementById('albumTrackLegend');
            const progressEl = document.getElementById('albumProgress');
            const downloadBtn = document.getElementById('albumDownloadBtn');
            const makeM3u = document.getElementById('albumMakeM3u');
            const m3uHintEl = document.getElementById('albumM3uHint');

            if (albumPollInterval) { clearInterval(albumPollInterval); albumPollInterval = null; }
            albumSelectedArtist = null;
            albumSelectedRelease = null;

            if (input) input.value = '';
            if (resultsEl) {
                resultsEl.style.display = 'none';
                resultsEl.innerHTML = '';
            }
            if (listSection) listSection.style.display = 'none';
            if (listEl) listEl.innerHTML = '';
            if (listHeading) listHeading.textContent = '';
            if (tracklistSection) tracklistSection.style.display = 'none';
            if (tracklistEl) tracklistEl.innerHTML = '';
            if (tracklistHeading) tracklistHeading.textContent = '';
            if (warningEl) { warningEl.style.display = 'none'; warningEl.textContent = ''; }
            if (legendEl) { legendEl.style.display = 'none'; legendEl.textContent = ''; }
            if (progressEl) { progressEl.style.display = 'none'; progressEl.innerHTML = ''; }
            if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }
            albumSelectedM3uName = null;
            if (makeM3u) makeM3u.checked = false;
            if (m3uHintEl) { m3uHintEl.style.display = 'none'; m3uHintEl.textContent = ''; }

            setAlbumResetVisible(false);

            const albumsTab = document.getElementById('albumsTabContainer');
            if (albumsTab) albumsTab.scrollIntoView({ behavior: 'smooth', block: 'start' });
            if (input) input.focus({ preventScroll: true });
        }

        // -------------------------------------------------------------------
        // Open album from search results (clickable album name / suggestion chip)
        // -------------------------------------------------------------------

        function findMatchingAlbum(albums, searchTitle) {
            if (!searchTitle || !albums.length) return null;
            function normalise(s) {
                return s.toLowerCase()
                    .replace(/\s*\(.*?\)\s*/g, ' ')
                    .replace(/\s*\[.*?\]\s*/g, ' ')
                    .replace(/[^\w\s]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
            }
            const norm = normalise(searchTitle);
            // Exact case-insensitive
            for (const a of albums) if (a.title.toLowerCase() === searchTitle.toLowerCase()) return a;
            // Normalised
            for (const a of albums) if (normalise(a.title) === norm) return a;
            // Containment (handles "Album" vs "Album (Deluxe)")
            for (const a of albums) {
                const na = normalise(a.title);
                if (na && norm && (na.includes(norm) || norm.includes(na))) return a;
            }
            return null;
        }

        async function openAlbumFromSearch(opts) {
            // opts: { artistName, albumTitle, artistMbid?, releaseMbid? }
            if (!opts.artistName || !opts.albumTitle) return;

            // Switch to Albums tab
            const albumsTabBtn = document.querySelector('[data-tab="albums"]');
            if (albumsTabBtn) albumsTabBtn.click();

            // Set the artist name in the input for visual context
            const input = document.getElementById('albumArtistInput');
            if (input) input.value = opts.artistName;

            const resultsEl = document.getElementById('albumArtistResults');
            const listSection = document.getElementById('albumListSection');
            const listEl = document.getElementById('albumList');
            const headingEl = document.getElementById('albumListHeading');
            const tracklistSection = document.getElementById('albumTracklistSection');
            const progressEl = document.getElementById('albumProgress');

            // Reset state
            albumSelectedArtist = null;
            albumSelectedRelease = null;
            albumSelectedM3uName = null;
            if (albumPollInterval) { clearInterval(albumPollInterval); albumPollInterval = null; }
            const downloadBtn = document.getElementById('albumDownloadBtn');
            if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }
            if (tracklistSection) tracklistSection.style.display = 'none';
            if (progressEl) progressEl.style.display = 'none';
            setAlbumResetVisible(false);

            if (opts.artistMbid && opts.releaseMbid) {
                // Fast path: backend already resolved MBIDs
                albumSelectedArtist = { mbid: opts.artistMbid, name: opts.artistName };

                // Show artist as selected
                if (resultsEl) {
                    resultsEl.style.display = '';
                    resultsEl.innerHTML = '';
                    const btn = document.createElement('button');
                    btn.className = 'album-artist-btn selected';
                    btn.innerHTML = `<span class="album-artist-name">${escapeHtml(opts.artistName)}</span>`;
                    btn.addEventListener('click', () => selectAlbumArtist(albumSelectedArtist, btn));
                    resultsEl.appendChild(btn);
                }

                // Load album list, then auto-select the matching one
                if (listSection) listSection.style.display = 'block';
                if (headingEl) headingEl.textContent = `Albums by ${opts.artistName}`;
                if (listEl) listEl.innerHTML = '<p class="bulk-intro-text">Loading albums\u2026</p>';

                try {
                    const resp = await apiFetch(`/api/albums/artist/${encodeURIComponent(opts.artistMbid)}/albums`);
                    if (!resp.ok) throw new Error('Failed to load albums');
                    const data = await resp.json();
                    const albums = data.albums || [];
                    if (!albums.length) {
                        if (listEl) listEl.innerHTML = '<p class="bulk-intro-text">No albums found.</p>';
                        return;
                    }
                    if (listEl) {
                        listEl.innerHTML = '';
                        let matchedBtn = null;
                        for (const album of albums) {
                            const abtn = document.createElement('button');
                            abtn.className = 'album-list-btn';
                            if (album.release_mbid === opts.releaseMbid) {
                                abtn.classList.add('selected');
                                matchedBtn = abtn;
                            }
                            abtn.innerHTML = `<span class="album-list-title">${escapeHtml(album.title)}</span>`
                                + (album.year ? ` <span class="album-list-year">${escapeHtml(album.year)}</span>` : '');
                            abtn.addEventListener('click', () => selectAlbum(album, abtn));
                            listEl.appendChild(abtn);
                        }
                    }
                    // Auto-select the album to load its tracklist
                    const target = albums.find(a => a.release_mbid === opts.releaseMbid);
                    if (target) {
                        await selectAlbum(target, listEl?.querySelector('.album-list-btn.selected'));
                    }
                } catch (e) {
                    if (listEl) listEl.innerHTML = `<p class="bulk-intro-text error-text">Failed: ${escapeHtml(e.message)}</p>`;
                }

            } else {
                // Slow path: need to look up artist and find the album
                if (resultsEl) {
                    resultsEl.style.display = '';
                    resultsEl.innerHTML = '<p class="bulk-intro-text">Searching for artist\u2026</p>';
                }

                try {
                    const artistResp = await apiFetch(`/api/albums/search-artist?q=${encodeURIComponent(opts.artistName)}`);
                    if (!artistResp.ok) throw new Error('Artist search failed');
                    const artistData = await artistResp.json();
                    const artists = artistData.artists || [];

                    if (!artists.length) {
                        if (resultsEl) resultsEl.innerHTML = '<p class="bulk-intro-text">No matching artist found on MusicBrainz.</p>';
                        return;
                    }

                    const bestArtist = artists[0];
                    albumSelectedArtist = bestArtist;

                    // Render artist buttons with the best one pre-selected
                    if (resultsEl) {
                        resultsEl.innerHTML = '';
                        for (const a of artists) {
                            const btn = document.createElement('button');
                            btn.className = 'album-artist-btn';
                            if (a.mbid === bestArtist.mbid) btn.classList.add('selected');
                            btn.innerHTML = `<span class="album-artist-name">${escapeHtml(a.name)}</span>`
                                + (a.disambiguation ? ` <span class="album-artist-disambig">${escapeHtml(a.disambiguation)}</span>` : '');
                            btn.addEventListener('click', () => selectAlbumArtist(a, btn));
                            resultsEl.appendChild(btn);
                        }
                    }

                    // Load albums
                    if (listSection) listSection.style.display = 'block';
                    if (headingEl) headingEl.textContent = `Albums by ${bestArtist.name}`;
                    if (listEl) listEl.innerHTML = '<p class="bulk-intro-text">Loading albums\u2026</p>';

                    const albumsResp = await apiFetch(`/api/albums/artist/${encodeURIComponent(bestArtist.mbid)}/albums`);
                    if (!albumsResp.ok) throw new Error('Failed to load albums');
                    const albumsData = await albumsResp.json();
                    const albums = albumsData.albums || [];

                    if (!albums.length) {
                        if (listEl) listEl.innerHTML = '<p class="bulk-intro-text">No albums found for this artist.</p>';
                        return;
                    }

                    // Fuzzy-match the album title
                    const matchedAlbum = findMatchingAlbum(albums, opts.albumTitle);

                    if (listEl) {
                        listEl.innerHTML = '';
                        for (const album of albums) {
                            const abtn = document.createElement('button');
                            abtn.className = 'album-list-btn';
                            if (matchedAlbum && album.release_mbid === matchedAlbum.release_mbid) {
                                abtn.classList.add('selected');
                            }
                            abtn.innerHTML = `<span class="album-list-title">${escapeHtml(album.title)}</span>`
                                + (album.year ? ` <span class="album-list-year">${escapeHtml(album.year)}</span>` : '');
                            abtn.addEventListener('click', () => selectAlbum(album, abtn));
                            listEl.appendChild(abtn);
                        }
                    }

                    if (matchedAlbum) {
                        await selectAlbum(matchedAlbum, listEl?.querySelector('.album-list-btn.selected'));
                    } else {
                        showToast(`Album "${opts.albumTitle}" not found in MusicBrainz. Pick one manually.`, true);
                    }
                } catch (e) {
                    if (resultsEl) resultsEl.innerHTML = `<p class="bulk-intro-text error-text">Failed: ${escapeHtml(e.message)}</p>`;
                }
            }
        }

        async function _readApiErrorDetail(resp) {
            // FastAPI puts errors in {detail: "..."}. Fall back to the status line if
            // the body is empty / not JSON.
            try {
                const data = await resp.json();
                if (data && data.detail) return String(data.detail);
            } catch {}
            return `HTTP ${resp.status}`;
        }

        function _renderAlbumRetryError(containerEl, message, onRetry, asListItem = false) {
            if (!containerEl) return;
            const wrap = document.createElement(asListItem ? 'li' : 'p');
            if (!asListItem) wrap.className = 'bulk-intro-text error-text';
            else wrap.className = 'album-track-item album-track-error';
            wrap.textContent = message + ' ';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'album-retry-btn';
            btn.textContent = 'Retry';
            btn.addEventListener('click', () => { onRetry(); });
            wrap.appendChild(btn);
            containerEl.innerHTML = '';
            containerEl.appendChild(wrap);
        }

        async function searchAlbumArtist() {
            const input = document.getElementById('albumArtistInput');
            const resultsEl = document.getElementById('albumArtistResults');
            const listSection = document.getElementById('albumListSection');
            const tracklistSection = document.getElementById('albumTracklistSection');
            const progressEl = document.getElementById('albumProgress');
            const q = input ? input.value.trim() : '';
            if (!q) return;

            resultsEl.innerHTML = '<p class="bulk-intro-text">Searching\u2026</p>';
            // Let CSS control layout mode (flex/grid); just remove the inline "display:none".
            resultsEl.style.display = '';
            if (listSection) listSection.style.display = 'none';
            if (tracklistSection) tracklistSection.style.display = 'none';
            if (progressEl) progressEl.style.display = 'none';
            setAlbumResetVisible(false);
            albumSelectedArtist = null;
            albumSelectedRelease = null;
            albumSelectedM3uName = null;
            // Reset download button and any in-flight poll from a previous download
            if (albumPollInterval) { clearInterval(albumPollInterval); albumPollInterval = null; }
            const downloadBtn = document.getElementById('albumDownloadBtn');
            if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }

            try {
                const resp = await apiFetch(`/api/albums/search-artist?q=${encodeURIComponent(q)}`);
                if (!resp.ok) {
                    const detail = await _readApiErrorDetail(resp);
                    _renderAlbumRetryError(resultsEl, `Search failed: ${detail}`, searchAlbumArtist);
                    return;
                }
                const data = await resp.json();
                const artists = data.artists || [];
                if (!artists.length) {
                    resultsEl.innerHTML = '<p class="bulk-intro-text">No artists found.</p>';
                    return;
                }
                resultsEl.innerHTML = '';
                for (const a of artists) {
                    const btn = document.createElement('button');
                    btn.className = 'album-artist-btn';
                    btn.innerHTML = `<span class="album-artist-name">${escapeHtml(a.name)}</span>`
                        + (a.disambiguation ? ` <span class="album-artist-disambig">${escapeHtml(a.disambiguation)}</span>` : '');
                    btn.addEventListener('click', () => selectAlbumArtist(a, btn));
                    resultsEl.appendChild(btn);
                }
            } catch (e) {
                _renderAlbumRetryError(resultsEl, `Search failed: ${e.message}`, searchAlbumArtist);
            }
        }

        async function selectAlbumArtist(artist, btn) {
            albumSelectedArtist = artist;
            albumSelectedRelease = null;
            albumSelectedM3uName = null;
            document.querySelectorAll('.album-artist-btn').forEach(b => b.classList.remove('selected'));
            if (btn) btn.classList.add('selected');

            const listSection = document.getElementById('albumListSection');
            const listEl = document.getElementById('albumList');
            const headingEl = document.getElementById('albumListHeading');
            const tracklistSection = document.getElementById('albumTracklistSection');
            if (listSection) listSection.style.display = 'block';
            if (tracklistSection) tracklistSection.style.display = 'none';
            setAlbumResetVisible(false);
            if (headingEl) headingEl.textContent = `Albums by ${artist.name}`;
            if (listEl) listEl.innerHTML = '<p class="bulk-intro-text">Loading albums\u2026</p>';

            try {
                const resp = await apiFetch(`/api/albums/artist/${encodeURIComponent(artist.mbid)}/albums`);
                if (!resp.ok) {
                    const detail = await _readApiErrorDetail(resp);
                    _renderAlbumRetryError(listEl, `Failed to load albums: ${detail}`, () => selectAlbumArtist(artist, btn));
                    return;
                }
                const data = await resp.json();
                const albums = data.albums || [];
                if (!listEl) return;
                if (!albums.length) {
                    listEl.innerHTML = '<p class="bulk-intro-text">No albums found.</p>';
                    return;
                }
                listEl.innerHTML = '';
                for (const album of albums) {
                    const albumBtn = document.createElement('button');
                    albumBtn.className = 'album-list-btn';
                    albumBtn.innerHTML = `<span class="album-list-title">${escapeHtml(album.title)}</span>`
                        + (album.year ? ` <span class="album-list-year">${escapeHtml(album.year)}</span>` : '');
                    albumBtn.addEventListener('click', () => selectAlbum(album, albumBtn));
                    listEl.appendChild(albumBtn);
                }
            } catch (e) {
                _renderAlbumRetryError(listEl, `Failed to load albums: ${e.message}`, () => selectAlbumArtist(artist, btn));
            }
        }

        async function selectAlbum(album, btn) {
            albumSelectedRelease = album;
            document.querySelectorAll('.album-list-btn').forEach(b => b.classList.remove('selected'));
            if (btn) btn.classList.add('selected');

            const tracklistSection = document.getElementById('albumTracklistSection');
            const tracklistEl = document.getElementById('albumTracklist');
            const headingEl = document.getElementById('albumTracklistHeading');
            const warningEl = document.getElementById('albumExistingWarning');
            const legendEl = document.getElementById('albumTrackLegend');
            const downloadBtn = document.getElementById('albumDownloadBtn');
            const makeM3u = document.getElementById('albumMakeM3u');
            const m3uHintEl = document.getElementById('albumM3uHint');
            // Reset button text whenever a different album is selected
            if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }
            setAlbumResetVisible(false);
            if (warningEl) { warningEl.style.display = 'none'; warningEl.textContent = ''; }
            if (legendEl) { legendEl.style.display = 'none'; legendEl.textContent = ''; }
            albumSelectedM3uName = null;
            if (makeM3u) makeM3u.checked = false;
            if (m3uHintEl) { m3uHintEl.style.display = 'none'; m3uHintEl.textContent = ''; }
            if (tracklistSection) tracklistSection.style.display = 'block';
            if (headingEl) headingEl.textContent = `${album.title}${album.year ? ' (' + album.year + ')' : ''}`;
            if (tracklistEl) tracklistEl.innerHTML = '<li>Loading\u2026</li>';
            if (downloadBtn) downloadBtn.disabled = true;

            try {
                const resp = await apiFetch(`/api/albums/release/${encodeURIComponent(album.release_mbid)}/tracks`);
                if (!resp.ok) {
                    const detail = await _readApiErrorDetail(resp);
                    _renderAlbumRetryError(tracklistEl, `Failed to load tracklist: ${detail}`, () => selectAlbum(album, btn), true);
                    return;
                }
                const data = await resp.json();
                const tracks = data.tracks || [];
                if (!tracklistEl) return;
                if (!tracks.length) {
                    tracklistEl.innerHTML = '<li>No tracks found.</li>';
                    return;
                }
                tracklistEl.innerHTML = '';
                for (const t of tracks) {
                    const li = document.createElement('li');
                    li.className = 'album-track-item';
                    li.textContent = t.title;
                    tracklistEl.appendChild(li);
                }
                if (albumSelectedArtist && warningEl) {
                    try {
                        const params = new URLSearchParams({
                            artist: albumSelectedArtist.name,
                            album_title: album.title,
                        });
                        const statusResp = await apiFetch(
                            `/api/albums/release/${encodeURIComponent(album.release_mbid)}/missing?${params.toString()}`
                        );
                        if (statusResp.ok) {
                            const statusData = await statusResp.json();
                            const existing = Number(statusData.existing_count || 0);
                            const missing = Number(statusData.missing_count || 0);
                            const total = Number(statusData.total_tracks || tracks.length || 0);

                            if (Array.isArray(statusData.tracks) && statusData.tracks.length === tracks.length) {
                                const liEls = tracklistEl.querySelectorAll('li');
                                statusData.tracks.forEach((st, idx) => {
                                    const li = liEls[idx];
                                    if (!li) return;
                                    li.classList.remove('album-track-existing', 'album-track-missing');
                                    li.classList.add(st.exists ? 'album-track-existing' : 'album-track-missing');
                                });
                            }

                            if (legendEl && existing > 0) {
                                legendEl.style.display = 'block';
                                legendEl.innerHTML = `<span class="missing">Green</span> = missing (will queue) &nbsp;·&nbsp; <span class="existing">dim</span> = already on disk`;
                            }

                            if (existing > 0) {
                                warningEl.style.display = 'block';
                                if (missing <= 0) {
                                    warningEl.textContent = `Album already exists in ${statusData.album_dir} (${existing}/${total} tracks). Download will queue nothing.`;
                                } else {
                                    warningEl.textContent = `${existing}/${total} track(s) already exist in ${statusData.album_dir}. Only ${missing} missing track(s) will be queued.`;
                                }
                            }

                            const m3uFiles = Array.isArray(statusData.existing_m3u_files) ? statusData.existing_m3u_files : [];
                            if (statusData.has_existing_m3u && m3uFiles.length > 0) {
                                albumSelectedM3uName = m3uFiles[0];
                                if (makeM3u) makeM3u.checked = true;
                                if (m3uHintEl) {
                                    m3uHintEl.style.display = 'block';
                                    m3uHintEl.textContent = `Existing M3U found (${albumSelectedM3uName}) - this will be updated.`;
                                }
                            }
                        }
                    } catch (e) {
                        // Non-fatal; user can still download.
                    }
                }
                if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }
            } catch (e) {
                _renderAlbumRetryError(tracklistEl, `Failed to load tracklist: ${e.message}`, () => selectAlbum(album, btn), true);
            }
        }

        async function downloadAlbum() {
            if (!albumSelectedArtist || !albumSelectedRelease) return;
            const downloadBtn = document.getElementById('albumDownloadBtn');
            const progressEl = document.getElementById('albumProgress');
            const makeM3u = document.getElementById('albumMakeM3u')?.checked || false;
            setAlbumResetVisible(false);

            if (downloadBtn) { downloadBtn.disabled = true; downloadBtn.textContent = 'Queuing\u2026'; }

            try {
                const resp = await apiFetch('/api/albums/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        artist: albumSelectedArtist.name,
                        album_title: albumSelectedRelease.title,
                        release_mbid: albumSelectedRelease.release_mbid,
                        make_m3u: makeM3u,
                        m3u_name: makeM3u ? (albumSelectedM3uName || null) : null,
                        convert_to_flac: convertToFlacCheckbox.checked,
                    }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Download failed');
                }
                const data = await resp.json();
                if (progressEl) {
                    progressEl.style.display = 'block';
                    const warningLine = data.warning
                        ? `<p class="settings-hint-sm" style="margin:0 0 8px;">${escapeHtml(data.warning)}</p>`
                        : '';
                    progressEl.innerHTML =
                        `${warningLine}<p class="settings-hint-sm" style="margin:0 0 8px;">Saving to: <code>${escapeHtml(data.album_dir)}</code></p>`;
                }

                if (!data.import_id) {
                    if (downloadBtn) { downloadBtn.disabled = true; downloadBtn.textContent = 'Already Complete'; }
                    setAlbumResetVisible(true);
                    return;
                }

                // Reuse bulk import polling + display
                if (albumPollInterval) clearInterval(albumPollInterval);
                const albumDirHint = `<p class="settings-hint-sm" style="margin:0 0 8px;">Saving to: <code>${escapeHtml(data.album_dir)}</code></p>`;
                albumPollInterval = setInterval(async () => {
                    try {
                        const statusResp = await apiFetch(`/api/bulk-import/${data.import_id}/status`);
                        if (!statusResp.ok) return;
                        const statusData = await statusResp.json();
                        if (progressEl) progressEl.innerHTML = albumDirHint + renderAlbumProgress(statusData);
                        if (statusData.complete) {
                            clearInterval(albumPollInterval);
                            albumPollInterval = null;
                            if (downloadBtn) { downloadBtn.disabled = true; downloadBtn.textContent = 'Downloaded'; }
                            setAlbumResetVisible(true);
                        }
                    } catch (e) { /* transient, keep polling */ }
                }, 2000);

            } catch (e) {
                if (downloadBtn) { downloadBtn.disabled = false; downloadBtn.textContent = 'Download Album'; }
                if (progressEl) {
                    progressEl.style.display = 'block';
                    progressEl.innerHTML = `<p class="error-text">Error: ${escapeHtml(e.message)}</p>`;
                }
            }
        }

        function renderAlbumProgress(data) {
            const done = data.completed || 0;
            const total = data.total_tracks || 0;
            const failed = data.failed || 0;
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const statusText = data.complete
                ? `Done: ${done}/${total} tracks${failed ? `, ${failed} failed` : ''}`
                : `Downloading: ${done}/${total} tracks\u2026`;
            return `<div class="bulk-progress-bar-container"><div class="bulk-progress-bar" style="width:${pct}%"></div></div>`
                + `<p class="bulk-intro-text">${escapeHtml(statusText)}</p>`;
        }

        // Wire up album tab buttons
        const albumArtistSearchBtn = document.getElementById('albumArtistSearchBtn');
        const albumArtistInput = document.getElementById('albumArtistInput');
        const albumDownloadBtn = document.getElementById('albumDownloadBtn');
        const albumResetBtn = document.getElementById('albumResetBtn');
        if (albumArtistSearchBtn) albumArtistSearchBtn.addEventListener('click', searchAlbumArtist);
        if (albumArtistInput) albumArtistInput.addEventListener('keydown', e => { if (e.key === 'Enter') searchAlbumArtist(); });
        if (albumDownloadBtn) albumDownloadBtn.addEventListener('click', downloadAlbum);
        if (albumResetBtn) albumResetBtn.addEventListener('click', resetAlbumFormAndScrollTop);

        let downloadablePage = 1;

        async function loadDownloadable(delta = 0) {
            downloadablePage = Math.max(1, downloadablePage + delta);
            const listEl = document.getElementById('downloadableList');
            const pagerEl = document.getElementById('downloadablePager');
            const infoEl = document.getElementById('downloadablePagerInfo');
            const prevBtn = document.getElementById('downloadablePrevBtn');
            const nextBtn = document.getElementById('downloadableNextBtn');
            if (!listEl) return;
            listEl.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await apiFetch(`/api/jobs/downloadable?page=${downloadablePage}&per_page=15`);
                const data = await res.json();
                if (!data.jobs || data.jobs.length === 0) {
                    listEl.innerHTML = '<div class="empty-state"><p>No completed downloads yet.</p></div>';
                    pagerEl.style.display = 'none';
                    return;
                }
                listEl.innerHTML = data.jobs.map(job => {
                    const label = job.artist ? `${escapeHtml(job.artist)} \u2013 ${escapeHtml(job.title)}` : escapeHtml(job.title);
                    const date = job.completed_at ? formatTimeAgo(job.completed_at) : '';
                    return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
                        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(job.artist||'')} - ${escapeHtml(job.title)}">${label}</span>
                        ${date ? `<span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;flex-shrink:0;">${date}</span>` : ''}
                        <button onclick="saveJobToDevice('${escapeAttr(job.id || '')}')" style="flex-shrink:0;padding:4px 10px;font-size:11px;font-family:inherit;font-weight:600;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);border-radius:6px;cursor:pointer;white-space:nowrap;">
                            <i class="fa-solid fa-download"></i> Save
                        </button>
                    </div>`;
                }).join('');
                if (data.pages > 1) {
                    pagerEl.style.display = 'flex';
                    infoEl.textContent = `Page ${data.page} of ${data.pages} (${data.total} tracks)`;
                    prevBtn.disabled = data.page <= 1;
                    nextBtn.disabled = data.page >= data.pages;
                } else {
                    pagerEl.style.display = 'none';
                }
            } catch (e) {
                listEl.innerHTML = '<div class="empty-state"><p>Failed to load downloads.</p></div>';
            }
        }

        async function clearQueue() {
            if (!confirm('Clear all completed, failed, and stale downloads from the queue?')) {
                return;
            }

            clearQueueBtn.disabled = true;

            try {
                const response = await apiFetch('/api/jobs/cleanup', {
                    method: 'DELETE'
                });

                if (!response.ok) throw new Error('Failed to clear queue');

                const data = await response.json();
                showToast(`Cleared ${data.deleted} job(s)`);
                loadJobs();
            } catch (error) {
                showToast('Failed to clear queue', true);
            } finally {
                clearQueueBtn.disabled = false;
            }
        }

        async function resetStats() {
            if (!confirm('Reset all dashboard stats? This clears completed/failed job history and search history.')) {
                return;
            }

            resetStatsBtn.disabled = true;

            try {
                const response = await apiFetch('/api/stats?confirm=true', {
                    method: 'DELETE'
                });

                if (!response.ok) throw new Error('Failed to reset stats');

                const data = await response.json();
                showToast(`Stats reset (${data.deleted_jobs} jobs, ${data.deleted_searches} searches)`);
                loadJobs();
                loadStats();
            } catch (error) {
                showToast('Failed to reset stats', true);
            } finally {
                resetStatsBtn.disabled = false;
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function escapeAttr(text) {
            // Safe for use inside single-quoted JS string literals in HTML attributes
            return escapeHtml(String(text)).replace(/'/g, '&#39;').replace(/\\/g, '\\\\');
        }

        // WARNING: jsStr is NOT safe inside double-quoted onclick attributes!
        // &quot; decoded by the HTML parser terminates the attribute boundary.
        // For onclick handlers, use escapeAttr with single-quoted JS strings:
        //   onclick="fn('${escapeAttr(val)}')"
        function jsStr(text) {
            return escapeHtml(JSON.stringify(String(text)));
        }

        // Line counter (no limit)
        function updateLineCounter() {
            const lines = bulkInput.value.split('\n').filter(line => line.trim());
            const count = lines.length;
            lineCounter.textContent = `${count} line${count !== 1 ? 's' : ''}`;
            lineCounter.style.color = 'var(--text-secondary)';
            lineCounter.style.background = 'var(--bg-tertiary)';
        }

        bulkInput.addEventListener('input', updateLineCounter);

        // File upload handler
        fileUpload.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;

            fileName.textContent = file.name;

            const reader = new FileReader();
            reader.onload = (event) => {
                bulkInput.value = event.target.result;
                updateLineCounter();
            };
            reader.readAsText(file);
        });

        // Event listeners
        searchBtn.addEventListener('click', search);
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                search();
            }
        });
        searchInput.addEventListener('focus', () => {
            if (searchInput.value.trim() === '') {
                showSearchHistory();
            }
        });
        searchInput.addEventListener('blur', hideSearchHistory);
        searchInput.addEventListener('input', () => {
            searchClearBtn.style.display = searchInput.value ? '' : 'none';
            if (searchInput.value.trim() === '') {
                showSearchHistory();
            } else {
                hideSearchHistory();
            }
        });
        searchClearBtn.addEventListener('click', () => {
            setSearchValue('');
            searchInput.focus();
            // Reset results back to the empty state
            stopPreview();
            relatedSuggestions.style.display = 'none';
            exploreBar.style.display = 'none';
            const _destRow3 = document.getElementById('destinationPickerRow');
            if (_destRow3) _destRow3.style.display = 'none';
            resultsTab.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon"><i class="fa-solid fa-headphones"></i></div>
                    <p>Search for music to get started</p>
                    <p style="font-size: 12px; margin-top: 8px; opacity: 0.6;">Hover over results to preview</p>
                </div>
            `;
            showSearchHistory();
        });
        watchedList.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action]');
            if (!btn || !watchedList.contains(btn)) return;

            const action = btn.dataset.action;
            if (action === 'retry-missing-track') {
                retryMissingTrack(
                    btn.dataset.playlistId || '',
                    btn.dataset.artist || '',
                    btn.dataset.title || '',
                    btn.dataset.rowId || ''
                );
                return;
            }

            if (action === 'search-missing-track') {
                searchMissingTrack(
                    btn.dataset.playlistId || '',
                    btn.dataset.playlistName || '',
                    btn.dataset.artist || '',
                    btn.dataset.title || ''
                );
                return;
            }

            if (action === 'find-missing-track-versions') {
                openMissingTrackVersionsModal(
                    btn.dataset.playlistId || '',
                    btn.dataset.playlistName || '',
                    btn.dataset.artist || '',
                    btn.dataset.title || '',
                    btn.dataset.rowId || ''
                );
                return;
            }

            if (action === 'replace-watched-track') {
                replaceTrack(
                    btn.dataset.playlistId || '',
                    btn.dataset.playlistName || '',
                    btn.dataset.artist || '',
                    btn.dataset.title || '',
                    btn.dataset.jobId || '',
                    btn.dataset.rowId || ''
                );
                return;
            }

            if (action === 'refresh-watched-playlist') {
                refreshWatchedPlaylist(btn.dataset.playlistId || '');
                return;
            }

            if (action === 'toggle-missing-tracks') {
                toggleMissingTracks(btn.dataset.playlistId || '');
                return;
            }

            if (action === 'toggle-watched-playlist') {
                toggleWatchedPlaylist(
                    btn.dataset.playlistId || '',
                    btn.dataset.enabled === 'true' ? 'false' : 'true'
                );
                return;
            }

            if (action === 'toggle-playlist-track-list') {
                toggleTrackList(
                    btn.dataset.playlistId || '',
                    btn.dataset.playlistName || ''
                );
                return;
            }

            if (action === 'copy-watched-playlist-url') {
                copyWatchedPlaylistUrl(btn.dataset.url || '');
                return;
            }

            if (action === 'delete-watched-playlist') {
                deleteWatchedPlaylist(
                    btn.dataset.playlistId || '',
                    btn.dataset.playlistName || ''
                );
            }
        });
        watchedArtistList.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action]');
            if (!btn || !watchedArtistList.contains(btn)) return;

            const action = btn.dataset.action;
            if (action === 'save-job-to-device') {
                saveJobToDevice(btn.dataset.jobId || '');
                return;
            }

            if (action === 'retry-all-artist-missing') {
                retryAllArtistMissing(
                    btn.dataset.artistId || '',
                    btn
                );
                return;
            }

            if (action === 'retry-artist-track') {
                retryArtistTrack(
                    btn.dataset.artistId || '',
                    btn.dataset.artist || '',
                    btn.dataset.title || '',
                    btn
                );
                return;
            }

            if (action === 'refresh-watched-artist') {
                refreshWatchedArtist(btn.dataset.artistId || '');
                return;
            }

            if (action === 'toggle-artist-missing-tracks') {
                toggleArtistMissingTracks(btn.dataset.artistId || '');
                return;
            }

            if (action === 'toggle-watched-artist') {
                toggleWatchedArtist(
                    btn.dataset.artistId || '',
                    btn.dataset.enabled === 'true' ? 'false' : 'true'
                );
                return;
            }

            if (action === 'toggle-artist-track-list') {
                toggleArtistTrackList(
                    btn.dataset.artistId || '',
                    btn.dataset.artistName || ''
                );
                return;
            }

            if (action === 'delete-watched-artist') {
                deleteWatchedArtist(
                    btn.dataset.artistId || '',
                    btn.dataset.artistName || ''
                );
            }
        });
        bulkImportBtn.addEventListener('click', bulkImport);
        clearQueueBtn.addEventListener('click', clearQueue);
        document.getElementById('emptyTrashBtn').addEventListener('click', emptyTrash);
        resetStatsBtn.addEventListener('click', resetStats);

        // Spotify playlist/album fetch handler
        async function fetchSpotifyPlaylist() {
            const url = spotifyUrlInput.value.trim();
            if (!url) {
                spotifyError.textContent = 'Please enter a playlist URL';
                spotifyError.style.display = 'block';
                return;
            }

            // URL validation - Spotify playlists/albums, Amazon Music playlists, Apple Music, YouTube/YT Music playlists, SoundCloud sets/likes, ListenBrainz, Tidal/Monochrome, Beatport
            const isSpotify = url.match(/^https?:\/\/open\.spotify\.com\/(playlist|album)\//);
            const isAmazon = url.match(/^https?:\/\/music\.amazon\.[a-z.]+\/(user-playlists|playlists)\//);
            const isApple = url.match(/^https?:\/\/music\.apple\.com\/(?:[a-z]{2}|library)\/(playlist|album)\//i);
            const isYouTube = url.match(/^https?:\/\/(www\.|music\.)?youtube\.com\/(playlist|watch)\?[^"]*list=/i);
            const isSoundCloud = url.match(/^https?:\/\/soundcloud\.com\/[^/]+\/(sets\/[^/?]+|likes)/i);
            const isListenBrainz = url.match(/^https?:\/\/listenbrainz\.org\/(playlist|user)\//i) || url.match(/^[a-zA-Z0-9_-]+$/);
            const isTidal = url.match(/^https?:\/\/tidal\.com\/(?:browse\/)?playlist\//i);
            const isMonochrome = url.match(/^https?:\/\/(?:www\.)?(?:monochrome\.tf|monochrome\.samidy\.com)\/playlist\//i);
            const isBeatport = url.match(/^https?:\/\/(?:www\.)?beatport\.com\/(top-100|genre\/[^/]+\/\d+\/top-100|chart\/[^/]+\/\d+)/i);
            if (!isSpotify && !isAmazon && !isApple && !isYouTube && !isSoundCloud && !isListenBrainz && !isTidal && !isMonochrome && !isBeatport) {
                spotifyError.textContent = 'Unsupported URL. Paste a Spotify, YouTube, Apple Music, Amazon Music, SoundCloud sets/likes, ListenBrainz, Tidal, Monochrome, or Beatport link.';
                spotifyError.style.display = 'block';
                return;
            }

            spotifyError.style.display = 'none';
            fetchSpotifyBtn.disabled = true;
            fetchSpotifyBtn.textContent = isAmazon ? 'Scraping...' : 'Fetching...';

            // For Spotify, the embed scrape returns in seconds; if the playlist
            // is large or private, the server falls back to a headless browser
            // which takes longer. Nudge the button text after a few seconds
            // so the user knows we haven't ghosted them.
            const slowHints = [];
            if (isSpotify) {
                slowHints.push(setTimeout(() => {
                    fetchSpotifyBtn.textContent = 'Spinning up browser...';
                }, 6000));
                slowHints.push(setTimeout(() => {
                    fetchSpotifyBtn.textContent = 'Scrolling playlist (large lists take a minute)...';
                }, 15000));
            }

            try {
                const response = await apiFetch('/api/fetch-playlist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });

                // Check content type before parsing
                const contentType = response.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    throw new Error('Server error - please check if the app is running');
                }

                const data = await response.json();

                if (!response.ok) {
                    if (data.detail === 'spotify_cookies_expired') {
                        throw new Error('Spotify cookies have expired. Go to Settings to update them.');
                    }
                    throw new Error(data.detail || 'Failed to fetch playlist');
                }

                if (data.tracks && data.tracks.length > 0) {
                    // Populate the textarea with all tracks (no limit)
                    bulkInput.value = data.tracks.join('\n');
                    updateLineCounter();

                    // Auto-fill playlist name if checkbox is checked
                    if (createPlaylistCheckbox.checked && data.playlist_name) {
                        playlistNameInput.value = data.playlist_name;
                        playlistNameInput.dataset.sourceUrl = url;
                    }

                    // For SoundCloud playlists, default source chips to SC only - the tracks
                    // are already on SoundCloud, so there's no reason to go hunting elsewhere
                    if (data.platform === 'soundcloud') {
                        setWatchedPreferredSource('soundcloud');
                    }

                    // Show toast with result (include warning if present)
                    if (data.warning) {
                        spotifyError.textContent = `Warning: ${data.warning}`;
                        spotifyError.style.color = 'var(--warning, #f59e0b)';
                        spotifyError.style.display = 'block';
                        showToast(`Loaded ${data.tracks.length} tracks (truncated) - ${data.warning}`, true);
                    } else {
                        showToast(`Loaded ${data.tracks.length} tracks from "${data.playlist_name}"`);
                    }

                    // Clear the URL input
                    spotifyUrlInput.value = '';
                } else {
                    throw new Error('No tracks found in playlist');
                }
            } catch (error) {
                spotifyError.textContent = error.message;
                spotifyError.style.color = 'var(--error)';
                spotifyError.style.display = 'block';
            } finally {
                slowHints.forEach(clearTimeout);
                fetchSpotifyBtn.disabled = false;
                fetchSpotifyBtn.textContent = 'Fetch Playlist';
            }
        }

        fetchSpotifyBtn.addEventListener('click', fetchSpotifyPlaylist);
        spotifyUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                fetchSpotifyPlaylist();
            }
        });

        // Show/hide playlist name input (and Playlists folder checkbox) when checkbox is toggled
        createPlaylistCheckbox.addEventListener('change', () => {
            playlistNameInput.style.display = createPlaylistCheckbox.checked ? 'block' : 'none';
            const playlistsDirRow = document.getElementById('usePlaylistsDirRow');
            const usePlaylistsDirCheckbox = document.getElementById('usePlaylistsDirCheckbox');
            if (playlistsDirRow) {
                playlistsDirRow.style.display = (createPlaylistCheckbox.checked && serverConfig.playlists_subdir) ? 'flex' : 'none';
            }
            if (usePlaylistsDirCheckbox && createPlaylistCheckbox.checked && serverConfig.playlists_subdir) {
                // Default playlist-creation imports to the Playlists folder when configured.
                usePlaylistsDirCheckbox.checked = true;
            }
            if (createPlaylistCheckbox.checked) {
                playlistNameInput.focus();
            }
        });

        // Close search history when clicking outside
        document.addEventListener('click', (e) => {
            if (!searchInput.contains(e.target) && !searchHistory.contains(e.target)) {
                searchHistory.classList.remove('show');
            }
        });

        // Initial empty state
        resultsTab.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">~</div>
                <p>Search for music to get started</p>
                <p style="font-size: 12px; margin-top: 8px; opacity: 0.6;">Hover over results to preview</p>
            </div>
        `;

        // =============================================================================
        // Watched Playlists
        // =============================================================================

        async function loadWatchedPlaylists(showLoading = true) {
            if (watchedLoadInFlight) return;
            watchedLoadInFlight = true;
            if (showLoading) {
                watchedList.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            }

            try {
                // Fetch schedule info and playlists in parallel
                const [scheduleRes, playlistsRes] = await Promise.all([
                    apiFetch('/api/watched-playlists/schedule'),
                    apiFetch('/api/watched-playlists')
                ]);

                // Update schedule info
                if (scheduleRes.ok) {
                    const schedule = await scheduleRes.json();
                    if (schedule.enabled) {
                        const hours = schedule.check_interval_hours;
                        let intervalText = `${hours} hours`;
                        if (hours === 24) intervalText = 'daily';
                        else if (hours === 168) intervalText = 'weekly';
                        else if (hours >= 720) intervalText = 'monthly';
                        watchedScheduleInfo.textContent = `Automatic checks run ${intervalText}. Per-playlist intervals determine when each is due.`;
                    } else {
                        watchedScheduleInfo.textContent = 'Automatic checks disabled. Use "Check All Now" or set WATCHED_PLAYLIST_CHECK_HOURS.';
                    }
                }

                if (!playlistsRes.ok) throw new Error('Failed to load watched playlists');

                const data = await playlistsRes.json();
                renderWatchedPlaylists(data.playlists);
                populateSourceChips();
            } catch (error) {
                if (showLoading) {
                    watchedList.innerHTML = `
                        <div class="empty-state">
                            <p style="color: var(--error);">Failed to load watched playlists</p>
                        </div>
                    `;
                }
            } finally {
                watchedLoadInFlight = false;
            }
        }

        function renderWatchedPlaylists(playlists) {
            if (!playlists || playlists.length === 0) {
                if (watchedRefreshPollInterval) {
                    clearInterval(watchedRefreshPollInterval);
                    watchedRefreshPollInterval = null;
                }
                watchedList.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon"><i class="fa-solid fa-eye"></i></div>
                        <p>No watched playlists yet</p>
                        <p style="font-size: 12px; margin-top: 8px; opacity: 0.6;">Add a Spotify or YouTube playlist above</p>
                    </div>
                `;
                return;
            }

            watchedList.innerHTML = playlists.map(p => {
                const refreshState = p.refresh_state || 'idle';
                const localRefresh = watchedRefreshPending.get(p.id);
                const isRefreshing = refreshState === 'running' || !!localRefresh;
                const refreshStage = (refreshState === 'running' ? p.refresh_stage : null) || (localRefresh ? localRefresh.stage : null);
                const refreshStartedAt = (refreshState === 'running' ? p.refresh_started_at : null)
                    || (localRefresh ? localRefresh.startedAt : null);
                const platformIcons = {
                    spotify: '<i class="fa-brands fa-spotify" title="Spotify"></i>',
                    youtube: '<i class="fa-brands fa-youtube" title="YouTube"></i>',
                    apple: '<i class="fa-brands fa-apple" title="Apple Music"></i>',
                    amazon: '<i class="fa-brands fa-amazon" title="Amazon Music"></i>',
                    listenbrainz: '<img src="/static/images/ListenBrainzLogo.svg" title="ListenBrainz" style="width:1em;height:1em;vertical-align:-0.125em;">',
                    soundcloud: '<i class="fa-brands fa-soundcloud" title="SoundCloud"></i>',
                    tidal: '<img src="/static/images/tidal-round-black-icon.svg" title="Tidal" style="width:1em;height:1em;vertical-align:-0.125em;">',
                    monochrome: '<span title="Monochrome" style="font-size:0.75em;font-weight:700;">MONO</span>',
                    beatport: '<img src="/static/images/BeatPortLogo.svg" title="Beatport" style="width:1em;height:1em;vertical-align:-0.125em;">'
                };
                const platformIcon = platformIcons[p.platform] || '<i class="fa-solid fa-list"></i>';
                const lastChecked = p.last_checked ? formatTimeAgo(p.last_checked) : 'Never';
                const statusColor = p.enabled ? 'var(--accent)' : 'var(--text-secondary)';
                const intervalText = p.refresh_interval_hours >= 720 ? 'monthly' :
                                     p.refresh_interval_hours === 168 ? 'weekly' :
                                     p.refresh_interval_hours === 24 ? 'daily' :
                                     p.refresh_interval_hours === 12 ? 'every 12h' :
                                     p.refresh_interval_hours === 6 ? 'every 6h' :
                                     p.refresh_interval_hours === 1 ? 'hourly' :
                                     p.refresh_interval_hours === 0.5 ? 'every 30min' :
                                     `every ${p.refresh_interval_hours}h`;
                const refreshLabel = isRefreshing
                    ? formatRefreshStage(refreshStage, refreshStartedAt, p.platform)
                    : '';

                return `
                    <div class="watched-card">
                        <div class="watched-card-header">
                            <span>${platformIcon}</span>
                            <span class="watched-card-name">${escapeHtml(p.name)}</span>
                            ${isRefreshing ? `<span class="watched-card-refreshing"><span class="watched-refresh-spinner"></span>${escapeHtml(refreshLabel)}</span>` : ''}
                            ${!p.enabled ? '<span class="watched-card-paused">Paused</span>' : ''}
                        </div>
                        <div class="watched-card-meta">
                            ${p.tracked_count} tracks · ${p.downloaded_count || 0} downloaded · ${intervalText} · Last checked: ${lastChecked}
                        </div>
                        ${refreshState === 'error' && p.refresh_error && !isRefreshing ? `
                        <div class="watched-card-refresh-error">
                            <i class="fa-solid fa-circle-exclamation"></i>
                            <span>${escapeHtml(p.refresh_error)}</span>
                        </div>` : ''}
                        <div class="watched-card-settings">
                            <label class="watched-card-toggle" title="Convert new tracks to the selected audio format">
                                Convert
                                <div class="toggle-switch">
                                    <input type="checkbox" ${p.convert_to_flac ? 'checked' : ''} onchange="updateWatchedPlaylistFlac('${p.id}', this.checked)">
                                    <span class="toggle-slider"></span>
                                </div>
                            </label>
                            <label class="watched-card-toggle" title="Generate and update a .m3u playlist file as tracks are downloaded">
                                M3U
                                <div class="toggle-switch">
                                    <input type="checkbox" ${p.make_m3u ? 'checked' : ''} onchange="updateWatchedPlaylistM3u('${p.id}', this.checked)">
                                    <span class="toggle-slider"></span>
                                </div>
                            </label>
                            ${serverConfig.playlists_subdir ? `
                            <label class="watched-card-toggle" title="Save downloaded tracks to the Playlists folder instead of Singles">
                                Playlists folder
                                <div class="toggle-switch">
                                    <input type="checkbox" ${p.use_playlists_dir ? 'checked' : ''} onchange="updateWatchedPlaylistUsePlaylists('${p.id}', this.checked)">
                                    <span class="toggle-slider"></span>
                                </div>
                            </label>` : ''}
                            <label class="watched-card-toggle" title="Append: M3U grows as new tracks arrive. Mirror: M3U stays in sync with upstream - removed tracks drop out (audio files kept).">
                                Sync
                                <select onchange="updateWatchedPlaylistSyncMode('${p.id}', this.value)" class="watched-card-select">
                                    <option value="append" ${(p.sync_mode || 'append') === 'append' ? 'selected' : ''}>Append</option>
                                    <option value="mirror" ${p.sync_mode === 'mirror' ? 'selected' : ''}>Mirror</option>
                                </select>
                            </label>
                            <label class="watched-card-toggle" title="Which search sources to use when downloading new tracks for this playlist. Deselect all to search everything.">
                                Sources
                                <div class="watched-sources-chips" data-playlist-id="${p.id}" data-preferred="${escapeAttr(p.preferred_sources || 'all')}">
                                    ${renderSourceChips(p.id, p.preferred_sources || 'all')}
                                </div>
                            </label>
                            <label class="watched-card-toggle" title="Give one source a huge score boost so it wins almost every close call. Useful when you want Soulseek to be primary and YouTube as fallback, for example.">
                                Preferred
                                <select onchange="updateWatchedPlaylistPrioritySource('${p.id}', this.value)" class="watched-card-select" data-priority-source="${escapeAttr(p.priority_source || '')}">
                                    ${renderPrioritySourceOptions(p.priority_source || '')}
                                </select>
                            </label>
                        </div>
                        ${p.stale_navidrome_paths > 0 ? `
                        <div class="watched-card-stale-warning">
                            <i class="fa-solid fa-triangle-exclamation"></i>
                            <span><strong>${p.stale_navidrome_paths} track${p.stale_navidrome_paths === 1 ? '' : 's'}</strong> in the M3U point to files that no longer exist on disk but are still in Navidrome's database. To fix: open Navidrome, go to <strong>Settings &gt; Missing Files</strong>, select all, and click <strong>Remove from Database</strong>. Then trigger a library scan and refresh this playlist.</span>
                        </div>` : ''}
                        <div class="watched-card-actions">
                            <button type="button"
                                data-action="refresh-watched-playlist"
                                data-playlist-id="${escapeAttr(p.id)}"
                                class="watched-action-btn" title="${isRefreshing ? `Refresh in progress: ${escapeAttr(refreshLabel)}` : 'Check for new tracks now'}" ${isRefreshing ? 'disabled' : ''}>${isRefreshing ? 'Checking...' : 'Refresh'}</button>
                            <button type="button"
                                data-action="toggle-missing-tracks"
                                data-playlist-id="${escapeAttr(p.id)}"
                                class="watched-action-btn" title="Show tracks that failed to download">Missing</button>
                            <button type="button"
                                data-action="toggle-playlist-track-list"
                                data-playlist-id="${escapeAttr(p.id)}"
                                data-playlist-name="${escapeAttr(p.name)}"
                                class="watched-action-btn" title="Show all tracks and their download status">Tracks</button>
                            <button type="button"
                                data-action="copy-watched-playlist-url"
                                data-url="${escapeAttr(p.url)}"
                                class="watched-action-btn" title="Copy playlist URL">Copy URL</button>
                            <button type="button"
                                data-action="toggle-watched-playlist"
                                data-playlist-id="${escapeAttr(p.id)}"
                                data-enabled="${p.enabled ? 'true' : 'false'}"
                                class="watched-action-btn watched-action-pause">${p.enabled ? 'Pause' : 'Resume'}</button>
                            <button type="button"
                                data-action="delete-watched-playlist"
                                data-playlist-id="${escapeAttr(p.id)}"
                                data-playlist-name="${escapeAttr(p.name)}"
                                class="watched-action-btn watched-action-delete">Delete</button>
                        </div>
                        <div id="missing-${p.id}" class="watched-card-expanded" style="display: none;">Loading...</div>
                        <div id="tracks-${p.id}" class="watched-card-expanded" style="display: none;">Loading...</div>
                    </div>
                `;
            }).join('');

            const anyRunning = playlists.some(p => p.refresh_state === 'running') || watchedRefreshPending.size > 0;
            if (currentTab === 'watched' && anyRunning && !watchedRefreshPollInterval) {
                watchedRefreshPollInterval = setInterval(() => {
                    loadWatchedPlaylists(false);
                }, 2000);
            } else if ((!anyRunning || currentTab !== 'watched') && watchedRefreshPollInterval) {
                clearInterval(watchedRefreshPollInterval);
                watchedRefreshPollInterval = null;
            }
        }

        function formatRefreshStage(stage, startedAt = null, platform = null) {
            const labels = {
                starting: 'Starting...',
                fetching: 'Fetching playlist...',
                diffing: 'Comparing tracks...',
                queueing: 'Queueing downloads...',
                finalizing: 'Finalizing...',
                rebuilding_m3u: 'Rebuilding M3U...',
                done: 'Done',
                failed: 'Failed'
            };
            const base = !stage ? 'Checking...' : (labels[stage] || stage.replace(/_/g, ' '));

            if (!startedAt) return base;
            const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(startedAt) ? startedAt : `${String(startedAt).replace(' ', 'T')}Z`;
            const startedMs = new Date(normalized).getTime();
            if (Number.isNaN(startedMs)) return base;
            const elapsedSec = Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
            const mins = Math.floor(elapsedSec / 60);
            const secs = elapsedSec % 60;
            const elapsedText = mins > 0 ? `${mins}m ${String(secs).padStart(2, '0')}s` : `${secs}s`;

            if (stage === 'fetching' && platform === 'spotify' && elapsedSec >= 90) {
                return `${base} ${elapsedText} (large playlists can take a few minutes)`;
            }
            return `${base} ${elapsedText}`;
        }

        function formatTimeAgo(isoString) {
            return formatDateYmd(isoString);
        }

        async function addWatchedPlaylist() {
            const url = watchedUrlInput.value.trim();
            if (!url) {
                watchedError.textContent = 'Please enter a playlist URL';
                watchedError.style.display = 'block';
                return;
            }

            // Detect platform so we can show helpful hints (e.g. Spotify large playlist warning)
            let platform = null;
            if (url.includes('spotify.com')) platform = 'spotify';
            else if (url.includes('youtube.com') || url.includes('youtu.be')) platform = 'youtube';
            else if (url.includes('music.apple.com')) platform = 'apple';
            else if (url.includes('amazon.') || url.includes('music.amazon')) platform = 'amazon';

            watchedError.style.display = 'none';
            addWatchedBtn.disabled = true;
            addWatchedBtn.textContent = 'Watch';

            const statusEl = document.getElementById('watchedAddStatus');
            const startedAt = new Date().toISOString();
            let timerInterval = null;

            function updateStatus(stage) {
                statusEl.innerHTML = `<span class="watched-card-refreshing"><span class="watched-refresh-spinner"></span>${escapeHtml(formatRefreshStage(stage, startedAt, platform))}</span>`;
                statusEl.style.display = 'block';
            }

            // Tick the elapsed timer every second while in-flight
            updateStatus('fetching');
            timerInterval = setInterval(() => updateStatus('fetching'), 1000);

            try {
                const response = await apiFetch('/api/watched-playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: url,
                        refresh_interval_hours: parseFloat(watchedIntervalSelect.value),
                        convert_to_flac: watchedConvertToFlac ? watchedConvertToFlac.checked : convertToFlacCheckbox.checked,
                        make_m3u: document.getElementById('watchedMakeM3u') ? document.getElementById('watchedMakeM3u').checked : false,
                        use_playlists_dir: document.getElementById('watchedUsePlaylistsDir') ? document.getElementById('watchedUsePlaylistsDir').checked : false,
                        sync_mode: document.getElementById('watchedSyncModeSelect') ? document.getElementById('watchedSyncModeSelect').value : 'append',
                        preferred_sources: getWatchedPreferredSources(),
                        priority_source: (document.getElementById('watchedPrioritySource')?.value || '') || null,
                        custom_subdir: (document.getElementById('watchedCustomSubdir')?.value || '').trim() || null
                    })
                });

                if (!response.ok) {
                    const ct = response.headers.get('content-type') || '';
                    if (ct.includes('application/json')) {
                        const error = await response.json();
                        if (error.detail === 'spotify_cookies_expired') {
                            throw new Error('Spotify cookies have expired. Go to Settings to update them.');
                        }
                        throw new Error(error.detail || 'Failed to add playlist');
                    }
                    const text = (await response.text()).slice(0, 200);
                    throw new Error(`Server error ${response.status}: ${text || response.statusText}`);
                }

                const data = await response.json();
                if (data.warning) {
                    watchedError.textContent = `Warning: ${data.warning}`;
                    watchedError.style.color = 'var(--warning, #f59e0b)';
                    watchedError.style.display = 'block';
                    showToast(`Now watching "${data.name}" (${data.track_count} tracks, truncated)`, true);
                } else {
                    showToast(`Now watching "${data.name}" (${data.track_count} tracks)`);
                }
                watchedUrlInput.value = '';
                const customSubdirInput = document.getElementById('watchedCustomSubdir');
                if (customSubdirInput) { customSubdirInput.value = ''; clearWatchedCustomSubdir(); }
                loadWatchedPlaylists();
            } catch (error) {
                watchedError.textContent = error.message;
                watchedError.style.color = 'var(--error)';
                watchedError.style.display = 'block';
            } finally {
                clearInterval(timerInterval);
                statusEl.style.display = 'none';
                addWatchedBtn.disabled = false;
                addWatchedBtn.textContent = 'Watch';
            }
        }

        function toggleLbRow() {
            const row = document.getElementById('lbRow');
            const btn = document.getElementById('lbToggleBtn');
            const visible = row.style.display !== 'none';
            row.style.display = visible ? 'none' : 'flex';
            btn.textContent = visible ? '+ ListenBrainz "Created for You" playlists' : '− ListenBrainz "Created for You" playlists';
            if (!visible) document.getElementById('lbUsernameInput').focus();
        }

        async function addListenBrainzPlaylists() {
            const username = document.getElementById('lbUsernameInput').value.trim();
            if (!username) {
                watchedError.textContent = 'Please enter a ListenBrainz username';
                watchedError.style.display = 'block';
                return;
            }

            watchedError.style.display = 'none';
            const btn = document.getElementById('addLbBtn');
            btn.disabled = true;
            btn.textContent = 'Add';

            const statusEl = document.getElementById('watchedAddStatus');
            const startedAt = new Date().toISOString();
            let timerInterval = null;

            function updateStatus(stage) {
                statusEl.innerHTML = `<span class="watched-card-refreshing"><span class="watched-refresh-spinner"></span>${escapeHtml(formatRefreshStage(stage, startedAt, 'listenbrainz'))}</span>`;
                statusEl.style.display = 'block';
            }

            updateStatus('fetching');
            timerInterval = setInterval(() => updateStatus('fetching'), 1000);

            try {
                const response = await apiFetch('/api/watched-playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: username,
                        refresh_interval_hours: parseFloat(watchedIntervalSelect.value),
                        convert_to_flac: watchedConvertToFlac ? watchedConvertToFlac.checked : convertToFlacCheckbox.checked,
                        make_m3u: document.getElementById('watchedMakeM3u') ? document.getElementById('watchedMakeM3u').checked : false,
                        use_playlists_dir: document.getElementById('watchedUsePlaylistsDir') ? document.getElementById('watchedUsePlaylistsDir').checked : false,
                        sync_mode: 'mirror',
                        custom_subdir: (document.getElementById('watchedCustomSubdir')?.value || '').trim() || null
                    })
                });

                if (!response.ok) {
                    const ct = response.headers.get('content-type') || '';
                    if (ct.includes('application/json')) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Failed to add ListenBrainz playlists');
                    }
                    const text = (await response.text()).slice(0, 200);
                    throw new Error(`Server error ${response.status}: ${text || response.statusText}`);
                }

                const data = await response.json();
                showToast(`Added ${data.created} ListenBrainz playlist(s)` + (data.skipped ? ` (${data.skipped} already watched)` : ''));
                document.getElementById('lbUsernameInput').value = '';
                toggleLbRow();
                loadWatchedPlaylists();
            } catch (error) {
                watchedError.textContent = error.message;
                watchedError.style.display = 'block';
            } finally {
                clearInterval(timerInterval);
                statusEl.style.display = 'none';
                btn.disabled = false;
                btn.textContent = 'Add';
            }
        }

        async function refreshWatchedPlaylist(playlistId) {
            if (watchedRefreshPending.has(playlistId)) return;
            watchedRefreshPending.set(playlistId, { stage: 'starting', startedAt: new Date().toISOString() });
            loadWatchedPlaylists(false);
            try {
                showToast('Checking for new tracks...');
                const response = await apiFetch(`/api/watched-playlists/${playlistId}/refresh`, {
                    method: 'POST'
                });

                if (!response.ok) throw new Error('Refresh failed');

                const data = await response.json();
                if (data.already_running) {
                    showToast('Refresh already in progress');
                } else if (data.error) {
                    showToast(`Error: ${data.error}`, true);
                } else {
                    const newCount = data.new_tracks || 0;
                    const missingCount = data.missing_tracks || 0;
                    if (newCount > 0 || missingCount > 0) {
                        const parts = [];
                        if (newCount > 0) parts.push(`${newCount} new`);
                        if (missingCount > 0) parts.push(`${missingCount} missing`);
                        showToast(`Found ${parts.join(' + ')} tracks, queued ${data.queued} for download`);
                    } else {
                        showToast('No new tracks found');
                    }
                }
            } catch (error) {
                showToast('Failed to refresh playlist', true);
            } finally {
                watchedRefreshPending.delete(playlistId);
                loadWatchedPlaylists();
            }
        }

        async function copyWatchedPlaylistUrl(url) {
            try {
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(url);
                } else {
                    const tempInput = document.createElement('input');
                    tempInput.value = url;
                    document.body.appendChild(tempInput);
                    tempInput.select();
                    document.execCommand('copy');
                    document.body.removeChild(tempInput);
                }
                showToast('Playlist URL copied');
            } catch (error) {
                showToast('Failed to copy playlist URL', true);
            }
        }

        async function toggleWatchedPlaylist(playlistId, enabled) {
            const isEnabled = enabled === true || enabled === 'true';
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: isEnabled })
                });

                if (!response.ok) throw new Error('Update failed');

                loadWatchedPlaylists();
                showToast(isEnabled ? 'Playlist watching resumed' : 'Playlist watching paused');
            } catch (error) {
                showToast('Failed to update playlist', true);
            }
        }

        async function updateWatchedPlaylistFlac(playlistId, convertToFlac) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ convert_to_flac: convertToFlac })
                });

                if (!response.ok) throw new Error('Update failed');

                showToast(convertToFlac ? 'Format: FLAC' : 'Format: Opus');
            } catch (error) {
                showToast('Failed to update format setting', true);
                loadWatchedPlaylists();
            }
        }

        async function updateWatchedPlaylistM3u(playlistId, makeM3u) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ make_m3u: makeM3u })
                });

                if (!response.ok) throw new Error('Update failed');

                showToast(makeM3u ? 'M3U generation enabled' : 'M3U generation disabled');
            } catch (error) {
                showToast('Failed to update M3U setting', true);
                loadWatchedPlaylists();
            }
        }

        async function updateWatchedPlaylistUsePlaylists(playlistId, usePlaylists) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ use_playlists_dir: usePlaylists })
                });

                if (!response.ok) throw new Error('Update failed');

                showToast(usePlaylists ? 'Downloads going to Playlists folder' : 'Downloads going to Singles folder');
            } catch (error) {
                showToast('Failed to update Playlists folder setting', true);
                loadWatchedPlaylists();
            }
        }

        async function toggleMissingTracks(playlistId) {
            const panel = document.getElementById(`missing-${playlistId}`);
            if (!panel) return;

            if (panel.style.display !== 'none') {
                panel.style.display = 'none';
                return;
            }

            panel.style.display = 'block';
            panel.textContent = 'Loading...';

            try {
                const resp = await apiFetch(`/api/watched-playlists/${playlistId}/missing`);
                if (!resp.ok) throw new Error('Failed to fetch');
                const data = await resp.json();

                if (!data.count) {
                    panel.textContent = 'No missing tracks - everything downloaded successfully.';
                    return;
                }

                const rows = data.missing.map((t, i) => {
                    const removed = t.removed_at ? ' <span style="color: var(--text-secondary); font-size: 10px;">(removed upstream)</span>' : '';
                    const trackId = `missing-track-${playlistId}-${i}`;
                    return `<div id="${trackId}" style="padding: 4px 0; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
                        <span style="flex: 1; min-width: 0;">${escapeHtml(t.artist)} &ndash; ${escapeHtml(t.title)}${removed}</span>
                        <div style="display: flex; gap: 4px; align-items: center; flex-shrink: 0;">
                            <span style="font-size: 10px; color: var(--text-secondary); white-space: nowrap;">${t.job_status || 'not attempted'}</span>
                            <button type="button"
                                data-action="retry-missing-track"
                                data-playlist-id="${escapeAttr(playlistId)}"
                                data-artist="${escapeAttr(t.artist)}"
                                data-title="${escapeAttr(t.title)}"
                                data-row-id="${escapeAttr(trackId)}"
                                style="padding: 3px 8px; font-size: 11px; font-family: inherit; background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; white-space: nowrap;"
                                title="Auto-search and re-queue this track">Retry</button>
                            <button type="button"
                                data-action="search-missing-track"
                                data-playlist-id="${escapeAttr(playlistId)}"
                                data-playlist-name="${escapeAttr(data.playlist_name)}"
                                data-artist="${escapeAttr(t.artist)}"
                                data-title="${escapeAttr(t.title)}"
                                style="padding: 3px 8px; font-size: 11px; font-family: inherit; background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; white-space: nowrap;"
                                title="Search manually and pick a result">Search</button>
                        </div>
                    </div>`;
                }).join('');

                panel.innerHTML = `<div style="font-weight: 600; margin-bottom: 6px;">${data.count} missing track${data.count !== 1 ? 's' : ''}:</div>${rows}`;
            } catch (e) {
                panel.textContent = 'Could not load missing tracks.';
            }
        }

        async function retryMissingTrack(playlistId, artist, title, rowId) {
            const row = document.getElementById(rowId);
            const retryBtn = row?.querySelector('button');
            if (retryBtn) { retryBtn.disabled = true; retryBtn.textContent = 'Searching...'; }

            try {
                const resp = await apiFetch(`/api/watched-playlists/${playlistId}/retry-track`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ artist, title })
                });
                if (!resp.ok) throw new Error('Failed');
                showToast(`Queued search for ${artist} - ${title}`);
                if (retryBtn) { retryBtn.textContent = 'Queued'; }
            } catch (e) {
                showToast('Failed to retry track', true);
                if (retryBtn) { retryBtn.disabled = false; retryBtn.textContent = 'Retry'; }
            }
        }

        async function searchMissingTrack(playlistId, playlistName, artist, title) {
            // Pre-fill the search input
            setSearchValue(`${artist} - ${title}`);

            // Open the destination picker in playlist mode and pre-select this playlist,
            // so the user can immediately download straight back into the right playlist.
            const sel = document.getElementById('playlistSelectorInput');
            const playlistPanel = document.getElementById('playlistSelector');
            const modePanel = document.getElementById('destinationModePanel');
            const pickerToggle = document.getElementById('destinationPickerToggle');
            if (sel && playlistName) {
                await loadPlaylists();
                let found = false;
                for (const opt of sel.options) {
                    if (opt.value === playlistName) {
                        sel.value = playlistName;
                        found = true;
                        break;
                    }
                }
                _destinationMode = 'playlist';
                if (modePanel) modePanel.style.display = 'none';
                if (playlistPanel) playlistPanel.style.display = 'flex';
                if (pickerToggle) {
                    pickerToggle.textContent = 'Adding to playlist';
                    pickerToggle.classList.add('active');
                }
                if (!found) sel.value = '';
                _updatePlaylistSelectorWarning();
            }

            // Switch to Results tab and fire the search
            const resultsTabBtn = document.querySelector('.tab[data-tab="results"]');
            if (resultsTabBtn) resultsTabBtn.click();

            search();
        }

        async function toggleTrackList(playlistId, playlistName) {
            const panel = document.getElementById(`tracks-${playlistId}`);
            if (!panel) return;

            if (panel.style.display !== 'none') {
                panel.style.display = 'none';
                return;
            }

            panel.style.display = 'block';
            panel.textContent = 'Loading...';

            try {
                const resp = await apiFetch(`/api/watched-playlists/${playlistId}/tracks`);
                if (!resp.ok) throw new Error('Failed to fetch');
                const data = await resp.json();

                if (!data.total) {
                    panel.textContent = 'No tracks tracked yet.';
                    return;
                }

                const tracks = data.tracks;
                const sections = {
                    downloaded: tracks.filter(t => t.downloaded_at),
                    failed: tracks.filter(t => !t.downloaded_at && ['failed', 'completed_with_errors', null].includes(t.job_status) && !t.removed_at),
                    pending: tracks.filter(t => !t.downloaded_at && ['queued', 'downloading'].includes(t.job_status)),
                    removed: tracks.filter(t => t.removed_at),
                };

                let html = `<div class="track-list-summary">${data.downloaded} downloaded &middot; ${data.failed} failed &middot; ${data.pending} pending</div>`;

                function trackRow(t, i, actions) {
                    const rowId = `tl-track-${playlistId}-${i}`;
                    return `<div id="${rowId}" class="track-list-row">
                        <span class="track-list-label">${escapeHtml(t.artist)} &ndash; ${escapeHtml(t.title)}</span>
                        <div class="track-list-actions">${actions(t, rowId)}</div>
                    </div>`;
                }

                if (sections.downloaded.length) {
                    html += `<div class="track-list-section-header">Downloaded (${sections.downloaded.length})</div>`;
                    html += sections.downloaded.map((t, i) => trackRow(t, `d${i}`, (t, rowId) =>
                        `<span class="track-status-chip track-status-ok">&#10003;</span>
                         ${t.job_id ? `<button onclick="saveJobToDevice('${escapeAttr(t.job_id)}')" title="Save this track to your device" class="track-action-btn">
                             <i class="fa-solid fa-download"></i></button>` : ''}
                         <button type="button"
                             data-action="replace-watched-track"
                             data-playlist-id="${escapeAttr(playlistId)}"
                             data-playlist-name="${escapeAttr(data.playlist_name)}"
                             data-artist="${escapeAttr(t.artist)}"
                             data-title="${escapeAttr(t.title)}"
                             data-job-id="${escapeAttr(t.job_id || '')}"
                             data-row-id="${escapeAttr(rowId)}"
                             class="track-replace-btn" title="Delete this file and search for the correct version">Replace</button>`
                    )).join('');
                }

                if (sections.failed.length) {
                    html += `<div class="track-list-section-header">Failed / Missing (${sections.failed.length})</div>`;
                    html += sections.failed.map((t, i) => trackRow(t, `f${i}`, (t, rowId) =>
                        `<span class="track-status-chip track-status-fail">&#10007;</span>
                         <button type="button"
                             data-action="retry-missing-track"
                             data-playlist-id="${escapeAttr(playlistId)}"
                             data-playlist-name="${escapeAttr(data.playlist_name)}"
                             data-artist="${escapeAttr(t.artist)}"
                             data-title="${escapeAttr(t.title)}"
                             data-row-id="${escapeAttr(rowId)}"
                             class="track-action-btn" title="Auto-search and re-queue">Retry</button>
                         <button type="button"
                             data-action="find-missing-track-versions"
                             data-playlist-id="${escapeAttr(playlistId)}"
                             data-playlist-name="${escapeAttr(data.playlist_name)}"
                             data-artist="${escapeAttr(t.artist)}"
                             data-title="${escapeAttr(t.title)}"
                             data-row-id="${escapeAttr(rowId)}"
                             class="track-action-btn" title="Preview and pick from the top alternatives">Find Versions</button>
                         <button type="button"
                             data-action="search-missing-track"
                             data-playlist-id="${escapeAttr(playlistId)}"
                             data-playlist-name="${escapeAttr(data.playlist_name)}"
                             data-artist="${escapeAttr(t.artist)}"
                             data-title="${escapeAttr(t.title)}"
                             class="track-action-btn" title="Search manually">Search</button>`
                    )).join('');
                }

                if (sections.pending.length) {
                    html += `<div class="track-list-section-header">In Progress (${sections.pending.length})</div>`;
                    html += sections.pending.map((t, i) => trackRow(t, `p${i}`, () =>
                        `<span class="track-status-chip track-status-pending">&#8987;</span>`
                    )).join('');
                }

                if (sections.removed.length) {
                    html += `<div class="track-list-section-header">Removed Upstream (${sections.removed.length})</div>`;
                    html += sections.removed.map((t, i) => trackRow(t, `r${i}`, () =>
                        `<span class="track-status-chip track-status-removed">removed</span>`
                    )).join('');
                }

                panel.innerHTML = html;
            } catch (e) {
                panel.textContent = 'Could not load tracks.';
            }
        }

        async function replaceTrack(playlistId, playlistName, artist, title, jobId, rowId) {
            const row = document.getElementById(rowId);
            const btn = row?.querySelector('.track-replace-btn');
            if (btn) { btn.disabled = true; btn.textContent = 'Trashing...'; }

            try {
                // Trash (or unlink from playlist) the bad file and clear downloaded_at
                const resp = await apiFetch(`/api/jobs/${jobId}/file`, { method: 'DELETE' });
                if (!resp.ok) throw new Error('Trash failed');
                const data = await resp.json();

                showToast(`Trashed ${artist} - ${title}. Pick the correct version below.`);

                // Update the row to show missing state with retry/search options
                if (row) {
                    const actionsEl = row.querySelector('.track-list-actions');
                    if (actionsEl) {
                        actionsEl.innerHTML = `<span class="track-status-chip track-status-fail">missing</span>
                            <button type="button"
                                data-action="retry-missing-track"
                                data-playlist-id="${escapeAttr(playlistId)}"
                                data-playlist-name="${escapeAttr(playlistName)}"
                                data-artist="${escapeAttr(artist)}"
                                data-title="${escapeAttr(title)}"
                                data-row-id="${escapeAttr(rowId)}"
                                class="track-action-btn">Retry</button>
                            <button type="button"
                                data-action="find-missing-track-versions"
                                data-playlist-id="${escapeAttr(playlistId)}"
                                data-playlist-name="${escapeAttr(playlistName)}"
                                data-artist="${escapeAttr(artist)}"
                                data-title="${escapeAttr(title)}"
                                data-row-id="${escapeAttr(rowId)}"
                                class="track-action-btn">Find Versions</button>
                            <button type="button"
                                data-action="search-missing-track"
                                data-playlist-id="${escapeAttr(playlistId)}"
                                data-playlist-name="${escapeAttr(playlistName)}"
                                data-artist="${escapeAttr(artist)}"
                                data-title="${escapeAttr(title)}"
                                class="track-action-btn">Search</button>`;
                    }
                }

                // Route to search tab pre-filled with this track + playlist selected
                searchMissingTrack(playlistId, playlistName, artist, title);
            } catch (e) {
                showToast('Failed to trash file', true);
                if (btn) { btn.disabled = false; btn.textContent = 'Replace'; }
            }
        }

        async function updateWatchedPlaylistSyncMode(playlistId, syncMode) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sync_mode: syncMode })
                });

                if (!response.ok) throw new Error('Update failed');

                showToast(syncMode === 'mirror' ? 'Sync mode: Mirror (M3U tracks upstream)' : 'Sync mode: Append (M3U grows over time)');
            } catch (error) {
                showToast('Failed to update sync mode', true);
                loadWatchedPlaylists();
            }
        }

        function clearWatchedCustomSubdir() {
            const input = document.getElementById('watchedCustomSubdir');
            const btn = document.getElementById('watchedClearDestBtn');
            const display = document.getElementById('watchedDestDisplay');
            if (input) input.value = '';
            if (btn) btn.style.display = 'none';
            if (display) display.textContent = '';
        }

        function updateWatchedDestDisplay() {
            const val = (document.getElementById('watchedCustomSubdir')?.value || '').trim();
            const display = document.getElementById('watchedDestDisplay');
            const clearBtn = document.getElementById('watchedClearDestBtn');
            const musicRoot = (serverConfig?.music_dir || '/music').replace(/\/+$/, '');
            if (display) display.textContent = val ? `${musicRoot}/${val}` : '';
            if (clearBtn) clearBtn.style.display = val ? 'inline-flex' : 'none';
        }

        // =============================================================================
        // Destination folder picker modal
        // =============================================================================
        let _destPickerCallback = null;
        let _destPickerBrowsePath = '';   // directory currently being listed
        let _destPickerSelected = '';     // path currently selected (confirmed in input)

        async function openDestPicker(currentValue, callback) {
            _destPickerCallback = callback;
            _destPickerSelected = (currentValue || '').trim();
            _destPickerBrowsePath = _destPickerSelected
                ? _destPickerSelected.split('/').slice(0, -1).join('/')
                : '';
            const input = document.getElementById('destPickerCustomInput');
            if (input) input.value = _destPickerSelected;
            const overlay = document.getElementById('destPickerOverlay');
            if (overlay) overlay.style.display = 'flex';
            await _destPickerBrowse(_destPickerBrowsePath);
        }

        function closeDestPicker() {
            const overlay = document.getElementById('destPickerOverlay');
            if (overlay) overlay.style.display = 'none';
            _destPickerCallback = null;
        }

        function confirmDestPicker() {
            const raw = (document.getElementById('destPickerCustomInput')?.value || '').trim();
            if (_destPickerCallback) _destPickerCallback(raw);
            closeDestPicker();
        }

        function destPickerOnCustomInput(value) {
            _destPickerSelected = value.trim();
            _destPickerRenderBreadcrumb(_destPickerBrowsePath);
        }

        async function _destPickerBrowse(path) {
            _destPickerBrowsePath = path;
            _destPickerRenderBreadcrumb(path);
            const listEl = document.getElementById('destPickerList');
            if (!listEl) return;
            listEl.innerHTML = '<div class="dest-picker-loading">Loading...</div>';
            try {
                const encoded = path ? encodeURIComponent(path) : '';
                const resp = await apiFetch(`/api/music-dirs${encoded ? '?path=' + encoded : ''}`);
                if (!resp.ok) throw new Error('Failed to load directories');
                const data = await resp.json();
                const dirs = Array.isArray(data.directories) ? data.directories : [];
                listEl.innerHTML = '';
                if (path) {
                    const upBtn = document.createElement('button');
                    upBtn.className = 'dest-picker-item dest-picker-up';
                    upBtn.innerHTML = '<i class="fa-solid fa-arrow-left"></i> Up';
                    upBtn.onclick = () => {
                        const parent = path.includes('/') ? path.split('/').slice(0, -1).join('/') : '';
                        _destPickerBrowse(parent);
                    };
                    listEl.appendChild(upBtn);
                    // "Select this folder" button for the current browsed path
                    const selfBtn = document.createElement('button');
                    selfBtn.className = 'dest-picker-item dest-picker-self';
                    selfBtn.innerHTML = `<i class="fa-solid fa-check"></i> Use: <strong>${escapeHtml(path)}</strong>`;
                    selfBtn.onclick = () => _destPickerSetValue(path);
                    listEl.appendChild(selfBtn);
                } else {
                    const rootBtn = document.createElement('button');
                    rootBtn.className = 'dest-picker-item dest-picker-self';
                    rootBtn.innerHTML = '<i class="fa-solid fa-check"></i> Use: <strong>music root</strong>';
                    rootBtn.onclick = () => _destPickerSetValue('.');
                    listEl.appendChild(rootBtn);
                }
                if (dirs.length === 0) {
                    const empty = document.createElement('div');
                    empty.className = 'dest-picker-empty';
                    empty.textContent = 'No subdirectories here.';
                    listEl.appendChild(empty);
                }
                for (const dir of dirs) {
                    const btn = document.createElement('button');
                    btn.className = 'dest-picker-item';
                    const name = dir.split('/').pop();
                    btn.innerHTML = `<i class="fa-solid fa-folder"></i> ${escapeHtml(name)}`;
                    btn.onclick = () => _destPickerBrowse(dir);
                    listEl.appendChild(btn);
                }
            } catch (e) {
                listEl.innerHTML = '<div class="dest-picker-empty">Could not load directories.</div>';
            }
        }

        function _destPickerSetValue(path) {
            _destPickerSelected = path;
            const input = document.getElementById('destPickerCustomInput');
            if (input) input.value = path === '.' ? '.' : path;
        }

        function _destPickerRenderBreadcrumb(path) {
            const el = document.getElementById('destPickerBreadcrumb');
            if (!el) return;
            const musicRoot = (serverConfig?.music_dir || '/music').replace(/\/+$/, '');
            const parts = path ? path.split('/') : [];
            let html = `<span class="dest-crumb dest-crumb-root" onclick="_destPickerBrowse('')">${escapeHtml(musicRoot)}</span>`;
            let cumulative = '';
            for (const part of parts) {
                cumulative = cumulative ? `${cumulative}/${part}` : part;
                const captured = cumulative;
                html += ` <span class="dest-crumb-sep">/</span> <span class="dest-crumb" onclick="_destPickerBrowse('${escapeAttr(captured)}')">${escapeHtml(part)}</span>`;
            }
            el.innerHTML = html;
        }

        async function updateWatchedPlaylistCustomSubdir(playlistId, value) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ custom_subdir: value || null })
                });
                if (!response.ok) throw new Error('Update failed');
                showToast(value ? `Destination: ${value}` : 'Destination reset to default');
                loadWatchedPlaylists();
            } catch (error) {
                showToast('Failed to update destination', true);
                loadWatchedPlaylists();
            }
        }

        // Source chips: render per-source toggle chips for a watched playlist card or the add form
        let _cachedSources = null;
        async function _fetchSources() {
            if (_cachedSources) return _cachedSources;
            try {
                const res = await apiFetch('/api/sources');
                if (res.ok) _cachedSources = (await res.json()).sources || [];
            } catch {}
            return _cachedSources || [];
        }

        function renderSourceChips(playlistId, preferredSources) {
            // Only show globally-enabled sources; repopulate once sources load if cache is empty
            const sources = (_cachedSources || []).filter(s => s.enabled);
            const active = preferredSources === 'all' ? [] : preferredSources.split(',').map(s => s.trim());
            return sources.map(s => {
                const on = active.length === 0 || active.includes(s.id);
                return `<button type="button" class="source-chip ${on ? 'on' : 'off'}" data-source="${escapeAttr(s.id)}"
                    onclick="toggleSourceChip(this, '${escapeAttr(playlistId)}')"
                    title="${escapeAttr(s.label)}">${escapeHtml(s.badge)}</button>`;
            }).join('');
        }

        async function populateSourceChips() {
            await _fetchSources();
            // Re-render any chips containers that used stale/empty data
            document.querySelectorAll('.watched-sources-chips[data-playlist-id]').forEach(el => {
                const pid = el.dataset.playlistId;
                // Use the authoritative preferred_sources from the API, not the DOM chip state
                const pref = el.dataset.preferred || 'all';
                el.innerHTML = renderSourceChips(pid, pref);
            });
            // Same dance for per-playlist priority-source dropdowns: they may have
            // been rendered before _cachedSources populated, so the only option was
            // "No preference". Re-fill them now using the stored value as the default.
            document.querySelectorAll('select[data-priority-source]').forEach(sel => {
                const current = sel.dataset.prioritySource || '';
                sel.innerHTML = renderPrioritySourceOptions(current);
            });
            // Populate the add-form selector (only globally-enabled sources, all on by default)
            const formSel = document.getElementById('watchedSourcesSelector');
            if (formSel && formSel.children.length === 0) {
                const sources = (_cachedSources || []).filter(s => s.enabled);
                formSel.innerHTML = sources.map(s =>
                    `<button type="button" class="source-chip on" data-source="${escapeAttr(s.id)}"
                        onclick="this.classList.toggle('on'); this.classList.toggle('off')"
                        title="${escapeAttr(s.label)}">${escapeHtml(s.badge)}</button>`
                ).join('');
            }
            // Populate priority-source dropdowns (one in bulk import, one in watched add form)
            populatePrioritySourceDropdowns();
        }

        function populatePrioritySourceDropdowns() {
            const sources = (_cachedSources || []).filter(s => s.enabled);
            const targets = ['bulkPrioritySource', 'watchedPrioritySource'];
            for (const id of targets) {
                const sel = document.getElementById(id);
                if (!sel || sel.options.length > 1) continue;  // already populated
                for (const s of sources) {
                    const opt = document.createElement('option');
                    opt.value = s.id;
                    opt.textContent = s.label;
                    sel.appendChild(opt);
                }
            }
        }

        function toggleSourceChip(btn, playlistId) {
            btn.classList.toggle('on');
            btn.classList.toggle('off');
            // Collect current state for this playlist
            const container = btn.closest('.watched-sources-chips');
            const chips = [...container.querySelectorAll('.source-chip')];
            const on = chips.filter(c => c.classList.contains('on')).map(c => c.dataset.source);
            // "all on" = send "all"; partial = comma list; none = "all" (fallback, don't allow locking out)
            const preferred = (on.length === 0 || on.length === chips.length) ? 'all' : on.join(',');
            container.dataset.preferred = preferred;
            updateWatchedPlaylistPreferredSources(playlistId, preferred);
        }

        function getWatchedPreferredSources() {
            const chips = [...document.querySelectorAll('#watchedSourcesSelector .source-chip')];
            if (chips.length === 0) return 'all';
            const on = chips.filter(c => c.classList.contains('on')).map(c => c.dataset.source);
            return (on.length === 0 || on.length === chips.length) ? 'all' : on.join(',');
        }

        function setWatchedPreferredSource(sourceId) {
            // Flip the add-form source chips to a single source, leaving others off
            const chips = [...document.querySelectorAll('#watchedSourcesSelector .source-chip')];
            chips.forEach(c => {
                const match = c.dataset.source === sourceId;
                c.classList.toggle('on', match);
                c.classList.toggle('off', !match);
            });
        }

        async function updateWatchedPlaylistPreferredSources(playlistId, preferred) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preferred_sources: preferred })
                });
                if (!response.ok) throw new Error('Update failed');
                const label = preferred === 'all' ? 'all sources' : preferred;
                showToast(`Sources: ${label}`);
            } catch (error) {
                showToast('Failed to update sources', true);
                loadWatchedPlaylists();
            }
        }

        function renderPrioritySourceOptions(current) {
            const sources = (_cachedSources || []).filter(s => s.enabled);
            const cur = (current || '').toLowerCase();
            const opts = [`<option value="" ${cur ? '' : 'selected'}>No preference</option>`];
            for (const s of sources) {
                const sel = cur === s.id ? 'selected' : '';
                opts.push(`<option value="${escapeAttr(s.id)}" ${sel}>${escapeHtml(s.label)}</option>`);
            }
            return opts.join('');
        }

        async function updateWatchedPlaylistPrioritySource(playlistId, priority) {
            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ priority_source: priority || '' })
                });
                if (!response.ok) throw new Error('Update failed');
                showToast(priority ? `Preferred source: ${priority}` : 'Preferred source cleared');
            } catch (error) {
                showToast('Failed to update preferred source', true);
                loadWatchedPlaylists();
            }
        }

        async function deleteWatchedPlaylist(playlistId, name) {
            if (!confirm(`Stop watching "${name}"? This won't delete any downloaded files.`)) {
                return;
            }

            try {
                const response = await apiFetch(`/api/watched-playlists/${playlistId}`, {
                    method: 'DELETE'
                });

                if (!response.ok) throw new Error('Delete failed');

                showToast(`Stopped watching "${name}"`);
                loadWatchedPlaylists();
            } catch (error) {
                showToast('Failed to delete playlist', true);
            }
        }

        async function refreshAllWatched() {
            refreshAllWatchedBtn.disabled = true;
            refreshAllWatchedBtn.textContent = 'Checking...';

            try {
                const response = await apiFetch('/api/watched-playlists/check-all', {
                    method: 'POST'
                });

                if (!response.ok) throw new Error('Check failed');

                const data = await response.json();
                if (data.checked === 0) {
                    showToast('No playlists due for refresh');
                } else if (data.total_new_tracks > 0) {
                    showToast(`Checked ${data.checked} playlists: ${data.total_new_tracks} new tracks, ${data.total_queued} queued`);
                } else {
                    showToast(`Checked ${data.checked} playlists: no new tracks`);
                }
                loadWatchedPlaylists();
            } catch (error) {
                showToast('Failed to check playlists', true);
            } finally {
                refreshAllWatchedBtn.disabled = false;
                refreshAllWatchedBtn.textContent = 'Check All Now';
            }
        }

        // Event listeners for watched playlists
        addWatchedBtn.addEventListener('click', addWatchedPlaylist);
        watchedUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') addWatchedPlaylist();
        });
        document.getElementById('lbUsernameInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') addListenBrainzPlaylists();
        });
        refreshAllWatchedBtn.addEventListener('click', refreshAllWatched);

        // Watched Upgrades controls (pager + rescan)
        (function wireUpgradeControls() {
            const prev = document.getElementById('upgradesPrevBtn');
            const next = document.getElementById('upgradesNextBtn');
            const rescan = document.getElementById('rescanUpgradesBtn');
            if (prev) prev.addEventListener('click', () => { if (upgradesPage > 1) { upgradesPage--; loadWatchedUpgrades(); } });
            if (next) next.addEventListener('click', () => { upgradesPage++; loadWatchedUpgrades(); });
            if (rescan) rescan.addEventListener('click', async () => {
                rescan.disabled = true;
                const original = rescan.textContent;
                rescan.textContent = 'Scanning...';
                try {
                    const resp = await apiFetch('/api/upgrades/rescan', { method: 'POST' });
                    if (resp.ok) {
                        const data = await resp.json();
                        const t = data.totals || {};
                        showToast(`Scan done: ${t.below_target || 0} below target across ${t.eligible || 0} files`);
                        upgradesPage = 1;
                        loadWatchedUpgrades();
                    } else if (resp.status === 400) {
                        showToast('Enable Track Upgrades in Settings first', true);
                    } else if (resp.status === 403) {
                        showToast('Admin only', true);
                    }
                } catch (e) {
                    showToast('Scan failed', true);
                } finally {
                    rescan.disabled = false;
                    rescan.textContent = original;
                }
            });
        })();

        // =============================================================================
        // Watched Artists
        // =============================================================================

        let selectedArtistMbid = null;
        let selectedArtistName = null;
        let artistRefreshPending = new Map(); // artist_id -> {stage, startedAt}
        let artistRefreshPollInterval = null;

        function startArtistRefreshPolling() {
            if (artistRefreshPollInterval) return;
            artistRefreshPollInterval = setInterval(() => loadWatchedArtists(false), 2000);
        }

        function stopArtistRefreshPolling() {
            if (artistRefreshPollInterval) {
                clearInterval(artistRefreshPollInterval);
                artistRefreshPollInterval = null;
            }
        }

        async function searchArtist() {
            const q = document.getElementById('artistSearchInput').value.trim();
            if (!q) return;
            const resultsEl = document.getElementById('artistSearchResults');
            const addForm = document.getElementById('artistAddForm');
            resultsEl.style.display = 'block';
            resultsEl.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);">Searching MusicBrainz...</div>';
            addForm.style.display = 'none';
            selectedArtistMbid = null;
            selectedArtistName = null;
            try {
                const res = await apiFetch(`/api/watched-artists/search?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                if (!data.results || data.results.length === 0) {
                    resultsEl.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);">No artists found on MusicBrainz.</div>';
                    return;
                }
                // Exact match or top 3
                const candidates = data.results[0].name.toLowerCase() === q.toLowerCase()
                    ? [data.results[0]]
                    : data.results.slice(0, 3);
                resultsEl.innerHTML = candidates.map(a => `
                    <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;margin-bottom:4px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:8px;">
                        <div style="flex:1;">
                            <span style="font-size:13px;font-weight:600;color:var(--text-primary);">${escapeHtml(a.name)}</span>
                            ${a.disambiguation ? `<span style="font-size:11px;color:var(--text-secondary);margin-left:6px;">${escapeHtml(a.disambiguation)}</span>` : ''}
                        </div>
                        <button class="action-btn" style="padding:4px 10px;font-size:12px;" onclick="selectArtist('${escapeAttr(a.mbid)}','${escapeAttr(a.name)}')">Select</button>
                    </div>
                `).join('');
            } catch (e) {
                resultsEl.innerHTML = '<div style="font-size:12px;color:var(--error);">Search failed. Check server logs.</div>';
            }
        }

        function selectArtist(mbid, name) {
            selectedArtistMbid = mbid;
            selectedArtistName = name;
            document.getElementById('selectedArtistName').textContent = name;
            // Default from_date to today in YYYY-MM-DD
            const today = new Date().toISOString().slice(0, 10);
            document.getElementById('artistFromDate').value = today;
            document.getElementById('artistSearchResults').style.display = 'none';
            document.getElementById('artistAddForm').style.display = 'block';
        }

        async function addWatchedArtist() {
            if (!selectedArtistMbid) return;
            const fromDate = document.getElementById('artistFromDate').value;
            const intervalHours = parseInt(document.getElementById('artistIntervalSelect').value);
            const convertToFlac = document.getElementById('artistConvertToFlac').checked;
            const statusEl = document.getElementById('artistAddStatus');
            const addBtn = document.getElementById('addArtistBtn');

            addBtn.disabled = true;
            statusEl.style.display = 'block';
            statusEl.textContent = 'Adding artist and fetching singles from MusicBrainz...';

            try {
                const res = await apiFetch('/api/watched-artists', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        mbid: selectedArtistMbid,
                        name: selectedArtistName,
                        from_date: fromDate,
                        refresh_interval_hours: intervalHours,
                        convert_to_flac: convertToFlac,
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Failed to add artist');
                statusEl.style.display = 'none';
                document.getElementById('artistAddForm').style.display = 'none';
                document.getElementById('artistSearchInput').value = '';
                selectedArtistMbid = null;
                selectedArtistName = null;
                // Seeding now runs in the background. Mark it as refreshing so the
                // card shows progress and polling kicks in (avoids a race with the
                // background thread setting refresh_state).
                if (data.id) {
                    artistRefreshPending.set(data.id, { stage: 'starting', startedAt: new Date().toISOString() });
                }
                showToast(`Now watching ${data.name}. Fetching the back-catalogue from MusicBrainz...`);
                loadWatchedArtists();
            } catch (e) {
                statusEl.textContent = `Error: ${e.message}`;
                addBtn.disabled = false;
            }
        }

        // =====================================================================
        // Watched Upgrades (track-upgrades Phase 2a: search + display, no swap yet)
        // =====================================================================
        const UPGRADES_PER_PAGE = 10;
        let upgradesPage = 1;
        let upgradesSearchToken = 0;  // bumped to abort an in-flight lazy-search sweep
        const UPGRADE_SOURCE_BADGES = {
            youtube: 'YT', monochrome: 'MONO', soulseek: 'SLK',
            soundcloud: 'SC', mp3phoenix: 'PX', zvu4no: 'ZV', freemp3cloud: 'FMC',
        };

        function upgradeTierLabel(tier) {
            return { 5: 'Lossless', 4: '320', 3: '256', 2: '192', 1: '128' }[tier] || '?';
        }

        function upgradeCurrentQuality(item) {
            if (item.file_tier === 5) return 'Lossless (' + (item.codec || '?').toUpperCase() + ')';
            const br = item.bitrate_kbps ? item.bitrate_kbps + 'kbps' : '';
            return [(item.codec || '?').toUpperCase(), br].filter(Boolean).join(' ');
        }

        async function loadWatchedUpgrades() {
            const list = document.getElementById('watchedUpgradesList');
            const pager = document.getElementById('upgradesPager');
            const disabledNote = document.getElementById('upgradesDisabledNote');
            if (!list) return;
            upgradesSearchToken++;  // abort any previous sweep
            try {
                const resp = await apiFetch(`/api/upgrades/candidates?page=${upgradesPage}&per_page=${UPGRADES_PER_PAGE}`);
                if (!resp.ok) { list.innerHTML = ''; return; }
                const data = await resp.json();
                if (data.enabled === false) {
                    if (disabledNote) disabledNote.style.display = '';
                    list.innerHTML = '';
                    if (pager) pager.style.display = 'none';
                    return;
                }
                if (disabledNote) disabledNote.style.display = 'none';

                if (!data.items.length) {
                    list.innerHTML = '<p class="watched-empty-text">No upgrade candidates. Either everything is already at target quality, or the library has not been scanned yet (try Rescan Library).</p>';
                    if (pager) pager.style.display = 'none';
                    return;
                }

                list.innerHTML = data.items.map(renderUpgradeRow).join('');
                data.items.forEach(item => {
                    if (item.found_searched) {
                        renderProposedUpgrade(item);
                    }
                });

                // Pager
                if (pager) {
                    const info = document.getElementById('upgradesPageInfo');
                    if (info) info.textContent = `Page ${data.page} of ${data.pages} (${data.total} below target)`;
                    document.getElementById('upgradesPrevBtn').disabled = data.page <= 1;
                    document.getElementById('upgradesNextBtn').disabled = data.page >= data.pages;
                    pager.style.display = data.pages > 1 ? '' : 'none';
                }

                // Lazily search the rows that have no fresh cached result, ~1/s, this page only.
                lazySearchUpgrades(data.items, ++upgradesSearchToken);
            } catch (e) {
                list.innerHTML = '';
            }
        }

        function renderUpgradeRow(item) {
            const title = escapeHtml(item.title || item.filename || '');
            const artist = escapeHtml(item.artist || '');
            const current = escapeHtml(upgradeCurrentQuality(item));
            return `
                <div class="result-item upgrade-row" id="upgrade-row-${item.id}" data-id="${item.id}">
                    <div class="upgrade-current">
                        <div class="upgrade-track"><strong>${artist}</strong>${artist ? ' &ndash; ' : ''}${title}</div>
                        <div class="upgrade-meta">Current: <span class="upgrade-quality-now">${current}</span></div>
                    </div>
                    <div class="upgrade-proposed" id="upgrade-proposed-${item.id}">
                        <span class="upgrade-searching">Checking for a better copy&hellip;</span>
                    </div>
                    <div class="upgrade-actions">
                        <button class="btn btn-ghost btn-sm" onclick="dismissUpgrade(${item.id})" title="Stop suggesting this one">Dismiss</button>
                    </div>
                </div>`;
        }

        function renderProposedUpgrade(item) {
            const cell = document.getElementById(`upgrade-proposed-${item.id}`);
            if (!cell) return;
            // Stash the source_url on the row so the preview handler can reach it
            // (url-based sources need it; it isn't safe to inline into an attribute).
            const rowEl = document.getElementById(`upgrade-row-${item.id}`);
            if (rowEl) rowEl._foundSourceUrl = item.found_source_url || null;
            if (!item.found_source) {
                cell.innerHTML = '<span class="upgrade-none">No better copy found</span>';
                return;
            }
            const badge = UPGRADE_SOURCE_BADGES[item.found_source] || item.found_source.toUpperCase().slice(0, 4);
            const tier = upgradeTierLabel(item.found_tier);
            const qual = escapeHtml(item.found_quality || tier);
            const conf = item.found_confidence != null ? Math.round(item.found_confidence * 100) + '%' : '';
            const verifiedBadge = item.found_verified
                ? '<span class="upgrade-verified" title="Quality confirmed by the source">verified</span>'
                : '<span class="upgrade-unverified" title="Estimated; confirmed only after download">needs download to confirm</span>';
            const canPreview = item.found_video_id && item.found_source !== 'soulseek';
            const previewBtn = canPreview
                ? `<button class="btn btn-ghost btn-sm upgrade-preview"
                        onmouseenter="previewUpgrade('${item.found_video_id}', '${item.found_source}', this, ${item.id})"
                        onmouseleave="stopPreview()"
                        onclick="previewUpgrade('${item.found_video_id}', '${item.found_source}', this, ${item.id})">&#9654; Preview</button>`
                : '';
            cell.innerHTML = `
                <span class="upgrade-arrow">&rarr;</span>
                <span class="source-badge ${item.found_source}">${badge}</span>
                <span class="upgrade-quality-new">${qual}</span>
                ${conf ? `<span class="upgrade-conf" title="Match confidence">${conf}</span>` : ''}
                ${verifiedBadge}
                ${previewBtn}
                <button class="btn btn-primary btn-sm upgrade-go" onclick="upgradeOne(${item.id}, this)" title="Download, verify, and swap in this copy">Upgrade</button>`;
        }

        // Walk the visible rows one at a time (~1/s) searching for a better copy.
        // Aborts cleanly if the token changes (user paginated away or left the tab).
        async function lazySearchUpgrades(items, token) {
            for (const item of items) {
                if (token !== upgradesSearchToken) return;
                if (item.found_searched) continue;  // already have a cached result rendered
                try {
                    const resp = await apiFetch(`/api/upgrades/candidates/${item.id}/search`, { method: 'POST' });
                    if (token !== upgradesSearchToken) return;
                    if (resp.ok) {
                        const updated = await resp.json();
                        renderProposedUpgrade(updated);
                    }
                } catch (e) { /* leave the row as-is */ }
                await new Promise(r => setTimeout(r, 1000));  // rate limit, be kind to sources
            }
        }

        function previewUpgrade(videoId, source, element, id) {
            // Reuse the search-result preview machinery with a synthetic result object.
            const row = document.getElementById(`upgrade-row-${id}`);
            let sourceUrl = null;
            // source_url isn't in the DOM; the preview endpoint needs it for url-based sources.
            // For those, fall back to the cached candidate data attached to the row.
            if (row && row._foundSourceUrl) sourceUrl = row._foundSourceUrl;
            startPreview(videoId, element, { source, source_url: sourceUrl });
        }

        async function upgradeOne(id, btn, force = false) {
            const row = document.getElementById(`upgrade-row-${id}`);
            const cell = document.getElementById(`upgrade-proposed-${id}`);
            if (btn) { btn.disabled = true; btn.textContent = force ? 'Forcing...' : 'Upgrading...'; }
            stopPreview();
            try {
                const url = `/api/upgrades/candidates/${id}/upgrade${force ? '?force=true' : ''}`;
                const resp = await apiFetch(url, { method: 'POST' });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.status === 'upgraded') {
                    if (row) {
                        row.style.transition = 'opacity 0.4s';
                        row.style.opacity = '0.5';
                        const c = row.querySelector('.upgrade-current .upgrade-meta');
                        if (c) c.innerHTML = '<span class="upgrade-verified">Upgraded</span> old file moved to quarantine';
                    }
                    if (cell) cell.innerHTML = '<span class="upgrade-verified">Done</span>';
                    showToast(force ? 'Force-upgraded; old file is in quarantine' : 'Upgraded; old file is in quarantine');
                    setTimeout(() => { if (row) row.remove(); if (!document.querySelectorAll('#watchedUpgradesList .upgrade-row').length) loadWatchedUpgrades(); }, 1200);
                } else if (data.status === 'rejected') {
                    // Offer a force override; the old file still goes to quarantine, so it's recoverable.
                    if (cell) cell.innerHTML = `<span class="upgrade-none">Rejected: ${escapeHtml(data.reason || 'failed checks')}</span>
                        <button class="btn btn-ghost btn-sm upgrade-force" onclick="forceUpgrade(${id}, this)" title="Swap it in anyway. The old file still goes to quarantine, so you can undo it.">Force anyway</button>`;
                    showToast('Upgrade rejected: ' + (data.reason || 'failed checks'), true);
                } else {
                    showToast('Upgrade failed: ' + (data.reason || resp.status), true);
                    if (btn) { btn.disabled = false; btn.textContent = force ? 'Force anyway' : 'Upgrade'; }
                }
            } catch (e) {
                showToast('Upgrade failed', true);
                if (btn) { btn.disabled = false; btn.textContent = force ? 'Force anyway' : 'Upgrade'; }
            }
        }

        function forceUpgrade(id, btn) {
            if (!confirm('Force this upgrade? The proposed copy failed a same-recording check (e.g. different length), so it may be a different version. The old file goes to quarantine and can be restored.')) return;
            upgradeOne(id, btn, true);
        }

        async function upgradeAll() {
            if (!confirm('Download and swap in every proposed upgrade on this page set? Old files are moved to quarantine (recoverable), not deleted.')) return;
            try {
                const resp = await apiFetch('/api/upgrades/upgrade-all', { method: 'POST' });
                if (resp.ok) {
                    showToast('Upgrading all in the background; this can take a while. Refresh to see progress.');
                } else if (resp.status === 400) {
                    showToast('Enable Track Upgrades in Settings first', true);
                }
            } catch (e) { showToast('Could not start upgrade-all', true); }
        }

        async function dismissUpgrade(id) {
            try {
                const resp = await apiFetch(`/api/upgrades/candidates/${id}/dismiss`, { method: 'POST' });
                if (resp.ok) {
                    const row = document.getElementById(`upgrade-row-${id}`);
                    if (row) row.remove();
                    if (!document.querySelectorAll('#watchedUpgradesList .upgrade-row').length) {
                        loadWatchedUpgrades();
                    }
                }
            } catch (e) { showToast('Could not dismiss', true); }
        }

        async function loadWatchedArtists(showLoading = true) {
            const listEl = document.getElementById('watchedArtistList');
            if (showLoading) listEl.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await apiFetch('/api/watched-artists');
                const data = await res.json();
                renderWatchedArtists(data.artists || []);
            } catch (e) {
                listEl.innerHTML = '<div class="empty-state"><p>Failed to load watched artists</p></div>';
            }
        }

        function renderWatchedArtists(artists) {
            const listEl = document.getElementById('watchedArtistList');
            if (!artists || artists.length === 0) {
                stopArtistRefreshPolling();
                listEl.innerHTML = '<div class="empty-state" style="padding: 1.5rem 0;"><div class="empty-state-icon"><i class="fa-solid fa-heart-circle-plus"></i></div><p>No artists followed yet</p></div>';
                return;
            }
            let anyRunning = false;
            listEl.innerHTML = artists.map(artist => {
                // Reconcile the optimistic "pending" flag against backend state.
                // Once the backend reports the refresh as running it's authoritative,
                // so drop the optimistic flag (otherwise it would linger after the
                // refresh finishes and spin the card forever). A safety timeout
                // covers refreshes so quick we never caught them "running".
                const pend = artistRefreshPending.get(artist.id);
                if (pend) {
                    const pendAgeMs = Date.now() - new Date(pend.startedAt).getTime();
                    if (artist.refresh_state === 'running' || pendAgeMs > 20000) {
                        artistRefreshPending.delete(artist.id);
                    }
                }
                const isRunning = artist.refresh_state === 'running' || artistRefreshPending.has(artist.id);
                if (isRunning) anyRunning = true;
                const isError = artist.refresh_state === 'error';
                const isPaused = !artist.enabled;
                const intervalLabel = artist.refresh_interval_hours >= 720 ? 'monthly'
                    : artist.refresh_interval_hours >= 168 ? 'weekly'
                    : artist.refresh_interval_hours >= 24 ? 'daily'
                    : artist.refresh_interval_hours >= 12 ? 'every 12h'
                    : artist.refresh_interval_hours >= 6 ? 'every 6h'
                    : artist.refresh_interval_hours >= 1 ? 'hourly'
                    : 'every 30min';
                const lastChecked = artist.last_checked
                    ? `Last checked: ${formatTimeAgo(artist.last_checked)}`
                    : 'Never checked';
                let stageHtml = '';
                if (isRunning) {
                    const pending = artistRefreshPending.get(artist.id);
                    const startedAt = pending?.startedAt || artist.refresh_started_at;
                    const elapsed = startedAt ? Math.floor((Date.now() - new Date(startedAt + 'Z').getTime()) / 1000) : 0;
                    const stageLabel = {
                        starting: 'Starting', fetching: 'Fetching from MusicBrainz',
                        diffing: 'Comparing tracks', queueing: 'Queueing downloads', done: 'Done',
                    }[artist.refresh_stage] || 'Refreshing';
                    stageHtml = `<span class="watched-card-refreshing"><span class="watched-refresh-spinner"></span>${escapeHtml(stageLabel)}${elapsed > 0 ? ` (${elapsed}s)` : ''}</span>`;
                }
                return `
                <div class="watched-card" id="artist-card-${artist.id}">
                    <div class="watched-card-header">
                        <span><i class="fa-brands fa-creative-commons-sampling"></i></span>
                        <span class="watched-card-name">${escapeHtml(artist.name)}</span>
                        ${stageHtml}
                        ${isPaused ? '<span class="watched-card-paused">Paused</span>' : ''}
                    </div>
                    <div class="watched-card-meta">
                        ${artist.tracked_count || 0} singles tracked &middot; ${artist.downloaded_count || 0} downloaded &middot; ${intervalLabel} &middot; ${lastChecked} &middot; From: ${artist.from_date}
                    </div>
                    ${isError ? `<div class="watched-card-refresh-error"><i class="fa-solid fa-circle-exclamation"></i><span>${escapeHtml(artist.refresh_error || 'Refresh failed')}</span></div>` : ''}
                    <div class="watched-card-settings">
                        <label class="watched-card-toggle" title="Convert singles to the selected audio format">
                            Convert
                            <div class="toggle-switch">
                                <input type="checkbox" ${artist.convert_to_flac ? 'checked' : ''}
                                    onchange="updateArtistFlac('${artist.id}', this.checked)">
                                <span class="toggle-slider"></span>
                            </div>
                        </label>
                        <label class="watched-card-toggle">
                            Check:
                            <select class="watched-card-select" onchange="updateArtistInterval('${artist.id}', this.value)">
                                <option value="0.5" ${artist.refresh_interval_hours == 0.5 ? 'selected' : ''}>Every 30 min</option>
                                <option value="1" ${artist.refresh_interval_hours == 1 ? 'selected' : ''}>Hourly</option>
                                <option value="6" ${artist.refresh_interval_hours == 6 ? 'selected' : ''}>Every 6 hours</option>
                                <option value="12" ${artist.refresh_interval_hours == 12 ? 'selected' : ''}>Every 12 hours</option>
                                <option value="24" ${artist.refresh_interval_hours == 24 ? 'selected' : ''}>Daily</option>
                                <option value="168" ${artist.refresh_interval_hours == 168 ? 'selected' : ''}>Weekly</option>
                                <option value="720" ${artist.refresh_interval_hours == 720 ? 'selected' : ''}>Monthly</option>
                            </select>
                        </label>
                    </div>
                    <div class="watched-card-actions">
                        <button class="watched-action-btn" type="button"
                            data-action="refresh-watched-artist"
                            data-artist-id="${escapeAttr(artist.id)}"
                            ${isRunning ? 'disabled' : ''}>Refresh</button>
                        <button class="watched-action-btn" type="button"
                            data-action="toggle-artist-missing-tracks"
                            data-artist-id="${escapeAttr(artist.id)}">Missing</button>
                        <button class="watched-action-btn" type="button"
                            data-action="toggle-artist-track-list"
                            data-artist-id="${escapeAttr(artist.id)}"
                            data-artist-name="${escapeAttr(artist.name)}">Tracks</button>
                        <button class="watched-action-btn watched-action-pause" type="button"
                            data-action="toggle-watched-artist"
                            data-artist-id="${escapeAttr(artist.id)}"
                            data-enabled="${artist.enabled ? 'true' : 'false'}">${isPaused ? 'Resume' : 'Pause'}</button>
                        <button class="watched-action-btn watched-action-delete" type="button"
                            data-action="delete-watched-artist"
                            data-artist-id="${escapeAttr(artist.id)}"
                            data-artist-name="${escapeAttr(artist.name)}">Delete</button>
                    </div>
                    <div id="artist-missing-${artist.id}" class="watched-card-expanded" style="display:none;"></div>
                    <div id="artist-tracks-${artist.id}" class="watched-card-expanded" style="display:none;"></div>
                </div>`;
            }).join('');

            if (anyRunning) {
                startArtistRefreshPolling();
            } else {
                stopArtistRefreshPolling();
                artistRefreshPending.clear();
            }
        }

        async function refreshWatchedArtist(artistId) {
            artistRefreshPending.set(artistId, { stage: 'starting', startedAt: new Date().toISOString() });
            loadWatchedArtists(false);
            try {
                const res = await apiFetch(`/api/watched-artists/${artistId}/refresh`, { method: 'POST' });
                const data = await res.json();
                if (data.already_running) {
                    showToast('Refresh already in progress');
                } else {
                    // Refresh runs in the background now; the card polls for progress.
                    showToast('Refresh started');
                }
            } catch (e) {
                showToast('Refresh failed', true);
                artistRefreshPending.delete(artistId);
            } finally {
                loadWatchedArtists();
            }
        }

        async function toggleWatchedArtist(artistId, enabled) {
            try {
                await apiFetch(`/api/watched-artists/${artistId}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ enabled })
                });
                loadWatchedArtists();
                showToast(enabled ? 'Artist watching resumed' : 'Artist watching paused');
            } catch (e) {
                showToast('Failed to update artist', true);
            }
        }

        async function updateArtistFlac(artistId, convertToFlac) {
            try {
                await apiFetch(`/api/watched-artists/${artistId}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ convert_to_flac: convertToFlac })
                });
            } catch (e) {
                showToast('Failed to update format setting', true);
                loadWatchedArtists();
            }
        }

        async function updateArtistInterval(artistId, hours) {
            try {
                await apiFetch(`/api/watched-artists/${artistId}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ refresh_interval_hours: parseFloat(hours) })
                });
            } catch (e) {
                showToast('Failed to update interval', true);
                loadWatchedArtists();
            }
        }

        async function deleteWatchedArtist(artistId, name) {
            if (!confirm(`Stop watching "${name}"? Downloaded tracks will not be deleted.`)) return;
            try {
                await apiFetch(`/api/watched-artists/${artistId}`, { method: 'DELETE' });
                showToast(`Stopped watching "${name}"`);
                loadWatchedArtists();
            } catch (e) {
                showToast('Failed to delete artist', true);
            }
        }

        async function toggleArtistMissingTracks(artistId) {
            const panel = document.getElementById(`artist-missing-${artistId}`);
            if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
            panel.style.display = 'block';
            panel.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await apiFetch(`/api/watched-artists/${artistId}/missing`);
                const data = await res.json();
                if (!data.tracks || data.tracks.length === 0) {
                    panel.innerHTML = '<p style="font-size:12px;color:var(--text-secondary);padding:8px 0;">No missing singles.</p>';
                    return;
                }
                panel.innerHTML = `
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <span style="font-size:12px;color:var(--text-secondary);">${data.tracks.length} missing single(s)</span>
                        <button type="button"
                            data-action="retry-all-artist-missing"
                            data-artist-id="${escapeAttr(artistId)}"
                            style="padding:4px 12px;font-size:12px;font-family:inherit;font-weight:600;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;">
                            Queue All
                        </button>
                    </div>` +
                    data.tracks.map(t => `
                        <div style="padding:4px 0;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span style="flex:1;min-width:0;font-size:12px;">${escapeHtml(t.artist || '')} &ndash; ${escapeHtml(t.title)}</span>
                            <div style="display:flex;gap:4px;align-items:center;flex-shrink:0;">
                                ${t.release_date ? `<span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;">${t.release_date}</span>` : ''}
                                <button type="button"
                                    data-action="retry-artist-track"
                                    data-artist-id="${escapeAttr(artistId)}"
                                    data-artist="${escapeAttr(t.artist || '')}"
                                    data-title="${escapeAttr(t.title)}"
                                    style="padding:3px 8px;font-size:11px;font-family:inherit;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);border-radius:4px;cursor:pointer;white-space:nowrap;"
                                    title="Auto-search and re-queue this track">Retry</button>
                            </div>
                        </div>`).join('');
            } catch (e) {
                panel.innerHTML = '<p style="font-size:12px;color:var(--error);">Failed to load missing tracks.</p>';
            }
        }

        const ARTIST_TRACKS_PAGE_SIZE = 50;

        async function toggleArtistTrackList(artistId, artistName) {
            const panel = document.getElementById(`artist-tracks-${artistId}`);
            if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
            panel.style.display = 'block';
            await loadArtistTrackPage(artistId, 0);
        }

        // Prolific artists run to hundreds of singles, so the track list is paged
        // ('cycles'). Prev/Next reload the panel at a new offset.
        async function loadArtistTrackPage(artistId, offset) {
            const panel = document.getElementById(`artist-tracks-${artistId}`);
            if (!panel) return;
            offset = Math.max(0, offset);
            panel.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await apiFetch(`/api/watched-artists/${artistId}/tracks?limit=${ARTIST_TRACKS_PAGE_SIZE}&offset=${offset}`);
                const data = await res.json();
                const total = data.total || 0;
                if (total === 0) {
                    panel.innerHTML = '<p style="font-size:12px;color:var(--text-secondary);padding:8px 0;">No tracks tracked yet.</p>';
                    return;
                }
                const tracksHtml = (data.tracks || []).map(t => {
                    const statusIcon = t.downloaded_at
                        ? '<i class="fa-solid fa-check" style="color:var(--success);"></i>'
                        : t.job_status === 'queued' || t.job_status === 'downloading'
                            ? '<i class="fa-solid fa-clock" style="color:var(--text-secondary);"></i>'
                            : '<i class="fa-solid fa-xmark" style="color:var(--error);"></i>';
                    const dlBtn = t.downloaded_at && t.job_id
                        ? `<button type="button"
                            data-action="save-job-to-device"
                            data-job-id="${escapeAttr(t.job_id)}"
                            style="padding:2px 8px;font-size:11px;font-family:inherit;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);border-radius:4px;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-download"></i></button>`
                        : '';
                    return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px;">
                        ${statusIcon}
                        <span style="flex:1;">${escapeHtml(t.artist || '')} &ndash; ${escapeHtml(t.title)}</span>
                        ${t.release_date ? `<span style="color:var(--text-secondary);font-size:11px;">${t.release_date}</span>` : ''}
                        ${dlBtn}
                    </div>`;
                }).join('');

                const start = offset + 1;
                const end = Math.min(offset + ARTIST_TRACKS_PAGE_SIZE, total);
                const hasPrev = offset > 0;
                const hasNext = end < total;
                const btnStyle = 'padding:3px 10px;font-size:11px;font-family:inherit;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;cursor:pointer;';
                const pager = total > ARTIST_TRACKS_PAGE_SIZE
                    ? `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;">
                            <button type="button" ${hasPrev ? '' : 'disabled'} onclick="loadArtistTrackPage('${artistId}', ${offset - ARTIST_TRACKS_PAGE_SIZE})" style="${btnStyle}${hasPrev ? '' : 'opacity:0.4;cursor:default;'}">&larr; Prev</button>
                            <span style="font-size:11px;color:var(--text-secondary);">${start}&ndash;${end} of ${total}</span>
                            <button type="button" ${hasNext ? '' : 'disabled'} onclick="loadArtistTrackPage('${artistId}', ${offset + ARTIST_TRACKS_PAGE_SIZE})" style="${btnStyle}${hasNext ? '' : 'opacity:0.4;cursor:default;'}">Next &rarr;</button>
                        </div>`
                    : '';

                panel.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">${total} single(s) tracked:</div>` + tracksHtml + pager;
            } catch (e) {
                panel.innerHTML = '<p style="font-size:12px;color:var(--error);">Failed to load tracks.</p>';
            }
        }

        async function retryArtistTrack(artistId, artist, title, btn) {
            btn.disabled = true;
            btn.textContent = 'Queued';
            try {
                await apiFetch(`/api/watched-artists/${artistId}/retry-track`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ artist, title })
                });
                showToast(`Queued: ${artist} - ${title}`);
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Retry';
                showToast('Retry failed', true);
            }
        }

        async function retryAllArtistMissing(artistId, btn) {
            btn.disabled = true;
            btn.textContent = 'Queuing...';
            try {
                const res = await apiFetch(`/api/watched-artists/${artistId}/retry-all-missing`, { method: 'POST' });
                const data = await res.json();
                showToast(`Queued ${data.queued} track(s)`);
                btn.textContent = `${data.queued} queued`;
                // Disable all individual retry buttons too
                const panel = document.getElementById(`artist-missing-${artistId}`);
                if (panel) panel.querySelectorAll('button[data-action="retry-artist-track"]').forEach(b => { b.disabled = true; b.textContent = 'Queued'; });
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Queue All';
                showToast('Failed to queue tracks', true);
            }
        }

        async function refreshAllArtists() {
            const btn = document.getElementById('refreshAllArtistsBtn');
            btn.disabled = true;
            try {
                const res = await apiFetch('/api/watched-artists/check-all', { method: 'POST' });
                const data = await res.json();
                if (data.checked === 0) {
                    showToast('No artists due for refresh');
                } else {
                    showToast(`Checked ${data.checked} artist(s), ${data.total_new_tracks} new single(s) found`);
                }
                loadWatchedArtists();
            } catch (e) {
                showToast('Check failed', true);
            } finally {
                btn.disabled = false;
            }
        }

        // Event listeners for watched artists
        document.getElementById('searchArtistBtn').addEventListener('click', searchArtist);
        document.getElementById('artistSearchInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') searchArtist();
        });
        document.getElementById('addArtistBtn').addEventListener('click', addWatchedArtist);
        document.getElementById('cancelArtistBtn').addEventListener('click', () => {
            document.getElementById('artistAddForm').style.display = 'none';
            document.getElementById('artistSearchResults').style.display = 'none';
            selectedArtistMbid = null;
            selectedArtistName = null;
        });
        document.getElementById('refreshAllArtistsBtn').addEventListener('click', refreshAllArtists);

        // =============================================================================
        // Statistics Dashboard
        // =============================================================================

        async function loadStats() {
            statsContent.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

            try {
                const response = await apiFetch('/api/stats');
                if (!response.ok) throw new Error('Failed to load stats');

                const data = await response.json();
                renderStats(data);
            } catch (error) {
                statsContent.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon"><i class="fa-solid fa-circle-exclamation"></i></div>
                        <p>Failed to load statistics</p>
                    </div>
                `;
            }

            loadMismatches();
        }

        async function loadMismatches() {
            const section = document.getElementById('mismatchSection');
            const content = document.getElementById('mismatchContent');
            if (!section || !content) return;

            try {
                const res = await apiFetch('/api/mismatches');
                if (!res.ok) { section.style.display = 'none'; return; }
                const data = await res.json();
                const rows = data.mismatches || [];
                section.style.display = rows.length > 0 ? 'block' : 'none';
                if (rows.length === 0) return;

                content.innerHTML = rows.map(m => `
                    <div class="mismatch-row">
                        <div class="mismatch-meta">
                            <span class="mismatch-playlist">${escapeHtml(m.playlist_name || m.playlist_id || '—')}</span>
                            <span class="mismatch-date">${m.created_at ? m.created_at.slice(0, 16).replace('T', ' ') : ''}</span>
                        </div>
                        <div class="mismatch-pair">
                            <div class="mismatch-line mismatch-expected">
                                <span class="mismatch-tag">Expected</span>
                                <span>${escapeHtml(m.expected_artist)} &ndash; ${escapeHtml(m.expected_title)}</span>
                            </div>
                            <div class="mismatch-line mismatch-got">
                                <span class="mismatch-tag">Got</span>
                                <span>${escapeHtml(m.actual_artist || 'Unknown')} &ndash; ${escapeHtml(m.actual_title || 'Unknown')}</span>
                            </div>
                        </div>
                        <details class="mismatch-normalised">
                            <summary>Normalised</summary>
                            <div class="mismatch-norm-line"><span class="mismatch-tag">Exp</span> ${escapeHtml(m.exp_normalised)}</div>
                            <div class="mismatch-norm-line"><span class="mismatch-tag">Got</span> ${escapeHtml(m.got_normalised)}</div>
                        </details>
                        <button class="mismatch-accept-btn" data-mismatch-id="${m.id}" title="Re-download this track, ignoring the name mismatch">Force Download</button>
                    </div>
                `).join('');

                // Wire up force-accept buttons via delegation
                content.querySelectorAll('.mismatch-accept-btn').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const mid = btn.dataset.mismatchId;
                        btn.disabled = true;
                        btn.textContent = 'Queued...';
                        try {
                            const res = await apiFetch(`/api/mismatches/${mid}/accept`, { method: 'POST' });
                            if (!res.ok) {
                                const err = await res.json().catch(() => ({}));
                                throw new Error(err.detail || 'Failed');
                            }
                            showToast('Re-queued with mismatch check disabled');
                            btn.closest('.mismatch-row').remove();
                            // Hide the section if no rows remain
                            if (!content.querySelector('.mismatch-row')) {
                                section.style.display = 'none';
                            }
                        } catch (e) {
                            showToast(`Force download failed: ${e.message}`, true);
                            btn.disabled = false;
                            btn.textContent = 'Force Download';
                        }
                    });
                });
            } catch {
                section.style.display = 'none';
            }
        }

        const clearMismatchesBtn = document.getElementById('clearMismatchesBtn');
        if (clearMismatchesBtn) {
            clearMismatchesBtn.addEventListener('click', async () => {
                if (!confirm('Clear the entire mismatch log?')) return;
                await apiFetch('/api/mismatches', { method: 'DELETE' });
                loadMismatches();
            });
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(1024));
            return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
        }

        function renderStats(data) {
            const successRate = data.total_jobs > 0
                ? Math.round((data.completed / data.total_jobs) * 100)
                : 0;
            const searchSuccessRate = data.total_searches > 0
                ? Math.round((data.successful_searches / data.total_searches) * 100)
                : 0;
            const searchToDownloadRate = data.total_searches > 0
                ? Math.round((data.converted_searches / data.total_searches) * 100)
                : 0;

            // Build daily chart (simple bar chart using divs)
            const maxDaily = Math.max(...data.daily.map(d => d.count), 1);
            const dailyBars = data.daily.length > 0
                ? data.daily.slice(-14).map(d => {
                    const height = Math.max(4, Math.round((d.count / maxDaily) * 80));
                    const dayLabel = d.day;
                    return `
                        <div style="display: flex; flex-direction: column; align-items: center; gap: 4px; flex: 1; min-width: 0;">
                            <div style="font-size: 10px; color: var(--text-secondary);">${d.count}</div>
                            <div style="width: 100%; max-width: 24px; height: ${height}px; background: var(--accent); border-radius: 3px;"></div>
                            <div style="font-size: 9px; color: var(--text-secondary); white-space: nowrap;">${dayLabel}</div>
                        </div>
                    `;
                }).join('')
                : '<div style="text-align: center; color: var(--text-secondary); font-size: 13px; padding: 20px;">No downloads yet</div>';

            // Source breakdown
            const ytCount = data.sources.youtube || 0;
            const pxCount = data.sources.mp3phoenix || 0;
            const scCount = data.sources.soundcloud || 0;
            const zvCount = data.sources.zvu4no || 0;
            const fmcCount = data.sources.freemp3cloud || 0;
            const slkCount = data.sources.soulseek || 0;
            const monoCount = data.sources.monochrome || 0;
            const sourceTotal = ytCount + pxCount + scCount + zvCount + fmcCount + slkCount + monoCount || 1;

            let html = `
                <!-- Summary cards -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 16px;">
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="Total downloads that completed successfully">
                        <div style="font-size: 22px; font-weight: 600;">${data.completed}</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">Completed</div>
                    </div>
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="Downloads that failed  -  check the Queue tab for error details">
                        <div style="font-size: 22px; font-weight: 600; color: var(--error);">${data.failed}</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">Failed</div>
                    </div>
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="Percentage of all download attempts that completed successfully">
                        <div style="font-size: 22px; font-weight: 600;">${successRate}%</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">Success rate</div>
                    </div>
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="Number of audio files in your library and total disk space used">
                        <div style="font-size: 22px; font-weight: 600;">${data.file_count}</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">${formatBytes(data.storage_bytes)}</div>
                    </div>
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="${searchSuccessRate}% of searches returned at least one result">
                        <div style="font-size: 22px; font-weight: 600;">${data.total_searches || 0}</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">Searches</div>
                    </div>
                    <div style="padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; text-align: center;" title="Percentage of searches that ended with you actually downloading something">
                        <div style="font-size: 22px; font-weight: 600;">${searchToDownloadRate}%</div>
                        <div style="font-size: 11px; color: var(--text-secondary);">Search → Download</div>
                    </div>
                </div>

                <!-- Search quality -->
                <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 16px;">
                    <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px;">Search Performance</div>
                    <div style="font-size: 13px; color: var(--text-secondary);">
                        Successful searches: <strong style="color: var(--text-primary);">${data.successful_searches || 0}</strong> /
                        ${data.total_searches || 0}
                        (${searchSuccessRate}% found at least one result)
                    </div>
                </div>

                <!-- Daily downloads chart -->
                <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 16px;">
                    <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 12px;">Downloads (last 14 days)</div>
                    <div style="display: flex; align-items: flex-end; gap: 4px; min-height: 100px;">
                        ${dailyBars}
                    </div>
                </div>

                <!-- Source breakdown -->
                <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 16px;">
                    <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 12px;">Sources</div>
                    <div style="display: flex; gap: 8px; height: 8px; border-radius: 4px; overflow: hidden; margin-bottom: 8px;">
                        ${ytCount > 0 ? `<div style="flex: ${ytCount}; background: #ff0000; border-radius: 4px;"></div>` : ''}
                        ${pxCount > 0 ? `<div style="flex: ${pxCount}; background: #e05c00; border-radius: 4px;"></div>` : ''}
                        ${scCount > 0 ? `<div style="flex: ${scCount}; background: #ff5500; border-radius: 4px;"></div>` : ''}
                        ${zvCount > 0 ? `<div style="flex: ${zvCount}; background: #7a6aee; border-radius: 4px;"></div>` : ''}
                        ${fmcCount > 0 ? `<div style="flex: ${fmcCount}; background: #3a2fd6; border-radius: 4px;"></div>` : ''}
                        ${monoCount > 0 ? `<div style="flex: ${monoCount}; background: #0f766e; border-radius: 4px;"></div>` : ''}
                        ${slkCount > 0 ? `<div style="flex: ${slkCount}; background: #4a9eff; border-radius: 4px;"></div>` : ''}
                    </div>
                    <div style="display: flex; gap: 16px; font-size: 12px; flex-wrap: wrap;">
                        <span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #ff0000; border-radius: 2px; margin-right: 4px;"></span>YouTube: ${ytCount}</span>
                        ${pxCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #e05c00; border-radius: 2px; margin-right: 4px;"></span>MP3Phoenix: ${pxCount}</span>` : ''}
                        ${scCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #ff5500; border-radius: 2px; margin-right: 4px;"></span>SoundCloud: ${scCount}</span>` : ''}
                        ${zvCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #7a6aee; border-radius: 2px; margin-right: 4px;"></span>zvu4no: ${zvCount}</span>` : ''}
                        ${fmcCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #3a2fd6; border-radius: 2px; margin-right: 4px;"></span>FreeMp3Cloud: ${fmcCount}</span>` : ''}
                        ${monoCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #0f766e; border-radius: 2px; margin-right: 4px;"></span>Monochrome: ${monoCount}</span>` : ''}
                        <span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #4a9eff; border-radius: 2px; margin-right: 4px;"></span>Soulseek: ${slkCount}</span>
                    </div>
                </div>
            `;

            // Top artists
            if (data.top_artists.length > 0) {
                const maxArtistCount = data.top_artists[0].count;
                html += `
                    <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 16px;">
                        <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 12px;">Top Artists</div>
                        ${data.top_artists.map((a, i) => `
                            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                                <span style="font-size: 11px; color: var(--text-secondary); width: 16px; text-align: right;">${i + 1}</span>
                                <div style="flex: 1; min-width: 0;">
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span style="font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(a.artist)}</span>
                                        <span style="font-size: 11px; color: var(--text-secondary); flex-shrink: 0;">${a.count}</span>
                                    </div>
                                    <div style="height: 4px; background: var(--bg-tertiary); border-radius: 2px; margin-top: 4px;">
                                        <div style="height: 100%; width: ${Math.round((a.count / maxArtistCount) * 100)}%; background: var(--accent); border-radius: 2px;"></div>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            // Top searched artists
            if (data.top_searched_artists && data.top_searched_artists.length > 0) {
                const maxSearchCount = data.top_searched_artists[0].count;
                html += `
                    <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 16px;">
                        <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 12px;">Most Searched Artists</div>
                        ${data.top_searched_artists.map((a, i) => `
                            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                                <span style="font-size: 11px; color: var(--text-secondary); width: 16px; text-align: right;">${i + 1}</span>
                                <div style="flex: 1; min-width: 0;">
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span style="font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(a.artist)}</span>
                                        <span style="font-size: 11px; color: var(--text-secondary); flex-shrink: 0;">${a.count}</span>
                                    </div>
                                    <div style="height: 4px; background: var(--bg-tertiary); border-radius: 2px; margin-top: 4px;">
                                        <div style="height: 100%; width: ${Math.round((a.count / maxSearchCount) * 100)}%; background: #f59e0b; border-radius: 2px;"></div>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            // Recent downloads
            if (data.recent.length > 0) {
                html += `
                    <div style="padding: 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px;">
                        <div style="font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 12px;">Recent Downloads</div>
                        ${data.recent.map(r => `
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px;">
                                <span class="source-badge ${escapeHtml(r.source || 'youtube')}" style="flex-shrink: 0;">${getSourceBadge(r.source || 'youtube')}</span>
                                <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(r.artist ? `${r.artist} - ${r.title}` : r.title)}</span>
                                <span style="margin-left: auto; color: var(--text-secondary); white-space: nowrap; flex-shrink: 0;">${r.completed_at ? formatTime(r.completed_at) : ''}</span>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            statsContent.innerHTML = html;
        }

        // =============================================================================
        // Settings Management
        // =============================================================================

        // Mapping from setting keys to form element IDs
        const settingsFieldMap = {
            'skip_dupes': 'settingSkipDupes',
            'enable_musicbrainz': 'settingEnableMusicbrainz',
            'enable_lyrics': 'settingEnableLyrics',
            'acoustid_api_key': 'settingAcoustidKey',
            'default_convert_to_flac': 'settingDefaultFlac',
            'audio_format': 'settingAudioFormat',
            'mp3_bitrate': 'settingMp3Bitrate',
            'opus_bitrate': 'settingOpusBitrate',
            'alac_bitrate': 'settingAlacBitrate',
            'min_audio_bitrate': 'settingMinBitrate',
            'enable_track_upgrades': 'settingEnableTrackUpgrades',
            'upgrade_scan_interval_hours': 'settingUpgradeScanInterval',
            'singles_subdir': 'settingSinglesSubdir',
            'playlists_subdir': 'settingPlaylistsSubdir',
            'albums_subdir': 'settingAlbumsSubdir',
            'organise_by_artist': 'settingOrganiseByArtist',
            'include_track_number_in_filename': 'settingIncludeTrackNumberInFilename',
            'auto_album_singles': 'settingAutoAlbumSingles',
            'auto_album_singles_use_albums_dir': 'settingAutoAlbumSinglesUseAlbumsDir',
            'singles_only_mode': 'settingSinglesOnlyMode',
            'source_youtube_enabled': 'settingSourceYoutube',
            'source_mp3phoenix_enabled': 'settingSourceMp3phoenix',
            'source_soundcloud_enabled': 'settingSourceSoundcloud',
            'source_zvu4no_enabled': 'settingSourceZvu4no',
            'source_freemp3cloud_enabled': 'settingSourceFreemp3cloud',
            'source_soulseek_enabled': 'settingSourceSoulseek',
            'source_monochrome_enabled': 'settingSourceMonochrome',
            'source_offline_fallback': 'settingSourceOfflineFallback',
            'source_health_checks_enabled': 'settingSourceHealthChecks',
            'source_health_check_interval_minutes': 'settingSourceHealthInterval',
            'source_health_cooldown_minutes': 'settingSourceHealthCooldown',
            'monochrome_hifi_api_url': 'settingMonochromeHifiUrl',
            'monochrome_qobuz_proxy_url': 'settingMonochromeQobuzUrl',
            'monochrome_qbdlx_fallback_enabled': 'settingMonochromeQbdlxFallback',
            'slskd_url': 'settingSlskdUrl',
            'slskd_user': 'settingSlskdUser',
            'slskd_pass': 'settingSlskdPass',
            'slskd_downloads_path': 'settingSlskdDownloads',
            'navidrome_url': 'settingNavidromeUrl',
            'navidrome_user': 'settingNavidromeUser',
            'navidrome_pass': 'settingNavidromePass',
            'jellyfin_url': 'settingJellyfinUrl',
            'jellyfin_api_key': 'settingJellyfinApiKey',
            'lidarr_url': 'settingLidarrUrl',
            'lidarr_api_key': 'settingLidarrApiKey',
            'notify_on': 'settingNotifyOn',
            'telegram_webhook_url': 'settingTelegramUrl',
            'webhook_url': 'settingWebhookUrl',
            'apprise_url': 'settingAppriseUrl',
            'smtp_host': 'settingSmtpHost',
            'smtp_port': 'settingSmtpPort',
            'smtp_user': 'settingSmtpUser',
            'smtp_pass': 'settingSmtpPass',
            'smtp_from': 'settingSmtpFrom',
            'smtp_to': 'settingSmtpTo',
            'smtp_tls': 'settingSmtpTls',
            'youtube_cookies': 'settingYoutubeCookies',
            'spotify_cookies': 'settingSpotifyCookies',
            'apple_music_user_token': 'settingAppleMusicUserToken',
            'spotify_browser_timeout_seconds': 'settingSpotifyBrowserTimeout',
            'spotify_browser_stall_seconds': 'settingSpotifyBrowserStall',
            'file_permissions': 'settingFilePermissions',
            'max_concurrent_downloads': 'settingMaxConcurrentDownloads',
            'api_key': 'settingApiKey'
        };

        // Track which fields are locked by env vars
        let envOverrides = [];
        let sensitiveFields = [];
        let settingsLoaded = false;
        let originalValues = {}; // Track original values to detect changes

        async function loadSettings() {
            if (settingsLoaded) return; // Only load once per session

            try {
                const response = await apiFetch('/api/settings');
                if (!response.ok) throw new Error('Failed to load settings');

                const data = await response.json();
                const settings = data.settings;
                envOverrides = data.env_overrides || [];
                sensitiveFields = data.sensitive_fields || [];

                // Populate form fields and track original values
                for (const [key, elementId] of Object.entries(settingsFieldMap)) {
                    const element = document.getElementById(elementId);
                    if (!element) continue;

                    const value = settings[key];
                    const isLocked = envOverrides.includes(key);

                    // Store original value for change detection
                    if (!element.dataset.defaultPlaceholder) {
                        element.dataset.defaultPlaceholder = element.placeholder || '';
                    }

                    if (element.type === 'checkbox') {
                        originalValues[key] = value === true || value === 'true';
                    } else if (sensitiveFields.includes(key) && value === '••••••••') {
                        originalValues[key] = null; // Indicates "configured but hidden"
                    } else {
                        originalValues[key] = value || '';
                    }

                    if (element.type === 'checkbox') {
                        element.checked = value === true || value === 'true';
                    } else {
                        // For sensitive fields, only show placeholder if value is masked
                        if (sensitiveFields.includes(key) && value === '••••••••') {
                            element.placeholder = 'Configured (hidden)';
                            element.value = '';
                        } else {
                            element.value = value || '';
                        }
                        // Sync format and quality picker buttons when their settings load
                        if (key === 'audio_format') setAudioFormat(value);
                        if (key === 'mp3_bitrate')  setMp3Bitrate(value);
                        if (key === 'opus_bitrate') setOpusBitrate(value);
                        if (key === 'alac_bitrate') setAlacBitrate(value);
                    }

                    // Mark fields locked by env vars
                    if (isLocked) {
                        element.disabled = true;
                        const row = element.closest('.settings-row') || element.closest('.settings-label');
                        if (row) {
                            row.classList.add('env-locked');
                            row.title = 'Set via environment variable (e.g., docker-compose.yml)';
                        }
                    }
                }

                settingsLoaded = true;

                // Sync notify_on checkboxes from the hidden field value
                _syncNotifyOnCheckboxes();

                // Populate singles subfolder dropdown from actual directories
                await _populateSubdirDropdown(settings['singles_subdir'] || 'Singles');
                // Populate playlists subfolder dropdown
                await _populatePlaylistsSubdirDropdown(settings['playlists_subdir'] || '');
                // Populate albums subfolder dropdown
                await _populateAlbumsSubdirDropdown(settings['albums_subdir'] || 'Albums');

                // Live path preview: update whenever filename layout toggles change
                const organiseToggle = document.getElementById('settingOrganiseByArtist');
                if (organiseToggle) organiseToggle.onchange = _updatePathPreviews;
                const trackNumberToggle = document.getElementById('settingIncludeTrackNumberInFilename');
                if (trackNumberToggle) trackNumberToggle.onchange = _updatePathPreviews;

                // Grey out "Route to Albums folder" when auto-album routing is off
                function _updateAlbumsDirToggle() {
                    const routeRow = document.getElementById('autoAlbumSinglesUseAlbumsDirRow');
                    const routeInput = document.getElementById('settingAutoAlbumSinglesUseAlbumsDir');
                    const autoAlbumOn = document.getElementById('settingAutoAlbumSingles')?.checked;
                    if (routeRow) routeRow.style.opacity = autoAlbumOn ? '' : '0.4';
                    if (routeInput) routeInput.disabled = !autoAlbumOn;
                }
                const autoAlbumToggle = document.getElementById('settingAutoAlbumSingles');
                if (autoAlbumToggle) {
                    autoAlbumToggle.addEventListener('change', _updateAlbumsDirToggle);
                    _updateAlbumsDirToggle();
                }

                // Update browser API key status
                updateBrowserApiKeyStatus();

                // Inject clear buttons into settings rows
                _injectSettingsClearButtons();

                // Show cookie expiry status if cookies are configured
                _updateCookieExpiryHint();
                _updateSpotifyCookieHint(settings);
            } catch (error) {
                console.error('Failed to load settings:', error);
                showToast('Failed to load settings', true);
            }
        }

        const SUBDIR_CUSTOM_VALUE = '__custom__';

        function _normaliseSubdirPath(rawValue) {
            const raw = String(rawValue || '').trim();
            if (!raw) return '';
            if (raw === '.') return '.';

            const parts = raw
                .replace(/\\/g, '/')
                .split('/')
                .map(part => part.trim())
                .filter(part => part && part !== '.');

            if (parts.some(part => part === '..')) return '';
            return parts.join('/');
        }

        async function _populateSubdirDropdown(currentValue) {
            const select = document.getElementById('settingSinglesSubdir');
            const customRow = document.getElementById('customSubdirRow');
            const customInput = document.getElementById('customSubdirInput');
            if (!select || !customRow || !customInput) return;

            const currentRaw = (currentValue || 'Singles').trim();
            const current = currentRaw === '.' ? '.' : (_normaliseSubdirPath(currentRaw) || 'Singles');

            let dirs = [];
            try {
                const resp = await apiFetch('/api/music-dirs?recursive=true&max_depth=2');
                if (resp.ok) {
                    const data = await resp.json();
                    dirs = Array.isArray(data.directories) ? data.directories : [];
                }
            } catch (e) {
                console.warn('Could not fetch music directories:', e);
            }

            const unique = new Set();
            for (const dir of dirs) {
                const normalised = _normaliseSubdirPath(dir);
                if (normalised && normalised !== '.') {
                    unique.add(normalised);
                }
            }

            // Keep defaults/current value available even if folder disappeared.
            unique.add('Singles');
            if (current !== '.') {
                unique.add(current);
            }

            const sortedDirs = Array.from(unique).sort((a, b) =>
                a.localeCompare(b, undefined, { sensitivity: 'base' })
            );

            select.innerHTML = '';

            const musicRoot = (serverConfig && serverConfig.music_dir) ? serverConfig.music_dir.replace(/\/+$/, '') : '/music';

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = `${musicRoot} (root)`;
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `${musicRoot}/${relPath}`;
                select.appendChild(opt);
            }

            const customOpt = document.createElement('option');
            customOpt.value = SUBDIR_CUSTOM_VALUE;
            customOpt.textContent = 'Custom path\u2026';
            select.appendChild(customOpt);

            const currentIsKnown = current === '.' || sortedDirs.includes(current);
            customInput.disabled = !!select.disabled;
            if (currentIsKnown) {
                select.value = current;
                customRow.style.display = 'none';
                customInput.value = '';
            } else {
                select.value = SUBDIR_CUSTOM_VALUE;
                customRow.style.display = 'block';
                customInput.value = current;
            }

            select.onchange = () => {
                if (select.value === SUBDIR_CUSTOM_VALUE) {
                    customRow.style.display = 'block';
                    customInput.focus();
                    _updatePathPreviews();
                    return;
                }
                customRow.style.display = 'none';
                customInput.value = '';
                _updatePathPreviews();
            };
            customInput.oninput = _updatePathPreviews;
            _updatePathPreviews();
        }

        async function _populatePlaylistsSubdirDropdown(currentValue) {
            const select = document.getElementById('settingPlaylistsSubdir');
            const customRow = document.getElementById('customPlaylistsSubdirRow');
            const customInput = document.getElementById('customPlaylistsSubdirInput');
            if (!select || !customRow || !customInput) return;

            const currentRaw = String(currentValue || '').trim();
            const current = currentRaw === '.' ? '.' : (_normaliseSubdirPath(currentRaw) || '');

            let dirs = [];
            try {
                const resp = await apiFetch('/api/music-dirs?recursive=true&max_depth=2');
                if (resp.ok) {
                    const data = await resp.json();
                    dirs = Array.isArray(data.directories) ? data.directories : [];
                }
            } catch (e) {
                console.warn('Could not fetch music directories:', e);
            }

            const unique = new Set();
            for (const dir of dirs) {
                const normalised = _normaliseSubdirPath(dir);
                if (normalised && normalised !== '.') {
                    unique.add(normalised);
                }
            }

            // Always include "Playlists" as a sensible default option
            unique.add('Playlists');
            if (current && current !== '.') {
                unique.add(current);
            }

            const sortedDirs = Array.from(unique).sort((a, b) =>
                a.localeCompare(b, undefined, { sensitivity: 'base' })
            );

            select.innerHTML = '';

            // First option: disabled (feature off)
            const disabledOpt = document.createElement('option');
            disabledOpt.value = '';
            disabledOpt.textContent = '(disabled)';
            select.appendChild(disabledOpt);

            const musicRoot = (serverConfig && serverConfig.music_dir) ? serverConfig.music_dir.replace(/\/+$/, '') : '/music';

            // Second option: musicRoot/Playlists as the obvious default
            const playlistsOpt = document.createElement('option');
            playlistsOpt.value = 'Playlists';
            playlistsOpt.textContent = `${musicRoot}/Playlists`;
            select.appendChild(playlistsOpt);

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = `${musicRoot} (root)`;
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs.filter(d => d !== 'Playlists')) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `${musicRoot}/${relPath}`;
                select.appendChild(opt);
            }

            const customOpt = document.createElement('option');
            customOpt.value = SUBDIR_CUSTOM_VALUE;
            customOpt.textContent = 'Custom path\u2026';
            select.appendChild(customOpt);

            const currentIsKnown = !current || current === '.' || sortedDirs.includes(current);
            customInput.disabled = !!select.disabled;
            if (currentIsKnown) {
                select.value = current;
                customRow.style.display = 'none';
                customInput.value = '';
            } else {
                select.value = SUBDIR_CUSTOM_VALUE;
                customRow.style.display = 'block';
                customInput.value = current;
            }

            select.onchange = () => {
                if (select.value === SUBDIR_CUSTOM_VALUE) {
                    customRow.style.display = 'block';
                    customInput.focus();
                    _updatePathPreviews();
                    return;
                }
                customRow.style.display = 'none';
                customInput.value = '';
                _updatePathPreviews();
            };
            customInput.oninput = _updatePathPreviews;
            _updatePathPreviews();
        }

        async function _populateAlbumsSubdirDropdown(currentValue) {
            const select = document.getElementById('settingAlbumsSubdir');
            const customRow = document.getElementById('customAlbumsSubdirRow');
            const customInput = document.getElementById('customAlbumsSubdirInput');
            if (!select || !customRow || !customInput) return;

            const currentRaw = String(currentValue || 'Albums').trim();
            const current = currentRaw === '.' ? '.' : (_normaliseSubdirPath(currentRaw) || 'Albums');

            let dirs = [];
            try {
                const resp = await apiFetch('/api/music-dirs?recursive=true&max_depth=2');
                if (resp.ok) {
                    const data = await resp.json();
                    dirs = Array.isArray(data.directories) ? data.directories : [];
                }
            } catch (e) {
                console.warn('Could not fetch music directories:', e);
            }

            const unique = new Set();
            for (const dir of dirs) {
                const normalised = _normaliseSubdirPath(dir);
                if (normalised && normalised !== '.') unique.add(normalised);
            }
            unique.add('Albums');
            if (current && current !== '.') unique.add(current);

            const sortedDirs = Array.from(unique).sort((a, b) =>
                a.localeCompare(b, undefined, { sensitivity: 'base' })
            );

            select.innerHTML = '';

            const musicRoot = (serverConfig && serverConfig.music_dir) ? serverConfig.music_dir.replace(/\/+$/, '') : '/music';

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = `${musicRoot} (root)`;
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `${musicRoot}/${relPath}`;
                select.appendChild(opt);
            }

            const customOpt = document.createElement('option');
            customOpt.value = SUBDIR_CUSTOM_VALUE;
            customOpt.textContent = 'Custom path\u2026';
            select.appendChild(customOpt);

            const currentIsKnown = current === '.' || sortedDirs.includes(current);
            customInput.disabled = !!select.disabled;
            if (currentIsKnown) {
                select.value = current;
                customRow.style.display = 'none';
                customInput.value = '';
            } else {
                select.value = SUBDIR_CUSTOM_VALUE;
                customRow.style.display = 'block';
                customInput.value = current;
            }

            select.onchange = () => {
                if (select.value === SUBDIR_CUSTOM_VALUE) {
                    customRow.style.display = 'block';
                    customInput.focus();
                    _updatePathPreviews();
                    return;
                }
                customRow.style.display = 'none';
                customInput.value = '';
                _updatePathPreviews();
            };
            customInput.oninput = _updatePathPreviews;
            _updatePathPreviews();
        }

        async function _updateCookieExpiryHint() {
            const hint = document.getElementById('cookieExpiryHint');
            if (!hint) return;
            try {
                const resp = await apiFetch('/api/settings/youtube-cookies/status');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data.has_setting || !data.auth_cookie_expiry) {
                    hint.style.display = 'none';
                    return;
                }
                const expiryMs = data.auth_cookie_expiry * 1000;
                const now = Date.now();
                const daysLeft = Math.floor((expiryMs - now) / 86400000);
                const expiryDate = new Date(expiryMs).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

                hint.style.display = 'block';
                if (now > expiryMs) {
                    hint.style.color = 'var(--error-color, #e53e3e)';
                    hint.textContent = `Cookies expired on ${expiryDate} - re-export them`;
                } else if (daysLeft <= 7) {
                    hint.style.color = 'var(--warning-color, #d97706)';
                    hint.textContent = `Cookies expire in ${daysLeft} day${daysLeft !== 1 ? 's' : ''} (${expiryDate}) - consider re-exporting soon`;
                } else {
                    hint.style.color = 'var(--text-secondary)';
                    hint.textContent = `Cookies valid until ${expiryDate}`;
                }
            } catch (e) {
                // Status fetch failing is not the end of the world
            }
        }

        function _updateSpotifyCookieHint(settings) {
            const banner = document.getElementById('spotifyCookiesExpiredBanner');
            const hint = document.getElementById('spotifyCookieExpiryHint');

            // Show expired banner when the flag is set and cookies are still present
            if (banner) {
                const expired = settings && settings['spotify_cookies_expired'] === true;
                const hasCookies = settings && settings['spotify_cookies'];
                banner.style.display = (expired && hasCookies) ? 'block' : 'none';
            }

            // Expiry hint: parse sp_dc expiry from the Netscape cookie text
            if (!hint) return;
            const cookiesText = settings && settings['spotify_cookies'];
            if (!cookiesText) {
                hint.style.display = 'none';
                return;
            }
            // Find sp_dc expiry from cookie lines
            let spDcExpiry = null;
            for (const line of cookiesText.split('\n')) {
                const l = line.trim();
                if (!l || l.startsWith('#')) continue;
                const parts = l.split('\t');
                if (parts.length >= 7 && parts[5] === 'sp_dc') {
                    const exp = parseInt(parts[4], 10);
                    if (exp > 0) spDcExpiry = exp;
                    break;
                }
            }
            if (!spDcExpiry) {
                hint.style.display = 'none';
                return;
            }
            const now = Date.now();
            const expiryMs = spDcExpiry * 1000;
            const daysLeft = Math.floor((expiryMs - now) / 86400000);
            const expiryDate = new Date(expiryMs).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
            hint.style.display = 'block';
            if (now > expiryMs) {
                hint.style.color = 'var(--error-color, #e53e3e)';
                hint.textContent = `sp_dc expired on ${expiryDate} - re-export cookies`;
            } else if (daysLeft <= 30) {
                hint.style.color = 'var(--warning-color, #d97706)';
                hint.textContent = `sp_dc expires in ${daysLeft} day${daysLeft !== 1 ? 's' : ''} (${expiryDate})`;
            } else {
                hint.style.color = 'var(--text-secondary)';
                hint.textContent = `sp_dc valid until ${expiryDate}`;
            }
        }

        function _updatePathPreviews() {
            const singlesEl = document.getElementById('singlesPathPreview');
            const playlistsEl = document.getElementById('playlistsPathPreview');
            const albumsEl = document.getElementById('albumsPathPreview');
            if (!singlesEl && !playlistsEl && !albumsEl) return;

            const singlesSelect = document.getElementById('settingSinglesSubdir');
            const singlesCustom = document.getElementById('customSubdirInput');
            const playlistsSelect = document.getElementById('settingPlaylistsSubdir');
            const playlistsCustom = document.getElementById('customPlaylistsSubdirInput');
            const albumsSelect = document.getElementById('settingAlbumsSubdir');
            const albumsCustom = document.getElementById('customAlbumsSubdirInput');
            const organiseToggle = document.getElementById('settingOrganiseByArtist');
            const trackNumberToggle = document.getElementById('settingIncludeTrackNumberInFilename');

            const singlesVal = singlesSelect && singlesSelect.value === SUBDIR_CUSTOM_VALUE
                ? (singlesCustom ? singlesCustom.value.trim() : '')
                : (singlesSelect ? singlesSelect.value : '');
            const playlistsVal = playlistsSelect && playlistsSelect.value === SUBDIR_CUSTOM_VALUE
                ? (playlistsCustom ? playlistsCustom.value.trim() : '')
                : (playlistsSelect ? playlistsSelect.value : '');
            const organise = organiseToggle ? organiseToggle.checked : true;
            const includeTrackNumber = trackNumberToggle ? trackNumberToggle.checked : false;
            const titleExample = includeTrackNumber ? '1 - Track Title.flac' : 'Track Title.flac';
            const flatTitleExample = includeTrackNumber ? 'Artist Name - 1 - Track Title.flac' : 'Artist Name - Track Title.flac';

            const musicRoot = (serverConfig && serverConfig.music_dir) ? serverConfig.music_dir.replace(/\/+$/, '') : '/music';

            if (singlesEl) {
                let p = musicRoot;
                if (singlesVal && singlesVal !== '.') p += '/' + singlesVal;
                if (organise) {
                    p += '/Artist Name/' + titleExample;
                } else {
                    p += '/' + flatTitleExample;
                }
                singlesEl.textContent = 'Files saved to: ' + p;
            }

            if (playlistsEl) {
                if (!playlistsVal || playlistsVal === '') {
                    playlistsEl.textContent = '(playlist tracks go to Singles folder)';
                } else {
                    let p = musicRoot;
                    if (playlistsVal !== '.') p += '/' + playlistsVal;
                    p += '/Playlist Name/' + (includeTrackNumber ? 'Artist - 1 - Title.flac' : 'Artist - Title.flac');
                    playlistsEl.textContent = 'Files saved to: ' + p;
                }
            }

            const albumsVal = albumsSelect && albumsSelect.value === SUBDIR_CUSTOM_VALUE
                ? (albumsCustom ? albumsCustom.value.trim() : '')
                : (albumsSelect ? albumsSelect.value : '');
            if (albumsEl) {
                let p = musicRoot;
                if (albumsVal && albumsVal !== '.') p += '/' + albumsVal;
                p += '/Artist/Album/' + (includeTrackNumber ? '1 - Track.flac' : 'Track.flac');
                albumsEl.textContent = 'Files saved to: ' + p;
            }
        }

        function _injectSettingsClearButtons() {
            // Skip these - they already have dedicated clear mechanisms
            const skipIds = new Set([
                'settingYoutubeCookies', 'settingSpotifyCookies', 'settingSmtpPort', 'settingMinBitrate',
                'settingSpotifyBrowserTimeout', 'settingSpotifyBrowserStall',
                'customSubdirInput', 'customPlaylistsSubdirInput', 'customAlbumsSubdirInput'
            ]);

            for (const row of document.querySelectorAll('.settings-row')) {
                const input = row.querySelector('input[type="text"], input[type="password"], input[type="number"]');
                if (!input || skipIds.has(input.id)) continue;
                if (input.disabled) continue; // env-locked

                const clearBtn = document.createElement('button');
                clearBtn.type = 'button';
                clearBtn.className = 'btn btn-sm btn-danger setting-clear-btn';
                clearBtn.textContent = 'Clear';

                // Find the settings key for this input
                const settingsKey = Object.entries(settingsFieldMap).find(([, id]) => id === input.id)?.[0];

                clearBtn.addEventListener('click', async () => {
                    input.value = '';
                    if (settingsKey && sensitiveFields.includes(settingsKey)) {
                        input.dataset.forceClear = 'true';
                        input.placeholder = input.defaultValue || '';
                    }
                    await saveSettings();
                });

                // Wrap input (or password-field div) and clear button together
                const passwordField = row.querySelector('.password-field');
                const target = passwordField || input;

                const wrapper = document.createElement('div');
                wrapper.className = 'setting-input-group';
                target.parentNode.insertBefore(wrapper, target);
                wrapper.appendChild(target);
                wrapper.appendChild(clearBtn);
            }
        }

        function updateBrowserApiKeyStatus() {
            // In session mode the browser API key row isn't really relevant, but we keep
            // the element around for backward compatibility. Show a polite "N/A" message.
            const statusEl = document.getElementById('browserApiKeyStatus');
            const clearBtn = document.getElementById('clearApiKeyBtn');
            if (!statusEl) return;

            if (serverConfig && serverConfig.users_exist) {
                const user = getCurrentUser();
                statusEl.textContent = user ? `Signed in as ${user.username}` : 'Session mode';
                statusEl.style.color = 'var(--text-secondary)';
                if (clearBtn) clearBtn.style.display = 'none';
            } else {
                // Legacy single-user API key mode
                const storedKey = localStorage.getItem('apiKey') || '';
                if (storedKey) {
                    statusEl.textContent = `Key stored (${storedKey.length} chars)`;
                    statusEl.style.color = 'var(--success)';
                    if (clearBtn) clearBtn.style.display = 'inline-block';
                } else {
                    statusEl.textContent = 'No key stored';
                    statusEl.style.color = 'var(--text-secondary)';
                    if (clearBtn) clearBtn.style.display = 'none';
                }
            }
        }

        // Clear stored (legacy) API key button
        document.getElementById('clearApiKeyBtn')?.addEventListener('click', () => {
            localStorage.removeItem('apiKey');
            updateBrowserApiKeyStatus();
            showToast('Stored API key cleared');
        });

        function _syncNotifyOnCheckboxes() {
            const val = document.getElementById('settingNotifyOn')?.value || '';
            const parts = val.split(',').map(s => s.trim());
            document.getElementById('settingNotifyOnSingles').checked  = parts.includes('singles');
            document.getElementById('settingNotifyOnPlaylists').checked = parts.includes('playlists');
            document.getElementById('settingNotifyOnBulk').checked     = parts.includes('bulk');
            document.getElementById('settingNotifyOnErrors').checked   = parts.includes('errors');
        }

        function _syncNotifyOnHidden() {
            const parts = [];
            if (document.getElementById('settingNotifyOnSingles').checked)  parts.push('singles');
            if (document.getElementById('settingNotifyOnPlaylists').checked) parts.push('playlists');
            if (document.getElementById('settingNotifyOnBulk').checked)     parts.push('bulk');
            if (document.getElementById('settingNotifyOnErrors').checked)   parts.push('errors');
            document.getElementById('settingNotifyOn').value = parts.join(',');
        }

        async function saveSettings() {
            const saveBtn = document.getElementById('saveSettingsBtn');
            const resultDiv = document.getElementById('settingsSaveResult');

            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            resultDiv.textContent = '';
            resultDiv.classList.remove('error');

            // Build the hidden notify_on value from checkboxes before reading it
            _syncNotifyOnHidden();

            try {
                const updates = {};

                for (const [key, elementId] of Object.entries(settingsFieldMap)) {
                    // Skip env-locked fields
                    if (envOverrides.includes(key)) continue;

                    const element = document.getElementById(elementId);
                    if (!element) continue;

                    if (element.type === 'checkbox') {
                        const currentValue = element.checked;
                        // Only include if changed from original
                        if (currentValue !== originalValues[key]) {
                            updates[key] = currentValue;
                        }
                    } else {
                        let value = element.value.trim();
                        // Singles subfolder: use direct picker value or custom path
                        if (key === 'singles_subdir') {
                            if (value === '.') {
                                value = '.';
                            } else if (value === SUBDIR_CUSTOM_VALUE) {
                                const customInput = document.getElementById('customSubdirInput');
                                value = _normaliseSubdirPath(customInput?.value || '') || 'Singles';
                            } else {
                                value = _normaliseSubdirPath(value) || 'Singles';
                            }
                            updates[key] = value;
                            continue;
                        }
                        // Playlists subfolder: same pattern, empty string = disabled
                        if (key === 'playlists_subdir') {
                            if (value === '' || value === '.') {
                                // keep as-is (empty = disabled, '.' = root)
                            } else if (value === SUBDIR_CUSTOM_VALUE) {
                                const customInput = document.getElementById('customPlaylistsSubdirInput');
                                value = _normaliseSubdirPath(customInput?.value || '') || 'Playlists';
                            } else {
                                value = _normaliseSubdirPath(value);
                            }
                            updates[key] = value;
                            continue;
                        }
                        // Albums subfolder: always enabled, defaults to 'Albums'
                        if (key === 'albums_subdir') {
                            if (value === '.') {
                                value = '.';
                            } else if (value === SUBDIR_CUSTOM_VALUE) {
                                const customInput = document.getElementById('customAlbumsSubdirInput');
                                value = _normaliseSubdirPath(customInput?.value || '') || 'Albums';
                            } else {
                                value = _normaliseSubdirPath(value) || 'Albums';
                            }
                            // Always include subdir fields — change detection fails when the
                            // saved value is itself a custom path (originalValues holds the
                            // resolved string, resolved value matches, gets skipped as "no change")
                            updates[key] = value;
                            continue;
                        }
                        // For sensitive fields with hidden values (null), only send if user entered something
                        if (sensitiveFields.includes(key)) {
                            if (originalValues[key] === null) {
                                // Field was "configured (hidden)" - only send if user typed something new
                                if (value && value !== '') {
                                    updates[key] = value;
                                } else if (element.dataset.forceClear === 'true') {
                                    updates[key] = '';
                                }
                            } else if (value !== originalValues[key]) {
                                // Field had visible value - send if changed (including to empty)
                                updates[key] = value;
                            }
                        } else {
                            // Non-sensitive field - only send if changed from original
                            if (value !== originalValues[key]) {
                                updates[key] = value;
                            }
                        }
                    }
                }

                // Don't make API call if nothing changed
                if (Object.keys(updates).length === 0) {
                    resultDiv.textContent = 'No changes to save';
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save Settings';
                    return;
                }

                const response = await apiFetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updates)
                });

                if (!response.ok) throw new Error('Failed to save settings');

                const data = await response.json();
                resultDiv.textContent = `Saved ${data.updated.length} setting(s)`;
                showToast('Settings saved');

                // Refresh Spotify cookie status (expired banner + expiry hint)
                if (data.settings) _updateSpotifyCookieHint(data.settings);

                // Update original values and clear sensitive fields after save
                for (const [key, value] of Object.entries(updates)) {
                    if (sensitiveFields.includes(key)) {
                        const elementId = settingsFieldMap[key];
                        if (elementId) {
                            const element = document.getElementById(elementId);
                            if (element && element.type !== 'checkbox') {
                                if (value === '') {
                                    originalValues[key] = '';
                                    element.value = '';
                                    element.placeholder = element.dataset.defaultPlaceholder || '';
                                } else {
                                    // Mark as "configured but hidden" and clear the field
                                    originalValues[key] = null;
                                    element.value = '';
                                    element.placeholder = 'Configured (hidden)';
                                }
                                element.dataset.forceClear = '';
                            }
                        }
                    } else {
                        // Update tracked original value
                        originalValues[key] = value;

                        // Sync header toggle when default_convert_to_flac is saved
                        if (key === 'default_convert_to_flac') {
                            convertToFlacCheckbox.checked = value;
                            localStorage.setItem(userStorageKey('convertToFlac'), value);
                            if (watchedConvertToFlac && !watchedFlacTouched) {
                                watchedConvertToFlac.checked = value;
                            }
                        }
                        // Sync format picker when audio_format is saved
                        if (key === 'audio_format') {
                            setAudioFormat(value);
                        }
                        // Sync playlists_subdir in serverConfig so watched/bulk-import UI updates
                        if (key === 'playlists_subdir') {
                            serverConfig.playlists_subdir = value;
                            const watchedToggle = document.getElementById('watchedPlaylistsDirToggle');
                            if (watchedToggle) watchedToggle.style.display = value ? 'flex' : 'none';
                            const watchedUsePlaylistsDir = document.getElementById('watchedUsePlaylistsDir');
                            if (watchedUsePlaylistsDir) watchedUsePlaylistsDir.checked = !!value;
                            const usePlaylistsDirCheckbox = document.getElementById('usePlaylistsDirCheckbox');
                            if (usePlaylistsDirCheckbox && value) usePlaylistsDirCheckbox.checked = true;
                        }
                    }
                }
            } catch (error) {
                console.error('Failed to save settings:', error);
                resultDiv.textContent = 'Failed to save settings';
                resultDiv.classList.add('error');
                showToast('Failed to save settings', true);
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Settings';
            }
        }

        async function testConnection(service) {
            const idMap = {
                'youtube-cookies': { btn: 'testYoutubeCookiesBtn', result: 'youtubeCookiesTestResult', label: 'Test Cookies' },
                'spotify-cookies': { btn: 'testSpotifyCookiesBtn', result: 'spotifyCookiesTestResult', label: 'Test Cookies' },
                'apprise': { btn: 'testAppriseBtn', result: 'appriseTestResult', label: 'Test Apprise' },
            };
            const ids = idMap[service] || { btn: `test${service.charAt(0).toUpperCase() + service.slice(1)}Btn`, result: `${service}TestResult`, label: 'Test Connection' };
            const btn = document.getElementById(ids.btn);
            const resultDiv = document.getElementById(ids.result);

            const btnLabel = ids.label;
            btn.disabled = true;
            btn.textContent = 'Testing...';
            resultDiv.className = 'test-result';
            resultDiv.style.display = 'none';
            // Reset Navidrome real-path status on each new test
            if (service === 'navidrome') {
                const rpDiv = document.getElementById('navidromeRealPathStatus');
                if (rpDiv) rpDiv.style.display = 'none';
            }

            // Gather current form values to test with (before saving)
            let body = {};
            if (service === 'slskd') {
                body = {
                    url: document.getElementById('settingSlskdUrl').value.trim(),
                    username: document.getElementById('settingSlskdUser').value.trim(),
                    password: document.getElementById('settingSlskdPass').value.trim(),
                    downloads_path: document.getElementById('settingSlskdDownloads').value.trim()
                };
            } else if (service === 'navidrome') {
                body = {
                    url: document.getElementById('settingNavidromeUrl').value.trim(),
                    username: document.getElementById('settingNavidromeUser').value.trim(),
                    password: document.getElementById('settingNavidromePass').value.trim()
                };
            } else if (service === 'jellyfin') {
                body = {
                    url: document.getElementById('settingJellyfinUrl').value.trim(),
                    api_key: document.getElementById('settingJellyfinApiKey').value.trim()
                };
            } else if (service === 'lidarr') {
                body = {
                    url: document.getElementById('settingLidarrUrl').value.trim(),
                    api_key: document.getElementById('settingLidarrApiKey').value.trim()
                };
            } else if (service === 'youtube-cookies') {
                body = {
                    cookies: document.getElementById('settingYoutubeCookies').value
                };
            } else if (service === 'spotify-cookies') {
                body = {
                    cookies: document.getElementById('settingSpotifyCookies').value
                };
            } else if (service === 'apprise') {
                body = {
                    url: document.getElementById('settingAppriseUrl').value.trim()
                };
            }

            try {
                const response = await apiFetch(`/api/settings/test/${service}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });

                const data = await response.json();

                if (data.success) {
                    resultDiv.textContent = data.message;
                    resultDiv.className = data.warning ? 'test-result warning' : 'test-result success';
                    // For Navidrome, show a second status line about real path support
                    if (service === 'navidrome') {
                        const rpDiv = document.getElementById('navidromeRealPathStatus');
                        if (rpDiv) {
                            if (data.real_path === true) {
                                rpDiv.textContent = 'Real file paths enabled — M3U playlist entries will use accurate paths';
                                rpDiv.className = 'test-result success';
                            } else {
                                rpDiv.textContent = data.real_path_hint || '';
                                rpDiv.className = 'test-result warning';
                            }
                            rpDiv.style.display = 'block';
                        }
                    }
                } else {
                    resultDiv.textContent = data.message || 'Connection failed';
                    resultDiv.className = 'test-result error';
                }
                resultDiv.style.display = 'block';
            } catch (error) {
                resultDiv.textContent = 'Connection test failed';
                resultDiv.className = 'test-result error';
                resultDiv.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.textContent = btnLabel;
            }
        }

        // Password field toggle
        document.querySelectorAll('.password-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                const targetId = btn.dataset.target;
                const input = document.getElementById(targetId);
                if (input) {
                    if (input.type === 'password') {
                        input.type = 'text';
                        btn.textContent = 'Hide';
                    } else {
                        input.type = 'password';
                        btn.textContent = 'Show';
                    }
                }
            });
        });

        // Settings event listeners
        document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
        document.getElementById('testSlskdBtn').addEventListener('click', () => testConnection('slskd'));
        document.getElementById('testNavidromeBtn').addEventListener('click', () => testConnection('navidrome'));
        document.getElementById('testJellyfinBtn').addEventListener('click', () => testConnection('jellyfin'));
        document.getElementById('testLidarrBtn').addEventListener('click', () => testConnection('lidarr'));
        document.getElementById('testYoutubeCookiesBtn').addEventListener('click', () => testConnection('youtube-cookies'));
        document.getElementById('testSpotifyCookiesBtn').addEventListener('click', () => testConnection('spotify-cookies'));
        document.getElementById('testAppriseBtn').addEventListener('click', () => testConnection('apprise'));
        const uploadYoutubeCookiesBtn = document.getElementById('uploadYoutubeCookiesBtn');
        const youtubeCookiesFile = document.getElementById('youtubeCookiesFile');
        const youtubeCookiesTextarea = document.getElementById('settingYoutubeCookies');
        const clearYoutubeCookiesBtn = document.getElementById('clearYoutubeCookiesBtn');

        uploadYoutubeCookiesBtn?.addEventListener('click', () => {
            if (envOverrides.includes('youtube_cookies')) {
                showToast('YouTube cookies are locked by environment settings', true);
                return;
            }
            youtubeCookiesFile?.click();
        });

        youtubeCookiesFile?.addEventListener('change', async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            try {
                const text = await file.text();
                if (youtubeCookiesTextarea) {
                    youtubeCookiesTextarea.value = text;
                }
                showToast('Cookies loaded, saving...');
                await saveSettings();
                showToast('Cookies updated');
                _updateCookieExpiryHint();
            } catch (error) {
                console.error('Failed to read cookies file:', error);
                showToast('Failed to read cookies file', true);
            } finally {
                event.target.value = '';
            }
        });

        clearYoutubeCookiesBtn?.addEventListener('click', async () => {
            if (envOverrides.includes('youtube_cookies')) {
                showToast('YouTube cookies are locked by environment settings', true);
                return;
            }
            if (!youtubeCookiesTextarea) return;
            youtubeCookiesTextarea.value = '';
            youtubeCookiesTextarea.dataset.forceClear = 'true';
            showToast('Clearing cookies...');
            await saveSettings();
            _updateCookieExpiryHint();
        });

        const uploadSpotifyCookiesBtn = document.getElementById('uploadSpotifyCookiesBtn');
        const spotifyCookiesFile = document.getElementById('spotifyCookiesFile');
        const spotifyCookiesTextarea = document.getElementById('settingSpotifyCookies');
        const clearSpotifyCookiesBtn = document.getElementById('clearSpotifyCookiesBtn');

        uploadSpotifyCookiesBtn?.addEventListener('click', () => {
            spotifyCookiesFile?.click();
        });

        spotifyCookiesFile?.addEventListener('change', async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            try {
                const text = await file.text();
                if (spotifyCookiesTextarea) {
                    spotifyCookiesTextarea.value = text;
                }
                showToast('Cookies loaded, saving...');
                await saveSettings();
                showToast('Spotify cookies updated');
            } catch (error) {
                console.error('Failed to read Spotify cookies file:', error);
                showToast('Failed to read cookies file', true);
            } finally {
                event.target.value = '';
            }
        });

        clearSpotifyCookiesBtn?.addEventListener('click', async () => {
            if (!spotifyCookiesTextarea) return;
            spotifyCookiesTextarea.value = '';
            spotifyCookiesTextarea.dataset.forceClear = 'true';
            showToast('Clearing Spotify cookies...');
            await saveSettings();
            _updateSpotifyCookieHint(null);
        });

        // Audio format segmented buttons (FLAC/Opus) and header toggle are wired
        // through setAudioFormat() - no direct sync needed here.

        // =============================================================================
        // Multi-User UI
        // =============================================================================

        function applyUserRoleToUI() {
            const admin = isAdmin();
            const peon = isPeon();

            // Toggle visibility of admin-only sections (set inline — no CSS class needed)
            document.querySelectorAll('.admin-only').forEach(el => {
                el.style.display = admin ? '' : 'none';
            });

            // Peons are kept on the happy path: no Settings, no Stats. Anything
            // else marked .peon-hide vanishes for them too. If they were sat on
            // a now-hidden tab, bounce them back to Results.
            document.querySelectorAll('.peon-hide').forEach(el => {
                el.style.display = peon ? 'none' : '';
            });
            if (peon) {
                const activeTab = document.querySelector('.tab.active');
                if (activeTab && activeTab.classList.contains('peon-hide')) {
                    const resultsTabBtn = document.querySelector('.tab[data-tab="results"]');
                    if (resultsTabBtn) resultsTabBtn.click();
                }
                // Pin the convert-to-format checkboxes to whatever the admin set
                // globally. The server overrides these on submit either way, but
                // mirroring the value here keeps the (hidden) UI honest if anyone
                // peeks via dev tools.
                const adminDefault = !!(serverConfig && serverConfig.default_convert_to_flac);
                const flac = document.getElementById('convertToFlac');
                const watchedFlac = document.getElementById('watchedConvertToFlac');
                if (flac) flac.checked = adminDefault;
                if (watchedFlac) watchedFlac.checked = adminDefault;
            }

            // Show logout button only in session mode
            const logoutBtn = document.getElementById('logoutBtn');
            if (logoutBtn) {
                logoutBtn.style.display = serverConfig && serverConfig.users_exist ? '' : 'none';
            }

            // Show current username in header if in session mode
            const userDisplay = document.getElementById('currentUserDisplay');
            if (userDisplay) {
                const user = getCurrentUser();
                if (user && serverConfig && serverConfig.users_exist) {
                    userDisplay.textContent = user.username;
                    userDisplay.style.display = '';
                } else {
                    userDisplay.style.display = 'none';
                }
            }

            // Show the "Change my password" button only when in session mode
            const changePwSection = document.getElementById('changePasswordSection');
            if (changePwSection) {
                changePwSection.style.display = (serverConfig && serverConfig.users_exist) ? '' : 'none';
            }

            // Singles-only mode hides the Albums tab. If the user was sitting on it,
            // bounce them back to Results so they're not staring at an empty page.
            const albumsTabBtn = document.getElementById('albumsTabBtn');
            if (albumsTabBtn) {
                const singlesOnly = !!(serverConfig && serverConfig.singles_only_mode);
                albumsTabBtn.style.display = singlesOnly ? 'none' : '';
                if (singlesOnly && albumsTabBtn.classList.contains('active')) {
                    const resultsTabBtn = document.querySelector('.tab[data-tab="results"]');
                    if (resultsTabBtn) resultsTabBtn.click();
                }
            }
        }

        document.getElementById('userInfoToggle')?.addEventListener('click', () => {
            const panel = document.getElementById('userInfoPanel');
            const btn = document.getElementById('userInfoToggle');
            if (!panel || !btn) return;
            const open = panel.hasAttribute('hidden');
            if (open) {
                panel.removeAttribute('hidden');
                btn.setAttribute('aria-expanded', 'true');
            } else {
                panel.setAttribute('hidden', '');
                btn.setAttribute('aria-expanded', 'false');
            }
        });

        async function loadUsers() {
            if (!isAdmin()) return;
            try {
                const resp = await apiFetch('/api/users');
                if (!resp.ok) return;
                const data = await resp.json();
                const listEl = document.getElementById('userList');
                const warningEl = document.getElementById('firstUserWarning');
                const roleEl = document.getElementById('newUserRole');
                if (!listEl) return;
                const currentUser = getCurrentUser();
                const noUsers = !data.users || data.users.length === 0;

                // Show the "point of no return" warning and lock role to Admin when
                // no users exist yet — the first account must always be admin.
                if (warningEl) warningEl.style.display = noUsers ? 'block' : 'none';
                if (roleEl) {
                    if (noUsers) roleEl.value = 'admin';
                    Array.from(roleEl.options).forEach(o => {
                        o.disabled = noUsers && o.value !== 'admin';
                    });
                }

                listEl.innerHTML = data.users.map(u => `
                    <div style="display:flex; align-items:center; gap:12px; padding:10px 0; border-bottom:1px solid var(--border);">
                        <span style="flex:1; font-weight:${u.id === currentUser?.id ? '600' : '400'};">${escapeHtml(u.username)}</span>
                        <span style="color:var(--text-secondary); font-size:13px;">${u.role}</span>
                        ${u.id !== currentUser?.id
                            ? `<button class="user-action-btn warning" onclick="forcePasswordReset('${escapeAttr(u.id)}', '${escapeAttr(u.username)}')">Force reset</button>
                               <button class="user-action-btn" onclick="deleteUser('${escapeAttr(u.id)}', '${escapeAttr(u.username)}')">Remove</button>`
                            : `<span style="color:var(--text-secondary); font-size:13px;">(you)</span>`}
                    </div>
                `).join('') || '<p style="color:var(--text-secondary); font-size:13px;">No users yet.</p>';
            } catch {}
        }

        async function createUser() {
            const username = document.getElementById('newUserUsername').value.trim();
            const password = document.getElementById('newUserPassword').value;
            const role = document.getElementById('newUserRole').value;
            const errorEl = document.getElementById('createUserError');

            if (!username || !password) {
                errorEl.textContent = 'Username and password are required.';
                errorEl.style.display = 'block';
                return;
            }

            try {
                const resp = await apiFetch('/api/users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password, role }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    errorEl.textContent = data.detail || 'Failed to create user.';
                    errorEl.style.display = 'block';
                    return;
                }
                errorEl.style.display = 'none';
                document.getElementById('newUserUsername').value = '';
                document.getElementById('newUserPassword').value = '';
                // Going from 0 to 1 users (first_user) or 1 to 2 (requires_login)
                // both flip into session-required mode. The current admin has no
                // session, so further requests will 401 — clear state and bounce
                // to login rather than leaving the UI stuck on a stale view.
                if (data.first_user || data.requires_login) {
                    localStorage.clear();
                    sessionStorage.clear();
                    window.location.reload();
                    return;
                }
                await loadUsers();
            } catch {
                errorEl.textContent = 'Error creating user.';
                errorEl.style.display = 'block';
            }
        }

        async function deleteUser(userId, username) {
            if (!confirm(`Remove user "${username}"? This cannot be undone.`)) return;
            try {
                const resp = await apiFetch(`/api/users/${userId}`, { method: 'DELETE' });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    alert(data.detail || 'Failed to remove user.');
                    return;
                }
                await loadUsers();
            } catch {
                alert('Error removing user.');
            }
        }

        async function forcePasswordReset(userId, username) {
            if (!confirm(`Force "${username}" to set a new password on next login?\n\nTheir current sessions will be terminated immediately.`)) return;
            try {
                const resp = await apiFetch(`/api/users/${userId}/force-password-change`, { method: 'PUT' });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    alert(data.detail || 'Failed to flag user for password reset.');
                    return;
                }
                await loadUsers();
            } catch {
                alert('Error flagging user for password reset.');
            }
        }

        // Destination picker - initialise on page load so it's ready before the first search
        initDestinationPicker();
