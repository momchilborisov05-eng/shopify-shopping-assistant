/* Nordvik Outdoor demo storefront: catalog grid, product page and cart.
   Vanilla JS, CSP-safe (no inline handlers, all data inserted as text). */
(function () {
  'use strict';

  var CART_KEY = 'nordvik-cart-v1';
  var FREE_DELIVERY = 60;
  var COLORS = {
    'black': '#1d1f1e', 'cream': '#ece4d2', 'ember orange': '#d4692e', 'ember red': '#b23a2a',
    'forest green': '#1f3d2b', 'graphite': '#3e4246', 'navy': '#1f2d4a', 'olive': '#6b6d3a',
    'rust': '#9c4a25', 'sky blue': '#86b3d6', 'steel': '#a7adb2', 'stone grey': '#9b968c',
    'white': '#f7f7f5', 'grey': '#8d918e', 'gray': '#8d918e', 'blue': '#2e5d8c', 'red': '#b8322a', 'green': '#3e6b48'
  };

  var state = {
    products: [],
    categories: [],
    currency: 'EUR',
    category: '',
    query: '',
    cart: [],
    product: null
  };

  // ---------- small helpers ----------

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key.indexOf('on') === 0 && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? '' : String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function svg(paths, viewBox) {
    var s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', viewBox || '0 0 24 24');
    s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (p) {
      var node = document.createElementNS(SVG_NS, p[0]);
      Object.keys(p[1]).forEach(function (k) { node.setAttribute(k, p[1][k]); });
      s.appendChild(node);
    });
    return s;
  }

  var fmtCache = {};
  function money(amount, currency) {
    currency = currency || state.currency || 'EUR';
    var n = Number(amount);
    if (!isFinite(n)) return '';
    try {
      if (!fmtCache[currency]) {
        fmtCache[currency] = new Intl.NumberFormat('en-IE', { style: 'currency', currency: currency });
      }
      return fmtCache[currency].format(n);
    } catch (e) {
      return n.toFixed(2) + ' ' + currency;
    }
  }

  function getJSON(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (res) {
      if (!res.ok) {
        var err = new Error('HTTP ' + res.status);
        err.status = res.status;
        throw err;
      }
      return res.json();
    });
  }

  // An image with a calm placeholder when the file is missing.
  function media(src, alt, extraClass) {
    var wrap = el('div', { class: 'media' + (extraClass ? ' ' + extraClass : '') });
    var ph = el('div', { class: 'media__ph', 'aria-hidden': 'true' }, [
      svg([
        ['path', { d: 'M2 19 9 8l4 6 2.5-3.5L22 19Z' }],
        ['circle', { cx: '17', cy: '6', r: '1.8' }]
      ]),
      el('span', { text: 'Nordvik' })
    ]);
    wrap.appendChild(ph);
    if (src) {
      var img = el('img', { alt: alt || '', loading: 'lazy', decoding: 'async' });
      img.addEventListener('error', function () { wrap.classList.add('is-missing'); });
      img.addEventListener('load', function () { ph.hidden = true; });
      img.src = src;
      wrap.appendChild(img);
    } else {
      wrap.classList.add('is-missing');
    }
    return wrap;
  }

  function stars(rating, reviews) {
    var r = Math.max(0, Math.min(5, Number(rating) || 0));
    var fill = el('span', { class: 'stars__fill' });
    fill.style.setProperty('--w', (r / 5 * 100).toFixed(1) + '%');
    var box = el('span', { class: 'rating' }, [
      el('span', { class: 'stars', role: 'img', 'aria-label': 'Rated ' + r.toFixed(1) + ' out of 5' }, [fill]),
      el('span', { text: r.toFixed(1) + (reviews ? ' (' + reviews + ')' : '') })
    ]);
    return box;
  }

  function priceBlock(p, cls) {
    var sale = p.compare_at_price && Number(p.compare_at_price) > Number(p.price);
    return el('div', { class: 'price' + (cls ? ' ' + cls : '') }, [
      el('span', { class: 'price__now' + (sale ? ' is-sale' : ''), text: money(p.price, p.currency) }),
      sale ? el('span', { class: 'price__was' }, [
        el('span', { class: 'sr-only', text: 'Regular price ' }),
        money(p.compare_at_price, p.currency)
      ]) : null
    ]);
  }

  // ---------- assistant bridge ----------

  function assistant() {
    var sa = window.ShopAssistant;
    return sa && typeof sa.ask === 'function' ? sa : null;
  }

  function ask(text) {
    var sa = assistant();
    if (sa) {
      try { sa.ask(text); } catch (e) { toast('The assistant could not open', true); }
    } else {
      toast('The assistant is still loading — try again in a moment', true);
    }
  }

  function onAssistantReady(cb) {
    if (assistant()) { cb(); return; }
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      if (assistant()) { clearInterval(timer); cb(); }
      else if (tries > 40) clearInterval(timer);
    }, 250);
  }

  document.addEventListener('click', function (e) {
    var target = e.target.closest ? e.target.closest('[data-ask]') : null;
    if (!target) return;
    e.preventDefault();
    ask(target.getAttribute('data-ask'));
  });

  // ---------- toast ----------

  var toastTimer = null;
  function toast(message, info) {
    var t = $('toast');
    t.textContent = message;
    t.classList.toggle('is-info', !!info);
    t.hidden = false;
    // next frame so the transition runs
    requestAnimationFrame(function () { t.classList.add('is-visible'); });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      t.classList.remove('is-visible');
      setTimeout(function () { if (!t.classList.contains('is-visible')) t.hidden = true; }, 300);
    }, 2600);
  }

  // ---------- cart ----------

  function loadCart() {
    try {
      var raw = localStorage.getItem(CART_KEY);
      var items = raw ? JSON.parse(raw) : [];
      state.cart = Array.isArray(items) ? items.filter(function (i) {
        return i && i.variantId && isFinite(Number(i.price)) && Number(i.qty) > 0;
      }) : [];
    } catch (e) {
      state.cart = [];
    }
  }

  function saveCart() {
    try { localStorage.setItem(CART_KEY, JSON.stringify(state.cart)); } catch (e) { /* private mode */ }
  }

  function cartCount() {
    return state.cart.reduce(function (n, i) { return n + Number(i.qty || 0); }, 0);
  }

  function cartSubtotal() {
    return state.cart.reduce(function (n, i) { return n + Number(i.price) * Number(i.qty); }, 0);
  }

  function addToCart(item, qty) {
    qty = Math.max(1, Math.min(20, parseInt(qty, 10) || 1));
    var id = String(item.variantId);
    var existing = null;
    state.cart.forEach(function (i) { if (String(i.variantId) === id) existing = i; });
    if (existing) {
      existing.qty = Math.min(20, existing.qty + qty);
    } else {
      state.cart.push({
        variantId: id,
        handle: String(item.handle || ''),
        title: String(item.title || 'Item'),
        variant: item.variant ? String(item.variant) : '',
        price: Number(item.price) || 0,
        currency: String(item.currency || state.currency || 'EUR'),
        image: item.image ? String(item.image) : (item.handle ? '/static/products/' + item.handle + '.jpg' : ''),
        qty: qty
      });
    }
    saveCart();
    renderCart(id);
    bumpBadge();
  }

  function setQty(variantId, qty) {
    state.cart = state.cart.map(function (i) {
      if (String(i.variantId) === String(variantId)) i.qty = Math.max(0, Math.min(20, qty));
      return i;
    }).filter(function (i) { return i.qty > 0; });
    saveCart();
    renderCart();
  }

  function bumpBadge() {
    var badge = $('cart-count');
    badge.classList.remove('is-bump');
    void badge.offsetWidth; // restart the animation
    badge.classList.add('is-bump');
  }

  function renderCart(newId) {
    var count = cartCount();
    var badge = $('cart-count');
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.hidden = count === 0;
    $('cart-open').setAttribute('aria-label', 'Open cart, ' + count + (count === 1 ? ' item' : ' items'));
    $('drawer-count').textContent = count ? '(' + count + ')' : '';

    var list = $('cart-list');
    list.textContent = '';
    state.cart.forEach(function (item) {
      var href = item.handle ? '/products/' + encodeURIComponent(item.handle) : null;
      var qtyOut = el('output', { text: String(item.qty), 'aria-live': 'polite' });
      var li = el('li', { class: String(item.variantId) === String(newId) ? 'is-new' : '' }, [
        media(item.image, ''),
        el('div', { class: 'cart-item' }, [
          href ? el('a', { class: 'cart-item__title', href: href, text: item.title })
               : el('span', { class: 'cart-item__title', text: item.title }),
          item.variant ? el('p', { class: 'cart-item__variant', text: item.variant }) : null,
          el('div', { class: 'cart-item__controls' }, [
            el('div', { class: 'qty' }, [
              el('button', { type: 'button', 'aria-label': 'Decrease quantity of ' + item.title, text: '−',
                onclick: function () { setQty(item.variantId, item.qty - 1); } }),
              qtyOut,
              el('button', { type: 'button', 'aria-label': 'Increase quantity of ' + item.title, text: '+',
                disabled: item.qty >= 20,
                onclick: function () { setQty(item.variantId, item.qty + 1); } })
            ]),
            el('button', { class: 'cart-item__remove', type: 'button', text: 'Remove',
              'aria-label': 'Remove ' + item.title,
              onclick: function () { setQty(item.variantId, 0); } })
          ])
        ]),
        el('p', { class: 'cart-item__price', text: money(item.price * item.qty, item.currency) })
      ]);
      list.appendChild(li);
    });
    $('cart-empty').hidden = state.cart.length > 0;

    var subtotal = cartSubtotal();
    var currency = state.cart.length ? state.cart[0].currency : state.currency;
    $('cart-subtotal').textContent = money(subtotal, currency);
    var note = $('cart-note');
    if (!state.cart.length) {
      note.textContent = 'Taxes included. Free delivery in Bulgaria over ' + money(FREE_DELIVERY, currency) + '.';
      note.classList.remove('is-free');
    } else if (subtotal >= FREE_DELIVERY) {
      note.textContent = 'Your order ships free in Bulgaria. Taxes included.';
      note.classList.add('is-free');
    } else {
      note.textContent = 'Add ' + money(FREE_DELIVERY - subtotal, currency) + ' more for free delivery in Bulgaria.';
      note.classList.remove('is-free');
    }
  }

  var drawerTimer = null;
  var lastFocus = null;
  function openCart(auto) {
    var drawer = $('cart');
    var overlay = $('cart-overlay');
    clearTimeout(drawerTimer);
    if (!auto) lastFocus = document.activeElement;
    overlay.hidden = false;
    requestAnimationFrame(function () {
      overlay.classList.add('is-open');
      drawer.classList.add('is-open');
    });
    drawer.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    if (!auto) setTimeout(function () { $('cart-close').focus(); }, 50);
  }

  function closeCart() {
    var drawer = $('cart');
    var overlay = $('cart-overlay');
    clearTimeout(drawerTimer);
    drawer.classList.remove('is-open');
    overlay.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
    setTimeout(function () { if (!drawer.classList.contains('is-open')) overlay.hidden = true; }, 350);
    if (lastFocus && lastFocus.focus) { lastFocus.focus(); lastFocus = null; }
  }

  function initCart() {
    loadCart();
    renderCart();
    $('cart-open').addEventListener('click', function () { openCart(false); });
    $('cart-close').addEventListener('click', closeCart);
    $('cart-overlay').addEventListener('click', closeCart);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $('cart').classList.contains('is-open')) closeCart();
    });
    // Keep tabs in sync.
    window.addEventListener('storage', function (e) {
      if (e.key === CART_KEY) { loadCart(); renderCart(); }
    });

    // The chat widget adds items with a DOM event.
    window.addEventListener('shop-assistant:add-to-cart', function (e) {
      var d = (e && e.detail) || {};
      if (!d.variantId) return;
      var product = findProduct(d.handle);
      var variant = d.variant || d.variantTitle || variantTitleFor(d.handle, d.variantId);
      addToCart({
        variantId: d.variantId,
        handle: d.handle,
        title: d.title || (product && product.title) || 'Item',
        variant: variant,
        price: d.price !== undefined && d.price !== null ? d.price : (product && product.price),
        currency: d.currency || (product && product.currency),
        image: d.image || (product && product.image)
      }, d.quantity || 1);
      toast('Added to cart: ' + (d.title || (product && product.title) || 'item'));
      // Peek the drawer open, then close it unless the shopper interacts.
      if (!$('cart').classList.contains('is-open')) {
        openCart(true);
        drawerTimer = setTimeout(function () {
          var drawer = $('cart');
          if (!drawer.matches(':hover') && !drawer.contains(document.activeElement)) closeCart();
        }, 2600);
      }
    });
  }

  function findProduct(handle) {
    if (!handle) return null;
    if (state.product && state.product.handle === handle) return state.product;
    var hit = null;
    state.products.forEach(function (p) { if (p.handle === handle) hit = p; });
    return hit;
  }

  function variantTitleFor(handle, variantId) {
    var p = state.product && state.product.handle === handle ? state.product : null;
    if (!p || !p.variants) return '';
    var v = p.variants.filter(function (x) { return String(x.id) === String(variantId); })[0];
    return v && v.title !== 'Default' ? v.title : '';
  }

  // ---------- catalog / navigation ----------

  function loadCatalog() {
    return getJSON('/api/catalog').then(function (data) {
      state.products = Array.isArray(data.products) ? data.products : [];
      state.categories = Array.isArray(data.categories) ? data.categories : [];
      if (state.products[0] && state.products[0].currency) state.currency = state.products[0].currency;
      renderNav();
      return data;
    });
  }

  function categoryHref(cat) {
    return cat ? '/?category=' + encodeURIComponent(cat) + '#shop' : '/#shop';
  }

  function renderNav() {
    var nav = $('catnav');
    nav.textContent = '';
    var cats = [''].concat(state.categories);
    cats.forEach(function (cat) {
      var a = el('a', {
        class: 'catnav__link' + (isHome() && cat === state.category ? ' is-active' : ''),
        href: categoryHref(cat),
        'data-category': cat,
        text: cat || 'All'
      });
      if (isHome() && cat === state.category) a.setAttribute('aria-current', 'page');
      nav.appendChild(el('li', null, [a]));
    });
    var foot = $('footer-cats');
    foot.textContent = '';
    state.categories.forEach(function (cat) {
      foot.appendChild(el('li', null, [el('a', { class: 'footer__link', href: categoryHref(cat), text: cat })]));
    });
  }

  function isHome() { return location.pathname === '/' || location.pathname === ''; }

  function setActiveNav() {
    var links = document.querySelectorAll('.catnav__link');
    Array.prototype.forEach.call(links, function (a) {
      var on = isHome() && a.getAttribute('data-category') === state.category;
      a.classList.toggle('is-active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  function syncUrl() {
    var params = new URLSearchParams();
    if (state.category) params.set('category', state.category);
    if (state.query) params.set('q', state.query);
    var qs = params.toString();
    try { history.replaceState(null, '', '/' + (qs ? '?' + qs : '') + location.hash); } catch (e) { /* ignore */ }
  }

  function initHeader() {
    var input = $('search');
    var form = $('search-form');
    var debounce = null;

    input.addEventListener('input', function () {
      if (!isHome()) return;
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        state.query = input.value.trim();
        renderGrid();
        syncUrl();
      }, 120);
    });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var q = input.value.trim();
      if (!isHome()) {
        location.href = '/?q=' + encodeURIComponent(q) + '#shop';
        return;
      }
      state.query = q;
      renderGrid();
      syncUrl();
      var shop = $('shop');
      if (shop) shop.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    $('catnav').addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('.catnav__link') : null;
      if (!a || !isHome()) return;
      e.preventDefault();
      state.category = a.getAttribute('data-category') || '';
      setActiveNav();
      renderGrid();
      syncUrl();
      $('shop').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  // ---------- home ----------

  function matches(p) {
    if (state.category && p.category !== state.category) return false;
    if (!state.query) return true;
    var hay = (p.title + ' ' + p.category + ' ' + p.handle.replace(/-/g, ' ')).toLowerCase();
    return state.query.toLowerCase().split(/\s+/).filter(Boolean).every(function (w) {
      if (hay.indexOf(w) >= 0) return true;
      // forgiving plural: "jackets" finds "jacket"
      return w.length > 3 && w.charAt(w.length - 1) === 's' && hay.indexOf(w.slice(0, -1)) >= 0;
    });
  }

  function card(p) {
    var sale = p.compare_at_price && Number(p.compare_at_price) > Number(p.price);
    var href = '/products/' + encodeURIComponent(p.handle);
    var m = media(p.image, p.title);
    var badges = el('div', { class: 'badges' }, [
      sale ? el('span', { class: 'badge badge--sale', text: 'Sale' }) : null,
      !p.in_stock ? el('span', { class: 'badge badge--soldout', text: 'Sold out' }) : null
    ]);
    m.appendChild(badges);

    var node = el('article', { class: 'card' + (p.in_stock ? '' : ' is-soldout') });
    var mediaBox = el('div', { class: 'card__media' }, [
      el('a', { href: href, tabindex: '-1', 'aria-hidden': 'true' }, [m])
    ]);
    if (p.in_stock && p.default_variant_id) {
      var quick = el('button', { class: 'quick-add', type: 'button', text: 'Quick add',
        'aria-label': 'Add ' + p.title + ' to cart' });
      quick.addEventListener('click', function () {
        addToCart({ variantId: p.default_variant_id, handle: p.handle, title: p.title, variant: '',
          price: p.price, currency: p.currency, image: p.image }, 1);
        toast('Added to cart: ' + p.title);
      });
      mediaBox.appendChild(quick);
    }
    node.appendChild(mediaBox);
    node.appendChild(el('a', { class: 'card__link', href: href }, [
      el('div', { class: 'card__body' }, [
        el('p', { class: 'card__cat', text: p.category }),
        el('h3', { class: 'card__title', text: p.title }),
        priceBlock(p),
        stars(p.rating, p.reviews)
      ])
    ]));
    return node;
  }

  function renderGrid() {
    var grid = $('grid');
    grid.setAttribute('aria-busy', 'false');
    grid.textContent = '';
    var shown = state.products.filter(matches);
    shown.forEach(function (p) { grid.appendChild(card(p)); });
    $('grid-empty').hidden = shown.length > 0;
    var title = state.category || 'All products';
    if (state.query) title = 'Results for “' + state.query + '”' + (state.category ? ' in ' + state.category : '');
    $('shop-title').textContent = title;
    $('shop-count').textContent = shown.length + (shown.length === 1 ? ' product' : ' products');
  }

  function skeletonGrid() {
    var grid = $('grid');
    grid.textContent = '';
    for (var i = 0; i < 8; i++) {
      grid.appendChild(el('div', { class: 'card skeleton', 'aria-hidden': 'true' }, [
        el('div', { class: 'media' }),
        el('div', { class: 'card__body' }, [
          el('div', { class: 'skeleton__line skeleton__line--short' }),
          el('div', { class: 'skeleton__line skeleton__line--mid' }),
          el('div', { class: 'skeleton__line skeleton__line--short' })
        ])
      ]));
    }
  }

  function showHome() {
    $('view-home').hidden = false;
    var params = new URLSearchParams(location.search);
    state.category = params.get('category') || '';
    state.query = (params.get('q') || '').slice(0, 80);
    $('search').value = state.query;
    skeletonGrid();

    $('hero-cta').addEventListener('click', function (e) {
      e.preventDefault();
      $('shop').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('clear-filters').addEventListener('click', function () {
      state.category = '';
      state.query = '';
      $('search').value = '';
      setActiveNav();
      renderGrid();
      syncUrl();
    });

    loadCatalog().then(function () {
      if (state.category && state.categories.indexOf(state.category) < 0) state.category = '';
      setActiveNav();
      renderGrid();
      if (location.hash === '#shop') {
        setTimeout(function () { $('shop').scrollIntoView({ block: 'start' }); }, 30);
      }
    }).catch(function () {
      var grid = $('grid');
      grid.textContent = '';
      grid.setAttribute('aria-busy', 'false');
      grid.appendChild(el('div', { class: 'error-box', text: 'The catalog could not be loaded. Please refresh the page.' }));
      $('shop-count').textContent = '';
    });
  }

  // ---------- product page ----------

  function stockClass(label) {
    if (/sold/i.test(label)) return 'stock--out';
    if (/only/i.test(label)) return 'stock--low';
    return 'stock--in';
  }

  function stockText(label) {
    var s = String(label || '');
    if (!s) return '';
    if (/only/i.test(s)) return s.charAt(0).toUpperCase() + s.slice(1) + ' — order soon';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function optionValues(product, name) {
    var seen = [];
    product.variants.forEach(function (v) {
      var val = v.options && v.options[name];
      if (val && seen.indexOf(val) < 0) seen.push(val);
    });
    return seen;
  }

  function findVariant(product, selected) {
    var names = product.options || [];
    return product.variants.filter(function (v) {
      return names.every(function (n) { return !v.options || v.options[n] === selected[n]; });
    })[0] || null;
  }

  // Is there an available variant with this value, keeping the other selected options?
  function isAvailable(product, selected, name, value) {
    var names = product.options || [];
    return product.variants.some(function (v) {
      if (!v.available || !v.options || v.options[name] !== value) return false;
      return names.every(function (n) { return n === name || !selected[n] || v.options[n] === selected[n]; });
    });
  }

  function existsWith(product, name, value) {
    return product.variants.some(function (v) { return v.options && v.options[name] === value; });
  }

  function renderProduct(p) {
    state.product = p;
    document.title = p.title + ' · Nordvik Outdoor';

    // breadcrumbs
    var crumbs = $('crumbs');
    crumbs.textContent = '';
    crumbs.appendChild(el('li', null, [el('a', { href: '/', text: 'Home' })]));
    crumbs.appendChild(el('li', null, [el('a', { href: categoryHref(p.category), text: p.category })]));
    crumbs.appendChild(el('li', null, [el('span', { 'aria-current': 'page', text: p.title })]));

    // media
    var mediaBox = $('pdp-media');
    var m = media(p.image, p.title, 'media--pdp');
    var sale = p.compare_at_price && Number(p.compare_at_price) > Number(p.price);
    if (sale || !p.in_stock) {
      m.appendChild(el('div', { class: 'badges' }, [
        sale ? el('span', { class: 'badge badge--sale', text: 'Sale' }) : null,
        !p.in_stock ? el('span', { class: 'badge badge--soldout', text: 'Sold out' }) : null
      ]));
    }
    mediaBox.replaceWith(m);
    m.id = 'pdp-media';

    // selection: first available variant, else first
    var start = p.variants.filter(function (v) { return v.available; })[0] || p.variants[0];
    var selected = {};
    (p.options || []).forEach(function (n) { selected[n] = start && start.options ? start.options[n] : null; });
    var qty = 1;

    var info = $('pdp-info');
    info.textContent = '';
    info.appendChild(el('p', { class: 'pdp__cat', text: p.category }));
    info.appendChild(el('h1', { class: 'pdp__title', text: p.title }));
    var rating = stars(p.rating, p.reviews);
    rating.classList.add('pdp__rating');
    rating.lastChild.textContent = Number(p.rating || 0).toFixed(1) + ' · ' + (p.reviews || 0) + ' reviews';
    info.appendChild(rating);

    var price = priceBlock(p, 'pdp__price');
    if (sale) {
      var pct = Math.round((1 - Number(p.price) / Number(p.compare_at_price)) * 100);
      price.appendChild(el('span', { class: 'badge badge--sale', text: '−' + pct + '%' }));
    }
    info.appendChild(price);
    info.appendChild(el('p', { class: 'pdp__tax', text: 'VAT included · Free delivery in Bulgaria over ' + money(FREE_DELIVERY, p.currency) }));

    if (p.description) {
      var first = String(p.description).match(/^[^.!?]+[.!?]/);
      info.appendChild(el('p', { class: 'pdp__lead', text: first ? first[0] : String(p.description) }));
    }

    var optionsBox = el('div', { class: 'pdp__options' });
    info.appendChild(optionsBox);
    var stock = el('p', { class: 'stock', 'aria-live': 'polite' });
    info.appendChild(stock);

    var qtyOut = el('output', { text: '1', 'aria-label': 'Quantity' });
    var minus = el('button', { type: 'button', text: '−', 'aria-label': 'Decrease quantity' });
    var plus = el('button', { type: 'button', text: '+', 'aria-label': 'Increase quantity' });
    var addBtn = el('button', { class: 'btn btn--primary', type: 'button', text: 'Add to cart' });
    info.appendChild(el('div', { class: 'buy' }, [el('div', { class: 'qty' }, [minus, qtyOut, plus]), addBtn]));

    var askBtn = el('button', { class: 'ask-btn', type: 'button', hidden: true }, [
      svg([['path', { d: 'M4 5h16v11H9l-5 4z' }], ['path', { d: 'M8 9.5h8M8 12.5h5' }]]),
      el('span', null, ['Ask about this product', el('small', { text: 'Sizing, fit, care — the assistant answers in seconds' })])
    ]);
    askBtn.addEventListener('click', function () {
      var hasSize = (p.options || []).indexOf('Size') >= 0;
      ask(hasSize ? 'Which size of ' + p.title + ' should I take?' : 'Tell me more about the ' + p.title + '. Is it right for me?');
    });
    info.appendChild(askBtn);
    onAssistantReady(function () { askBtn.hidden = false; });

    info.appendChild(el('ul', { class: 'perks' }, [
      el('li', null, [svg([['path', { d: 'M3 7h11v9H3zM14 10h4l3 3v3h-7z' }]]), 'Ships from Sofia in 1–2 working days']),
      el('li', null, [svg([['path', { d: 'M4 12a8 8 0 1 0 2.4-5.7' }], ['path', { d: 'M4 4v4h4' }]]), 'Free 30-day returns']),
      el('li', null, [svg([['path', { d: 'M12 3 5 6v6c0 4.2 3 7.7 7 9 4-1.3 7-4.8 7-9V6z' }]]), '2-year warranty and free seam repairs'])
    ]));

    function current() { return findVariant(p, selected); }

    function renderOptions() {
      optionsBox.textContent = '';
      (p.options || []).forEach(function (name) {
        var values = optionValues(p, name);
        if (!values.length) return;
        var isColor = /colou?r/i.test(name);
        var labelId = 'opt-' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        var list = el('div', { class: 'opt__list', role: 'group', 'aria-labelledby': labelId });
        values.forEach(function (value) {
          var available = isAvailable(p, selected, name, value);
          var exists = existsWith(p, name, value);
          var on = selected[name] === value;
          var btn;
          if (isColor) {
            var dot = el('span', { class: 'swatch__dot' });
            dot.style.setProperty('--swatch', COLORS[String(value).toLowerCase()] || '#b9b4a8');
            btn = el('button', { class: 'swatch' + (available ? '' : ' is-unavailable'), type: 'button',
              'aria-pressed': on ? 'true' : 'false',
              'aria-label': value + (available ? '' : ' (sold out)') }, [dot, el('span', { class: 'swatch__name', text: value })]);
          } else {
            btn = el('button', { class: 'size' + (available ? '' : ' is-unavailable'), type: 'button',
              'aria-pressed': on ? 'true' : 'false',
              'aria-label': name + ' ' + value + (available ? '' : ' (sold out)'), text: value });
          }
          btn.setAttribute('data-opt', name);
          btn.setAttribute('data-val', value);
          if (!exists) btn.disabled = true;
          btn.addEventListener('click', function () {
            selected[name] = value;
            // if this combination doesn't exist, move the other options to an available one
            if (!current()) {
              var withValue = p.variants.filter(function (v) { return v.options && v.options[name] === value; });
              var alt = withValue.filter(function (v) { return v.available; })[0] || withValue[0];
              if (alt) Object.keys(alt.options).forEach(function (k) { selected[k] = alt.options[k]; });
            }
            update();
            Array.prototype.forEach.call(optionsBox.querySelectorAll('button[data-opt]'), function (b) {
              if (b.getAttribute('data-opt') === name && b.getAttribute('data-val') === value) b.focus();
            });
          });
          list.appendChild(btn);
        });
        optionsBox.appendChild(el('fieldset', { class: 'opt' }, [
          el('legend', { class: 'opt__label', id: labelId }, [name + ':', el('span', { class: 'opt__value', text: selected[name] || 'Choose' })]),
          list
        ]));
      });
    }

    function update() {
      renderOptions();
      var v = current();
      var label = v ? v.stock : 'unavailable';
      stock.className = 'stock ' + (v ? stockClass(label) : 'stock--out');
      stock.textContent = v ? stockText(label) : 'This combination is not available';
      var ok = v && v.available;
      addBtn.disabled = !ok;
      addBtn.textContent = ok ? 'Add to cart · ' + money(Number(v.price || p.price) * qty, p.currency) : 'Sold out';
      minus.disabled = !ok || qty <= 1;
      plus.disabled = !ok || qty >= 10;
      qtyOut.textContent = String(qty);
    }

    minus.addEventListener('click', function () { qty = Math.max(1, qty - 1); update(); });
    plus.addEventListener('click', function () { qty = Math.min(10, qty + 1); update(); });
    addBtn.addEventListener('click', function () {
      var v = current();
      if (!v || !v.available) return;
      addToCart({ variantId: v.id, handle: p.handle, title: p.title, variant: v.title === 'Default' ? '' : v.title,
        price: v.price || p.price, currency: p.currency, image: p.image }, qty);
      toast('Added to cart: ' + p.title + (v.title && v.title !== 'Default' ? ' · ' + v.title : ''));
      openCart(false);
    });
    update();

    // details
    var details = $('pdp-details');
    details.textContent = '';
    var about = el('section', { class: 'detail' }, [el('h2', { class: 'detail__title', text: 'About this product' })]);
    String(p.description || '').split(/\n{2,}/).forEach(function (para) {
      if (para.trim()) about.appendChild(el('p', { text: para.trim() }));
    });
    details.appendChild(about);

    if (p.features && p.features.length) {
      var ul = el('ul', { class: 'features' });
      p.features.forEach(function (f) { ul.appendChild(el('li', { text: String(f) })); });
      details.appendChild(el('section', { class: 'detail' }, [el('h2', { class: 'detail__title', text: 'Features' }), ul]));
    }

    var side = el('section', { class: 'detail' }, [el('h2', { class: 'detail__title', text: 'Materials & care' })]);
    var dl = el('dl');
    if (p.materials) { dl.appendChild(el('dt', { text: 'Materials' })); dl.appendChild(el('dd', { text: p.materials })); }
    if (p.care) { dl.appendChild(el('dt', { text: 'Care' })); dl.appendChild(el('dd', { text: p.care })); }
    side.appendChild(dl);
    if (p.fit) {
      side.appendChild(el('div', { class: 'fit-note' }, [
        el('p', { class: 'detail__fit', text: 'Fit' }),
        el('p', { text: p.fit })
      ]));
    }
    details.appendChild(side);
    $('pdp').setAttribute('aria-busy', 'false');
  }

  function productSkeleton() {
    var info = $('pdp-info');
    info.textContent = '';
    info.appendChild(el('div', { class: 'pdp-skel', 'aria-hidden': 'true' }, [
      el('div', { class: 'skeleton__line skeleton__line--short' }),
      el('div', { class: 'skeleton__line skeleton__line--title' }),
      el('div', { class: 'skeleton__line skeleton__line--short' }),
      el('div', { class: 'skeleton__line skeleton__line--mid' }),
      el('div', { class: 'skeleton__line' }),
      el('div', { class: 'skeleton__line skeleton__line--mid' })
    ]));
    $('pdp-media').classList.add('skeleton');
  }

  function showMissing() {
    $('view-product').hidden = true;
    $('view-missing').hidden = false;
    document.title = 'Not found · Nordvik Outdoor';
  }

  function showProduct(handle) {
    $('view-product').hidden = false;
    productSkeleton();
    loadCatalog().catch(function () { /* nav is optional here */ });
    getJSON('/api/products/' + encodeURIComponent(handle)).then(function (p) {
      if (!p || !Array.isArray(p.variants)) { showMissing(); return; }
      renderProduct(p);
    }).catch(function (err) {
      if (err && err.status === 404) { showMissing(); return; }
      var info = $('pdp-info');
      info.textContent = '';
      info.appendChild(el('div', { class: 'error-box', text: 'This product could not be loaded. Please refresh the page.' }));
    });
  }

  // ---------- boot ----------

  function boot() {
    initCart();
    initHeader();
    var path = location.pathname.replace(/\/+$/, '') || '/';
    var match = path.match(/^\/products\/([^/]+)$/);
    if (path === '/') {
      showHome();
    } else if (match) {
      var handle;
      try { handle = decodeURIComponent(match[1]); } catch (e) { handle = match[1]; }
      showProduct(handle);
    } else {
      loadCatalog().catch(function () {});
      showMissing();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
