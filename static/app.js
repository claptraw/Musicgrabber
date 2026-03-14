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

        // Namespaced localStorage key — keeps per-user preferences separate on shared browsers.
        // Session-global keys (theme, seen_version, sessionToken, etc.) are NOT namespaced.
        function userStorageKey(key) {
            const user = getCurrentUser();
            return user && user.id ? `mg_${user.id}_${key}` : `mg_${key}`;
        }

        function buildJobDownloadPath(jobId) {
            return `/api/jobs/${encodeURIComponent(String(jobId || ''))}/download`;
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
                a.href = url;
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
            const response = await fetch(url, { ...options, headers });

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
                const resp = await fetch('/api/auth/login', {
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
                    await fetch('/api/auth/logout', {
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
            const pl = getSelectedPlaylist();
            const warn = document.getElementById('playlistSelectorWarn');
            if (warn) warn.style.display = (pl && pl.is_watched) ? 'inline' : 'none';
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

            // Keep hidden input in sync so settings save picks it up
            const hiddenInput = document.getElementById('settingAudioFormat');
            if (hiddenInput) hiddenInput.value = audioFormat;

            localStorage.setItem(userStorageKey('audioFormat'), audioFormat);
        }

        const versionLabel = document.getElementById('versionLabel');
        const PLAYLIST_SERVICES = ["Spotify", "YouTube", "Apple Music", "Amazon Music", "Tidal"];

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
                const resp = await fetch('/api/config');
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
        let currentSearchLogToken = null;
        let currentSource = 'all'; // Always search all sources
        const expandedJobIds = new Set();
        const watchedRefreshPending = new Map();

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
        let currentBulkImportId = null;
        let bulkImportPollInterval = null;
        let queuePollInterval = null;
        let watchedRefreshPollInterval = null;
        let watchedLoadInFlight = false;

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
                    if ((previewSource === 'soundcloud' || previewSource === 'mp3phoenix') && result.source_url) {
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
                    if (queuePollInterval) clearInterval(queuePollInterval);
                    queuePollInterval = setInterval(() => loadJobs(false), 3000);
                } else {
                    if (queuePollInterval) {
                        clearInterval(queuePollInterval);
                        queuePollInterval = null;
                    }
                }

                if (currentTab === 'watched') {
                    loadWatchedPlaylists();
                    loadWatchedArtists();
                    populateSourceChips();
                } else if (watchedRefreshPollInterval) {
                    clearInterval(watchedRefreshPollInterval);
                    watchedRefreshPollInterval = null;
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
                renderResults(data.results);
                showRelatedSuggestions(data.results);

                // If slskd is enabled and we're searching YouTube or All, fetch slskd results too
                if (data.slskd_enabled && (currentSource === 'youtube' || currentSource === 'all')) {
                    pendingSlskdToken = searchToken;
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

                if (!response.ok) return;

                const data = await response.json();
                if (searchToken !== currentSearchToken || pendingSlskdToken !== searchToken) {
                    return;
                }
                if (data.results && data.results.length > 0) {
                    // Merge slskd results with existing results
                    mergeSlskdResults(data.results);
                }
            } catch (error) {
                console.log('slskd search failed:', error);
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
        }

        // Show related search suggestions based on artists
        function showRelatedSuggestions(results) {
            if (!results || results.length === 0) return;

            // Extract unique artists/channels
            const artists = [...new Set(results.map(r => r.channel))].slice(0, 5);

            if (artists.length === 0) return;

            relatedSuggestions.innerHTML = `
                <div class="related-suggestions">
                    <div class="related-title">Related searches</div>
                    <div class="suggestion-chips">
                        ${artists.map(artist => `
                            <button class="suggestion-chip" data-query="${escapeHtml(artist)}">
                                ${escapeHtml(artist)}
                            </button>
                        `).join('')}
                    </div>
                </div>
            `;

            relatedSuggestions.querySelectorAll('.suggestion-chip').forEach(chip => {
                chip.addEventListener('click', () => {
                    setSearchValue(chip.dataset.query);
                    search();
                });
            });

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

        function getSourceBadge(source) {
            const badges = { youtube: 'YT', mp3phoenix: 'PX', soundcloud: 'SC', monochrome: 'MO', soulseek: 'SLK' };
            return badges[source] || source.toUpperCase().slice(0, 3);
        }

        function getSourceLabel(source) {
            const labels = { youtube: 'YouTube', mp3phoenix: 'MP3Phoenix', soundcloud: 'SoundCloud', monochrome: 'Monochrome', soulseek: 'Soulseek' };
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
            // Show album for Monochrome results - they have proper metadata
            if (result.album) {
                parts.push(escapeHtml(result.album));
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
            });
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
                if ((result.source === 'soundcloud' || result.source === 'monochrome' || result.source === 'mp3phoenix') && result.source_url) {
                    payload.source_url = result.source_url;
                }

                // Add slskd-specific fields if this is a Soulseek result
                if (result.source === 'soulseek') {
                    payload.slskd_username = result.slskd_username;
                    payload.slskd_filename = result.slskd_filename;
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
            try {
                const response = await apiFetch('/api/jobs?limit=100');
                if (!response.ok) return 0;
                const data = await response.json();
                // Count queued and downloading jobs
                return data.jobs.filter(j => j.status === 'queued' || j.status === 'downloading').length;
            } catch (error) {
                return 0;
            }
        }

        async function loadJobs(showLoading = true) {
            if (showLoading) {
                queueTab.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            }

            try {
                const response = await apiFetch('/api/jobs?limit=30');
                if (!response.ok) throw new Error('Failed to load jobs');

                const data = await response.json();
                renderJobs(data.jobs);
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
                        <div class="job-meta">${formatJobStatus(job.status)} • ${formatTime(job.created_at)}</div>
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
                            <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
                                <button onclick="event.stopPropagation(); redownloadJob('${escapeAttr(job.id || '')}')" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Re-download</button>
                                <button class="report-btn" data-job-id="${escapeHtml(job.id || '')}" data-video-id="${escapeHtml(job.video_id || '')}" data-uploader="${escapeHtml(job.uploader || '')}" data-source="${escapeHtml(job.source || 'youtube')}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--warning); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Report</button>
                                ${job.status !== 'failed' ? (fileDeleted
                                    ? `<button disabled style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-secondary); color: var(--text-muted); border: 1px solid var(--border); border-radius: 6px; cursor: not-allowed; opacity: 0.8;">File Deleted</button>`
                                    : `<button class="delete-file-btn" data-job-id="${escapeHtml(job.id || '')}" data-track-name="${escapeHtml(job.artist ? job.artist + ' - ' + job.title : job.title)}" onclick="event.stopPropagation()" style="padding: 6px 12px; font-size: 11px; font-family: inherit; font-weight: 600; background: var(--bg-tertiary); color: var(--error); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">Delete File</button>
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
                'monochrome_guessed': 'Monochrome embedded/guessed',
                'monochrome_api': 'Monochrome/Tidal API',
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
            if (!confirm(`Delete "${trackName}" from your library?`)) return;

            try {
                const response = await apiFetch(`/api/jobs/${jobId}/file`, { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || 'Delete failed');
                }
                const data = await response.json();
                if (data.deleted.length > 0) {
                    showToast(`Deleted ${data.deleted.length} file(s)`);
                } else {
                    showToast('File was already missing - marked as deleted');
                }
                loadJobs();
            } catch (error) {
                showToast(error.message || 'Delete failed', true);
                loadJobs(); // Refresh anyway so buttons reflect current state
            }
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

        // Close on overlay click (not the dialog itself)
        document.getElementById('reportOverlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeReportDialog();
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
                    const usePlaylistsDirCheckbox = document.getElementById('usePlaylistsDirCheckbox');
                    if ((usePlaylistsDirCheckbox && usePlaylistsDirCheckbox.checked) || (!usePlaylistsDirCheckbox && serverConfig.playlists_subdir)) {
                        requestBody.use_playlists_dir = true;
                    }
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
                if (!resp.ok) throw new Error('Search failed');
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
                resultsEl.innerHTML = `<p class="bulk-intro-text error-text">Search failed: ${escapeHtml(e.message)}</p>`;
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
                if (!resp.ok) throw new Error('Failed to load albums');
                const data = await resp.json();
                const albums = data.albums || [];
                if (!listEl) return;
                if (!albums.length) {
                    listEl.innerHTML = '<p class="bulk-intro-text">No albums found.</p>';
                    return;
                }
                listEl.innerHTML = '';
                for (const album of albums) {
                    const btn = document.createElement('button');
                    btn.className = 'album-list-btn';
                    btn.innerHTML = `<span class="album-list-title">${escapeHtml(album.title)}</span>`
                        + (album.year ? ` <span class="album-list-year">${escapeHtml(album.year)}</span>` : '');
                    btn.addEventListener('click', () => selectAlbum(album, btn));
                    listEl.appendChild(btn);
                }
            } catch (e) {
                if (listEl) listEl.innerHTML = `<p class="bulk-intro-text error-text">Failed to load albums: ${escapeHtml(e.message)}</p>`;
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
                if (!resp.ok) throw new Error('Failed to load tracklist');
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
                if (tracklistEl) tracklistEl.innerHTML = `<li>Failed: ${escapeHtml(e.message)}</li>`;
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
                const res = await apiFetch(`/api/jobs/downloadable?page=${downloadablePage}&per_page=50`);
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
        bulkImportBtn.addEventListener('click', bulkImport);
        clearQueueBtn.addEventListener('click', clearQueue);
        resetStatsBtn.addEventListener('click', resetStats);

        // Spotify playlist/album fetch handler
        async function fetchSpotifyPlaylist() {
            const url = spotifyUrlInput.value.trim();
            if (!url) {
                spotifyError.textContent = 'Please enter a playlist URL';
                spotifyError.style.display = 'block';
                return;
            }

            // URL validation - Spotify playlists/albums, Amazon Music playlists, Tidal, Apple Music, and YouTube/YT Music playlists
            const isSpotify = url.match(/^https?:\/\/open\.spotify\.com\/(playlist|album)\//);
            const isAmazon = url.match(/^https?:\/\/music\.amazon\.[a-z.]+\/(user-playlists|playlists)\//);
            const isTidal = url.match(/^https?:\/\/(www\.)?tidal\.com\/(browse\/)?playlist\/[0-9a-f-]{36}/i);
            const isApple = url.match(/^https?:\/\/music\.apple\.com\/[a-z]{2}\/(playlist|album)\//i);
            const isYouTube = url.match(/^https?:\/\/(www\.|music\.)?youtube\.com\/(playlist|watch)\?[^"]*list=/i);
            if (!isSpotify && !isAmazon && !isTidal && !isApple && !isYouTube) {
                spotifyError.textContent = 'Unsupported URL. Paste a Spotify, YouTube, Apple Music, Amazon Music, or Tidal playlist link.';
                spotifyError.style.display = 'block';
                return;
            }

            spotifyError.style.display = 'none';
            fetchSpotifyBtn.disabled = true;
            fetchSpotifyBtn.textContent = isAmazon ? 'Scraping...' : 'Fetching...';

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
                    tidal: '<i class="fa-solid fa-water" title="Tidal"></i>',
                    listenbrainz: '<i class="fa-solid fa-music" title="ListenBrainz"></i>'
                };
                const platformIcon = platformIcons[p.platform] || '<i class="fa-solid fa-list"></i>';
                const lastChecked = p.last_checked ? formatTimeAgo(p.last_checked) : 'Never';
                const statusColor = p.enabled ? 'var(--accent)' : 'var(--text-secondary)';
                const intervalText = p.refresh_interval_hours === 24 ? 'daily' :
                                     p.refresh_interval_hours === 168 ? 'weekly' :
                                     p.refresh_interval_hours >= 720 ? 'monthly' :
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
                                <div class="watched-sources-chips" data-playlist-id="${p.id}">
                                    ${renderSourceChips(p.id, p.preferred_sources || 'all')}
                                </div>
                            </label>
                        </div>
                        ${p.stale_navidrome_paths > 0 ? `
                        <div class="watched-card-stale-warning">
                            <i class="fa-solid fa-triangle-exclamation"></i>
                            <span><strong>${p.stale_navidrome_paths} track${p.stale_navidrome_paths === 1 ? '' : 's'}</strong> in the M3U point to files that no longer exist on disk but are still in Navidrome's database. To fix: open Navidrome, go to <strong>Settings &gt; Missing Files</strong>, select all, and click <strong>Remove from Database</strong>. Then trigger a library scan and refresh this playlist.</span>
                        </div>` : ''}
                        <div class="watched-card-actions">
                            <button onclick="refreshWatchedPlaylist('${p.id}')" class="watched-action-btn" title="${isRefreshing ? `Refresh in progress: ${escapeAttr(refreshLabel)}` : 'Check for new tracks now'}" ${isRefreshing ? 'disabled' : ''}>${isRefreshing ? 'Checking...' : 'Refresh'}</button>
                            <button onclick="toggleMissingTracks('${p.id}')" class="watched-action-btn" title="Show tracks that failed to download">Missing</button>
                            <button onclick="toggleTrackList('${p.id}', '${escapeAttr(p.name)}')" class="watched-action-btn" title="Show all tracks and their download status">Tracks</button>
                            <button onclick="copyWatchedPlaylistUrl('${escapeAttr(p.url)}')" class="watched-action-btn" title="Copy playlist URL">Copy URL</button>
                            <button onclick="toggleWatchedPlaylist('${p.id}', ${p.enabled ? 'false' : 'true'})" class="watched-action-btn watched-action-pause">${p.enabled ? 'Pause' : 'Resume'}</button>
                            <button onclick="deleteWatchedPlaylist('${p.id}', '${escapeAttr(p.name)}')" class="watched-action-btn watched-action-delete">Delete</button>
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
            else if (url.includes('tidal.com')) platform = 'tidal';

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
                        refresh_interval_hours: parseInt(watchedIntervalSelect.value),
                        convert_to_flac: watchedConvertToFlac ? watchedConvertToFlac.checked : convertToFlacCheckbox.checked,
                        make_m3u: document.getElementById('watchedMakeM3u') ? document.getElementById('watchedMakeM3u').checked : false,
                        use_playlists_dir: document.getElementById('watchedUsePlaylistsDir') ? document.getElementById('watchedUsePlaylistsDir').checked : false,
                        sync_mode: document.getElementById('watchedSyncModeSelect') ? document.getElementById('watchedSyncModeSelect').value : 'append',
                        preferred_sources: getWatchedPreferredSources()
                    })
                });

                if (!response.ok) {
                    const error = await response.json();
                    if (error.detail === 'spotify_cookies_expired') {
                        throw new Error('Spotify cookies have expired. Go to Settings to update them.');
                    }
                    throw new Error(error.detail || 'Failed to add playlist');
                }

                const data = await response.json();
                showToast(`Now watching "${data.name}" (${data.track_count} tracks)`);
                watchedUrlInput.value = '';
                loadWatchedPlaylists();
            } catch (error) {
                watchedError.textContent = error.message;
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
                        refresh_interval_hours: parseInt(watchedIntervalSelect.value),
                        convert_to_flac: watchedConvertToFlac ? watchedConvertToFlac.checked : convertToFlacCheckbox.checked,
                        make_m3u: document.getElementById('watchedMakeM3u') ? document.getElementById('watchedMakeM3u').checked : false,
                        use_playlists_dir: document.getElementById('watchedUsePlaylistsDir') ? document.getElementById('watchedUsePlaylistsDir').checked : false,
                        sync_mode: 'mirror'
                    })
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to add ListenBrainz playlists');
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
                            <button onclick="retryMissingTrack('${playlistId}', '${escapeAttr(t.artist)}', '${escapeAttr(t.title)}', '${trackId}')"
                                style="padding: 3px 8px; font-size: 11px; font-family: inherit; background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; white-space: nowrap;"
                                title="Auto-search and re-queue this track">Retry</button>
                            <button onclick="searchMissingTrack('${playlistId}', '${escapeAttr(data.playlist_name)}', '${escapeAttr(t.artist)}', '${escapeAttr(t.title)}')"
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

        function searchMissingTrack(playlistId, playlistName, artist, title) {
            // Pre-fill the search input
            setSearchValue(`${artist} - ${title}`);

            // Pre-select the watched playlist in the selector and expand the panel
            const toggle = document.getElementById('playlistSelectorToggle');
            const panel = document.getElementById('playlistSelector');
            const sel = document.getElementById('playlistSelectorInput');
            if (sel && playlistName) {
                for (const opt of sel.options) {
                    if (opt.value === playlistName) {
                        sel.value = playlistName;
                        // Expand the panel if it's collapsed so the selection is visible
                        // and getSelectedPlaylist() returns the value
                        if (panel && panel.style.display === 'none' && toggle) {
                            toggle.click();
                        }
                        _updatePlaylistSelectorWarning();
                        break;
                    }
                }
            }

            // Switch to Results tab by clicking it (reuses existing tab-switch logic)
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
                         <button onclick="replaceTrack('${playlistId}', '${escapeAttr(data.playlist_name)}', '${escapeAttr(t.artist)}', '${escapeAttr(t.title)}', '${t.job_id}', '${rowId}')"
                             class="track-replace-btn" title="Delete this file and search for the correct version">Replace</button>`
                    )).join('');
                }

                if (sections.failed.length) {
                    html += `<div class="track-list-section-header">Failed / Missing (${sections.failed.length})</div>`;
                    html += sections.failed.map((t, i) => trackRow(t, `f${i}`, (t, rowId) =>
                        `<span class="track-status-chip track-status-fail">&#10007;</span>
                         <button onclick="retryMissingTrack('${playlistId}', '${escapeAttr(t.artist)}', '${escapeAttr(t.title)}', '${rowId}')"
                             class="track-action-btn" title="Auto-search and re-queue">Retry</button>
                         <button onclick="searchMissingTrack('${playlistId}', '${escapeAttr(data.playlist_name)}', '${escapeAttr(t.artist)}', '${escapeAttr(t.title)}')"
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
            if (btn) { btn.disabled = true; btn.textContent = 'Deleting...'; }

            try {
                // Delete (or unlink from playlist) the bad file and clear downloaded_at
                const resp = await apiFetch(`/api/jobs/${jobId}/file`, { method: 'DELETE' });
                if (!resp.ok) throw new Error('Delete failed');
                const data = await resp.json();

                if (data.file_kept) {
                    showToast(`${artist} - ${title} removed from playlist. Library file kept. Pick the correct version below.`);
                } else {
                    showToast(`Deleted bad file for ${artist} - ${title}. Pick the correct version below.`);
                }

                // Update the row to show missing state with retry/search options
                if (row) {
                    const actionsEl = row.querySelector('.track-list-actions');
                    if (actionsEl) {
                        actionsEl.innerHTML = `<span class="track-status-chip track-status-fail">missing</span>
                            <button onclick="retryMissingTrack('${playlistId}', '${escapeAttr(artist)}', '${escapeAttr(title)}', '${rowId}')"
                                class="track-action-btn">Retry</button>
                            <button onclick="searchMissingTrack('${playlistId}', '${escapeAttr(playlistName)}', '${escapeAttr(artist)}', '${escapeAttr(title)}')"
                                class="track-action-btn">Search</button>`;
                    }
                }

                // Route to search tab pre-filled with this track + playlist selected
                searchMissingTrack(playlistId, playlistName, artist, title);
            } catch (e) {
                showToast('Failed to delete file', true);
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
                // Read current chip state before replacing
                const existing = [...el.querySelectorAll('.source-chip')];
                let pref = 'all';
                if (existing.length > 0) {
                    const on = existing.filter(c => c.classList.contains('on')).map(c => c.dataset.source);
                    pref = on.length === existing.length ? 'all' : on.join(',');
                }
                el.innerHTML = renderSourceChips(pid, pref);
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
            updateWatchedPlaylistPreferredSources(playlistId, preferred);
        }

        function getWatchedPreferredSources() {
            const chips = [...document.querySelectorAll('#watchedSourcesSelector .source-chip')];
            if (chips.length === 0) return 'all';
            const on = chips.filter(c => c.classList.contains('on')).map(c => c.dataset.source);
            return (on.length === 0 || on.length === chips.length) ? 'all' : on.join(',');
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
                const msg = data.queued > 0
                    ? `Now watching ${data.name}. Queued ${data.queued} single(s) for download.`
                    : `Now watching ${data.name}. No new singles since ${fromDate}.`;
                showToast(msg);
                loadWatchedArtists();
            } catch (e) {
                statusEl.textContent = `Error: ${e.message}`;
                addBtn.disabled = false;
            }
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
                const isRunning = artist.refresh_state === 'running' || artistRefreshPending.has(artist.id);
                if (isRunning) anyRunning = true;
                const isError = artist.refresh_state === 'error';
                const isPaused = !artist.enabled;
                const intervalLabel = artist.refresh_interval_hours >= 720 ? 'monthly'
                    : artist.refresh_interval_hours >= 168 ? 'weekly' : 'daily';
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
                                <option value="24" ${artist.refresh_interval_hours == 24 ? 'selected' : ''}>Daily</option>
                                <option value="168" ${artist.refresh_interval_hours == 168 ? 'selected' : ''}>Weekly</option>
                                <option value="720" ${artist.refresh_interval_hours == 720 ? 'selected' : ''}>Monthly</option>
                            </select>
                        </label>
                    </div>
                    <div class="watched-card-actions">
                        <button class="watched-action-btn" onclick="refreshWatchedArtist('${artist.id}')" ${isRunning ? 'disabled' : ''}>Refresh</button>
                        <button class="watched-action-btn" onclick="toggleArtistMissingTracks('${artist.id}')">Missing</button>
                        <button class="watched-action-btn" onclick="toggleArtistTrackList('${artist.id}', '${escapeAttr(artist.name)}')">Tracks</button>
                        <button class="watched-action-btn watched-action-pause" onclick="toggleWatchedArtist('${artist.id}', ${!artist.enabled})">${isPaused ? 'Resume' : 'Pause'}</button>
                        <button class="watched-action-btn watched-action-delete" onclick="deleteWatchedArtist('${artist.id}', '${escapeAttr(artist.name)}')">Delete</button>
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
                } else if (data.new_tracks > 0) {
                    showToast(`Found ${data.new_tracks} new single(s) for download`);
                } else {
                    showToast('No new singles found');
                }
            } catch (e) {
                showToast('Refresh failed', true);
            } finally {
                artistRefreshPending.delete(artistId);
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
                    body: JSON.stringify({ refresh_interval_hours: parseInt(hours) })
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
                        <button onclick="retryAllArtistMissing('${artistId}', this)"
                            style="padding:4px 12px;font-size:12px;font-family:inherit;font-weight:600;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;">
                            Queue All
                        </button>
                    </div>` +
                    data.tracks.map(t => `
                        <div style="padding:4px 0;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span style="flex:1;min-width:0;font-size:12px;">${escapeHtml(t.artist || '')} &ndash; ${escapeHtml(t.title)}</span>
                            <div style="display:flex;gap:4px;align-items:center;flex-shrink:0;">
                                ${t.release_date ? `<span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;">${t.release_date}</span>` : ''}
                                <button onclick="retryArtistTrack('${artistId}','${escapeAttr(t.artist||'')}','${escapeAttr(t.title)}',this)"
                                    style="padding:3px 8px;font-size:11px;font-family:inherit;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);border-radius:4px;cursor:pointer;white-space:nowrap;"
                                    title="Auto-search and re-queue this track">Retry</button>
                            </div>
                        </div>`).join('');
            } catch (e) {
                panel.innerHTML = '<p style="font-size:12px;color:var(--error);">Failed to load missing tracks.</p>';
            }
        }

        async function toggleArtistTrackList(artistId, artistName) {
            const panel = document.getElementById(`artist-tracks-${artistId}`);
            if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
            panel.style.display = 'block';
            panel.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await apiFetch(`/api/watched-artists/${artistId}/tracks`);
                const data = await res.json();
                if (!data.tracks || data.tracks.length === 0) {
                    panel.innerHTML = '<p style="font-size:12px;color:var(--text-secondary);padding:8px 0;">No tracks tracked yet.</p>';
                    return;
                }
                panel.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">${data.tracks.length} single(s) tracked:</div>` +
                    data.tracks.map(t => {
                        const statusIcon = t.downloaded_at
                            ? '<i class="fa-solid fa-check" style="color:var(--success);"></i>'
                            : t.job_status === 'queued' || t.job_status === 'downloading'
                                ? '<i class="fa-solid fa-clock" style="color:var(--text-secondary);"></i>'
                                : '<i class="fa-solid fa-xmark" style="color:var(--error);"></i>';
                        const dlBtn = t.downloaded_at && t.job_id
                            ? `<button onclick="saveJobToDevice('${escapeAttr(t.job_id)}')" style="padding:2px 8px;font-size:11px;font-family:inherit;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);border-radius:4px;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-download"></i></button>`
                            : '';
                        return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px;">
                            ${statusIcon}
                            <span style="flex:1;">${escapeHtml(t.artist || '')} &ndash; ${escapeHtml(t.title)}</span>
                            ${t.release_date ? `<span style="color:var(--text-secondary);font-size:11px;">${t.release_date}</span>` : ''}
                            ${dlBtn}
                        </div>`;
                    }).join('');
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
                if (panel) panel.querySelectorAll('button:not([onclick*="retryAllArtistMissing"])').forEach(b => { b.disabled = true; b.textContent = 'Queued'; });
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
                    </div>
                `).join('');
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
            const moCount = data.sources.monochrome || 0;
            const slkCount = data.sources.soulseek || 0;
            const sourceTotal = ytCount + pxCount + scCount + moCount + slkCount || 1;

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
                        ${moCount > 0 ? `<div style="flex: ${moCount}; background: #00bcd4; border-radius: 4px;"></div>` : ''}
                        ${slkCount > 0 ? `<div style="flex: ${slkCount}; background: #4a9eff; border-radius: 4px;"></div>` : ''}
                    </div>
                    <div style="display: flex; gap: 16px; font-size: 12px; flex-wrap: wrap;">
                        <span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #ff0000; border-radius: 2px; margin-right: 4px;"></span>YouTube: ${ytCount}</span>
                        ${pxCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #e05c00; border-radius: 2px; margin-right: 4px;"></span>MP3Phoenix: ${pxCount}</span>` : ''}
                        ${scCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #ff5500; border-radius: 2px; margin-right: 4px;"></span>SoundCloud: ${scCount}</span>` : ''}
                        ${moCount > 0 ? `<span style="color: var(--text-secondary);"><span style="display: inline-block; width: 8px; height: 8px; background: #00bcd4; border-radius: 2px; margin-right: 4px;"></span>Monochrome: ${moCount}</span>` : ''}
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
            'min_audio_bitrate': 'settingMinBitrate',
            'singles_subdir': 'settingSinglesSubdir',
            'playlists_subdir': 'settingPlaylistsSubdir',
            'albums_subdir': 'settingAlbumsSubdir',
            'organise_by_artist': 'settingOrganiseByArtist',
            'auto_album_singles': 'settingAutoAlbumSingles',
            'source_youtube_enabled': 'settingSourceYoutube',
            'source_mp3phoenix_enabled': 'settingSourceMp3phoenix',
            'source_soundcloud_enabled': 'settingSourceSoundcloud',
            'source_monochrome_enabled': 'settingSourceMonochrome',
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
            'spotify_browser_timeout_seconds': 'settingSpotifyBrowserTimeout',
            'spotify_browser_stall_seconds': 'settingSpotifyBrowserStall',
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
                        // Sync format picker buttons when audio_format loads
                        if (key === 'audio_format') {
                            setAudioFormat(value);
                        }
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

                // Live path preview: update whenever organise-by-artist toggle changes
                const organiseToggle = document.getElementById('settingOrganiseByArtist');
                if (organiseToggle) organiseToggle.onchange = _updatePathPreviews;

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

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = '/music (root)';
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `/music/${relPath}`;
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

            // Second option: /music/Playlists as the obvious default
            const playlistsOpt = document.createElement('option');
            playlistsOpt.value = 'Playlists';
            playlistsOpt.textContent = '/music/Playlists';
            select.appendChild(playlistsOpt);

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = '/music (root)';
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs.filter(d => d !== 'Playlists')) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `/music/${relPath}`;
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

            const rootOpt = document.createElement('option');
            rootOpt.value = '.';
            rootOpt.textContent = '/music (root)';
            select.appendChild(rootOpt);

            for (const relPath of sortedDirs) {
                const opt = document.createElement('option');
                opt.value = relPath;
                opt.textContent = `/music/${relPath}`;
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

            const singlesVal = singlesSelect && singlesSelect.value === SUBDIR_CUSTOM_VALUE
                ? (singlesCustom ? singlesCustom.value.trim() : '')
                : (singlesSelect ? singlesSelect.value : '');
            const playlistsVal = playlistsSelect && playlistsSelect.value === SUBDIR_CUSTOM_VALUE
                ? (playlistsCustom ? playlistsCustom.value.trim() : '')
                : (playlistsSelect ? playlistsSelect.value : '');
            const organise = organiseToggle ? organiseToggle.checked : true;

            if (singlesEl) {
                let p = '/music';
                if (singlesVal && singlesVal !== '.') p += '/' + singlesVal;
                if (organise) {
                    p += '/Artist Name/Track Title.flac';
                } else {
                    p += '/Artist Name - Track Title.flac';
                }
                singlesEl.textContent = 'Files saved to: ' + p;
            }

            if (playlistsEl) {
                if (!playlistsVal || playlistsVal === '') {
                    playlistsEl.textContent = '(playlist tracks go to Singles folder)';
                } else {
                    let p = '/music';
                    if (playlistsVal !== '.') p += '/' + playlistsVal;
                    p += '/Playlist Name/Artist - Title.flac';
                    playlistsEl.textContent = 'Files saved to: ' + p;
                }
            }

            const albumsVal = albumsSelect && albumsSelect.value === SUBDIR_CUSTOM_VALUE
                ? (albumsCustom ? albumsCustom.value.trim() : '')
                : (albumsSelect ? albumsSelect.value : '');
            if (albumsEl) {
                let p = '/music';
                if (albumsVal && albumsVal !== '.') p += '/' + albumsVal;
                p += '/Artist/Album/Track.flac';
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
                clearBtn.className = 'setting-clear-btn';
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
                    password: document.getElementById('settingSlskdPass').value.trim()
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
                    resultDiv.className = 'test-result success';
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

            // Toggle visibility of admin-only sections (set inline — no CSS class needed)
            document.querySelectorAll('.admin-only').forEach(el => {
                el.style.display = admin ? '' : 'none';
            });

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
        }

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
