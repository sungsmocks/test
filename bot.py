import sys
import time
import argparse
import random
import requests
import json
import os
import csv
import inspect
import platform
from seleniumbase import SB
from utils import get_otp
from dotenv import load_dotenv

load_dotenv()

if platform.system() == "Windows":
    def lock_file(file):
        pass

    def unlock_file(file):
        pass
else:
    import fcntl

    def lock_file(file):
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)

    def unlock_file(file):
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

COUNTRY_POOL = [
    "United States of America",
    "Germany",
    "Great Britain",
    "France",
    "Japan",
    "Italy",
    "Australia",
    "Canada",
    "Spain",
    "Brazil",
]


def is_truthy(value):
    return str(value).lower() in {"1", "true", "yes", "y"}


def is_ci():
    return is_truthy(os.getenv("CI")) or is_truthy(os.getenv("GITHUB_ACTIONS"))


def build_chromium_args():
    width = random.randint(1200, 1440)
    height = random.randint(720, 900)
    return [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--window-size={width},{height}",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--mute-audio",
    ]


def create_sb():
    is_github = is_ci()
    headless_env = is_truthy(os.getenv("HEADLESS"))
    chromium_args = build_chromium_args()

    try:
        sig_params = inspect.signature(SB).parameters
    except (TypeError, ValueError):
        sig_params = {}

    sb_kwargs = {"uc": True}

    if is_github:
        if "xvfb" in sig_params:
            sb_kwargs["xvfb"] = True
    elif headless_env:
        if "headless2" in sig_params:
            sb_kwargs["headless2"] = True
        elif "headless" in sig_params:
            sb_kwargs["headless"] = True

    if is_github or headless_env:
        for key in ("no_sandbox", "disable_gpu", "disable_dev_shm"):
            if key in sig_params:
                sb_kwargs[key] = True

    if chromium_args:
        if "chromium_arg" in sig_params:
            sb_kwargs["chromium_arg"] = chromium_args
        elif "chromium_args" in sig_params:
            sb_kwargs["chromium_args"] = chromium_args

    if sig_params:
        sb_kwargs = {k: v for k, v in sb_kwargs.items() if k in sig_params}
    sb = SB(**sb_kwargs)

    if (
        chromium_args
        and hasattr(sb, "add_chromium_arg")
        and "chromium_arg" not in sig_params
        and "chromium_args" not in sig_params
    ):
        for arg in chromium_args:
            try:
                sb.add_chromium_arg(arg)
            except Exception:
                pass

    return sb


def normalize_field(name):
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def resolve_column(fieldnames, candidates):
    normalized = {normalize_field(name): name for name in fieldnames if name}
    for candidate in candidates:
        key = normalize_field(candidate)
        if key in normalized:
            return normalized[key]
    return None


