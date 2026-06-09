import os
import time
import sys
import random
import re
import requests
from playwright.sync_api import sync_playwright

# --- 全局配置 ---
HIDENCLOUD_COOKIE = os.environ.get('HIDENCLOUD_COOKIE')
HIDENCLOUD_EMAIL = os.environ.get('HIDENCLOUD_EMAIL')
HIDENCLOUD_PASSWORD = os.environ.get('HIDENCLOUD_PASSWORD')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
SERVICE_URL = f"{BASE_URL}/service/218089/manage"  # 请确认这是你的服务ID
COOKIE_NAME = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

def send_telegram_notification(account, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 配置缺失，跳过通知")
        return False

    text = (
        f"🎉 HidenCloud 自动续期成功\n"
        f"👤 账号：{account}\n"
        f"🕒 续期时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

def handle_cloudflare(page):
    """通用验证处理逻辑"""
    iframe_selector = 'iframe[src*="challenges.cloudflare.com"]'
    if page.locator(iframe_selector).count() == 0:
        return True
    log("⚠️ 检测到 Cloudflare 验证...")
    start_time = time.time()
    while time.time() - start_time < 60:
        if page.locator(iframe_selector).count() == 0:
            log("✅ 验证通过！")
            return True
        try:
            frame = page.frame_locator(iframe_selector)
            checkbox = frame.locator('input[type="checkbox"]')
            if checkbox.is_visible():
                log("点击验证复选框...")
                time.sleep(random.uniform(0.5, 1.5))
                checkbox.click()
                log("已点击，等待验证结果...")
                time.sleep(5)
            else:
                time.sleep(1)
        except Exception:
            pass
    log("❌ 验证超时。")
    return False

def login(page):
    log("开始登录流程...")
    # 1. Cookie 登录尝试
    if HIDENCLOUD_COOKIE:
        log("尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': COOKIE_NAME,
                'value': HIDENCLOUD_COOKIE,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)
            if "auth/login" not in page.url:
                log("✅ Cookie 登录成功！")
                return True
            log("Cookie 失效。")
        except:
            pass

    # 2. 账号密码登录
    if not HIDENCLOUD_EMAIL or not HIDENCLOUD_PASSWORD:
        return False
    log("尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page.fill('input[name="email"]', HIDENCLOUD_EMAIL)
        page.fill('input[name="password"]', HIDENCLOUD_PASSWORD)
        time.sleep(0.5)
        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)
        handle_cloudflare(page)
        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        if "auth/login" in page.url:
            log("❌ 登录失败。")
            return False
        log("✅ 账号密码登录成功！")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_due_date(page):
    """获取服务到期日期"""
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
                log(f"获取到Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"获取Due Date失败: {e}")
    return "未知"

def renew_service(page):
    """
    执行续费操作。
    返回：
        True        - 续费成功
        False       - 续费失败（真正的错误，比如网络问题、弹窗未出现等）
        "NOT_TIME"  - 未到续期时间（正常情况，不应视为失败）
    """
    try:
        log("进入续费流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        log("准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice")')

        modal_opened = False
        for i in range(3):
            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"第 {i+1} 次尝试点击 'Renew'...")
                renew_btn.click()

                # 等待一小段时间，检测是否出现“未到续期时间”弹窗
                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"   # 特殊状态

                log("等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except:
                    log("⚠️ 弹窗未出现，可能是点击未响应，准备重试...")
                    time.sleep(2)
            except Exception as e:
                log(f"点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        handle_cloudflare(page)
        log("点击 'Create Invoice'...")
        create_btn.click()

        log("等待发票生成...")
        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
                log("⚠️ 遇到拦截，尝试处理...")
                handle_cloudflare(page)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
        handle_cloudflare(page)

        log("查找 'Pay' 按钮...")
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
        log("❌ 缺少 HidenCloud 登录凭证")
        sys.exit(1)
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ 未设置 Telegram 环境变量 (TG_BOT_TOKEN/TG_CHAT_ID)，将不会发送通知")

    with sync_playwright() as p:
        try:
            log("启动官方 Chrome (Linux版)...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            if not login(page):
                sys.exit(1)

            # 获取旧到期时间
            old_due = get_due_date(page)
            log(f"续费前到期时间：{old_due}")

            # 执行续费
            renew_result = renew_service(page)

            if renew_result == "NOT_TIME":
                log("⚠️ 未到续期时间，目前无法续期")
                sys.exit(0)
            elif renew_result is False:
                log("❌ 续费失败（真正错误），脚本退出。")
                sys.exit(1)
            else:  # renew_result is True
                # 获取新到期时间
                new_due = get_due_date(page)
                log(f"续费后到期时间：{new_due}")

                # 发送 Telegram 通知
                account = HIDENCLOUD_EMAIL or "Cookie登录"
                send_telegram_notification(account, old_due, new_due)

                log("🎉 任务全部完成！")
                sys.exit(0)

        except Exception as e:
            log(f"💥 严重错误: {e}")
            sys.exit(1)
        finally:
            if 'browser' in locals() and browser:
                browser.close()

if __name__ == "__main__":
    main()
