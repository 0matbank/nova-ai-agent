# WhatsApp — official Cloud API setup (Phase 21)

Owner's choice (2026-10-16): Meta's official WhatsApp Business Cloud API, first with
Meta's free **test number**. Your personal WhatsApp number is NOT moved or
disconnected — it is only added as an allowed recipient.

How it works: your WhatsApp → Meta → (Cloudflare quick tunnel, outbound only — no
port forwarding) → Nova's local webhook on 127.0.0.1 → the same handler as Telegram
(tasks, approvals, verifier). Every incoming request must carry Meta's signature
(App Secret); only your number is accepted. Admin commands (/lockdown …) stay on
Telegram. WhatsApp allows free replies only within 24 h of your last message — after
that Nova writes on Telegram instead.

## Your steps (one time, ~15 minutes)

> নাম/মেনু Meta মাঝে মাঝে বদলায় — কাছাকাছি নামের অপশন বেছে নিন।

1. **App বানান** — https://developers.facebook.com → আপনার Facebook দিয়ে login →
   *My Apps* → *Create App* → use case: *Other* → type: *Business* → নাম: `Nova AI` → Create।
2. **WhatsApp যোগ করুন** — app dashboard-এ *Add product* → **WhatsApp** → *Set up*।
   Meta একটা free **test number** আর একটা test Business Account বানিয়ে দেবে।
3. **WhatsApp → API Setup** পাতায়:
   - **Phone number ID** আর **WhatsApp Business Account ID** কপি করুন।
   - *To* → *Manage phone number list* → আপনার নিজের WhatsApp নম্বর যোগ করুন →
     WhatsApp-এ আসা code দিয়ে verify। (এতে আপনার WhatsApp-এ কিছু বদলায় না।)
   - পাতায় দেখানো **test number**-টা phone-এ save করে রাখুন — Nova এই নম্বর থেকেই কথা বলবে।
4. **স্থায়ী token** — https://business.facebook.com → *Business settings* → *Users* →
   *System users* → *Add* (role: Admin) → সেই user-এ *Assign assets*: app `Nova AI`
   (full control) আর test WhatsApp account (full control) → *Generate new token* →
   app `Nova AI`, expiry **Never**, permissions **whatsapp_business_messaging** আর
   **whatsapp_business_management** → token কপি করুন।
5. **App ID আর App Secret** — app dashboard → *App settings* → *Basic* → App ID আর
   App Secret (*Show*)।
6. **`D:\Personal-Agent\secrets\.env`-এ লিখুন** (chat-এ কখনো পাঠাবেন না):

   ```
   WHATSAPP_ALLOWED_NUMBERS=8801XXXXXXXXX        # আপনার নম্বর, দেশের কোডসহ, + ছাড়া
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_BUSINESS_ACCOUNT_ID=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_APP_ID=...
   WHATSAPP_APP_SECRET=...
   ```

7. Nova-কে জানান — Nova `scripts/whatsapp_setup.py` দিয়ে যাচাই করবে (মান কখনো দেখায় না),
   WhatsApp চালু করবে, Meta-তে webhook register করবে। তারপর আপনি test number-এ "hi"
   পাঠালেই কথা শুরু।

## What Nova does on its own

- Downloads `cloudflared` (Cloudflare's official tunnel client, Apache-2.0) from the
  official release, pinned version, SHA-256 checked → `D:\Personal-Agent\tools\`.
- Generates the webhook verify token in the secrets folder.
- On every start: opens the tunnel, registers the new address with Meta, which checks
  it once; then signed events flow in.

## Later (optional)

- A fixed address instead of the quick tunnel (own Cloudflare named tunnel + domain):
  `whatsapp.tunnel: fixed`, `whatsapp.public_url: https://…` in config/default.yaml.
- A real business number instead of the test number (Meta business verification).
