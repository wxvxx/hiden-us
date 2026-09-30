#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,re,sys,time,random,requests

# --- 浏览器内核：优先 patchright（隐身版 Playwright），没装则退回 playwright ---
try:
    from patchright.sync_api import sync_playwright
    ENGINE = "patchright"
except Exception:
    from playwright.sync_api import sync_playwright
    ENGINE = "playwright"

# --- 环境变量 ---
HIDENCLOUD_COOKIE = os.environ.get('HIDENCLOUD_COOKIE') or ""    # remember_web cookie 值，必填
HIDENCLOUD_EMAIL  = os.environ.get('HIDENCLOUD_EMAIL') or "6886766@gmail.com"           # 登录邮箱,可选，作为备用,TG通知需要填写
HIDENCLOUD_PASSWORD     = os.environ.get('HIDENCLOUD_PASSWORD') or "Qaz567890@"        # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token,可选
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID,可选

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# 固定使用一个真实的 Chrome 用户目录：Cloudflare 通过后会写入 cf_clearance，
# 复用该目录可以让后续运行直接跳过安全验证页
PROFILE_DIR = os.environ.get('BROWSER_PROFILE_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.chrome-profile')

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        # log(f"请求出口IP完成, status={resp.status_code}")
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    
    # 获取运行时间
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in HIDENCLOUD_EMAIL:
        name, domain = HIDENCLOUD_EMAIL.split('@', 1)
        if len(name) > 4:
            masked_HIDENCLOUD_EMAIL = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_HIDENCLOUD_EMAIL = f"{name}@{domain}"
    else:
        masked_HIDENCLOUD_EMAIL = HIDENCLOUD_EMAIL[:2] + '****' 

    text = (
        f"🎉 HidenCloud 续期通知\n\n"
        f"{status}\n"
        f"👤 账号: {masked_HIDENCLOUD_EMAIL}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 续期时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

TURNSTILE_IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'

def _turnstile_count(page):
    """页面上 Turnstile 验证框的数量"""
    try:
        return page.locator(TURNSTILE_IFRAME_SELECTOR).count()
    except Exception:
        return 0

def _turnstile_box(page):
    """第一个可见验证框的位置(CSS像素)，没有则返回 None"""
    try:
        locator = page.locator(TURNSTILE_IFRAME_SELECTOR)
        for i in range(locator.count()):
            element = locator.nth(i)
            try:
                if element.is_visible():
                    box = element.bounding_box()
                    if box:
                        return box
            except Exception:
                continue
    except Exception:
        pass
    return None

def turnstile_token(page):
    """读取 Turnstile token（官方 JS 接口 或 表单隐藏域），未通过时返回空串"""
    try:
        token = page.evaluate(
            "() => { try { if (window.turnstile && window.turnstile.getResponse) {"
            " const t = window.turnstile.getResponse(); if (t) return t; } } catch (e) {}"
            " const el = document.querySelector('input[name=\"cf-turnstile-response\"]');"
            " return el ? (el.value || '') : ''; }")
        return token if isinstance(token, str) else ''
    except Exception:
        return ''

def is_turnstile_solved(page):
    """token 已拿到 或 页面上已经没有验证框，都算通过"""
    if turnstile_token(page):
        return True
    return _turnstile_count(page) == 0

def _human_move(page, x, y):
    """拟人化鼠标移动后点击"""
    try:
        page.mouse.move(x - random.uniform(60, 180), y - random.uniform(40, 130), steps=random.randint(8, 14))
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.move(x, y, steps=random.randint(4, 8))
        time.sleep(random.uniform(0.1, 0.3))
    except Exception:
        pass

def click_turnstile(page):
    """点击“验证您是真人”复选框。组件内部是 closed shadow DOM，只能按坐标发真实鼠标点击"""
    box = _turnstile_box(page)
    if not box:
        return False
    x = box['x'] + 30                       # 复选框位于验证框左侧、垂直居中
    y = box['y'] + box['height'] / 2
    log(f"🖱️ 点击 Turnstile 验证框 ({x:.0f},{y:.0f})")
    _human_move(page, x, y)
    try:
        page.mouse.click(x, y)
        return True
    except Exception as e:
        log(f"⚠️ 点击验证框失败: {e}")
        return False

def wait_page_ready(page, goal, timeout=180, tag="页面", first_wait=8, click_interval=10,
                    reload_after=35, max_reloads=2):
    """
    等待目标状态（登录表单出现 / dashboard 打开），期间处理 Cloudflare 安全验证：
    先等验证框自动通过（默认 8 秒），没通过就点验证框；
    一直没进展（验证框压根没出来，或点了多次仍拿不到 token）就重新加载页面重试。
    """
    deadline = time.time() + timeout
    next_click = time.time() + first_wait
    next_log = time.time() + 15
    last_activity = time.time()
    first_click = None
    clicks = 0
    reloads = 0
    while time.time() < deadline:
        if goal():
            return True
        has_widget = _turnstile_box(page) is not None
        if has_widget:
            last_activity = time.time()
            if time.time() >= next_click and not turnstile_token(page):
                click_turnstile(page)
                clicks += 1
                first_click = first_click or time.time()
                next_click = time.time() + click_interval + random.uniform(0, 3)
        elif time.time() - last_activity > 3:
            try:    # 页面没有验证框时轻微动一下鼠标，保持“有人在使用”的状态
                page.mouse.move(random.randint(80, 900), random.randint(80, 600), steps=5)
            except Exception:
                pass

        if reload_after and reloads < max_reloads:
            stuck_no_widget = not has_widget and time.time() - last_activity > reload_after
            stuck_no_token = clicks >= 3 and first_click and time.time() - first_click > 90
            if stuck_no_widget or stuck_no_token:
                reloads += 1
                last_activity = time.time()
                first_click, clicks = None, 0
                log(f"🔄 {tag}：长时间没有进展，重新加载页面（第 {reloads} 次）重试")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    log(f"⚠️ 页面重新加载失败: {e}")

        if time.time() >= next_log:
            next_log = time.time() + 15
            log(f"⏳ {tag}：等待中（剩余 {int(deadline - time.time())}s，"
                f"token={'已获取' if turnstile_token(page) else '未获取'}，已点击 {clicks} 次）")
        time.sleep(0.4)
    return False

def solve_turnstile(page, timeout=90, tag="Cloudflare Turnstile", wait_appear=0):
    """
    处理当前页面的 Turnstile：等自动通过 / 点击验证框，直到拿到 token。
    wait_appear: 先等验证框渲染出来的秒数（弹窗刚打开时组件可能还没加载）
    """
    if wait_appear > 0:
        appear_until = time.time() + wait_appear
        while time.time() < appear_until and _turnstile_count(page) == 0:
            time.sleep(0.5)

    if _turnstile_count(page) == 0 and not turnstile_token(page):
        return True         # 页面上没有验证框，无需处理

    log(f"🛡️ 检测到 {tag}...")
    if wait_page_ready(page, lambda: bool(turnstile_token(page)), timeout=timeout,
                       tag=tag, reload_after=None):
        log(f"✅ {tag}已通过")
        return True
    log(f"❌ {tag}未通过")
    return False

def handle_cloudflare(page, timeout=60):
    """页面跳转后处理可能出现的 Cloudflare Turnstile 验证（页面内容能读到就不用等他）"""
    return solve_turnstile(page, timeout=timeout, tag="Cloudflare Turnstile")

def _dashboard_ready(page):
    """dashboard 是否已加载出服务列表"""
    try:
        if "auth/login" in page.url:
            return False
        if _turnstile_count(page) > 0 and not turnstile_token(page):
            return False        # 还在过验证
        return page.locator('a[href*="/service/"]').count() > 0
    except Exception:
        return False

def _click_my_account(page):
    """点击右上角 “My Account”（账号密码登录后可能先落在官网首页）"""
    for locator in (page.locator('a:has-text("My Account"), button:has-text("My Account")').first,
                    page.get_by_text("My Account", exact=True).first):
        try:
            if locator.count() > 0 and locator.is_visible():
                log("🖱️ 点击右上角 “My Account” 进入 dashboard...")
                locator.click()
                return True
        except Exception as e:
            log(f"⚠️ 点击 “My Account” 失败: {e}")
    return False

def goto_dashboard(page, timeout=180):
    """登录后进入 dashboard：账号密码登录可能先落到官网首页，Cookie 登录则直接就是 dashboard"""
    if _dashboard_ready(page):
        return True
    # 官网首页：按右上角 “My Account” 进 dashboard
    if "dash." not in page.url:
        _click_my_account(page)
    if "/dashboard" not in page.url:
        log("➡ 打开 dashboard 页面...")
        try:
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"⚠️ 打开 dashboard 失败: {e}")
    if wait_page_ready(page, _dashboard_ready, timeout=timeout, tag="dashboard 加载"):
        return True
    if _click_my_account(page):     # 兜底再试一次右上角入口
        return wait_page_ready(page, _dashboard_ready, timeout=60, tag="dashboard 加载")
    return False