def load_row_by_index(row_index, data_path="data.csv"):
    if row_index < 0:
        return None, "invalid_index"
    if not os.path.exists(data_path):
        return None, "missing_data"

    with open(data_path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None, "missing_header"

        email_key = resolve_column(reader.fieldnames, ["email"])
        password_key = resolve_column(reader.fieldnames, ["password", "pass"])
        first_key = resolve_column(reader.fieldnames, ["first_name", "firstname", "first"])
        last_key = resolve_column(reader.fieldnames, ["last_name", "lastname", "last"])
        zip_key = resolve_column(reader.fieldnames, ["zip_code", "zipcode", "zip"])

        if not all([email_key, password_key, first_key, last_key, zip_key]):
            return None, "missing_columns"

        for index, row in enumerate(reader):
            if index == row_index:
                row_data = {
                    "email": row.get(email_key, "").strip(),
                    "password": row.get(password_key, "").strip(),
                    "first_name": row.get(first_key, "").strip(),
                    "last_name": row.get(last_key, "").strip(),
                    "zip_code": row.get(zip_key, "").strip(),
                }
                if not all(row_data.values()):
                    return None, "missing_values"
                return row_data, None

    return None, "no_rows"


def human_pause(sb, min_s=0.15, max_s=0.45):
    try:
        sb.cdp.sleep(random.uniform(min_s, max_s))
    except Exception:
        time.sleep(random.uniform(min_s, max_s))


def human_mouse_move(sb, selector):
    try:
        js_code = f"""
        (function() {{
            let el = null;
            const selector = {json.dumps(selector)};
            try {{
                el = document.querySelector(selector);
            }} catch(e) {{}}
            if (!el && selector.startsWith('/')) {{
                const result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                el = result.singleNodeValue;
            }}
            if (!el) return null;
            el.scrollIntoView({{ block: 'center', inline: 'center' }});
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.left + rect.width / 2 + (Math.random() - 0.5) * rect.width * 0.3,
                y: rect.top + rect.height / 2 + (Math.random() - 0.5) * rect.height * 0.3
            }};
        }})();
        """
        pos = sb.cdp.evaluate(js_code)
        if pos and "x" in pos and "y" in pos:
            sb.cdp.sleep(random.uniform(0.05, 0.15))
            sb.cdp.gui_hover_element(selector)
            sb.cdp.sleep(random.uniform(0.05, 0.1))
    except Exception:
        pass


def human_click(sb, selector):
    try:
        sb.cdp.wait_for_element(selector, timeout=10)
        human_pause(sb, 0.05, 0.2)
        human_mouse_move(sb, selector)
        sb.cdp.sleep(random.uniform(0.1, 0.25))
        sb.cdp.click(selector)
        sb.cdp.sleep(random.uniform(0.15, 0.35))
        return True
    except Exception:
        try:
            sb.cdp.click(selector)
            return True
        except Exception:
            print(f"Click failed on selector: {selector}")
            return False


def select_option_by_text_strict(sb, selector, text, timeout=10):
    try:
        sb.cdp.wait_for_element(selector, timeout=timeout)
        human_mouse_move(sb, selector)
        human_pause(sb, 0.05, 0.2)
        sb.cdp.select_option_by_text(selector, (text or "").strip())
        sb.cdp.sleep(random.uniform(0.2, 0.4))
        return True
    except Exception:
        pass

    js_code = f"""
    (function() {{
        let el = null;
        const selector = {json.dumps(selector)};
        const targetText = {json.dumps((text or "").strip())};
        try {{
            el = document.querySelector(selector);
        }} catch (e) {{}}
        if (!el && selector.startsWith('/')) {{
            const result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            el = result.singleNodeValue;
        }}
        if (!el) return {{ok: false, reason: 'select not found'}};
        const options = Array.from(el.options || []);
        const match = options.find(o => (o.textContent || '').trim() === targetText);
        if (!match) return {{ok: false, reason: 'option not found'}};
        el.value = match.value;
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return {{ok: true}};
    }})();
    """
    result = sb.cdp.evaluate(js_code)
    return isinstance(result, dict) and result.get("ok", False)


def select_option_by_text_safe(sb, selector, text, timeout=10):
    def _alt_text_candidates(t):
        t = (t or "").strip()
        if not t:
            return []
        alts = [t]
        alt = t.replace("&", "and")
        if alt != t:
            alts.append(alt)
        alt2 = t.replace(" and ", " & ")
        if alt2 != t:
            alts.append(alt2)
        out = []
        for a in alts:
            if a not in out:
                out.append(a)
        return out

    try:
        sb.cdp.wait_for_element(selector, timeout=timeout)
        human_mouse_move(sb, selector)
        human_pause(sb, 0.05, 0.2)
        for candidate in _alt_text_candidates(text):
            try:
                sb.cdp.select_option_by_text(selector, candidate)
                sb.cdp.sleep(random.uniform(0.2, 0.4))
                return True
            except Exception:
                continue
    except Exception:
        pass

    js_code = f"""
    (function() {{
        let el = null;
        const selector = {json.dumps(selector)};
        const targetTextRaw = {json.dumps(text)};
        try {{
            el = document.querySelector(selector);
        }} catch (e) {{}}
        if (!el && selector.startsWith('/')) {{
            const result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            el = result.singleNodeValue;
        }}
        if (!el) return {{ok: false, reason: 'select not found'}};

        const normalize = (s) => (s || '')
            .replace(/\\u00a0/g, ' ')
            .replace(/\\s+/g, ' ')
            .trim()
            .toLowerCase()
            .replace(/&/g, 'and');

        const options = Array.from(el.options || []);
        const targetText = (targetTextRaw || '').trim();
        const nt = normalize(targetText);
        if (!nt) return {{ok: false, reason: 'empty target text'}};

        let match = options.find(o => (o.textContent || '').trim() === targetText);

        if (!match) {{
            match = options.find(o => normalize(o.textContent) === nt);
        }}

        if (!match) {{
            match = options.find(o => normalize(o.textContent).includes(nt) || nt.includes(normalize(o.textContent)));
        }}

        if (!match) return {{ok: false, reason: 'option not found'}};

        el.value = match.value;
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return {{ok: true, chosen: (match.textContent || '').trim()}};
    }})();
    """
    result = sb.cdp.evaluate(js_code)
    return isinstance(result, dict) and result.get("ok", False)


def click_add_another_for_select(sb, selector, timeout=8):
    try:
        sb.cdp.wait_for_element(selector, timeout=timeout)
    except Exception:
        return False

    js_code = """
    (function() {
        let el = null;
        const selector = __SELECTOR__;
        try {
            el = document.querySelector(selector);
        } catch (e) {}
        if (!el && selector.startsWith('/')) {
            const result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            el = result.singleNodeValue;
        }
        if (!el) return {ok: false, reason: 'select not found'};

        const isVisible = (node) => {
            if (!node) return false;
            const style = window.getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const r = node.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        };

        const looksLikeAddAnother = (node) => {
            if (!node) return false;
            const t = (node.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            return t === 'add another' || t.includes('add another');
        };

        const queryAddBtn = (root) => {
            if (!root || !root.querySelector) return null;
            return root.querySelector(
                '[data-qa="add-button"], button[data-qa="add-button"], span[data-qa="add-button"], [aria-label*="add another" i]'
            );
        };

        let container = el;
        for (let depth = 0; depth < 10 && container; depth++) {
            let btn = queryAddBtn(container);
            if (btn && isVisible(btn)) {
                btn.scrollIntoView({block: 'center', inline: 'center'});
                btn.click();
                return {ok: true, via: 'qa'};
            }

            const candidates = Array.from(container.querySelectorAll('button,a,span,div'))
                .filter(n => looksLikeAddAnother(n) && isVisible(n));
            if (candidates.length) {
                const chosen = candidates.find(n => n.tagName === 'BUTTON') || candidates[0];
                chosen.scrollIntoView({block: 'center', inline: 'center'});
                chosen.click();
                return {ok: true, via: 'text'};
            }

            container = container.parentElement;
        }

        return {ok: false, reason: 'add button not found'};
    })();
    """
    js_code = js_code.replace("__SELECTOR__", json.dumps(selector))

    result = sb.cdp.evaluate(js_code)
    if isinstance(result, dict) and result.get("ok", False):
        sb.cdp.sleep(random.uniform(0.4, 0.8))
        return True
    return False


def enter_otp_code(sb, otp, timeout=60, fallback_selector=None):
    otp = (otp or "").strip()
    if not otp:
        return False

    js_fill = f"""
    (function() {{
      const code = {json.dumps(otp)};
      const isVisible = (el) => {{
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }};

      const nativeSet = (el, val) => {{
        try {{
          const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, val);
        }} catch (e) {{
          el.value = val;
        }}
        try {{ el.dispatchEvent(new Event('input', {{bubbles:true}})); }} catch(e){{}}
        try {{ el.dispatchEvent(new Event('change', {{bubbles:true}})); }} catch(e){{}}
        try {{ el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}})); }} catch(e){{}}
      }};

      const collectInputs = (root) => {{
        const inputs = [];
        const pushAll = (node) => {{
          if (!node) return;
          if (node.querySelectorAll) {{
            inputs.push(...Array.from(node.querySelectorAll('input')));
          }}
          if (node.querySelectorAll) {{
            const withShadow = Array.from(node.querySelectorAll('*')).filter(n => n && n.shadowRoot);
            for (const host of withShadow) {{
              try {{
                inputs.push(...Array.from(host.shadowRoot.querySelectorAll('input')));
              }} catch (e) {{}}
            }}
          }}
        }};
        pushAll(root);
        return Array.from(new Set(inputs)).filter(isVisible);
      }};

      const tryFillInRoot = (root) => {{
        const inputs = collectInputs(root);
        if (!inputs.length) return {{ok:false, reason:'no inputs'}};

        let single = inputs.find(i => (i.getAttribute('autocomplete')||'').toLowerCase() === 'one-time-code');
        if (!single) single = inputs.find(i => /otp|one.?time|verification|verify|code/i.test(i.name||'') || /otp|verification|code/i.test(i.id||''));
        if (!single) single = inputs.find(i => /otp|verification|code/i.test(i.getAttribute('aria-label')||'') || /otp|verification|code/i.test(i.getAttribute('placeholder')||''));

        if (single) {{
          single.focus();
          nativeSet(single, code);
          return {{ok:true, mode:'single'}};
        }}

        const split = inputs
          .filter(i => {{
            const ml = parseInt(i.getAttribute('maxlength')||'0',10);
            return ml === 1;
          }});

        if (split.length >= 6) {{
          const first6 = split.slice(0, 6);
          for (let idx=0; idx<6; idx++) {{
            const el = first6[idx];
            el.focus();
            nativeSet(el, (code[idx] || ''));
          }}
          return {{ok:true, mode:'split', count:first6.length}};
        }}

        return {{ok:false, reason:'no otp pattern', inputCount: inputs.length}};
      }};

      let r = tryFillInRoot(document);
      if (r && r.ok) return r;

      if (__FALLBACK_SELECTOR__) {{
        try {{
          const fs = __FALLBACK_SELECTOR__;
          let el = document.querySelector(fs);
          if (!el && fs.startsWith('/')) {{
            el = document.evaluate(fs, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
          }}
          if (el) {{
            el.focus();
            nativeSet(el, code);
            return {{ok:true, mode:'explicit_fallback'}};
          }}
        }} catch(e) {{}}
      }}

      const iframes = Array.from(document.querySelectorAll('iframe'));
      for (const f of iframes) {{
        try {{
          const doc = f.contentDocument;
          if (!doc) continue;
          const rr = tryFillInRoot(doc);
          if (rr && rr.ok) return {{...rr, via:'iframe'}};
        }} catch (e) {{}}
      }}

      return r || {{ok:false, reason:'unknown'}};
    }})();
    """

    js_fill = js_fill.replace("__FALLBACK_SELECTOR__", json.dumps(fallback_selector) if fallback_selector else "null")
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = sb.cdp.evaluate(js_fill)
            if isinstance(last, dict) and last.get("ok"):
                return True
        except Exception:
            last = None
        sb.cdp.sleep(0.8)

    try:
        url = sb.cdp.get_current_url()
        counts = sb.cdp.evaluate(
            "(function(){return {inputs: document.querySelectorAll('input').length, iframes: document.querySelectorAll('iframe').length, url: location.href}})();"
        )
        print(f"OTP entry failed after timeout. url={url} info={counts} last={last}")
    except Exception:
        pass

    return False


def select_nth_named_select_option(sb, name_contains, index, option_text, timeout=12):
    def _list_selects():
        js = f"""
        (function() {{
            const needle = {json.dumps(name_contains)};
            const isVisible = (node) => {{
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const r = node.getBoundingClientRect();
                return (r.width > 0 && r.height > 0);
            }};
            const selects = Array.from(document.querySelectorAll('select'))
                .filter(s => s && s.name && s.name.includes(needle))
                .map(s => ({{
                    name: s.name,
                    visible: isVisible(s),
                    disabled: !!s.disabled,
                    options: (s.options ? s.options.length : 0)
                }}));
            return {{count: selects.length, selects}};
        }})();
        """
        return sb.cdp.evaluate(js) or {}

    for attempt in range(6):
        info = _list_selects()
        selects = info.get("selects") if isinstance(info, dict) else None
        if not selects:
            if attempt == 5 and is_truthy(os.getenv("DEBUG_DOM")):
                try:
                    all_names = sb.cdp.evaluate(
                        "(function(){return Array.from(document.querySelectorAll('select')).map(s=>s.name||s.id||null).filter(Boolean).slice(0,50)})();"
                    )
                    print(f"DEBUG_DOM no selects matching '{name_contains}'. First select names/ids: {all_names}")
                except Exception:
                    pass
            sb.cdp.sleep(0.8)
            continue

        if len(selects) < index:
            last_name = selects[-1].get("name")
            if last_name:
                last_sel = f"select[name={json.dumps(last_name)}]"
                click_add_another_for_select(sb, last_sel, timeout=4)
            sb.cdp.sleep(0.9)
            continue

        target = selects[index - 1]
        target_name = target.get("name")
        target_visible = target.get("visible")

        if not target_visible and index > 1:
            prev_name = selects[index - 2].get("name")
            if prev_name:
                prev_sel = f"select[name={json.dumps(prev_name)}]"
                click_add_another_for_select(sb, prev_sel, timeout=4)
            sb.cdp.sleep(0.9)
            continue

        if not target_name:
            sb.cdp.sleep(0.5)
            continue

        target_selector = f"select[name={json.dumps(target_name)}]"
        return select_option_by_text_safe(sb, target_selector, option_text, timeout=timeout)

    return False


def select_random_option_in_nth_named_select(sb, name_contains, index, exclude_texts=None, include_texts=None, timeout=12):
    exclude_texts = {t.strip() for t in (exclude_texts or []) if t and str(t).strip()}
    include_texts = {t.strip() for t in (include_texts or []) if t and str(t).strip()}

    js = f"""
    (function() {{
        const needle = {json.dumps(name_contains)};
        const isVisible = (node) => {{
            if (!node) return false;
            const style = window.getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const r = node.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        }};
        const selects = Array.from(document.querySelectorAll('select'))
            .filter(s => s && s.name && s.name.includes(needle));
        const i = {index} - 1;
        if (selects.length <= i) return {{ok:false, reason:'select index missing', count: selects.length}};
        const sel = selects[i];
        const options = Array.from(sel.options || [])
            .map(o => ({{
                text: (o.textContent || '').replace(/\\s+/g,' ').trim(),
                value: o.value,
                disabled: !!o.disabled
            }}));
        return {{
            ok: true,
            name: sel.name,
            visible: isVisible(sel),
            options
        }};
    }})();
    """

    info = sb.cdp.evaluate(js)
    if not (isinstance(info, dict) and info.get("ok")):
        return None

    sel_name = info.get("name")
    options = info.get("options") or []
    if not sel_name or not options:
        return None

    cleaned = []
    for opt in options:
        t = (opt.get("text") or "").strip()
        if not t:
            continue
        lt = t.lower()
        if lt in {"select", "select one", "please select", "-", "--"}:
            continue
        if t in exclude_texts:
            continue
        if include_texts and t not in include_texts:
            continue
        if opt.get("disabled"):
            continue
        cleaned.append(t)

    if not cleaned:
        return None

    random.shuffle(cleaned)
    target_selector = f"select[name={json.dumps(sel_name)}]"
    for candidate in cleaned[:10]:
        if select_option_by_text_safe(sb, target_selector, candidate, timeout=timeout):
            return candidate

    return None


def human_type(sb, selector, text):
    try:
        sb.cdp.wait_for_element(selector, timeout=10)
        human_mouse_move(sb, selector)
        human_pause(sb, 0.1, 0.3)
        
        # Gigya and Akamai monitor JS 'value' setters and dispatchEvent speeds to flag bots (zero keyup/keydown latency).
        # We must use the browser's native protocol to natively simulate physical hardware keystrokes.
        sb.cdp.click(selector)
        human_pause(sb, 0.1, 0.2)
        sb.cdp.type(selector, text)
        human_pause(sb, 0.2, 0.4)

    except Exception:
        print(f"Typing error on selector: {selector}")
        try:
            sb.cdp.type(selector, text)
        except Exception:
            pass


def run_registration(
    email,
    password,
    first_name,
    last_name,
    zip_code,
    country="United States of America",
    row_index=None,
):
    row_label = f"row index {row_index}" if row_index is not None else "manual run"
    print(f"Starting registration for {row_label}")

    sb = create_sb()
    with sb as sb:
        try:
            try:
                sb.driver.set_page_load_timeout(60)
                sb.driver.set_script_timeout(60)
                sb.driver.implicitly_wait(5)
                sb.driver.set_window_size(
                    random.randint(1200, 1440),
                    random.randint(720, 900),
                )
            except Exception:
                pass

            try:
                stealth_js = """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                try { 
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                      parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                    );
                } catch(e) {}
                """
                sb.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
            except Exception:
                pass

            print("Navigating to LA28 Registration via CDP Mode...")
            for attempt in range(3):
                try:
                    sb.activate_cdp_mode("https://tickets.la28.org/mycustomerdata/?affiliate=28A")
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2)

            email_selector = '#register-site-login > div:nth-child(1) > div.gigya-layout-row > div > input'

            print("Waiting for page to load...")
            try:
                sb.cdp.wait_for_element(email_selector, timeout=20)
                human_pause(sb, 0.8, 1.6)
            except Exception:
                sb.cdp.sleep(10)
            print("Page load wait complete.")

            try:
                if sb.cdp.is_element_visible("button#onetrust-accept-btn-handler"):
                    print("Accepting cookies...")
                    human_click(sb, "button#onetrust-accept-btn-handler")
                    human_pause(sb, 0.3, 0.7)
            except Exception:
                pass

            print("Filling form fields...")

            human_type(sb, email_selector, email)

            human_type(
                sb,
                "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[2]/div[1]/div/input",
                first_name,
            )

            human_type(
                sb,
                "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[2]/div[2]/div/input",
                last_name,
            )

            human_type(
                sb,
                "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[4]/div/input",
                password,
            )

            print("Selecting country...")
            select_option_by_text_strict(
                sb,
                "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[5]/select",
                country,
                timeout=10,
            )

            human_pause(sb, 0.6, 1.2)

            human_type(
                sb,
                "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[6]/div/input",
                zip_code,
            )

            print("  - Checking required checkboxes...")
            human_click(sb, "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[8]/input")
            sb.cdp.sleep(random.uniform(0.2, 0.5))
            human_click(sb, "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[9]/input")
            sb.cdp.sleep(random.uniform(0.3, 0.6))

            print("Submitting form...")
            human_click(sb, "/html/body/div[2]/div[2]/div[2]/div[2]/div/form/div[2]/div[3]/div[12]/input")

            otp_input_xpath = "#gigya-textbox-code"
            otp_wait_candidates = [
                'input[autocomplete="one-time-code"]',
                'input[type="tel"]',
                'input[maxlength="1"]',
                'input[name*="code" i]',
                'input[aria-label*="code" i]',
                'input[placeholder*="code" i]',
                otp_input_xpath,
            ]

            print("Waiting for OTP page to load...")
            loaded = False
            for cand in otp_wait_candidates:
                try:
                    sb.cdp.wait_for_element(cand, timeout=6)
                    loaded = True
                    break
                except Exception:
                    continue

            if not loaded:
                try:
                    sb.cdp.sleep(3)
                except Exception:
                    time.sleep(3)

            print("OTP page load wait complete.")

            if is_truthy(os.getenv("AUTO_RESEND_OTP")):
                try:
                    js = """
                    (function(){
                      const btns = Array.from(document.querySelectorAll('button,input[type="button"],input[type="submit"],a'));
                      const norm = (t)=> (t||'').replace(/\\s+/g,' ').trim().toLowerCase();
                      const targets = btns.filter(b=>{
                        const t = norm(b.textContent || b.value || b.getAttribute('aria-label') || '');
                        return t.includes('resend') || t.includes('send code') || t.includes('send verification') || t === 'send';
                      });
                      const b = targets[0];
                      if (!b) return {ok:false, reason:'no send/resend button found'};
                      b.scrollIntoView({block:'center', inline:'center'});
                      b.click();
                      return {ok:true, clicked: (b.textContent||b.value||b.getAttribute('aria-label')||'').trim()};
                    })();
                    """
                    r = sb.cdp.evaluate(js)
                    if isinstance(r, dict) and r.get("ok"):
                        sb.cdp.sleep(random.uniform(0.4, 0.8))
                except Exception:
                    pass

            imap_user = os.getenv("IMAP_USER")
            imap_pass = os.getenv("IMAP_PASS")
            if not imap_user or not imap_pass:
                print("IMAP credentials missing; cannot retrieve OTP.")
                return False

            # Poll for OTP up to 2 minutes, every 10 seconds
            otp = None
            otp_deadline = time.time() + 120  # 2 minutes max
            attempt = 0

            while time.time() < otp_deadline and not otp:
                attempt += 1
                otp = get_otp(imap_user, imap_pass, email)
                if otp:
                    break

                remaining = int(otp_deadline - time.time())
                print(f"OTP not found yet (attempt {attempt}). Retrying in 10s... ({remaining}s left)")
                try:
                    sb.cdp.sleep(10)
                except Exception:
                    time.sleep(10)

            if otp:
                print("OTP retrieved.")

                if not enter_otp_code(sb, otp, timeout=60, fallback_selector=otp_input_xpath):
                    print("Failed to enter OTP.")
                    return False
                human_pause(sb, 0.6, 1.2)

                print("Clicking Verify...")
                verify_btn_xpath = "input.gigya-input-submit[value='Verify']"
                if not human_click(sb, verify_btn_xpath):
                    try:
                        js = """
                        (function(){
                          const norm = (t)=> (t||'').replace(/\\s+/g,' ').trim().toLowerCase();
                          const nodes = Array.from(document.querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]'));
                          const cand = nodes.find(n=>{const t=norm(n.textContent||n.value||n.getAttribute('aria-label')||''); return t==='verify' || t.includes('verify') || t.includes('continue');});
                          if(!cand) return {ok:false};
                          cand.scrollIntoView({block:'center', inline:'center'});
                          cand.click();
                          return {ok:true};
                        })();
                        """
                        r = sb.cdp.evaluate(js)
                        if isinstance(r, dict) and r.get("ok"):
                            sb.cdp.sleep(random.uniform(0.2, 0.5))
                    except Exception:
                        pass

                birth_year_selector = 'select[name^="additionalCustomerAttributes-1_"]'

                print("Waiting for profile page to load...")
                try:
                    sb.cdp.wait_for_element(birth_year_selector, timeout=18)
                    human_pause(sb, 0.8, 1.6)
                except Exception:
                    try:
                        sb.cdp.wait_for_element('select[name*="additionalCustomerAttributes"]', timeout=25)
                        human_pause(sb, 0.8, 1.6)
                    except Exception:
                        sb.cdp.sleep(10)
                print("Profile page load wait complete.")

                print("Profile page loaded.")
                human_pause(sb, 0.8, 1.6)

                birth_years = [
                    "1960",
                    "1961",
                    "1962",
                    "1963",
                    "1964",
                    "1965",
                    "1966",
                    "1967",
                    "1968",
                    "1969",
                    "1970",
                    "1971",
                    "1972",
                    "1973",
                    "1974",
                    "1975",
                    "1976",
                    "1977",
                    "1978",
                    "1979",
                    "1980",
                    "1981",
                    "1982",
                    "1983",
                    "1984",
                    "1985",
                    "1986",
                    "1987",
                    "1988",
                    "1989",
                    "1990",
                    "1991",
                    "1992",
                    "1993",
                    "1994",
                    "1995",
                    "1996",
                    "1997",
                    "1998",
                    "1999",
                    "2000",
                    "2001",
                    "2002",
                    "2003",
                    "2004",
                    "2005",
                    "2006",
                    "2007",
                ]

                print("Selecting birth year...")
                try:
                    chosen_year = select_random_option_in_nth_named_select(
                        sb,
                        name_contains="additionalCustomerAttributes",
                        index=1,
                        exclude_texts=[],
                        include_texts=birth_years,
                        timeout=14,
                    )
                    if chosen_year:
                        print(f"  Selected birth year: {chosen_year}")
                        human_pause(sb, 0.8, 1.6)
                    else:
                        random_year = random.choice(birth_years)
                        if select_option_by_text_safe(sb, birth_year_selector, random_year, timeout=12):
                            print(f"  Selected birth year (fallback): {random_year}")
                            human_pause(sb, 0.8, 1.6)
                        else:
                            print("  Birth year selection failed.")
                except Exception:
                    print("  Birth year selection failed.")

                if is_truthy(os.getenv("DEBUG_DOM")):
                    try:
                        js = """
                        (function(){
                          const selects = Array.from(document.querySelectorAll('select'))
                            .map(s => ({name: s.name || null, id: s.id || null}))
                            .filter(x => x.name || x.id);
                          const fav = selects.filter(x => (x.name||'').toLowerCase().includes('favorite') || (x.id||'').toLowerCase().includes('favorite'));
                          return {
                            totalSelects: selects.length,
                            first10: selects.slice(0,10),
                            favorites: fav.slice(0,30),
                          };
                        })();
                        """
                        dbg = sb.cdp.evaluate(js)
                        print(f"DEBUG_DOM selects: {dbg}")
                    except Exception:
                        pass

                olympic_sports = [
                    "Basketball",
                    "Swimming",
                    "Artistic Gymnastics",
                    "Athletics",
                    "Football (Soccer)",
                    "Baseball",
                    "Olympic Ceremonies",
                    "Beach Volleyball",
                    "Tennis",
                    "Golf",
                    "Softball",
                    "Volleyball",
                    "Wrestling",
                    "Boxing",
                    "Skateboarding",
                ]

                chosen_sports = random.sample(olympic_sports, k=min(5, len(olympic_sports)))

                print(f"Selecting Olympic sport preferences ({len(chosen_sports)})...")
                selected_sports = []
                for i, sport in enumerate(chosen_sports, start=1):
                    select_timeout = 15 if i == 1 else 12
                    if select_nth_named_select_option(
                        sb,
                        name_contains="categoryFavorites",
                        index=i,
                        option_text=sport,
                        timeout=select_timeout,
                    ):
                        print(f"  Selected Olympic sport {i}: {sport}")
                        selected_sports.append(sport)
                        human_pause(sb, 0.6, 1.2)
                    else:
                        chosen = select_random_option_in_nth_named_select(
                            sb,
                            name_contains="categoryFavorites",
                            index=i,
                            exclude_texts=selected_sports,
                            timeout=select_timeout,
                        )
                        if chosen:
                            print(f"  Selected Olympic sport {i} (fallback): {chosen}")
                            selected_sports.append(chosen)
                            human_pause(sb, 0.6, 1.2)
                        else:
                            print(f"  Olympic sport {i} selection failed.")
                            continue

                teams = random.sample(COUNTRY_POOL, k=min(3, len(COUNTRY_POOL)))

                print(f"Selecting team preferences ({len(teams)})...")
                for i, team in enumerate(teams, start=1):
                    select_timeout = 15 if i == 1 else 12
                    if select_nth_named_select_option(
                        sb,
                        name_contains="artistFavorites",
                        index=i,
                        option_text=team,
                        timeout=select_timeout,
                    ):
                        print(f"  Selected team {i}: {team}")
                        human_pause(sb, 0.6, 1.2)
                    else:
                        print(f"  Team {i} selection failed.")
                        continue

                print("Saving profile...")

                save_clicked = False
                for sel in [
                    "app-sports-profile-save-section button",
                    "app-sports-profile-save-section ev-pl-button button",
                    "app-sports-profile-save-section [role='button']",
                ]:
                    if human_click(sb, sel):
                        save_clicked = True
                        break

                if not save_clicked:
                    print("Save button click failed with CSS, trying XPath.")
                    save_clicked = human_click(
                        sb,
                        "/html/body/div[3]/main/div/app-root/app-customer-data-page/app-sports-profile/app-sports-profile-save-section/section/div/div/div/ev-pl-button/button",
                    )

                if not save_clicked:
                    try:
                        js = """
                        (function(){
                          const norm = (t)=> (t||'').replace(/\\s+/g,' ').trim().toLowerCase();
                          const nodes = Array.from(document.querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]'));
                          const cand = nodes.find(n=>{const t=norm(n.textContent||n.value||n.getAttribute('aria-label')||''); return t==='save' || t.includes('save');});
                          if(!cand) return {ok:false};
                          cand.scrollIntoView({block:'center', inline:'center'});
                          cand.click();
                          return {ok:true};
                        })();
                        """
                        r = sb.cdp.evaluate(js)
                        if isinstance(r, dict) and r.get("ok"):
                            save_clicked = True
                    except Exception:
                        pass

                human_pause(sb, 4, 6)

                final_url = sb.cdp.get_current_url()
                if "mydatasuccess" in final_url:
                    print("=" * 60)
                    print("SIGNUP SUCCESSFUL!")
                    print("=" * 60)

                    if DISCORD_WEBHOOK_URL:
                        send_discord_webhook(row_index=row_index)
                    return True
                else:
                    print("Profile saved, checking status...")
                    return True
            else:
                print("Failed to get OTP within 2 minutes.")
                return False

        except Exception:
            print("Error in execution.")
            return False


