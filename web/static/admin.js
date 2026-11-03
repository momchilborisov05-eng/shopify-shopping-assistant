/* Team dashboard for the AI shopping assistant.
   Vanilla JS, CSP-safe: no inline handlers, every value inserted as text. */
(function () {
  'use strict';

  var TOKEN_KEY = 'shop-assistant-admin-token';
  var REFRESH_MS = 30000;

  var TOOL_NAMES = {
    search_products: 'Product searches',
    get_product: 'Product details',
    get_store_policy: 'Policy answers',
    lookup_order: 'Order lookups',
    handoff_to_human: 'Handoffs to the team'
  };
  var LANGS = { en: '🇬🇧', bg: '🇧🇬', de: '🇩🇪', fr: '🇫🇷', es: '🇪🇸', it: '🇮🇹', ro: '🇷🇴', el: '🇬🇷', tr: '🇹🇷', ru: '🇷🇺' };

  var state = {
    info: null,
    days: 30,
    ticketStatus: 'open',
    openConversation: null,
    openMessageCount: -1,
    lastLoad: 0,
    loading: false,
    gated: false
  };

  // ---------- helpers ----------

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else node.setAttribute(key, value === true ? '' : String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function usd(v, digits) {
    if (!isNum(v)) return '—';
    return '$' + Number(v).toFixed(digits);
  }

  function humanTopic(topic) {
    var s = String(topic || '').replace(/[_-]+/g, ' ').trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
  }

  function humanTool(name) {
    return TOOL_NAMES[name] || humanTopic(name);
  }

  function parseDate(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function relTime(iso) {
    var d = parseDate(iso);
    if (!d) return '—';
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 45) return 'just now';
    var m = Math.round(s / 60);
    if (m < 60) return m + ' min ago';
    var h = Math.round(m / 60);
    if (h < 24) return h + ' h ago';
    var days = Math.round(h / 24);
    if (days < 7) return days + (days === 1 ? ' day ago' : ' days ago');
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  }

  function fullTime(iso) {
    var d = parseDate(iso);
    return d ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';
  }

  function clock(iso) {
    var d = parseDate(iso);
    return d ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
  }

  function truncate(text, n) {
    var s = String(text || '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function showAlert(message) {
    var a = $('alert');
    if (!message) { a.hidden = true; a.textContent = ''; return; }
    a.textContent = message;
    a.hidden = false;
  }

  // ---------- token + API ----------

  function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return state.memToken || ''; }
  }

  function setToken(token) {
    state.memToken = token;
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token); else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* storage blocked: keep it in memory */ }
  }

  function AuthError() { this.name = 'AuthError'; this.message = 'Admin token required'; }
  AuthError.prototype = Object.create(Error.prototype);

  function api(path, options) {
    options = options || {};
    var headers = { 'Accept': 'application/json' };
    var token = getToken();
    if (token && path.indexOf('/api/admin/') === 0) headers['Authorization'] = 'Bearer ' + token;
    return fetch(path, { method: options.method || 'GET', headers: headers, cache: 'no-store' }).then(function (res) {
      if (res.status === 401) {
        showGate(!!token);
        throw new AuthError();
      }
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error((body && body.error) || ('Request failed (HTTP ' + res.status + ')'));
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    });
  }

  function showGate(rejected) {
    state.gated = true;
    $('gate').hidden = false;
    $('gate-error').hidden = !rejected;
    var input = $('token');
    input.value = '';
    setTimeout(function () { input.focus(); }, 30);
  }

  function hideGate() {
    state.gated = false;
    $('gate').hidden = true;
  }

  function initGate() {
    $('gate-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var token = $('token').value.trim();
      if (!token) return;
      setToken(token);
      hideGate();
      loadAll(true);
    });
  }

  // ---------- header / info ----------

  function renderInfo(info) {
    var store = info.store || 'Store';
    $('title').textContent = store + ' · AI assistant';
    document.title = store + ' · AI assistant dashboard';
    var model = $('model');
    model.hidden = false;
    if (info.demo) {
      model.textContent = 'demo mode';
      model.classList.add('is-demo');
      model.title = 'Answers come from a scripted demo model, not the live API';
    } else {
      model.textContent = info.model || 'model';
      model.classList.remove('is-demo');
      model.removeAttribute('title');
    }
    $('store-link').hidden = !info.demo_store;
  }

  function renderUpdated() {
    var u = $('updated');
    if (!state.lastLoad) { u.textContent = ''; return; }
    u.textContent = 'Updated ' + new Date(state.lastLoad).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
      ' · refreshes every 30 s';
  }

  // ---------- KPIs ----------

  function setKpi(key, value, sub, warn) {
    var v = document.querySelector('[data-kpi="' + key + '"]');
    var s = document.querySelector('[data-kpi-sub="' + key + '"]');
    if (v) { v.textContent = value; v.classList.toggle('is-warn', !!warn); }
    if (s) s.textContent = sub || '';
  }

  function renderStats(st) {
    var days = st.days || state.days;
    var conv = isNum(st.conversations) ? Number(st.conversations) : null;
    setKpi('conversations', conv === null ? '—' : conv.toLocaleString(),
      isNum(st.messages) ? Number(st.messages).toLocaleString() + ' shopper messages' : '');
    setKpi('resolved', isNum(st.resolved_by_ai_pct) ? Number(st.resolved_by_ai_pct).toFixed(1).replace(/\.0$/, '') + '%' : '—',
      isNum(st.handed_off) ? st.handed_off + ' handed to the team' : '');
    var open = isNum(st.open_tickets) ? Number(st.open_tickets) : null;
    setKpi('open', open === null ? '—' : String(open), open ? 'waiting for a reply' : 'all caught up', open > 0);
    setKpi('cpc', usd(st.cost_per_conversation_usd, 4), 'estimated from tokens');
    var rated = isNum(st.rated) ? Number(st.rated) : 0;
    setKpi('rating', isNum(st.avg_rating) && rated ? Number(st.avg_rating).toFixed(1) + ' / 5' : '—',
      rated ? rated + (rated === 1 ? ' rating' : ' ratings') : 'no ratings yet');
    var cost = isNum(st.cost_usd) ? Number(st.cost_usd) : null;
    setKpi('cost', cost === null ? '—' : usd(cost, cost < 1 ? 4 : 2), 'last ' + days + ' days');

    renderBars($('bars-searches'), (st.top_searches || []).map(function (r) { return { name: r.q, n: r.n }; }),
      'Searches the assistant runs for shoppers will appear here.');
    renderBars($('bars-policies'), (st.policy_topics || []).map(function (r) { return { name: humanTopic(r.topic), n: r.n }; }),
      'Questions about delivery, returns, sizes and payment will appear here.');
    renderBars($('bars-tools'), (st.tools || []).map(function (r) { return { name: humanTool(r.name), n: r.n }; }),
      'What the assistant did — searches, order lookups, handoffs — will appear here.');
  }

  function renderBars(list, rows, emptyText) {
    list.textContent = '';
    rows = rows.filter(function (r) { return r && r.name && isNum(r.n); });
    if (!rows.length) {
      list.appendChild(el('li', { class: 'bars__empty', text: emptyText }));
      return;
    }
    var max = Math.max.apply(null, rows.map(function (r) { return Number(r.n); })) || 1;
    rows.forEach(function (r) {
      var fill = el('div', { class: 'bar__fill' });
      fill.style.setProperty('--w', Math.max(2, Number(r.n) / max * 100).toFixed(1) + '%');
      list.appendChild(el('li', { class: 'bar' }, [
        el('div', { class: 'bar__row' }, [
          el('span', { class: 'bar__name', title: String(r.name), text: String(r.name) }),
          el('span', { class: 'bar__n', text: Number(r.n).toLocaleString() })
        ]),
        el('div', { class: 'bar__track', 'aria-hidden': 'true' }, [fill])
      ]));
    });
  }

  // ---------- tickets ----------

  function renderTickets(tickets) {
    var body = $('tickets');
    body.textContent = '';
    var empty = $('tickets-empty');
    $('tickets-table').hidden = !tickets.length;
    if (!tickets.length) {
      empty.textContent = '';
      var title = state.ticketStatus === 'open' ? 'No open tickets' : 'No tickets yet';
      empty.appendChild(el('strong', { text: title }));
      empty.appendChild(document.createTextNode(
        'When the assistant can’t help — a refund, a damaged item, or a shopper asking for a person — it opens a ticket here with the reason and the shopper’s email.'));
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    tickets.forEach(function (t) {
      var urgent = t.priority === 'urgent';
      var open = t.status === 'open';
      var view = el('button', { class: 'link-btn', type: 'button', text: 'View chat' });
      view.addEventListener('click', function () { openConversation(t.conversation_id); });
      var actions = el('div', { class: 'actions' }, [view]);
      if (open) {
        var close = el('button', { class: 'btn btn--ghost btn--small', type: 'button', text: 'Close' });
        close.addEventListener('click', function () { closeTicket(t.id, close); });
        actions.appendChild(close);
      }
      var email = t.email ? el('a', { href: 'mailto:' + t.email, text: t.email }) : el('span', { class: 'muted', text: '—' });
      body.appendChild(el('tr', { class: urgent && open ? 'is-urgent' : '' }, [
        el('td', { class: 'nowrap muted', text: '#' + t.id }),
        el('td', { class: 'nowrap', title: fullTime(t.created_at), text: relTime(t.created_at) }),
        el('td', { class: 'cell-reason', text: t.reason || '—' }),
        el('td', { class: 'cell-email' }, [email]),
        el('td', null, [el('span', { class: 'tag' + (urgent ? ' tag--urgent' : ''), text: urgent ? 'Urgent' : 'Normal' })]),
        el('td', null, [el('span', { class: 'tag' + (open ? ' tag--open' : ''), text: open ? 'Open' : 'Closed' })]),
        el('td', null, [actions])
      ]));
    });
  }

  function closeTicket(id, button) {
    button.disabled = true;
    button.textContent = 'Closing…';
    api('/api/admin/tickets/' + encodeURIComponent(id) + '/close', { method: 'POST' }).then(function () {
      return loadAll(false);
    }).catch(function (err) {
      if (err instanceof AuthError) return;
      button.disabled = false;
      button.textContent = 'Close';
      showAlert('Could not close the ticket: ' + err.message);
    });
  }

  function initTicketFilter() {
    var buttons = document.querySelectorAll('.seg__btn');
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener('click', function () {
        state.ticketStatus = b.getAttribute('data-status') || '';
        Array.prototype.forEach.call(buttons, function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        loadTickets().catch(handleError);
      });
    });
  }

  function loadTickets() {
    var q = state.ticketStatus ? '?status=' + encodeURIComponent(state.ticketStatus) : '';
    return api('/api/admin/tickets' + q).then(function (data) {
      renderTickets(Array.isArray(data.tickets) ? data.tickets : []);
    });
  }

  // ---------- conversations ----------

  function langCell(code) {
    if (!code) return el('span', { class: 'muted', text: '—' });
    var c = String(code).toLowerCase();
    return el('span', { class: 'lang', title: c }, [(LANGS[c] ? LANGS[c] + ' ' : '') + c.toUpperCase()]);
  }

  function ratingCell(r) {
    if (!isNum(r)) return el('span', { class: 'muted', text: '—' });
    r = Number(r);
    if (r >= 4) return el('span', { class: 'rate', role: 'img', 'aria-label': 'Helpful', title: 'Rated ' + r + '/5', text: '👍' });
    if (r <= 2) return el('span', { class: 'rate', role: 'img', 'aria-label': 'Not helpful', title: 'Rated ' + r + '/5', text: '👎' });
    return el('span', { text: r + '/5' });
  }

  function renderConversations(convs) {
    var body = $('convs');
    body.textContent = '';
    var empty = $('convs-empty');
    $('convs-table').hidden = !convs.length;
    if (!convs.length) {
      empty.textContent = '';
      empty.appendChild(el('strong', { text: 'No conversations yet' }));
      if (state.info && state.info.demo_store) {
        empty.appendChild(document.createTextNode('Open the '));
        empty.appendChild(el('a', { href: '/', text: 'store' }));
        empty.appendChild(document.createTextNode(' and ask the assistant something — the chat shows up here within seconds.'));
      } else {
        empty.appendChild(document.createTextNode('Once shoppers chat with the assistant on your store, every conversation shows up here.'));
      }
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    convs.forEach(function (c) {
      var tr = el('tr', { tabindex: '0', 'data-id': c.id, class: c.id === state.openConversation ? 'is-active' : '' }, [
        el('td', { class: 'nowrap', title: fullTime(c.updated_at || c.created_at), text: relTime(c.updated_at || c.created_at) }),
        el('td', { class: 'cell-question', title: c.first_question || '', text: truncate(c.first_question || '(no question)', 90) }),
        el('td', null, [langCell(c.language)]),
        el('td', { class: 'num', text: isNum(c.turns) ? String(c.turns) : '—' }),
        el('td', { class: 'num', text: usd(c.cost_usd, 4) }),
        el('td', null, [ratingCell(c.rating)]),
        el('td', null, [Number(c.tickets) > 0
          ? el('span', { class: 'tag tag--handoff', text: Number(c.tickets) > 1 ? 'Handed off ×' + c.tickets : 'Handed off' })
          : el('span', { class: 'muted', text: '—' })])
      ]);
      tr.setAttribute('aria-label', 'Open conversation: ' + truncate(c.first_question || '', 60));
      body.appendChild(tr);
    });
  }

  function initConversationTable() {
    var body = $('convs');
    body.addEventListener('click', function (e) {
      var tr = e.target.closest('tr[data-id]');
      if (tr) openConversation(tr.getAttribute('data-id'));
    });
    body.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var tr = e.target.closest('tr[data-id]');
      if (!tr) return;
      e.preventDefault();
      openConversation(tr.getAttribute('data-id'));
    });
  }

  function markActiveRow() {
    Array.prototype.forEach.call(document.querySelectorAll('#convs tr[data-id]'), function (tr) {
      tr.classList.toggle('is-active', tr.getAttribute('data-id') === state.openConversation);
    });
  }

  // ---------- transcript ----------

  var SAFE_URL = /^(https?:\/\/|\/(?!\/)|mailto:)/i;

  // Renders **bold** and [text](url); everything else stays plain text with line breaks.
  function richText(text, container) {
    var lines = String(text || '').split('\n');
    var pattern = /\*\*([^*]+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
    lines.forEach(function (line, i) {
      if (i > 0) container.appendChild(document.createElement('br'));
      var last = 0;
      var m;
      pattern.lastIndex = 0;
      while ((m = pattern.exec(line)) !== null) {
        if (m.index > last) container.appendChild(document.createTextNode(line.slice(last, m.index)));
        if (m[1] !== undefined) {
          container.appendChild(el('strong', { text: m[1] }));
        } else if (SAFE_URL.test(m[3])) {
          container.appendChild(el('a', { href: m[3], target: '_blank', rel: 'noopener noreferrer', text: m[2] }));
        } else {
          container.appendChild(document.createTextNode(m[2] + ' (' + m[3] + ')'));
        }
        last = pattern.lastIndex;
      }
      if (last < line.length) container.appendChild(document.createTextNode(line.slice(last)));
    });
  }

  function money(amount, currency) {
    if (!isNum(amount)) return '';
    try {
      return new Intl.NumberFormat('en-IE', { style: 'currency', currency: currency || 'EUR' }).format(Number(amount));
    } catch (e) {
      return Number(amount).toFixed(2) + ' ' + (currency || '');
    }
  }

  function renderTranscript(conv) {
    var meta = $('side-meta');
    meta.textContent = '';
    var bits = [
      fullTime(conv.created_at),
      conv.language ? String(conv.language).toUpperCase() : null,
      isNum(conv.turns) ? conv.turns + (Number(conv.turns) === 1 ? ' message' : ' messages') : null,
      isNum(conv.cost_usd) ? usd(conv.cost_usd, 4) : null,
      isNum(conv.rating) ? (Number(conv.rating) >= 4 ? '👍 helpful' : Number(conv.rating) <= 2 ? '👎 not helpful' : conv.rating + '/5') : null
    ];
    bits.forEach(function (b) { if (b) meta.appendChild(el('span', { text: b })); });
    if (conv.page) {
      var page = String(conv.page);
      meta.appendChild(SAFE_URL.test(page)
        ? el('a', { href: page, target: '_blank', rel: 'noopener noreferrer', text: truncate(page.replace(/^https?:\/\/[^/]+/, ''), 48) || '/' })
        : el('span', { text: truncate(page, 48) }));
    }

    var tickets = $('side-tickets');
    tickets.textContent = '';
    (conv.tickets || []).forEach(function (t) {
      var open = t.status === 'open';
      var row = el('div', { class: 'side-ticket' }, [
        el('p', null, [
          el('strong', { text: (t.priority === 'urgent' ? 'Urgent ticket' : 'Ticket') + ' #' + t.id + ' · ' + (open ? 'open' : 'closed') }),
          el('br'),
          String(t.reason || ''),
          t.email ? ' · ' + t.email : ''
        ])
      ]);
      if (open) {
        var btn = el('button', { class: 'btn btn--ghost btn--small', type: 'button', text: 'Close' });
        btn.addEventListener('click', function () {
          btn.disabled = true;
          api('/api/admin/tickets/' + encodeURIComponent(t.id) + '/close', { method: 'POST' }).then(function () {
            state.openMessageCount = -1;
            return Promise.all([loadAll(false), loadTranscript(conv.id || state.openConversation, true)]);
          }).catch(function (err) {
            btn.disabled = false;
            if (!(err instanceof AuthError)) showAlert('Could not close the ticket: ' + err.message);
          });
        });
        row.appendChild(btn);
      }
      tickets.appendChild(row);
    });

    var body = $('side-body');
    body.textContent = '';
    var msgs = conv.messages || [];
    if (!msgs.length) {
      body.appendChild(el('p', { class: 'loading', text: 'This conversation has no messages yet.' }));
      return;
    }
    msgs.forEach(function (m) {
      var isUser = m.role === 'user';
      var bubble = el('div', { class: 'bubble' });
      if (isUser) plainText(m.text, bubble);
      else richText(m.text, bubble);
      var wrap = el('div', { class: 'msg ' + (isUser ? 'msg--user' : 'msg--assistant') }, [bubble]);
      if (!isUser) {
        var chips = el('div', { class: 'chips' });
        (m.tools || []).forEach(function (t) {
          var name = typeof t === 'string' ? t : t && t.name;
          if (!name) return;
          var err = t && t.error;
          chips.appendChild(el('span', { class: 'chip chip--tool' + (err ? ' chip--error' : ''),
            title: err ? 'This tool call failed' : name, text: humanTool(name) + (err ? ' (failed)' : '') }));
        });
        (m.products || []).forEach(function (p) {
          if (!p || !p.title) return;
          var price = money(p.price, p.currency);
          chips.appendChild(el('span', { class: 'chip chip--product', text: p.title + (price ? ' · ' + price : '') }));
        });
        if (chips.childNodes.length) wrap.appendChild(chips);
      }
      var time = clock(m.created_at);
      if (time) wrap.appendChild(el('span', { class: 'msg__time', title: fullTime(m.created_at), text: (isUser ? 'Shopper' : 'Assistant') + ' · ' + time }));
      body.appendChild(wrap);
    });
  }

  function plainText(text, container) {
    String(text || '').split('\n').forEach(function (line, i) {
      if (i > 0) container.appendChild(document.createElement('br'));
      container.appendChild(document.createTextNode(line));
    });
  }

  function loadTranscript(id, quiet) {
    return api('/api/admin/conversations/' + encodeURIComponent(id)).then(function (conv) {
      if (state.openConversation !== id) return;
      var count = (conv.messages || []).length + ':' + (conv.tickets || []).map(function (t) { return t.status; }).join(',');
      if (quiet && count === state.openMessageCount) return; // nothing new: keep the scroll position
      var body = $('side-body');
      var atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
      var first = state.openMessageCount === -1;
      state.openMessageCount = count;
      renderTranscript(conv);
      if (first || atBottom) body.scrollTop = first ? 0 : body.scrollHeight;
    }).catch(function (err) {
      if (err instanceof AuthError || state.openConversation !== id) return;
      var body = $('side-body');
      body.textContent = '';
      body.appendChild(el('p', { class: 'loading', text: err.status === 404 ? 'This conversation no longer exists.' : 'Could not load the conversation: ' + err.message }));
    });
  }

  var lastFocus = null;
  function openConversation(id) {
    if (!id) return;
    var side = $('side');
    var scrim = $('scrim');
    if (!side.classList.contains('is-open')) lastFocus = document.activeElement;
    state.openConversation = id;
    state.openMessageCount = -1;
    markActiveRow();
    $('side-meta').textContent = '';
    $('side-tickets').textContent = '';
    var body = $('side-body');
    body.textContent = '';
    body.appendChild(el('p', { class: 'loading', text: 'Loading the conversation…' }));
    scrim.hidden = false;
    requestAnimationFrame(function () { scrim.classList.add('is-open'); side.classList.add('is-open'); });
    side.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    setTimeout(function () { $('side-close').focus(); }, 60);
    try { history.replaceState(null, '', '#c=' + encodeURIComponent(id)); } catch (e) { /* ignore */ }
    loadTranscript(id, false);
  }

  function closeConversation() {
    var side = $('side');
    var scrim = $('scrim');
    state.openConversation = null;
    markActiveRow();
    side.classList.remove('is-open');
    scrim.classList.remove('is-open');
    side.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
    setTimeout(function () { if (!side.classList.contains('is-open')) scrim.hidden = true; }, 300);
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* ignore */ }
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
    lastFocus = null;
  }

  function initSide() {
    $('side-close').addEventListener('click', closeConversation);
    $('scrim').addEventListener('click', closeConversation);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && state.openConversation) closeConversation();
    });
  }

  // ---------- loading ----------

  function handleError(err) {
    if (err instanceof AuthError) return;
    showAlert('Could not load the dashboard: ' + (err && err.message ? err.message : 'network error') + '. Retrying in 30 s.');
  }

  function loadAll(spin) {
    if (state.gated) return Promise.resolve();
    if (state.loading) return state.loading;
    var btn = $('refresh');
    if (spin) btn.classList.add('is-spinning');
    state.loading = Promise.all([
      api('/api/admin/stats?days=' + state.days).then(renderStats),
      loadTickets(),
      api('/api/admin/conversations').then(function (data) {
        renderConversations(Array.isArray(data.conversations) ? data.conversations : []);
      })
    ]).then(function () {
      state.lastLoad = Date.now();
      renderUpdated();
      showAlert('');
      if (state.openConversation) return loadTranscript(state.openConversation, true);
    }).catch(handleError).then(function () {
      state.loading = false;
      btn.classList.remove('is-spinning');
    });
    return state.loading;
  }

  function initControls() {
    var period = $('period');
    try {
      var saved = sessionStorage.getItem('shop-assistant-admin-days');
      if (saved && /^(7|30|90)$/.test(saved)) { period.value = saved; state.days = Number(saved); }
    } catch (e) { /* ignore */ }
    period.addEventListener('change', function () {
      state.days = Number(period.value) || 30;
      try { sessionStorage.setItem('shop-assistant-admin-days', String(state.days)); } catch (e) { /* ignore */ }
      api('/api/admin/stats?days=' + state.days).then(renderStats).catch(handleError);
    });
    $('refresh').addEventListener('click', function () { loadAll(true); });

    setInterval(function () {
      if (document.visibilityState === 'visible' && !state.gated) loadAll(false);
      renderUpdated();
    }, REFRESH_MS);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && Date.now() - state.lastLoad > REFRESH_MS) loadAll(false);
    });
  }

  function boot() {
    initGate();
    initTicketFilter();
    initConversationTable();
    initSide();
    initControls();

    api('/api/info').then(function (info) {
      state.info = info;
      renderInfo(info);
      if (info.admin_protected && !getToken()) {
        showGate(false);
        return;
      }
      return loadAll(true).then(function () {
        var m = location.hash.match(/^#c=([\w-]+)$/);
        if (m) openConversation(m[1]);
      });
    }).catch(function (err) {
      showAlert('The assistant server is not reachable: ' + (err && err.message ? err.message : 'network error'));
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
