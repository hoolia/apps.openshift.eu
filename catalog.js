// Chart list of the website: index.yaml (released charts) merged with catalog.json
// (type, category and vendor from Chart.yaml on main), facets and the URL hash.
(function (root) {
  'use strict';

  var TYPES = { app: 'App', 'mcp-server': 'MCP server', 'mcp-bundle': 'MCP bundle' };
  var TYPE_ORDER = ['app', 'mcp-server', 'mcp-bundle'];
  var FACETS = [
    { key: 'type', title: 'Type' },
    { key: 'category', title: 'Category' },
    { key: 'vendor', title: 'Vendor' },
    { key: 'bundle', title: 'MCP bundle' }
  ];
  // links of the first version of the site
  var LEGACY_TYPE = { server: 'mcp-server', bundle: 'mcp-bundle' };

  function unquote(v) {
    v = v.trim();
    if (/^".*"$/.test(v)) {
      try { return JSON.parse(v); } catch (e) { return v.slice(1, -1); }
    }
    if (/^'.*'$/.test(v)) return v.slice(1, -1).replace(/''/g, "'");
    return v;
  }

  // the latest version of every entry in a Helm repository index.yaml
  function parseIndex(text) {
    var charts = [], cur = null, version = 0, section = '', last = '';
    text.split('\n').forEach(function (l) {
      var m = /^  ([A-Za-z0-9._-]+):\s*$/.exec(l);
      if (m) {
        cur = { id: m[1], keywords: [], annotations: {} };
        charts.push(cur); version = 0; section = ''; last = '';
        return;
      }
      if (!cur || /^\S/.test(l)) { if (/^\S/.test(l)) cur = null; return; }
      if (/^  - /.test(l)) { version++; l = '    ' + l.slice(4); section = ''; last = ''; }
      if (version !== 1) return;
      if ((m = /^    ([A-Za-z0-9_]+):\s*(.*)$/.exec(l))) {
        section = m[2] === '' ? m[1] : '';
        last = m[2] === '' ? '' : m[1];
        if (!section) cur[m[1]] = unquote(m[2]);
        return;
      }
      if (section === 'keywords' && (m = /^    - (.*)$/.exec(l))) { cur.keywords.push(unquote(m[1])); return; }
      if (section === 'annotations' && (m = /^      ([^:\s]+):\s*(.*)$/.exec(l))) {
        cur.annotations[m[1]] = unquote(m[2]); return;
      }
      // continuation of a folded scalar such as a long description
      if (last && /^      \S/.test(l) && typeof cur[last] === 'string') cur[last] += ' ' + l.trim();
    });
    return charts;
  }

  function typeOf(c) {
    var a = c.annotations, k = c.keywords;
    if (a['mcp.openshift.eu/server'] || k.indexOf('mcp-server') >= 0) return 'mcp-server';
    if (/^mcp-bundle-/.test(c.id) || c.id === 'mcp-all' || k.indexOf('mcp-bundle') >= 0) return 'mcp-bundle';
    return 'app';
  }

  // catalog.json wins; a chart missing from it keeps what index.yaml carries
  function merge(charts, catalog) {
    var cat = (catalog && catalog.charts) || {};
    return charts.map(function (c) {
      var a = c.annotations, e = cat[c.id] || {};
      var out = {
        id: c.id,
        name: a['charts.openshift.io/name'] || c.id,
        provider: a['charts.openshift.io/provider'] || '',
        description: c.description || '',
        version: c.version || '',
        appVersion: c.appVersion || '',
        icon: c.icon || '',
        keywords: c.keywords,
        type: e.type || typeOf(c),
        category: e.category || a['apps.openshift.eu/category'] || a['mcp.openshift.eu/category'] || '',
        vendor: e.vendor || a['apps.openshift.eu/vendor'] || '',
        bundle: e.bundle || a['mcp.openshift.eu/bundle'] || ''
      };
      out.text = [out.id, out.name, out.description, out.category, out.vendor, out.bundle]
        .concat(out.keywords).join(' ').toLowerCase();
      return out;
    });
  }

  function emptyState() { return { q: '', type: [], category: [], vendor: [], bundle: [] }; }

  function parseHash(hash) {
    var s = emptyState();
    (hash || '').replace(/^#/, '').split('&').forEach(function (p) {
      if (!p) return;
      var i = p.indexOf('='), k = decodeURIComponent(i < 0 ? p : p.slice(0, i));
      var v = i < 0 ? '' : decodeURIComponent(p.slice(i + 1).replace(/\+/g, ' '));
      if (k === 'q') s.q = v;
      else if (k in s && v) {
        s[k] = v.split(',').filter(Boolean).map(function (x) { return k === 'type' && LEGACY_TYPE[x] || x; });
      }
    });
    return s;
  }

  function toHash(s) {
    var parts = [];
    if (s.q) parts.push('q=' + encodeURIComponent(s.q));
    FACETS.forEach(function (f) {
      if (s[f.key].length) parts.push(f.key + '=' + s[f.key].map(encodeURIComponent).join(','));
    });
    return parts.length ? '#' + parts.join('&') : '';
  }

  // a chart matches when it matches the search and every facet, except the one skipped
  function matches(c, s, skip) {
    if (s.q && c.text.indexOf(s.q.toLowerCase()) < 0) return false;
    return FACETS.every(function (f) {
      return f.key === skip || !s[f.key].length || s[f.key].indexOf(c[f.key]) >= 0;
    });
  }

  function filter(charts, s) {
    return charts.filter(function (c) { return matches(c, s); }).sort(function (a, b) {
      return TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type) || a.name.localeCompare(b.name);
    });
  }

  // values of each facet with the number of charts a click would show
  function facets(charts, s) {
    return FACETS.map(function (f) {
      var values = [];
      charts.forEach(function (c) { if (c[f.key] && values.indexOf(c[f.key]) < 0) values.push(c[f.key]); });
      values.sort(function (a, b) {
        return f.key === 'type' ? TYPE_ORDER.indexOf(a) - TYPE_ORDER.indexOf(b) : a.localeCompare(b);
      });
      var base = charts.filter(function (c) { return matches(c, s, f.key); });
      return {
        key: f.key, title: f.title,
        values: values.map(function (v) {
          return {
            value: v,
            label: f.key === 'type' ? TYPES[v] || v : v,
            count: base.filter(function (c) { return c[f.key] === v; }).length,
            selected: s[f.key].indexOf(v) >= 0
          };
        })
      };
    }).filter(function (f) { return f.values.length; });
  }

  var api = { TYPES: TYPES, FACETS: FACETS, parseIndex: parseIndex, merge: merge, emptyState: emptyState,
    parseHash: parseHash, toHash: toHash, filter: filter, facets: facets };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Catalog = api;
})(this);
