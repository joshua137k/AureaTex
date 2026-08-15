class CollabClient {
    constructor(monacoEditor, projectId, token) {
        this.editor          = monacoEditor;
        this.projectId       = projectId;
        this.token           = token;

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            this.userId = parseInt(payload.sub, 10);
        } catch (e) {
            this.userId = 0;
        }

        this.ws              = null;
        this.revision        = 0;
        this.pending         = [];
        this.buffer          = [];
        this.applyingRemote  = false;
        this.currentFile     = null;
        this.cursors         = {};
        this.cursorPositions = {};
        this._pingInterval   = null;
        this._changeDisposable = null;
    }

    connect() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = `${proto}://${location.host}/ws/projects/${this.projectId}/collab?token=${this.token}`;

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            this._startPing();

            if (this.currentFile) {
                this._sendJoin(this.currentFile);
            }
        };

        this.ws.onmessage = (e) => this._onMessage(JSON.parse(e.data));

        this.ws.onclose = (e) => {
            clearInterval(this._pingInterval);

            // Código usado pelo backend para forçar reload depois do pull.
            if (
                e.code === 4100 ||
                e.reason === 'Pull finished' ||
                e.reason === 'Project pull/reload' ||
                e.reason === 'Project reload/pull'
            ) {
                console.warn('[Collab] Server forced reload after pull.');
                setTimeout(() => {
                    window.location.reload();
                }, 300);
                return;
            }

            if (e.code >= 4000 && e.code <= 4099) {
                console.warn(`[Collab] WS closed (Code ${e.code}). Reconnection aborted.`);
                return;
            }

            setTimeout(() => this.connect(), 3000);
        };

        this.ws.onerror = (e) => console.error('[Collab] WS Error:', e);
    }

    joinFile(filename) {
        this.currentFile = filename;
        this.pending = [];
        this.buffer = [];
        this.revision = 0;

        this._clearCursors();
        this._detachChangeListener();

        return new Promise((resolve) => {
            this._onInitResolve = resolve;

            if (this.ws?.readyState === WebSocket.OPEN) {
                this._sendJoin(filename);
            } else {
                this._resolveInitFallback();
            }

            setTimeout(() => this._resolveInitFallback(), 3000);
        });
    }

    _resolveInitFallback() {
        if (this._onInitResolve) {
            this._onInitResolve();
            this._onInitResolve = null;
            this._attachChangeListener();
        }
    }

    leaveFile() {
        this.currentFile = null;

        this._detachChangeListener();
        this._clearCursors();

        this.pending = [];
        this.buffer = [];
    }

    destroy() {
        clearInterval(this._pingInterval);
        this._detachChangeListener();
        this._clearCursors();

        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }
    }

    _onMessage(msg) {
        switch (msg.type) {
            case 'init':
                this._handleInit(msg);
                break;

            case 'op':
                this._handleRemoteOp(msg);
                break;

            case 'ack':
                this._handleAck(msg);
                break;

            case 'cursor':
                this._handleCursor(msg);
                break;

            case 'user_join':
                this._showPresence(msg.user_id, msg.username, msg.color, true);
                break;

            case 'user_leave':
                this._removeCursor(msg.user_id);
                this._showPresence(msg.user_id, null, null, false);
                break;

            case 'file_added':
                this._handleFileAdded(msg);
                break;

            case 'file_deleted':
                this._handleFileDeleted(msg);
                break;

            case 'file_renamed':
                this._handleFileRenamed(msg);
                break;

            case 'file_main_changed':
                this._handleFileMainChanged(msg);
                break;

            case 'project_reloaded':
                this.pending = [];
                this.buffer = [];
                window.location.reload();
                break;

            case 'pong':
                break;
        }
    }

    _handleInit(msg) {
        if (msg.file !== this.currentFile) {
            if (typeof openModels !== 'undefined' && openModels.has(msg.file)) {
                const bgModel = openModels.get(msg.file);

                if (bgModel.getValue() !== msg.content) {
                    bgModel.setValue(msg.content);
                }
            }

            if (typeof files !== 'undefined') {
                const f = files.find(x => x.name === msg.file);

                if (f) {
                    f.content = msg.content;
                }
            }

            return;
        }

        this.revision = msg.revision;
        this.pending = [];
        this.buffer = [];
        this.applyingRemote = true;

        const model = this.editor.getModel();

        if (model && model.getValue() !== msg.content) {
            model.setValue(msg.content);
        }

        this.applyingRemote = false;

        if (msg.users && Array.isArray(msg.users)) {
            msg.users.forEach(u => {
                this._showPresence(u.user_id, u.username, u.color, true);
            });
        }

        if (typeof files !== 'undefined') {
            const fileEntry = files.find(f => f.name === msg.file);

            if (fileEntry && fileEntry.content === null) {
                fileEntry.content = msg.content;
            }
        }

        this._attachChangeListener();

        if (this._onInitResolve) {
            this._onInitResolve();
            this._onInitResolve = null;
        }
    }

    _handleRemoteOp(msg) {
        if (msg.file !== this.currentFile) return;

        let transformed = msg.op;
        const incomingUserId = msg.user_id;

        for (const pending of this.pending) {
            transformed = OT.transform(pending, transformed, this.userId, incomingUserId);
        }

        this.revision = msg.revision;

        this._applyToEditor(transformed);
    }

    _handleAck(msg) {
        if (msg.file !== this.currentFile) return;

        this.revision = msg.revision;
        this.pending.shift();

        if (this.buffer.length > 0) {
            const next = this.buffer.shift();

            this.pending.push(next);

            this._send({
                type: 'op',
                file: this.currentFile,
                revision: this.revision,
                op: next
            });
        }
    }

    _handleCursor(msg) {
        if (msg.file !== this.currentFile) return;

        const model = this.editor.getModel();

        if (!model) return;

        if (!this.cursors[msg.user_id]) {
            this._createCursorWidget(msg.user_id, msg.username, msg.color);
        }

        const maxLen = model.getValueLength();
        const safePos = Math.min(msg.pos, maxLen);
        const pos = model.getPositionAt(safePos);

        this._moveCursor(msg.user_id, pos);
    }

    _handleFileAdded(msg) {
        const exists = files.find(f => f.name === msg.file.name);

        if (!exists) {
            files.push({
                name: msg.file.name,
                content: null,
                is_main: msg.file.is_main,
                binary: false
            });

            if (typeof renderFileTree === 'function') {
                renderFileTree();
            }
        }
    }

    _handleFileDeleted(msg) {
        const idx = files.findIndex(f => f.name === msg.name);

        if (idx === -1) return;

        if (openModels.has(msg.name)) {
            openModels.get(msg.name).dispose();
            openModels.delete(msg.name);
        }

        files.splice(idx, 1);

        const wasActive = (activeFile === idx);

        openTabs = openTabs
            .filter(i => i !== idx)
            .map(i => i > idx ? i - 1 : i);

        if (wasActive) {
            if (this.currentFile === msg.name) {
                this.leaveFile();
            }

            activeFile = -1;

            if (openTabs.length > 0) {
                switchFile(openTabs[openTabs.length - 1]);
            } else {
                const mainIdx = files.findIndex(x => x.is_main);

                if (mainIdx >= 0) {
                    switchFile(mainIdx);
                } else {
                    renderTabs();
                    renderFileTree();
                }
            }
        } else {
            if (activeFile > idx) activeFile--;

            renderTabs();
            renderFileTree();
        }
    }

    _handleFileRenamed(msg) {
        if (typeof window.__applyRemoteRename === 'function') {
            window.__applyRemoteRename(msg.old_name, msg.new_name);
        }
    }

    _handleFileMainChanged(msg) {
        files.forEach(f => f.is_main = (f.name === msg.name));

        if (typeof renderFileTree === 'function') {
            renderFileTree();
        }
    }

    _attachChangeListener() {
        this._detachChangeListener();

        this._changeDisposable = this.editor.onDidChangeModelContent((e) => this._onChange(e));
    }

    _detachChangeListener() {
        if (this._changeDisposable) {
            this._changeDisposable.dispose();
            this._changeDisposable = null;
        }
    }

    _onChange(e) {
        if (this.applyingRemote || !this.currentFile) return;

        for (const change of e.changes) {
            const op = this._changeToOp(change);

            if (!op) continue;

            this._enqueue(op);
        }

        this._sendCursor();
    }

    _enqueue(op) {
        if (this.pending.length === 0) {
            this.pending.push(op);

            this._send({
                type: 'op',
                file: this.currentFile,
                revision: this.revision,
                op
            });
        } else {
            if (this.buffer.length > 0) {
                const composed = OT.compose(this.buffer[this.buffer.length - 1], op);

                if (composed) {
                    this.buffer[this.buffer.length - 1] = composed;
                    return;
                }
            }

            this.buffer.push(op);
        }
    }

    _changeToOp(change) {
        const from = change.rangeOffset;
        const removedLen = change.rangeLength;
        const added = change.text;

        if (removedLen === 0 && added.length === 0) return null;

        if (removedLen > 0 && added.length > 0) {
            const deleteOp = {
                type: 'delete',
                pos: from,
                len: removedLen
            };

            this._enqueue(deleteOp);

            return {
                type: 'insert',
                pos: from,
                text: added
            };
        }

        if (added.length > 0) {
            return {
                type: 'insert',
                pos: from,
                text: added
            };
        }

        if (removedLen > 0) {
            return {
                type: 'delete',
                pos: from,
                len: removedLen
            };
        }

        return null;
    }

    _applyToEditor(op) {
        this.applyingRemote = true;

        try {
            const model = this.editor.getModel();

            if (!model) return;

            if (op.type === 'insert') {
                const pos = model.getPositionAt(op.pos);

                model.pushEditOperations(
                    [],
                    [{
                        range: new monaco.Range(
                            pos.lineNumber,
                            pos.column,
                            pos.lineNumber,
                            pos.column
                        ),
                        text: op.text
                    }],
                    () => null
                );
            } else if (op.type === 'delete') {
                const from = model.getPositionAt(op.pos);
                const to = model.getPositionAt(op.pos + op.len);

                model.pushEditOperations(
                    [],
                    [{
                        range: new monaco.Range(
                            from.lineNumber,
                            from.column,
                            to.lineNumber,
                            to.column
                        ),
                        text: ''
                    }],
                    () => null
                );
            }
        } finally {
            this.applyingRemote = false;
        }
    }

    _injectCursorStyle(userId, color) {
        let styleEl = document.getElementById('collab-cursor-styles');

        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = 'collab-cursor-styles';
            document.head.appendChild(styleEl);
        }

        styleEl.appendChild(document.createTextNode(
            `.collab-cursor-${userId} { border-left: 2px solid ${color} !important; }`
        ));
    }

    _createCursorWidget(userId, username, color) {
        const domNode = document.createElement('div');

        domNode.className = 'collab-cursor-label';
        domNode.textContent = username;
        domNode.style.cssText = `
            background: ${color};
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            font-family: 'Space Grotesk', sans-serif;
            padding: 1px 5px;
            border-radius: 3px;
            white-space: nowrap;
            pointer-events: none;
            transform: translateY(-100%);
        `;

        const widget = {
            getId: () => `collab-cursor-widget-${userId}`,
            getDomNode: () => domNode,
            getPosition: () => ({
                position: this.cursorPositions[userId] || { lineNumber: 1, column: 1 },
                preference: [monaco.editor.ContentWidgetPositionPreference.EXACT],
            }),
        };

        this.cursors[userId] = {
            widget,
            decorationIds: [],
            color,
            username
        };

        this._injectCursorStyle(userId, color);

        this.editor.addContentWidget(widget);
    }

    _moveCursor(userId, pos) {
        const c = this.cursors[userId];

        if (!c) return;

        this.cursorPositions[userId] = pos;

        this.editor.layoutContentWidget(c.widget);

        c.decorationIds = this.editor.deltaDecorations(c.decorationIds, [{
            range: new monaco.Range(
                pos.lineNumber,
                pos.column,
                pos.lineNumber,
                pos.column
            ),
            options: {
                className: `collab-cursor-${userId}`,
                stickiness: 1
            },
        }]);
    }

    _removeCursor(userId) {
        const c = this.cursors[userId];

        if (!c) return;

        this.editor.removeContentWidget(c.widget);
        this.editor.deltaDecorations(c.decorationIds, []);

        delete this.cursors[userId];
        delete this.cursorPositions[userId];
    }

    _clearCursors() {
        Object.keys(this.cursors).forEach(id => this._removeCursor(Number(id)));
    }

    _showPresence(userId, username, color, joined) {
        let bar = document.getElementById('collab-presence-bar');

        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'collab-presence-bar';
            bar.style.cssText = 'display:flex;align-items:center;gap:6px;padding:0 8px;';

            const headerActions = document.querySelector('.header-actions');

            if (headerActions) {
                headerActions.prepend(bar);
            }
        }

        const existingAvatar = document.getElementById(`collab-avatar-${userId}`);

        if (!joined) {
            if (existingAvatar) {
                existingAvatar.remove();
            }

            return;
        }

        if (!existingAvatar) {
            const safeName = username || 'Guest';
            const initial = safeName.charAt(0).toUpperCase();

            const avatar = document.createElement('div');

            avatar.id = `collab-avatar-${userId}`;
            avatar.title = safeName;
            avatar.textContent = initial;
            avatar.style.cssText = `
                width:24px;
                height:24px;
                border-radius:50%;
                background:${color};
                color:#fff;
                font-size:11px;
                font-weight:700;
                font-family:'Space Grotesk',sans-serif;
                display:flex;
                align-items:center;
                justify-content:center;
                border: 2px solid ${color};
                box-shadow: 0 0 0 1px #fff;
            `;

            bar.appendChild(avatar);
        }
    }

    _send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    _sendJoin(filename) {
        this._send({
            type: 'join',
            file: filename
        });
    }

    _sendCursor() {
        const model = this.editor.getModel();

        if (!model) return;

        const pos = this.editor.getPosition();
        const offset = model.getOffsetAt(pos);

        this._send({
            type: 'cursor',
            file: this.currentFile,
            pos: offset
        });
    }

    _startPing() {
        clearInterval(this._pingInterval);

        this._pingInterval = setInterval(() => {
            this._send({ type: 'ping' });
        }, 25000);
    }
}

