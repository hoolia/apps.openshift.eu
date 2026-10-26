// node --test test/catalog.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const C = require('../catalog.js');

const index = fs.readFileSync(path.join(__dirname, 'index.yaml'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, 'catalog.json'), 'utf8'));
const charts = C.merge(C.parseIndex(index), catalog);
const byId = Object.fromEntries(charts.map((c) => [c.id, c]));
const ids = (list) => list.map((c) => c.id);

test('index.yaml: latest version, folded description, quoted value, no dependency leak', () => {
  assert.deepStrictEqual(Object.keys(byId).sort(), ['atlassian-mcp', 'mcp-all', 'mcp-bundle-google-workspace',
    'mcp-gmail', 'mcp-sonarqube', 'nextcloud', 'openhands']);
  assert.strictEqual(byId.nextcloud.version, '0.8.0');
  assert.strictEqual(byId.nextcloud.name, 'Nextcloud');
  assert.strictEqual(byId.nextcloud.description, 'Nextcloud files: calendar and contacts');
  assert.match(byId['atlassian-mcp'].description, /for a tenant or one of its teams, with each caller/);
  assert.deepStrictEqual(byId.nextcloud.keywords, ['files', 'collaboration']);
});

test('type, category and vendor: catalog.json first, index.yaml as fallback', () => {
  assert.strictEqual(byId.nextcloud.type, 'app');
  assert.strictEqual(byId.openhands.type, 'app', 'the keyword mcp alone makes no MCP chart');
  assert.strictEqual(byId['atlassian-mcp'].type, 'mcp-server');
  assert.strictEqual(byId['mcp-sonarqube'].type, 'mcp-server', 'derived from the server annotation');
  assert.strictEqual(byId['mcp-sonarqube'].category, 'code-quality');
  assert.strictEqual(byId['mcp-sonarqube'].vendor, '');
  assert.strictEqual(byId['mcp-all'].type, 'mcp-bundle');
  assert.strictEqual(byId['mcp-bundle-google-workspace'].type, 'mcp-bundle');
  assert.strictEqual(byId.openhands.vendor, 'All Hands AI');
  assert.strictEqual(byId['mcp-gmail'].bundle, 'google-workspace');
  assert.strictEqual(C.merge(C.parseIndex(index), null).find((c) => c.id === 'nextcloud').vendor, '');
});

test('filter: OR within a facet, AND across facets, search, apps sorted first', () => {
  const s = C.emptyState();
  assert.deepStrictEqual(ids(C.filter(charts, s)).slice(0, 2), ['nextcloud', 'openhands']);
  s.type = ['mcp-server', 'mcp-bundle'];
  assert.strictEqual(C.filter(charts, s).length, 5);
  s.category = ['productivity'];
  assert.deepStrictEqual(ids(C.filter(charts, s)), ['atlassian-mcp', 'mcp-gmail', 'mcp-bundle-google-workspace']);
  s.vendor = ['Google'];
  assert.deepStrictEqual(ids(C.filter(charts, s)), ['mcp-gmail', 'mcp-bundle-google-workspace']);
  s.q = 'GMAIL';
  assert.deepStrictEqual(ids(C.filter(charts, s)), ['mcp-gmail']);
});

test('facets: counts exclude the facet itself, values sorted, bundle facet only with bundles', () => {
  const s = C.emptyState();
  s.type = ['app'];
  const f = Object.fromEntries(C.facets(charts, s).map((x) => [x.key, x]));
  assert.deepStrictEqual(f.type.values.map((v) => [v.label, v.count, v.selected]),
    [['App', 2, true], ['MCP server', 3, false], ['MCP bundle', 2, false]]);
  assert.deepStrictEqual(f.category.values.map((v) => [v.value, v.count]),
    [['ai-agent', 1], ['code-quality', 0], ['collaboration', 1], ['productivity', 0]]);
  assert.deepStrictEqual(f.vendor.values.map((v) => v.value), ['All Hands AI', 'Google', 'Nextcloud GmbH', 'sooperset']);
  assert.deepStrictEqual(f.bundle.values.map((v) => v.value), ['all', 'google-workspace']);
  const appsOnly = C.merge(C.parseIndex(index), catalog).filter((c) => c.type === 'app');
  assert.ok(!C.facets(appsOnly, C.emptyState()).some((x) => x.key === 'bundle'));
});

test('URL hash: round trip, multi-select, first-version links', () => {
  const s = C.emptyState();
  s.q = 'jira board';
  s.type = ['mcp-server', 'mcp-bundle'];
  s.vendor = ['Nextcloud GmbH'];
  const h = C.toHash(s);
  assert.strictEqual(h, '#q=jira%20board&type=mcp-server,mcp-bundle&vendor=Nextcloud%20GmbH');
  assert.deepStrictEqual(C.parseHash(h), s);
  assert.deepStrictEqual(C.parseHash('#type=server&bundle=google-workspace').type, ['mcp-server']);
  assert.deepStrictEqual(C.parseHash('#bundle=google-workspace').bundle, ['google-workspace']);
  assert.strictEqual(C.toHash(C.emptyState()), '');
  assert.deepStrictEqual(C.parseHash('#unknown=1&q='), C.emptyState());
});
