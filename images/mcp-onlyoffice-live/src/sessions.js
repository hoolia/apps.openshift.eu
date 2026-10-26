'use strict';

const { normalizePath, extension, NextcloudError } = require('./nextcloud');

const BRIDGE_PATH = '/sdkjs-plugins/agent-bridge/';
const EDITOR_BY_EXT = {
  docx: 'word', doc: 'word', odt: 'word', rtf: 'word', txt: 'word', md: 'word',
  xlsx: 'cell', xls: 'cell', ods: 'cell', csv: 'cell',
  pptx: 'slide', ppt: 'slide', odp: 'slide',
};

class SessionError extends Error {
  constructor(message, status = 409) {
    super(message);
    this.status = status;
  }
}

// One headless browser context per (account, file): the agent's seat in the co-editing session
class Sessions {
  constructor(config, nextcloud, launch, now = () => Date.now()) {
    this.config = config;
    this.nextcloud = nextcloud;
    this.launch = launch;
    this.now = now;
    this.sessions = new Map();
    this.opening = new Map();
    this.browser = null;
  }

  key(user, path) { return `${user}\n${path}`; }

  list(user) {
    return [...this.sessions.values()].filter(s => !user || s.user === user).map(s => this.describe(s));
  }

  describe(s) {
    return {
      path: s.path, editor: s.editor, user: s.user, displayName: s.displayName,
      openedAt: new Date(s.openedAt).toISOString(), idleSeconds: Math.round((this.now() - s.lastUsed) / 1000),
    };
  }

  checkLimits(user) {
    if (this.sessions.size + this.opening.size >= this.config.maxSessions) {
      throw new SessionError(`all ${this.config.maxSessions} live sessions are in use; close one first`, 429);
    }
    const mine = [...this.sessions.values()].filter(s => s.user === user).length
      + [...this.opening.keys()].filter(k => k.startsWith(user + '\n')).length;
    if (mine >= this.config.maxSessionsPerUser) {
      throw new SessionError(`this account already has ${mine} live sessions (limit ${this.config.maxSessionsPerUser}); close one first`, 429);
    }
  }

  async getBrowser() {
    if (!this.browser || (this.browser.isConnected && !this.browser.isConnected())) this.browser = await this.launch();
    return this.browser;
  }

  async open(authorization, rawPath, { create = false } = {}) {
    const path = normalizePath(rawPath);
    const me = await this.nextcloud.identity(authorization);
    const key = this.key(me.id, path);
    const existing = this.sessions.get(key);
    if (existing) { existing.lastUsed = this.now(); return { ...this.describe(existing), reused: true }; }
    if (this.opening.has(key)) return this.opening.get(key);
    this.checkLimits(me.id);
    const started = this.now();
    const task = (async () => {
      const url = create ? await this.nextcloud.createUrl(authorization, path) : await this.nextcloud.openUrl(authorization, path);
      const browser = await this.getBrowser();
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US' });
      try {
        const page = await context.newPage();
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: this.config.openTimeoutSeconds * 1000 });
        const bridge = await waitForBridge(page, this.config.openTimeoutSeconds * 1000);
        let editor = '';
        try { editor = await bridge.evaluate(() => window.agentBridge.editorType()); } catch (e) { editor = ''; }
        if (!['word', 'cell', 'slide'].includes(editor)) editor = EDITOR_BY_EXT[extension(path)] || 'word';
        const s = { key, path, user: me.id, displayName: me.displayName, editor, context, page, bridge, openedAt: this.now(), lastUsed: this.now() };
        this.sessions.set(key, s);
        return { ...this.describe(s), reused: false, openMs: this.now() - started };
      } catch (err) {
        await context.close().catch(() => {});
        throw err;
      }
    })();
    this.opening.set(key, task);
    try { return await task; } finally { this.opening.delete(key); }
  }

  async get(authorization, rawPath) {
    const path = normalizePath(rawPath);
    const me = await this.nextcloud.identity(authorization);
    const s = this.sessions.get(this.key(me.id, path));
    if (!s) throw new SessionError(`no live session for ${path}: call office_live_open first`, 404);
    s.lastUsed = this.now();
    if (s.page.isClosed()) { this.sessions.delete(s.key); throw new SessionError('the editor page closed; open the file again', 410); }
    return s;
  }

  async command(s, source, scope = {}, recalculate = true) {
    const result = await s.bridge.evaluate(([src, sc, rc]) => window.agentBridge.command(src, sc, rc), [source, scope, recalculate]);
    s.lastUsed = this.now();
    if (result && typeof result === 'object' && result.onlyoffice_id_error_message) throw new SessionError(result.onlyoffice_id_error_message, 422);
    return result;
  }

  async method(s, name, args) {
    const result = await s.bridge.evaluate(([n, a]) => window.agentBridge.method(n, a), [name, args]);
    s.lastUsed = this.now();
    return result;
  }

  // the editor frame's own API, for what no plugin method covers (save)
  async editorEval(s, fn) {
    const frame = s.page.frames().find(f => f.url().includes('/web-apps/apps/') && f.url().includes('/main/index'));
    if (!frame) throw new SessionError('editor frame not found', 500);
    return frame.evaluate(fn);
  }

  async close(authorization, rawPath) {
    const s = await this.get(authorization, rawPath);
    await this.drop(s);
    return { closed: s.path };
  }

  async drop(s) {
    this.sessions.delete(s.key);
    await s.context.close().catch(() => {});
  }

  async sweep() {
    const limit = this.config.idleTimeoutSeconds * 1000;
    for (const s of [...this.sessions.values()]) {
      if (this.now() - s.lastUsed > limit || s.page.isClosed()) await this.drop(s);
    }
    if (this.sessions.size === 0 && this.opening.size === 0 && this.browser) {
      const b = this.browser; this.browser = null; await b.close().catch(() => {});
    }
  }

  async shutdown() {
    for (const s of [...this.sessions.values()]) await this.drop(s);
    if (this.browser) await this.browser.close().catch(() => {});
  }
}

async function waitForBridge(page, timeout) {
  const end = Date.now() + timeout;
  let editorSeen = false;
  while (Date.now() < end) {
    for (const f of page.frames()) {
      const u = f.url();
      if (u.includes('/web-apps/apps/')) editorSeen = true;
      if (!u.includes(BRIDGE_PATH)) continue;
      try {
        if (await f.evaluate(() => !!(window.agentBridge && window.agentBridge.ready))) return f;
      } catch (e) { /* frame navigating */ }
    }
    await page.waitForTimeout(250);
  }
  if (!editorSeen) throw new NextcloudError('the editor did not load: check that Nextcloud opens the file in ONLYOFFICE', 504);
  throw new NextcloudError('the editor loaded without the agent-bridge plugin: the Document Server must carry it in sdkjs-plugins', 501);
}

module.exports = { Sessions, SessionError, waitForBridge, EDITOR_BY_EXT };
