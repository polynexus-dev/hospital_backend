/* Polynexus website enquiry widget. Served by apps.enquiries.website with
 * __SITE_API__ replaced by this hospital's endpoint. No dependencies; renders
 * inside a shadow root so the host page's CSS can't break it (or vice versa).
 *   <div data-polynexus-enquiry></div>  -> inline form
 *   otherwise                           -> floating button + pop-up form   */
(function () {
  "use strict"
  if (window.__polynexusWidget) return
  window.__polynexusWidget = true
  var API = "__SITE_API__"
  var startedAt = Date.now()

  function params() {
    var q = new URLSearchParams(window.location.search), out = {}
    ;["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"].forEach(function (k) {
      var v = q.get(k)
      try {
        if (v) sessionStorage.setItem("pnx_" + k, v)
        else v = sessionStorage.getItem("pnx_" + k)
      } catch (e) { /* storage blocked */ }
      if (v) out[k] = v
    })
    try {
      if (!sessionStorage.getItem("pnx_landing")) sessionStorage.setItem("pnx_landing", window.location.href)
      out.landing_page = sessionStorage.getItem("pnx_landing")
    } catch (e) { out.landing_page = window.location.href }
    if (document.referrer && document.referrer.indexOf(window.location.host) === -1) out.referrer = document.referrer
    return out
  }

  function el(tag, attrs, children) {
    var n = document.createElement(tag)
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k]
      else n.setAttribute(k, attrs[k])
    })
    ;(children || []).forEach(function (c) { if (c) n.appendChild(c) })
    return n
  }

  function css(accent) {
    return ":host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}" +
      ".card{background:#fff;color:#1f2937;border:1px solid #e5e7eb;border-radius:14px;padding:20px;max-width:440px;width:100%;box-shadow:0 10px 30px rgba(0,0,0,.08)}" +
      "h2{margin:0 0 4px;font-size:19px}p.intro{margin:0 0 14px;color:#6b7280;font-size:14px}" +
      "label{display:block;font-size:13px;font-weight:600;margin:10px 0 4px}" +
      "input,select,textarea{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:9px;font-size:15px;background:#fff;color:#111827}" +
      "input:focus,select:focus,textarea:focus{outline:2px solid " + accent + ";border-color:" + accent + "}" +
      ".consent{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:#4b5563;font-weight:400;margin-top:12px}.consent input{width:auto;margin-top:2px}" +
      "button.submit{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;background:" + accent + ";color:#fff;font-size:15px;font-weight:600;cursor:pointer}" +
      "button.submit[disabled]{opacity:.6;cursor:wait}.err{color:#b42318;font-size:12.5px;margin-top:4px}.ok{font-size:15px;color:#065f46;padding:12px 0}" +
      ".hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}" +
      ".fab{position:fixed;right:20px;bottom:20px;z-index:2147483000;border:0;border-radius:999px;padding:13px 20px;background:" + accent + ";color:#fff;font-size:15px;font-weight:600;box-shadow:0 8px 24px rgba(0,0,0,.2);cursor:pointer}" +
      ".overlay{position:fixed;inset:0;z-index:2147483001;background:rgba(17,24,39,.45);display:flex;align-items:center;justify-content:center;padding:16px}" +
      ".close{float:right;border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b7280}"
  }

  function buildForm(cfg, onDone) {
    var errs = {}
    function field(name, label, input) {
      errs[name] = el("div", { class: "err", role: "alert" })
      input.id = "pnx-" + name
      input.name = name
      return el("div", {}, [el("label", { for: input.id, text: label }), input, errs[name]])
    }
    var form = el("form", { novalidate: "" })
    var name = el("input", { autocomplete: "name", required: "" })
    var mobile = el("input", { type: "tel", inputmode: "numeric", autocomplete: "tel", maxlength: "14", placeholder: "10-digit mobile number", required: "" })
    var email = el("input", { type: "email", autocomplete: "email", placeholder: "Optional" })
    var service = null
    if (cfg.services && cfg.services.length) {
      service = el("select", {}, [el("option", { value: "", text: "Select…" })])
      cfg.services.forEach(function (s) { service.appendChild(el("option", { value: s, text: s })) })
    }
    var message = el("textarea", { rows: "3", maxlength: "1000", placeholder: "Optional" })
    var consent = el("input", { type: "checkbox", id: "pnx-consent" })
    errs.consent = el("div", { class: "err", role: "alert" })
    var hp = el("input", { tabindex: "-1", autocomplete: "off", name: "website" })
    var submit = el("button", { class: "submit", type: "submit", text: cfg.button_text || "Send" })
    var status = el("div", { class: "err", role: "alert" })

    form.appendChild(field("name", "Your name", name))
    form.appendChild(field("mobile", "Mobile number", mobile))
    form.appendChild(field("email", "Email", email))
    if (service) form.appendChild(field("service", "What do you need?", service))
    form.appendChild(field("message", "Message", message))
    form.appendChild(el("div", { class: "hp", "aria-hidden": "true" }, [el("label", { text: "Website" }), hp]))
    form.appendChild(el("label", { class: "consent", for: "pnx-consent" }, [consent, el("span", { text: cfg.consent_text })]))
    form.appendChild(errs.consent)
    form.appendChild(submit)
    form.appendChild(status)

    form.addEventListener("submit", function (e) {
      e.preventDefault()
      Object.keys(errs).forEach(function (k) { errs[k].textContent = "" })
      status.textContent = ""
      var digits = mobile.value.replace(/\D/g, "").slice(-10)
      var bad = false
      if (name.value.trim().length < 2) { errs.name.textContent = "Please enter your name."; bad = true }
      if (!/^[6-9]\d{9}$/.test(digits)) { errs.mobile.textContent = "Please enter a valid 10-digit mobile number."; bad = true }
      if (!consent.checked) { errs.consent.textContent = "Please tick the box so we can contact you."; bad = true }
      if (bad) return
      var body = Object.assign(params(), {
        name: name.value.trim(), mobile: digits, email: email.value.trim(), service: service ? service.value : "",
        message: message.value.trim(), consent: true, website: hp.value, started_at: startedAt,
      })
      submit.disabled = true
      fetch(API + "enquiry/", { method: "POST", headers: { "Content-Type": "text/plain;charset=UTF-8" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j } }) })
        .then(function (res) {
          submit.disabled = false
          if (res.status === 201) return onDone(res.body.message || cfg.thank_you_message)
          if (res.status === 400) {
            Object.keys(res.body).forEach(function (k) { if (errs[k]) errs[k].textContent = res.body[k]; else status.textContent = res.body[k] })
            return
          }
          status.textContent = res.body.detail || "Something went wrong. Please call us instead."
        })
        .catch(function () {
          submit.disabled = false
          status.textContent = "Couldn't send. Please check your connection or call us."
        })
    })
    return form
  }

  function card(cfg, onClose) {
    var c = el("div", { class: "card", role: "dialog", "aria-label": cfg.title })
    if (onClose) {
      var x = el("button", { class: "close", type: "button", "aria-label": "Close", text: "×" })
      x.addEventListener("click", onClose)
      c.appendChild(x)
    }
    c.appendChild(el("h2", { text: cfg.title }))
    if (cfg.intro) c.appendChild(el("p", { class: "intro", text: cfg.intro }))
    var form = buildForm(cfg, function (msg) {
      form.replaceWith(el("div", { class: "ok", role: "status", text: msg }))
    })
    c.appendChild(form)
    return c
  }

  function mount(cfg) {
    if (!cfg.form_enabled) return
    var inline = document.querySelector("[data-polynexus-enquiry]")
    var host = inline || el("div", { id: "polynexus-enquiry-widget" })
    if (!inline) document.body.appendChild(host)
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host
    root.appendChild(el("style", { text: css(cfg.accent_color || "#0f766e") }))
    if (inline) {
      root.appendChild(card(cfg))
      return
    }
    var fab = el("button", { class: "fab", type: "button", text: cfg.button_text || "Enquire now" })
    var overlay = null
    fab.addEventListener("click", function () {
      if (overlay) return
      overlay = el("div", { class: "overlay" })
      var close = function () { overlay.remove(); overlay = null }
      overlay.addEventListener("click", function (e) { if (e.target === overlay) close() })
      overlay.appendChild(card(cfg, close))
      root.appendChild(overlay)
    })
    root.appendChild(fab)
  }

  function start() {
    fetch(API + "config/").then(function (r) { return r.ok ? r.json() : null }).then(function (cfg) { if (cfg) mount(cfg) }).catch(function () {})
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start)
  else start()
})()
