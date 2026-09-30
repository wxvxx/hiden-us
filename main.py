#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,re,sys,time,random,requests
from playwright.sync_api import sync_playwright

# --- 环境变量 ---
HIDENCLOUD_COOKIE = os.environ.get('HIDENCLOUD_COOKIE') or ""    # remember_web cookie 值，必填
HIDENCLOUD_EMAIL        = os.environ.get('HIDENCLOUD_EMAIL') or ""           # 登录邮箱,可选，作为备用,TG通知需要填写
HIDENCLOUD_PASSWORD     = os.environ.get('HIDENCLOUD_PASSWORD') or ""        # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token,可选
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID,可选

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

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

def _turnstile_iframe_count(page):
    """页面上 Turnstile 验证框的数量"""
    try:
        return page.locator(TURNSTILE_IFRAME_SELECTOR).count()
    except Exception:
        return 0

def _visible_turnstile_index(page):
    """返回第一个可见 Turnstile 验证框的下标，找不到返回 None"""
    try:
        iframes = page.locator(TURNSTILE_IFRAME_SELECTOR)
        count = iframes.count()
    except Exception:
        return None
    fallback = None
    for i in range(count):
        try:
            if iframes.nth(i).is_visible():
                return i
        except Exception:
            pass
        if fallback is None:
            fallback = i
    return fallback

def is_turnstile_solved(page):
    """判断 Turnstile 是否已通过验证"""
    if _turnstile_iframe_count(page) == 0:
        return True  # 验证框已消失，视为通过

    # 1) 官方 JS API 读取 token
    try:
        token = page.evaluate(
            "() => { try { return (window.turnstile && window.turnstile.getResponse) "
            "? (window.turnstile.getResponse() || '') : ''; } catch (e) { return ''; } }"
        )
        if isinstance(token, str) and len(token) > 20:
            return True
    except Exception:
        pass

    # 2) 页面表单里的隐藏域
    try:
        hidden = page.locator('input[name="cf-turnstile-response"]')
        for i in range(hidden.count()):
            value = hidden.nth(i).input_value()
            if value and len(value) > 20:
                return True
    except Exception:
        pass

    # 3) iframe 内部状态（token / 复选框已勾选）
    for frame in page.frames:
        if "challenges.cloudflare.com" not in (frame.url or ""):
            continue
        try:
            solved = frame.evaluate(
                """() => {
                    const resp = document.querySelector('input[name="cf-turnstile-response"]');
                    if (resp && resp.value && resp.value.length > 20) return 1;
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb && cb.checked) return 1;
                    if (document.querySelector('.ctp-checkbox-label--checked, [aria-checked="true"]')) return 1;
                    return 0;
                }"""
            )
            if solved:
                return True
        except Exception:
            continue
    return False

def _move_mouse_like_human(page, x, y):
    """模拟真人：先移动鼠标再点击"""
    try:
        page.mouse.move(x - random.uniform(40, 140), y - random.uniform(40, 100), steps=10)
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.move(x, y, steps=6)
        time.sleep(random.uniform(0.1, 0.3))
    except Exception:
        pass

def click_turnstile(page, attempt=1):
    """点击 Turnstile「验证您是真人」复选框"""
    index = _visible_turnstile_index(page)
    if index is None:
        return False
    frame = page.frame_locator(f'{TURNSTILE_IFRAME_SELECTOR} >> nth={index}')

    # 方案1：定位复选框/标签，用真实鼠标点击
    for selector in ('input[type="checkbox"]', '.cb-lb', 'label'):
        try:
            locator = frame.locator(selector).first
            if locator.count() == 0:
                continue
            box = locator.bounding_box()
            if not box or box['width'] < 2 or box['height'] < 2:
                continue
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            if selector != 'input[type="checkbox"]':
                x = box['x'] + min(14.0, box['width'] / 2)  # 复选框在容器左侧
            log(f"🖱️ 点击 Turnstile 验证框（第 {attempt} 次，命中 {selector}）")
            _move_mouse_like_human(page, x, y)
            page.mouse.click(x, y)
            return True
        except Exception:
            continue

    # 方案2：按验证框整体位置推算复选框坐标（左侧垂直居中）
    try:
        box = page.locator(TURNSTILE_IFRAME_SELECTOR).nth(index).bounding_box()
        if box:
            x = box['x'] + 30
            y = box['y'] + box['height'] / 2
            log(f"🖱️ 点击 Turnstile 验证框（第 {attempt} 次，按坐标推算）")
            _move_mouse_like_human(page, x, y)
            page.mouse.click(x, y)
            return True
    except Exception:
        pass
    return False