def login(page):
    # 1. Cookie 登录尝试
    if HIDENCLOUD_COOKIE:
        log("📇 尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
                'value': HIDENCLOUD_COOKIE,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
            # 可能先经过安全验证页；登录表单出现说明 Cookie 无效，dashboard 打开说明 Cookie 有效
            wait_page_ready(
                page,
                lambda: "auth/login" not in page.url
                        or page.locator('input[type="HIDENCLOUD_PASSWORD"]').count() > 0,
                timeout=180, tag="Cookie 登录", reload_after=35, max_reloads=2)
            log(f"📝 当前Title: {page.title()}")
            if "auth/login" not in page.url:
                if goto_dashboard(page):
                    log("✅ Cookie 登录成功！当前已到达dashboard页面")
                else:
                    log("⚠️ Cookie 登录后 dashboard 未加载出服务列表，继续尝试...")
                return True
            log("❌ Cookie 失效，请更换")
        except Exception as e:
            log(f"⚠️ Cookie 登录异常: {e}")

    # 2. 账号密码登录
    if not HIDENCLOUD_EMAIL or not HIDENCLOUD_PASSWORD:
        return False
    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        # 先过“Security Verification”安全验证页：等 10 秒左右，没过就点验证框
        log("🛡️ 等待安全验证页通过（先等自动验证，未通过会自动点击验证框）...")
        if not wait_page_ready(page, lambda: page.locator('input[type="HIDENCLOUD_PASSWORD"]').count() > 0,
                               timeout=180, tag="登录页安全验证", reload_after=35, max_reloads=2):
            log("❌ 长时间未通过安全验证，无法进入登录表单。")
            page.screenshot(path="login_verification_failed.png")
            return False
        log("✅ 已进入登录表单页")

        # 填账号密码（当前字段名为 username，兼容 HIDENCLOUD_EMAIL）
        user_input = page.locator('input[name="username"]:visible, input[name="HIDENCLOUD_EMAIL"]:visible').first
        user_input.wait_for(state="visible", timeout=20000)
        user_input.fill(HIDENCLOUD_EMAIL)
        page.locator('input[name="HIDENCLOUD_PASSWORD"]:visible').first.fill(HIDENCLOUD_PASSWORD)
        log("📝 已填写账号密码")

        # 表单上的 Turnstile：先等自动通过，没通过就点验证框，直到拿到 token
        if solve_turnstile(page, timeout=120, tag="登录表单 Turnstile", wait_appear=10):
            log("✅ 登录表单 Turnstile 已通过")
        else:
            log("⚠️ 登录表单 Turnstile 未通过，仍尝试提交（可能失败）")
            page.screenshot(path="login_turnstile_failed.png")

        log("🖱️ 点击 “Sign in to your account” 登录...")
        page.locator('button[type="submit"]:visible').first.click()

        # 等待离开登录页；提交后若又出现验证框，继续处理
        deadline = time.time() + 120
        while time.time() < deadline and "/auth/login" in page.url:
            if _turnstile_count(page) and not turnstile_token(page):
                click_turnstile(page)
            time.sleep(0.5)

        if "/auth/login" in page.url:
            log("❌ 登录失败，仍停留在登录页。")
            page.screenshot(path="login_fail.png")
            return False

        log(f"✅ 账号密码登录成功！当前URL: {page.url}")
        # 账号密码登录后可能先落在官网首页，需要自己进 dashboard
        if goto_dashboard(page):
            log("✅ 已到达dashboard页面")
        else:
            log("⚠️ 未能在 dashboard 看到服务列表，继续尝试...")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        time.sleep(3)
        for attempt in range(2):
            html = page.content()
            log(f"📝 页面长度: {len(html)}, URL: {page.url}")

            # 方案1: 从 href 链接中提取 /service/数字/manage
            matches = re.findall(r'/service/(\d+)/manage', html) or re.findall(r'/service/(\d+)', html)
            if matches:
                server_id = matches[0]
                log(f"✅ 从链接中获取到 Server ID: {server_id}")
                return server_id

            # 方案2: 从 span 标签中提取 #数字 (如 "Free Server #218079")
            matches = re.findall(r'#(\d{4,})', html)
            if matches:
                server_id = matches[0]
                log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
                return server_id

            if attempt == 0:
                # 页面可能没加载完或在过验证，处理验证后再读一次
                log("⚠️ 未找到 Server ID，处理页面验证后重试...")
                handle_cloudflare(page, timeout=60)
                time.sleep(3)

        log("❌ 所有 URL 均未找到 Server ID")
        return None
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        page.screenshot(path="server_id_error.png")
        return None

def get_due_date(page):
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for attempt in range(2):
            body_text = page.locator("body").inner_text()
            for pattern in patterns:
                match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
                if match:
                    due_date = match.group(1).strip()
                    log(f"📅 获取到Due Date: {due_date}")
                    return due_date
            if attempt == 0:
                # 页面可能没加载完或在过验证，处理验证后再读一次
                log("⚠️ 未读取到 Due Date，处理页面验证后重试...")
                handle_cloudflare(page, timeout=60)
                time.sleep(3)
    except Exception as e:
        log(f"❌ 获取Due Date失败: {e}")
    return "未知"

def _wait_page_scripts(page, timeout=30):
    """等服务页脚本加载完成：Flowbite 没就绪时点 Renew 没有任何反应"""
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        try:
            state = page.evaluate(
                "() => ({ready: document.readyState,"
                " fb: typeof window.Flowbite !== 'undefined' || typeof window.flowbite !== 'undefined'})")
        except Exception:
            state = {}
        if state.get('fb') or state.get('ready') == 'complete':
            log("✅ 页面脚本已就绪")
            return True
        time.sleep(1)
    log(f"⚠️ 页面脚本等待超时（{state}），继续尝试")
    return False

def _show_renew_modal_by_dom(page):
    """兜底：页面脚本没就绪（点 Renew 没反应）时，直接把续期弹窗元素显示出来"""
    try:
        return bool(page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button')).find(
                b => (b.innerText || '').trim() === 'Renew' && b.getAttribute('data-modal-target'));
            const modal = btn ? document.getElementById(btn.getAttribute('data-modal-target')) : null;
            if (!modal) return false;
            modal.classList.remove('hidden');
            modal.setAttribute('aria-hidden', 'false');
            modal.style.display = 'flex';
            return true;
        }"""))
    except Exception:
        return False

def renew_service(page):

    try:
        log("➡ 进入续期流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)

        log("⏳ 等待服务页加载完成（Renew 是弹窗按钮，页面脚本没就绪时点了没反应）...")
        _wait_page_scripts(page, timeout=30)
        time.sleep(2)

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice"):visible').first

        # 按钮还没出来说明页面可能卡在验证上，先处理验证
        try:
            renew_btn.first.wait_for(state="visible", timeout=15000)
        except Exception:
            log("⚠️ 未找到 Renew 按钮，处理页面验证后重试...")
            handle_cloudflare(page, timeout=90)

        modal_opened = False
        for i in range(4):
            try:
                renew_btn.first.wait_for(state="visible", timeout=15000)
                renew_btn.first.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                renew_btn.first.click()

                # 等待一小段时间，检测是否出现“未到续期时间”弹窗
                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"   # 特殊状态

                log("🖲️ 等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=15000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except Exception:
                    log("⚠️ 弹窗未出现，可能是页面脚本还没就绪，准备重试...")
                    _wait_page_scripts(page, timeout=10)
            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            # 兜底：页面脚本没就绪时点击无效，直接把弹窗元素显示出来
            log("🖱️ 直接显示续期弹窗（跳过页面脚本）...")
            if _show_renew_modal_by_dom(page):
                try:
                    create_btn.wait_for(state="visible", timeout=10000)
                    modal_opened = True
                    log("✅ 续期弹窗已显示")
                except Exception:
                    log("⚠️ 直接显示弹窗后仍未找到 'Create Invoice'")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        # 弹窗内新增 Turnstile 人机验证：先点击验证框，通过后再点 Create Invoice
        log("🛡️ 处理弹窗内的 Turnstile 人机验证...")
        if not solve_turnstile(page, timeout=120, tag="续期弹窗 Turnstile", wait_appear=15):
            log("⚠️ 未能确认 Turnstile 通过，仍尝试点击 Create Invoice（失败请查看截图）。")
            page.screenshot(path="turnstile_not_passed.png")
        time.sleep(random.uniform(0.6, 1.5))

        log("🖱️ 点击 'Create Invoice'...")
        create_btn.click()

        new_invoice_url = None
        start_wait = time.time()
        resubmitted = False
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            # 提交后若验证框仍未通过（token 失效或验证框被重置），重新验证并再提交一次
            if not resubmitted and _turnstile_count(page) > 0 and not is_turnstile_solved(page):
                resubmitted = True
                log("⚠️ 仍检测到未通过的验证框，重新处理...")
                solve_turnstile(page, timeout=60, tag="续期弹窗 Turnstile")
                try:
                    create_btn.click(timeout=15000)
                    log("🖱️ 已重新点击 'Create Invoice'。")
                except Exception as e:
                    log(f"⚠️ 重新点击 'Create Invoice' 失败: {e}")
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
        handle_cloudflare(page)

        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.click()
        log("✅ 'Pay' 按钮已点击。")

        # 等待支付确认页面或跳转回服务页
        time.sleep(5)
        # 返回服务管理页面以获取新的到期时间
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        page.screenshot(path="renew_error.png")
        return False

def main():
    # 检查必要环境变量
    if not HIDENCLOUD_COOKIE or not (HIDENCLOUD_EMAIL and HIDENCLOUD_PASSWORD):
        log("❌ 缺少登录凭证")
        sys.exit(1)

    global SERVICE_URL

    with sync_playwright() as p:
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")
            
            # 获取当前出口ip
            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log(f"🚀 启动浏览器（{ENGINE}）...")
            launch_kwargs = dict(
                user_data_dir=PROFILE_DIR,     # 复用真实用户目录，保留 cf_clearance 等状态
                channel="chrome",
                headless=False,                # Turnstile 验证需要真实有头浏览器
                no_viewport=True,              # 不固定窗口尺寸，减少自动化特征
                proxy={"server": PROXY_SERVER} if IS_PROXY else None,
            )
            if ENGINE == "patchright":
                # patchright 自带隐身补丁，额外加参数反而容易被识别
                launch_kwargs["args"] = []
            else:
                launch_kwargs["args"] = ['--disable-blink-features=AutomationControlled', '--disable-infobars']
                if sys.platform.startswith('linux'):
                    launch_kwargs["args"].append('--no-sandbox')
                launch_kwargs["ignore_default_args"] = ['--enable-automation']
            try:
                context = p.chromium.launch_persistent_context(**launch_kwargs)
            except Exception as e:
                log(f"⚠️ 浏览器配置目录启动失败({e})，换用全新目录重试...")
                launch_kwargs["user_data_dir"] = PROFILE_DIR + "-fresh"
                context = p.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            if ENGINE != "patchright":
                page.add_init_script(STEALTH_JS)
            page.bring_to_front()
            page.set_default_timeout(30000)

            if not login(page):
                sys.exit(1)

            # 登录成功后，自动获取 Server ID
            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)
            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            # 获取旧到期时间
            old_due = get_due_date(page)
            log(f"📆 续费前到期时间：{old_due}")

            # 执行续费
            renew_result = renew_service(page)

            new_due = old_due
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:  # renew_result is True
                new_due = get_due_date(page)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            # 发送 Telegram 通知
            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except Exception as e:
            log(f"❌ 浏览器启动出错: {e}")
            sys.exit(1)
        finally:
            if 'context' in locals() and context:
                try:
                    context.close()
                except Exception:
                    pass
                
if __name__ == "__main__":
    main()
