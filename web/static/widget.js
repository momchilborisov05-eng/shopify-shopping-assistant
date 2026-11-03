/*
 * The shopping assistant chat widget.
 *
 * Embed it on any page:
 *   <script src="https://ASSISTANT_HOST/widget.js" defer data-color="#1f3d2b"
 *           data-title="Nordvik Outdoor" data-subtitle="..." data-lang="auto"></script>
 *
 * Everything lives in a Shadow DOM, styled only by widget.css, so the shop's CSS
 * can't leak in or out. It works under a strict CSP (style-src 'self',
 * script-src 'self'): no inline styles, no eval, no inline handlers. The one
 * style it sets from script is the --sa-accent custom property on the host.
 *
 * The page can drive it through window.ShopAssistant: open(prefill), close(), ask(message).
 */
(function () {
  "use strict";

  if (window.ShopAssistant && window.ShopAssistant.__loaded) return;

  // ---------- configuration (read while document.currentScript is still set) ----------

  const script = document.currentScript || Array.prototype.slice.call(document.scripts)
    .filter((s) => /\/widget\.js(\?|#|$)/.test(s.src)).pop() || null;
  const data = (script && script.dataset) || {};
  const SRC = script && script.src ? script.src : location.origin + "/widget.js";
  // The API lives where the script came from: strip the file name (and a /static folder).
  const API = SRC.split(/[?#]/)[0].replace(/\/[^/]*$/, "").replace(/\/static$/, "");
  const CSS_URL = API + "/static/widget.css";

  const DEFAULT_COLOR = "#1f3d2b";
  const MAX_CHARS = 1000;
  const KEY_CONVERSATION = "sa_conversation";
  const KEY_NUDGED = "sa_nudged";
  const KEY_PULSED = "sa_pulsed";

  function pickColor(value) {
    const v = (value || "").trim();
    if (v && window.CSS && CSS.supports && CSS.supports("color", v)) return v;
    return DEFAULT_COLOR;
  }

  function pickLang(value) {
    const v = (value || "auto").toLowerCase();
    if (v === "en" || v === "bg") return v;
    const page = (document.documentElement.lang || navigator.language || "").toLowerCase();
    return page.indexOf("bg") === 0 ? "bg" : "en";
  }

  // Light accents (yellow, mint...) get dark text; everything else white text.
  function isLightColor(color) {
    let r, g, b;
    const hex = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    const rgb = color.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
    if (hex) {
      const h = hex[1].length === 3 ? hex[1].replace(/./g, "$&$&") : hex[1];
      r = parseInt(h.slice(0, 2), 16); g = parseInt(h.slice(2, 4), 16); b = parseInt(h.slice(4, 6), 16);
    } else if (rgb) {
      r = +rgb[1]; g = +rgb[2]; b = +rgb[3];
    } else {
      return false;
    }
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b) > 0.45;
  }

  const config = {
    color: pickColor(data.color),
    title: (data.title || "").trim(),
    subtitle: (data.subtitle || "").trim(),
    lang: pickLang(data.lang),
  };

  // ---------- strings ----------

  const STRINGS = {
    en: {
      launcherOpen: "Open shopping assistant",
      launcherClose: "Close shopping assistant",
      dialog: "Shopping assistant chat",
      conversation: "Conversation",
      defaultTitle: "Shopping assistant",
      subtitle: "AI assistant · replies instantly",
      newChat: "New chat",
      close: "Close chat",
      nudge: "Need help choosing? Ask me!",
      dismiss: "Dismiss",
      greeting: (store) => store
        ? "Hi! I'm the " + store + " shopping assistant. Ask me about products, sizes, delivery or your order."
        : "Hi! I'm your shopping assistant. Ask me about products, sizes, delivery or your order.",
      chips: ["Find a waterproof jacket", "Which size should I take?", "Where is my order?", "Delivery and returns"],
      suggestions: "Suggested questions",
      placeholder: "Ask about products, sizes, orders…",
      inputLabel: "Your message",
      send: "Send message",
      powered: "Powered by Claude · AI can make mistakes",
      human: "Talk to a person",
      typing: "The assistant is typing",
      products: "Suggested products",
      view: "View",
      addToCart: "Add to cart",
      adding: "Adding…",
      added: "Added ✓",
      addFailed: "Try again",
      soldOut: "Sold out",
      sale: "Sale",
      rating: (r, n) => "Rated " + r + " out of 5" + (n ? " from " + n + " reviews" : ""),
      helpful: "Was this helpful?",
      thumbsUp: "Helpful",
      thumbsDown: "Not helpful",
      thanks: "Thanks for the feedback!",
      handoff: (n) => "Passed to our team · ticket #" + n,
      retry: "Retry",
      error: "Something went wrong. Please try again.",
      offline: "Couldn't reach the assistant. Check your connection and try again.",
      charsLeft: (n) => n + " characters left",
      status: {},
    },
    bg: {
      launcherOpen: "Отвори асистента за пазаруване",
      launcherClose: "Затвори асистента за пазаруване",
      dialog: "Чат с асистента за пазаруване",
      conversation: "Разговор",
      defaultTitle: "Асистент за пазаруване",
      subtitle: "AI асистент · отговаря веднага",
      newChat: "Нов чат",
      close: "Затвори чата",
      nudge: "Нужда от помощ при избора? Попитайте ме!",
      dismiss: "Затвори",
      greeting: (store) => store
        ? "Здравейте! Аз съм асистентът за пазаруване на " + store + ". Питайте ме за продукти, размери, доставка или поръчка."
        : "Здравейте! Аз съм вашият асистент за пазаруване. Питайте ме за продукти, размери, доставка или поръчка.",
      chips: ["Търся водоустойчиво яке", "Какъв размер да взема?", "Къде е поръчката ми?", "Доставка и връщане"],
      suggestions: "Предложени въпроси",
      placeholder: "Питайте за продукти, размери, поръчки…",
      inputLabel: "Вашето съобщение",
      send: "Изпрати съобщението",
      powered: "Работи с Claude · AI може да греши",
      human: "Свържи ме с човек",
      typing: "Асистентът пише",
      products: "Предложени продукти",
      view: "Виж",
      addToCart: "В количката",
      adding: "Добавям…",
      added: "Добавено ✓",
      addFailed: "Опитай пак",
      soldOut: "Изчерпан",
      sale: "Намаление",
      rating: (r, n) => "Оценка " + r + " от 5" + (n ? " от " + n + " отзива" : ""),
      helpful: "Полезен ли беше отговорът?",
      thumbsUp: "Полезен",
      thumbsDown: "Не е полезен",
      thanks: "Благодарим за отзива!",
      handoff: (n) => "Предадено на екипа · запитване #" + n,
      retry: "Опитай пак",
      error: "Нещо се обърка. Моля, опитайте отново.",
      offline: "Няма връзка с асистента. Проверете интернета и опитайте пак.",
      charsLeft: (n) => "Остават " + n + " знака",
      status: {
        "Searching the catalog": "Търся в каталога",
        "Checking sizes and stock": "Проверявам размери и наличност",
        "Reading the store policy": "Чета условията на магазина",
        "Looking up the order": "Проверявам поръчката",
        "Passing to the team": "Свързвам ви с екипа",
        "Working": "Работя по въпроса",
      },
    },
  };
  const t = STRINGS[config.lang];

  function statusLabel(label) {
    const text = (label && t.status[label]) || label || (config.lang === "bg" ? "Работя по въпроса" : "Working");
    return text.replace(/[.…]+$/, "") + "…";
  }

  // ---------- small helpers ----------

  function el(tag, props, children) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((key) => {
        const value = props[key];
        if (value == null || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.indexOf("on") === 0 && typeof value === "function") node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : String(value));
      });
    }
    if (children != null) {
      [].concat(children).forEach((child) => {
        if (child != null && child !== false) node.append(child);
      });
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // Icons are built as SVG nodes (strings are paths, arrays are filled circles).
  const ICONS = {
    chat: ["M20.5 11.5a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1.4-4.6A8.5 8.5 0 1 1 20.5 11.5z",
      [8.5, 11.5, 1.1], [12, 11.5, 1.1], [15.5, 11.5, 1.1]],
    chevron: ["M6 9.5l6 6 6-6"],
    close: ["M6.5 6.5l11 11", "M17.5 6.5l-11 11"],
    send: ["M12 19V5.5", "M5.5 12L12 5.5l6.5 6.5"],
    compose: ["M12 20h8.5", "M16.4 3.6a2.1 2.1 0 0 1 3 3L7.5 18.5l-4 1 1-4z"],
    thumbUp: ["M7 10.5V20.5H3.5v-10z", "M7 10.5l3.6-7.2a2.6 2.6 0 0 1 2.9 2.6v3.8h5.2a2 2 0 0 1 2 2.3l-1.3 7.2a2 2 0 0 1-2 1.3H7"],
    thumbDown: ["M17 13.5V3.5h3.5v10z", "M17 13.5l-3.6 7.2a2.6 2.6 0 0 1-2.9-2.6v-3.8H5.3a2 2 0 0 1-2-2.3l1.3-7.2a2 2 0 0 1 2-1.3H17"],
    image: ["M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V6A1.5 1.5 0 0 1 5 4.5z",
      "M3.5 16l4.5-4.5 4 4 3-3 5.5 5.5", [15.5, 9, 1.4]],
    person: ["M12 11.5a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5z", "M4.5 20.5a7.5 7.5 0 0 1 15 0"],
    retry: ["M4.5 12a7.5 7.5 0 0 1 13-5.1L19.5 9", "M19.5 4.5V9H15", "M19.5 12a7.5 7.5 0 0 1-13 5.1L4.5 15", "M4.5 19.5V15H9"],
    alert: ["M12 3.5a8.5 8.5 0 1 1 0 17 8.5 8.5 0 0 1 0-17z", "M12 8v4.5", [12, 16, 1.1]],
  };

  function icon(name, cls) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    if (cls) svg.setAttribute("class", cls);
    ICONS[name].forEach((part) => {
      let node;
      if (Array.isArray(part)) {
        node = document.createElementNS(NS, "circle");
        node.setAttribute("cx", part[0]);
        node.setAttribute("cy", part[1]);
        node.setAttribute("r", part[2]);
        node.setAttribute("fill", "currentColor");
        node.setAttribute("stroke", "none");
      } else {
        node = document.createElementNS(NS, "path");
        node.setAttribute("d", part);
      }
      svg.appendChild(node);
    });
    return svg;
  }

  const storage = {
    get(key, session) {
      try { return (session ? sessionStorage : localStorage).getItem(key); } catch (e) { return null; }
    },
    set(key, value, session) {
      try { (session ? sessionStorage : localStorage).setItem(key, value); } catch (e) { /* storage blocked */ }
    },
    remove(key) {
      try { localStorage.removeItem(key); } catch (e) { /* storage blocked */ }
    },
  };

  // Links in answers: only http(s), relative ones resolved against the page.
  function safeLink(url) {
    try {
      const u = new URL(url, location.href);
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  // Product images and URLs come from the API, so relative ones are relative to it.
  function apiUrl(url) {
    if (!url) return null;
    try {
      const u = new URL(url, API + "/");
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  let storeCurrency = "";

  function money(value, currency) {
    if (value == null || value === "" || isNaN(+value)) return "";
    const amount = +value;
    const code = currency || storeCurrency || "EUR";
    try {
      return new Intl.NumberFormat(config.lang === "bg" ? "bg-BG" : "en-IE", {
        style: "currency", currency: code,
        minimumFractionDigits: amount % 1 ? 2 : 0, maximumFractionDigits: 2,
      }).format(amount);
    } catch (e) {
      return amount + " " + code;
    }
  }

  // ---------- Markdown-lite: paragraphs, line breaks, lists, **bold**, [links](url) ----------
  // Every piece of model text becomes a text node; nothing is ever parsed as HTML.

  const INLINE = /\*\*([^*\n]+?)\*\*|\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()]*[^\s<>().,!?;:'"])/g;

  function renderInline(parent, text, live, inBold) {
    INLINE.lastIndex = 0;
    const matches = [];
    let m;
    while ((m = INLINE.exec(text)) !== null) matches.push({ m: m, index: m.index, end: INLINE.lastIndex });
    let pos = 0;
    matches.forEach((hit) => {
      if (hit.index > pos) parent.append(text.slice(pos, hit.index));
      const mm = hit.m;
      if (mm[1] != null && !inBold) {
        const strong = el("strong");
        renderInline(strong, mm[1], false, true);
        parent.append(strong);
      } else if (mm[2] != null) {
        const href = safeLink(mm[3]);
        parent.append(href ? el("a", { href: href, target: "_blank", rel: "noopener noreferrer", text: mm[2] }) : mm[2]);
      } else if (mm[4] != null) {
        const href = safeLink(mm[4]);
        parent.append(href ? el("a", { href: href, target: "_blank", rel: "noopener noreferrer", text: mm[4] }) : mm[4]);
      } else {
        parent.append(mm[0]);
      }
      pos = hit.end;
    });
    let rest = text.slice(pos);
    // While streaming, an opened but not yet closed **bold** shows as bold instead of stars.
    const open = rest.indexOf("**");
    if (live && !inBold && open >= 0) {
      if (open > 0) parent.append(rest.slice(0, open));
      const tail = rest.slice(open + 2);
      if (tail) parent.append(el("strong", { text: tail }));
      return;
    }
    if (rest) parent.append(rest);
  }

  function renderMarkdown(target, source, live) {
    const frag = document.createDocumentFragment();
    const lines = source.replace(/\r/g, "").split("\n");
    let block = null;

    function flush() {
      if (!block) return;
      if (block.type === "p") {
        const p = el("p");
        block.lines.forEach((line, i) => {
          if (i) p.append(el("br"));
          renderInline(p, line, live);
        });
        frag.append(p);
      } else {
        const list = el(block.type, block.type === "ol" && block.start !== 1 ? { start: block.start } : null);
        block.items.forEach((item) => {
          const li = el("li");
          renderInline(li, item, live);
          list.append(li);
        });
        frag.append(list);
      }
      block = null;
    }

    lines.forEach((line) => {
      if (!line.trim()) { flush(); return; }
      const ul = line.match(/^\s*[-*•]\s+(.*)$/);
      const ol = line.match(/^\s*(\d{1,3})[.)]\s+(.*)$/);
      const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (ul) {
        if (!block || block.type !== "ul") { flush(); block = { type: "ul", items: [] }; }
        block.items.push(ul[1]);
      } else if (ol) {
        if (!block || block.type !== "ol") { flush(); block = { type: "ol", items: [], start: +ol[1] || 1 }; }
        block.items.push(ol[2]);
      } else if (block && block.type !== "p" && /^\s+/.test(line)) {
        block.items[block.items.length - 1] += " " + line.trim();  // continuation of a list item
      } else {
        if (block && block.type !== "p") flush();
        if (!block) block = { type: "p", lines: [] };
        block.lines.push(heading ? "**" + heading[1].replace(/\*\*/g, "") + "**" : line);
      }
    });
    flush();
    clear(target);
    target.append(frag);
  }

  // ---------- the widget ----------

  let ui = null;              // DOM references, built on init
  let conversationId = storage.get(KEY_CONVERSATION);
  let storeName = config.title;
  let isOpen = false;
  let streaming = false;
  let controller = null;
  let restorePromise = null;
  let lastUserText = "";
  let stickToBottom = true;
  let greetingBody = null;
  let chipsEl = null;
  let epoch = 0;              // bumped by "New chat", so a late history restore is dropped
  const pending = [];         // ShopAssistant calls made before init

  function init() {
    const host = el("shop-assistant-widget", { "data-shop-assistant": "" });
    host.style.setProperty("--sa-accent", config.color);
    const shadow = host.attachShadow({ mode: "open" });

    const root = el("div", { class: "sa" + (isLightColor(config.color) ? " sa-accent-light" : ""), hidden: true });
    const link = el("link", { rel: "stylesheet", href: CSS_URL });
    // Stay invisible until the stylesheet is in, so the shop never sees unstyled widget markup.
    link.addEventListener("load", () => { root.hidden = false; });
    link.addEventListener("error", () => { console.warn("[ShopAssistant] could not load " + CSS_URL); });

    // Launcher and the greeting nudge.
    const launcher = el("button", {
      class: "sa-launcher", type: "button", "aria-label": t.launcherOpen,
      "aria-expanded": "false", "aria-controls": "sa-panel", "aria-haspopup": "dialog",
      onclick: () => (isOpen ? close() : open()),
    }, [icon("chat", "sa-ico-chat"), icon("chevron", "sa-ico-close")]);

    const nudgeText = el("button", { class: "sa-nudge-text", type: "button", text: t.nudge, onclick: () => open() });
    const nudgeClose = el("button", {
      class: "sa-nudge-close", type: "button", "aria-label": t.dismiss,
      onclick: () => hideNudge(),
    }, icon("close"));
    const nudge = el("div", { class: "sa-nudge", role: "status", hidden: true }, [nudgeText, nudgeClose]);

    // Header.
    const avatar = el("div", { class: "sa-avatar", "aria-hidden": "true" });
    const title = el("h2", { class: "sa-title", id: "sa-title" });
    const subtitle = el("p", { class: "sa-sub" }, [
      el("span", { class: "sa-dot", "aria-hidden": "true" }),
      el("span", { text: config.subtitle || t.subtitle }),
    ]);
    const newChatBtn = el("button", {
      class: "sa-icon-btn", type: "button", "aria-label": t.newChat, title: t.newChat, onclick: () => newChat(),
    }, icon("compose"));
    const closeBtn = el("button", {
      class: "sa-icon-btn", type: "button", "aria-label": t.close, title: t.close, onclick: () => close(),
    }, icon("close"));
    const header = el("header", { class: "sa-header" }, [
      avatar,
      el("div", { class: "sa-head-text" }, [title, subtitle]),
      el("div", { class: "sa-head-actions" }, [newChatBtn, closeBtn]),
    ]);

    // Messages.
    const log = el("div", {
      class: "sa-log", role: "log", "aria-live": "polite", "aria-relevant": "additions",
      "aria-label": t.conversation, tabindex: "0",
    });
    log.addEventListener("scroll", () => {
      stickToBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 48;
    }, { passive: true });

    // Footer.
    const input = el("textarea", {
      class: "sa-input", rows: "1", maxlength: String(MAX_CHARS),
      placeholder: t.placeholder, "aria-label": t.inputLabel, enterkeyhint: "send",
    });
    const sendBtn = el("button", { class: "sa-send", type: "submit", "aria-label": t.send, disabled: true }, icon("send"));
    const form = el("form", { class: "sa-form", novalidate: true }, [input, sendBtn]);
    const counter = el("span", { class: "sa-count", "aria-live": "polite", hidden: true });
    const humanBtn = el("button", {
      class: "sa-human", type: "button", onclick: () => ask(t.human),
    }, [icon("person"), el("span", { text: t.human })]);
    const footer = el("footer", { class: "sa-footer" }, [
      form,
      el("div", { class: "sa-meta" }, [el("span", { class: "sa-powered", text: t.powered }), counter, humanBtn]),
    ]);

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitInput();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submitInput();
      }
    });
    input.addEventListener("input", () => { grow(); updateSend(); });

    const panel = el("div", {
      class: "sa-panel", id: "sa-panel", role: "dialog", "aria-modal": "false",
      "aria-labelledby": "sa-title", "aria-label": t.dialog, "aria-hidden": "true", inert: true,
    }, [header, log, footer]);

    root.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (isOpen) { e.stopPropagation(); close(); } else if (!nudge.hidden) hideNudge();
    });

    root.append(nudge, panel, launcher);
    shadow.append(link, root);
    document.body.appendChild(host);

    ui = { host, root, launcher, nudge, panel, avatar, title, log, input, sendBtn, counter };
    setTitle(storeName || t.defaultTitle);

    // The store name from the API is the default title.
    fetch(API + "/api/info", { credentials: "omit" })
      .then((r) => (r.ok ? r.json() : null))
      .then((info) => {
        if (!info) return;
        if (info.currency) storeCurrency = info.currency;
        if (!config.title && info.store) {
          storeName = info.store;
          setTitle(storeName);
          if (greetingBody) renderMarkdown(greetingBody, t.greeting(storeName), false);
        }
      })
      .catch(() => { /* optional */ });

    // A one-time pulse and, a little later, the greeting nudge (once per session).
    if (!storage.get(KEY_PULSED, true)) {
      storage.set(KEY_PULSED, "1", true);
      launcher.classList.add("sa-pulse");
      launcher.addEventListener("animationend", () => launcher.classList.remove("sa-pulse"));
    }
    if (!storage.get(KEY_NUDGED, true)) {
      setTimeout(() => {
        if (isOpen || storage.get(KEY_NUDGED, true)) return;
        storage.set(KEY_NUDGED, "1", true);
        nudge.hidden = false;
        requestAnimationFrame(() => nudge.classList.add("is-visible"));
      }, 4000);
    }

    pending.splice(0).forEach((fn) => fn());
  }

  function setTitle(name) {
    ui.title.textContent = name;
    ui.avatar.textContent = (name.trim().charAt(0) || "S").toUpperCase();
  }

  function hideNudge() {
    storage.set(KEY_NUDGED, "1", true);
    ui.nudge.classList.remove("is-visible");
    ui.nudge.hidden = true;
  }

  // ---------- open / close ----------

  function open() {
    if (!isOpen) {
      isOpen = true;
      hideNudge();
      ui.root.classList.add("sa-open");
      ui.panel.classList.add("is-open");
      ui.panel.removeAttribute("inert");
      ui.panel.setAttribute("aria-hidden", "false");
      ui.launcher.setAttribute("aria-expanded", "true");
      ui.launcher.setAttribute("aria-label", t.launcherClose);
      ensureRestored();
      stickToBottom = true;
      scrollToBottom(true);
    }
    requestAnimationFrame(() => ui.input.focus({ preventScroll: true }));
  }

  function close() {
    if (!isOpen) return;
    const hadFocus = ui.root.contains(ui.root.getRootNode().activeElement);
    isOpen = false;
    ui.root.classList.remove("sa-open");
    ui.panel.classList.remove("is-open");
    ui.panel.setAttribute("inert", "");
    ui.panel.setAttribute("aria-hidden", "true");
    ui.launcher.setAttribute("aria-expanded", "false");
    ui.launcher.setAttribute("aria-label", t.launcherOpen);
    if (hadFocus) ui.launcher.focus({ preventScroll: true });
  }

  // ---------- conversation state ----------

  function saveConversation(id) {
    conversationId = id;
    storage.set(KEY_CONVERSATION, id);
  }

  function forgetConversation() {
    conversationId = null;
    storage.remove(KEY_CONVERSATION);
  }

  function showGreeting() {
    greetingBody = el("div", { class: "sa-md" });
    renderMarkdown(greetingBody, t.greeting(storeName), false);
    ui.log.append(el("div", { class: "sa-row sa-row-bot sa-greeting" }, [
      el("div", { class: "sa-turn" }, el("div", { class: "sa-bubble sa-bubble-bot" }, greetingBody)),
    ]));
  }

  function showChips() {
    chipsEl = el("div", { class: "sa-chips", role: "group", "aria-label": t.suggestions },
      t.chips.map((text) => el("button", { class: "sa-chip", type: "button", text: text, onclick: () => send(text) })));
    ui.log.append(chipsEl);
  }

  function removeChips() {
    if (chipsEl) { chipsEl.remove(); chipsEl = null; }
  }

  function ensureRestored() {
    if (!restorePromise) restorePromise = restore();
    return restorePromise;
  }

  // On first open, bring back the stored conversation (or start fresh).
  async function restore() {
    clear(ui.log);
    showGreeting();
    if (!conversationId) { showChips(); return; }
    const id = conversationId;
    const mine = epoch;
    const loading = el("div", { class: "sa-row sa-row-bot" },
      el("div", { class: "sa-typing", role: "status", "aria-label": t.typing }, [el("span"), el("span"), el("span")]));
    ui.log.append(loading);
    let messages = [];
    try {
      const res = await fetch(API + "/api/conversations/" + encodeURIComponent(id), { credentials: "omit" });
      if (res.status === 404) forgetConversation();
      else if (res.ok) messages = ((await res.json()) || {}).messages || [];
    } catch (e) { /* offline: start with the greeting */ }
    loading.remove();
    if (mine !== epoch) return;  // a new chat started meanwhile
    if (!messages.length) { showChips(); return; }
    messages.forEach((msg) => {
      if (msg.role === "user") addUserBubble(msg.text || "");
      else createTurn({ text: msg.text || "", products: msg.products || [] });
    });
    scrollToBottom(true);
  }

  function newChat() {
    if (controller) controller.abort();
    controller = null;
    epoch += 1;
    setStreaming(false);
    forgetConversation();
    restorePromise = Promise.resolve();
    clear(ui.log);
    showGreeting();
    showChips();
    ui.input.value = "";
    grow();
    updateSend();
    ui.input.focus({ preventScroll: true });
  }

  // ---------- composing ----------

  // The textarea grows by rows (no inline styles), up to 5 lines, then scrolls.
  function grow() {
    const input = ui.input;
    input.rows = 1;
    let rows = 1;
    while (input.scrollHeight > input.clientHeight + 1 && rows < 5) {
      rows += 1;
      input.rows = rows;
    }
    const left = MAX_CHARS - input.value.length;
    ui.counter.hidden = left > 100;
    ui.counter.textContent = t.charsLeft(left);
  }

  function updateSend() {
    ui.sendBtn.disabled = streaming || !ui.input.value.trim();
  }

  function setStreaming(on) {
    streaming = on;
    ui.log.setAttribute("aria-busy", on ? "true" : "false");
    ui.root.classList.toggle("sa-busy", on);
    updateSend();
  }

  function submitInput() {
    const text = ui.input.value.trim();
    if (!text || streaming) return;
    ui.input.value = "";
    grow();
    send(text);
  }

  function scrollToBottom(force) {
    if (!ui || !(force || stickToBottom)) return;
    ui.log.scrollTop = ui.log.scrollHeight;
  }

  function addUserBubble(text) {
    ui.log.append(el("div", { class: "sa-row sa-row-user" }, el("div", { class: "sa-bubble sa-bubble-user", text: text })));
  }

  // ---------- an assistant answer ----------

  function createTurn(saved) {
    const live = !saved;
    const md = el("div", { class: "sa-md" });
    const bubble = el("div", { class: "sa-bubble sa-bubble-bot", hidden: true }, md);
    const typing = live ? el("div", { class: "sa-typing", role: "status", "aria-label": t.typing },
      [el("span"), el("span"), el("span")]) : null;
    const status = el("div", { class: "sa-status", hidden: true, "aria-live": "polite" });
    const col = el("div", { class: "sa-turn" }, [bubble, typing, status]);
    const row = el("div", { class: "sa-row sa-row-bot" }, col);
    ui.log.append(row);

    let raw = "";
    let streamingText = live;
    let queued = false;
    let hasProducts = false;

    function paint() {
      queued = false;
      const text = raw.trim();
      bubble.hidden = !text;
      if (text) renderMarkdown(md, text, streamingText);
      md.classList.toggle("is-streaming", streamingText);
      scrollToBottom();
    }

    function schedule() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(paint);
    }

    function stopWaiting() {
      if (typing) typing.remove();
      status.hidden = true;
    }

    const turn = {
      row: row,
      get text() { return raw; },
      append(text) {
        raw += text;
        if (raw.trim()) stopWaiting();
        schedule();
      },
      replace(text) {
        raw = text || "";
        stopWaiting();
        schedule();
      },
      status(label) {
        clear(status);
        status.append(el("span", { class: "sa-spinner", "aria-hidden": "true" }),
          el("span", { class: "sa-status-text", text: statusLabel(label) }));
        status.hidden = false;
        scrollToBottom();
      },
      products(list) {
        if (!list || !list.length) return;
        hasProducts = true;
        col.insertBefore(productRow(list), status);
        scrollToBottom();
      },
      notice(text) {
        col.insertBefore(el("div", { class: "sa-notice", role: "status" }, [icon("person"), el("span", { text: text })]), status);
        scrollToBottom();
      },
      finish() {
        streamingText = false;
        stopWaiting();
        paint();
        if (!raw.trim() && !hasProducts && !col.querySelector(".sa-notice")) row.remove();
      },
      fail(message) {
        streamingText = false;
        stopWaiting();
        paint();
        const retry = el("button", { class: "sa-retry", type: "button" }, [icon("retry"), el("span", { text: t.retry })]);
        const box = el("div", { class: "sa-error", role: "alert" }, [icon("alert"), el("span", { class: "sa-error-text", text: message || t.error }), retry]);
        retry.addEventListener("click", () => {
          if (streaming) return;
          box.remove();
          if (!raw.trim() && !hasProducts) row.remove();
          send(lastUserText, true);
        });
        col.appendChild(box);
        scrollToBottom();
      },
      feedback() {
        if (!conversationId || !raw.trim()) return;
        col.appendChild(feedbackRow());
        scrollToBottom();
      },
    };

    if (saved) {
      raw = saved.text;
      paint();
      turn.products(saved.products);
    }
    return turn;
  }

  function feedbackRow() {
    const box = el("div", { class: "sa-feedback" });
    const rate = (value) => {
      box.classList.add("is-done");
      clear(box);
      box.append(el("span", { class: "sa-feedback-thanks", role: "status", text: t.thanks }));
      fetch(API + "/api/conversations/" + encodeURIComponent(conversationId) + "/rating", {
        method: "POST", credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: value }),
      }).catch(() => { /* feedback is best effort */ });
    };
    box.append(
      el("span", { class: "sa-feedback-label", text: t.helpful }),
      el("button", { class: "sa-thumb", type: "button", "aria-label": t.thumbsUp, title: t.thumbsUp, onclick: () => rate(5) }, icon("thumbUp")),
      el("button", { class: "sa-thumb", type: "button", "aria-label": t.thumbsDown, title: t.thumbsDown, onclick: () => rate(1) }, icon("thumbDown")),
    );
    return box;
  }

  // ---------- product cards ----------

  function placeholder() {
    return el("div", { class: "sa-ph", "aria-hidden": "true" }, icon("image"));
  }

  function productRow(products) {
    return el("div", { class: "sa-products", role: "list", "aria-label": t.products }, products.map(productCard));
  }

  function productCard(p) {
    const url = apiUrl(p.url);
    const image = apiUrl(p.image);
    const onSale = p.compare_at_price != null && +p.compare_at_price > +p.price;

    const media = el(url ? "a" : "div", { class: "sa-card-media", href: url, tabindex: url ? "-1" : null, "aria-hidden": "true" });
    if (image) {
      const img = el("img", { src: image, alt: "", loading: "lazy", decoding: "async" });
      img.addEventListener("error", () => img.replaceWith(placeholder()));
      media.append(img);
    } else {
      media.append(placeholder());
    }
    if (!p.in_stock) media.append(el("span", { class: "sa-badge sa-badge-out", text: t.soldOut }));
    else if (onSale) media.append(el("span", { class: "sa-badge sa-badge-sale", text: t.sale }));

    const title = el(url ? "a" : "span", { class: "sa-card-title", href: url, text: p.title || "" });
    const price = el("div", { class: "sa-price" + (onSale ? " is-sale" : "") }, [
      el("span", { class: "sa-price-now", text: money(p.price, p.currency) }),
      onSale ? el("s", { class: "sa-price-was", text: money(p.compare_at_price, p.currency) }) : null,
    ]);
    const rating = p.rating != null && +p.rating > 0
      ? el("div", { class: "sa-rating", role: "img", "aria-label": t.rating(p.rating, p.reviews) }, [
        el("span", { class: "sa-star", "aria-hidden": "true", text: "★" }),
        el("span", { "aria-hidden": "true", text: String(p.rating) }),
        p.reviews ? el("span", { class: "sa-reviews", "aria-hidden": "true", text: "(" + p.reviews + ")" }) : null,
      ])
      : null;

    const canAdd = !!p.in_stock && p.default_variant_id != null && p.default_variant_id !== "";
    const addBtn = el("button", { class: "sa-btn sa-btn-primary", type: "button", disabled: !canAdd, text: p.in_stock ? t.addToCart : t.soldOut });
    addBtn.addEventListener("click", () => addToCart(p, image, addBtn));
    const actions = el("div", { class: "sa-card-actions" }, [
      url ? el("a", { class: "sa-btn sa-btn-ghost", href: url, text: t.view }) : null,
      addBtn,
    ]);

    return el("div", { class: "sa-card", role: "listitem" }, [
      media,
      el("div", { class: "sa-card-body" }, [title, rating, price, actions]),
    ]);
  }

  async function addToCart(p, image, button) {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = t.adding;
    try {
      if (window.Shopify) {
        // A real Shopify storefront: the Ajax Cart API on the shop's own origin.
        const root = (window.Shopify.routes && window.Shopify.routes.root) || "/";
        const res = await fetch(root + "cart/add.js", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ items: [{ id: Number(p.default_variant_id), quantity: 1 }] }),
        });
        if (!res.ok) throw new Error("cart " + res.status);
        document.dispatchEvent(new CustomEvent("cart:refresh"));
      } else {
        // The demo storefront listens for this and keeps its own cart.
        window.dispatchEvent(new CustomEvent("shop-assistant:add-to-cart", {
          detail: {
            variantId: p.default_variant_id, handle: p.handle, title: p.title,
            price: p.price, currency: p.currency, image: image,
          },
        }));
      }
      button.textContent = t.added;
      button.classList.add("is-added");
      setTimeout(() => {
        button.textContent = t.addToCart;
        button.classList.remove("is-added");
        button.disabled = false;
      }, 2600);
    } catch (e) {
      button.textContent = t.addFailed;
      button.disabled = false;
    }
  }

  // ---------- talking to the API ----------

  async function send(text, isRetry) {
    text = String(text || "").trim().slice(0, MAX_CHARS);
    if (!text || streaming) return;
    removeChips();
    ui.log.querySelectorAll(".sa-feedback:not(.is-done)").forEach((node) => node.remove());
    if (!isRetry) addUserBubble(text);
    lastUserText = text;
    stickToBottom = true;

    const mine = new AbortController();
    controller = mine;
    setStreaming(true);
    const turn = createTurn(null);
    scrollToBottom(true);

    let done = false;
    let failed = false;
    const handle = (event) => {
      switch (event.type) {
        case "meta":
          if (event.conversation_id) saveConversation(event.conversation_id);
          break;
        case "status":
          turn.status(event.label);
          break;
        case "text":
          turn.append(event.text || "");
          break;
        case "replace":
          turn.replace(event.text);
          break;
        case "products":
          turn.products(event.products);
          break;
        case "handoff":
          turn.notice(t.handoff(event.ticket));
          break;
        case "done":
          done = true;
          turn.finish();
          turn.feedback();
          break;
        case "error":
          failed = true;
          turn.fail(event.message);
          break;
        default:
          break;
      }
    };

    try {
      const res = await fetch(API + "/api/chat", {
        method: "POST", credentials: "omit", signal: mine.signal,
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId || undefined,
          page: location.pathname.slice(0, 300),
        }),
      });
      if (!res.ok) {
        let message = null;
        try { message = ((await res.json()) || {}).error; } catch (e) { /* not JSON */ }
        if (res.status === 404 && conversationId) forgetConversation();
        failed = true;
        turn.fail(typeof message === "string" && message ? message : t.error);
        return;
      }
      await readEvents(res, handle);
      if (!done && !failed) {
        if (turn.text.trim()) turn.finish();
        else turn.fail(t.error);
      }
    } catch (e) {
      if (e && e.name === "AbortError") return;  // "New chat" dropped this turn
      if (!done && !failed) turn.fail(t.offline);
    } finally {
      if (controller === mine) {
        controller = null;
        setStreaming(false);
      }
    }
  }

  // Server-sent events over fetch: "data: {...}\n\n" blocks.
  async function readEvents(res, onEvent) {
    const parse = (block) => {
      const payload = block.split("\n")
        .filter((line) => line.indexOf("data:") === 0)
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!payload) return;
      try { onEvent(JSON.parse(payload)); } catch (e) { /* skip a malformed event */ }
    };
    if (!res.body || !res.body.getReader) {
      (await res.text()).replace(/\r\n/g, "\n").split("\n\n").forEach(parse);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        parse(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) parse(buffer);
  }

  // ---------- public API ----------

  function ready(fn) {
    if (ui) fn(); else pending.push(fn);
  }

  function ask(message) {
    ready(async () => {
      open();
      await ensureRestored();
      const text = String(message || "").trim();
      if (!text) return;
      if (streaming) {
        ui.input.value = text.slice(0, MAX_CHARS);
        grow();
        updateSend();
        return;
      }
      send(text);
    });
  }

  window.ShopAssistant = {
    __loaded: true,
    open(prefill) {
      ready(() => {
        open();
        if (typeof prefill === "string" && prefill) {
          ui.input.value = prefill.slice(0, MAX_CHARS);
          grow();
          updateSend();
          ui.input.focus({ preventScroll: true });
        }
      });
    },
    close() {
      ready(close);
    },
    ask: ask,
  };

  if (document.body) init();
  else document.addEventListener("DOMContentLoaded", init);
})();