def send_discord_webhook(row_index=None):
    if not DISCORD_WEBHOOK_URL:
        return

    fields = []
    if row_index is not None:
        fields.append({"name": "Row Index", "value": str(row_index), "inline": True})

    embed = {
        "title": "LA28 Registration Successful",
        "color": 5763719,
        "fields": fields,
        "footer": {"text": "LA28 Bot"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code == 204:
            print("Discord webhook sent successfully")
        else:
            print(f"Discord webhook failed: {response.status_code}")
    except Exception as e:
        print(f"Discord webhook error: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-index", type=int, default=None)
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--first")
    parser.add_argument("--last")
    parser.add_argument("--zip")

    args = parser.parse_args()

    if args.row_index is not None:
        row_data, reason = load_row_by_index(args.row_index)
        if reason == "no_rows":
            print("No rows remaining for the requested index.")
            sys.exit(0)
        if row_data is None:
            if reason == "missing_data":
                print("data.csv not found.")
            elif reason == "missing_columns":
                print("data.csv is missing required columns.")
            elif reason == "missing_values":
                print("Selected row is missing required values.")
            elif reason == "invalid_index":
                print("Row index must be >= 0.")
            else:
                print("Failed to load row for processing.")
            sys.exit(1)

        success = run_registration(
            email=row_data["email"],
            password=row_data["password"],
            first_name=row_data["first_name"],
            last_name=row_data["last_name"],
            zip_code=row_data["zip_code"],
            row_index=args.row_index,
        )
        sys.exit(0 if success else 1)

    if args.email:
        if not all([args.password, args.first, args.last, args.zip]):
            print("Direct mode requires email, password, first, last, and zip.")
            sys.exit(1)
        success = run_registration(
            args.email,
            args.password,
            args.first,
            args.last,
            args.zip,
            row_index=None,
        )
        sys.exit(0 if success else 1)

    parser.error("Requires --row-index or --email.")


if __name__ == "__main__":
    main()