def solve_turnstile(page, timeout=90, wait_appear=0, tag="Cloudflare 验证", max_clicks=3):
    """
    处理 Cloudflare Turnstile 人机验证：点击复选框并等待通过。
    wait_appear: 先等待验证框出现的最长秒数（弹窗刚打开时 iframe 可能还没渲染）
    返回 True 表示验证通过（页面上没有验证框时也视为通过）
    """
    if wait_appear > 0:
        appear_until = time.time() + wait_appear
        while time.time() < appear_until and _turnstile_iframe_count(page) == 0:
            time.sleep(0.5)

    if _turnstile_iframe_count(page) == 0:
        return True

    log(f"🛡️ 检测到 {tag}...")
    deadline = time.time() + timeout
    clicks = 0
    while time.time() < deadline:
        if is_turnstile_solved(page):
            log(f"✅ {tag}已通过！")
            return True
        if clicks >= max_clicks:
            break
        clicks += 1
        click_turnstile(page, clicks)
        # 每次点击后最多等 20 秒验证结果
        wait_until = min(time.time() + 20, deadline)
        while time.time() < wait_until:
            time.sleep(1)
            if is_turnstile_solved(page):
                log(f"✅ {tag}已通过！")
                return True

    if is_turnstile_solved(page):
        log(f"✅ {tag}已通过！")
        return True
    log(f"❌ {tag}未通过。")
    return False

def handle_cloudflare(page):
    """处理页面上的 Cloudflare 验证（登录、页面跳转等场景）"""
    return solve_turnstile(page, timeout=60, wait_appear=5, tag="Cloudflare 验证")

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
            handle_cloudflare(page)
            page_title = page.title()
            log(f"📝 当前Title: {page_title}")
            if "auth/login" not in page.url:
                log(f"✅ Cookie 登录成功！当前已到达dashboard页面")
                return True
            log("❌ Cookie 失效，请更换")
        except:
            pass

    # 2. 账号密码登录
    if not HIDENCLOUD_EMAIL or not HIDENCLOUD_PASSWORD:
        return False
    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page.fill('input[name="HIDENCLOUD_EMAIL"]', HIDENCLOUD_EMAIL)
        page.fill('input[name="HIDENCLOUD_PASSWORD"]', HIDENCLOUD_PASSWORD)
        time.sleep(0.5)
        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)
        handle_cloudflare(page)
        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page_title = page.title()
        log(f"📝 当前Title: {page_title}")
        if "auth/login" in page.url:
            log("❌ 登录失败。")
            return False
        log(f"✅ 账号密码登录成功！当前已到达dashboard页面")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        handle_cloudflare(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        # 方案1: 从 href 链接中提取 /service/数字/manage
        matches = re.findall(r'/service/(\d+)/manage', html)
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
        handle_cloudflare(page)
        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"❌ 获取Due Date失败: {e}")
    return "未知"

def renew_service(page):

    try:
        log("➡ 进入续期流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice"):visible').first

        modal_opened = False
        for i in range(6):
            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                renew_btn.click()

                # 等待一小段时间，检测是否出现“未到续期时间”弹窗
                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"   # 特殊状态

                log("🖲️ 等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except:
                    log("⚠️ 弹窗未出现，可能是点击未响应，准备重试...")
                    time.sleep(2)
            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        # 弹窗内新增 Turnstile 人机验证：先点击验证框，通过后再点 Create Invoice
        log("🛡️ 处理弹窗内的 Turnstile 人机验证...")
        if not solve_turnstile(page, timeout=90, wait_appear=20, tag="Turnstile 人机验证"):
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
            if not resubmitted and _turnstile_iframe_count(page) > 0 and not is_turnstile_solved(page):
                resubmitted = True
                log("⚠️ 仍检测到未通过的验证框，重新处理...")
                solve_turnstile(page, timeout=45, wait_appear=0, tag="Turnstile 人机验证")
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
    if not HIDENCLOUD_COOKIE and not (HIDENCLOUD_EMAIL and HIDENCLOUD_PASSWORD):
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

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                # 去掉 --enable-automation，减少被 Turnstile 识别的特征
                ignore_default_args=['--enable-automation'],
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

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
            if 'browser' in locals() and browser:
                browser.close()
                
if __name__ == "__main__":
    main()