const OT = {
    transform(pending, incoming, localUserId, incomingUserId) {
        if (pending.type === 'insert' && incoming.type === 'insert') {
            if (
                pending.pos < incoming.pos ||
                (pending.pos === incoming.pos && localUserId < incomingUserId)
            ) {
                return {
                    ...incoming,
                    pos: incoming.pos + pending.text.length
                };
            }

            return incoming;
        }

        if (pending.type === 'delete' && incoming.type === 'insert') {
            if (pending.pos < incoming.pos) {
                return {
                    ...incoming,
                    pos: Math.max(pending.pos, incoming.pos - pending.len)
                };
            }

            return incoming;
        }

        if (pending.type === 'insert' && incoming.type === 'delete') {
            if (pending.pos <= incoming.pos) {
                return {
                    ...incoming,
                    pos: incoming.pos + pending.text.length
                };
            }

            if (pending.pos < incoming.pos + incoming.len) {
                return {
                    ...incoming,
                    len: incoming.len + pending.text.length
                };
            }

            return incoming;
        }

        if (pending.type === 'delete' && incoming.type === 'delete') {
            const s1 = pending.pos;
            const e1 = pending.pos + pending.len;
            const s2 = incoming.pos;
            const e2 = incoming.pos + incoming.len;

            if (e1 <= s2) {
                return {
                    ...incoming,
                    pos: incoming.pos - pending.len
                };
            }

            if (e2 <= s1) {
                return incoming;
            }

            const start = Math.min(s1, s2);
            const overlap = Math.max(0, Math.min(e1, e2) - Math.max(s1, s2));

            return {
                ...incoming,
                pos: start,
                len: Math.max(0, incoming.len - overlap)
            };
        }

        return incoming;
    },

    compose(op1, op2) {
        if (op1.type === 'insert' && op2.type === 'insert') {
            if (op2.pos === op1.pos + op1.text.length) {
                return {
                    type: 'insert',
                    pos: op1.pos,
                    text: op1.text + op2.text
                };
            }
        }

        if (op1.type === 'delete' && op2.type === 'delete') {
            if (op2.pos === op1.pos) {
                return {
                    type: 'delete',
                    pos: op1.pos,
                    len: op1.len + op2.len
                };
            }
        }

        return null;
    },
};