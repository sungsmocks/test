import time
import re
from datetime import datetime, timezone
from imap_tools import MailBox, AND


def format_proxy(proxy):
    if not proxy:
        return None

    if "@" in proxy and proxy.count(":") >= 2:
        return proxy

    parts = proxy.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"{user}:{password}@{host}:{port}"
    if len(parts) == 2:
        return proxy

    return None

def get_otp(imap_user, imap_pass, target_email, retries=10, delay=5):
    host = "imap.gmail.com"
    print("Connecting to IMAP server...")

    def _extract_otp(msg):
        body = msg.text or msg.html or ""
        match = re.search(r"\b\d{6}\b", body)
        return match.group(0) if match else None

    def _is_expected_sender(sender):
        sender = (sender or "").lower()
        return (
            "noreply_at_email_olympicid_olympics_com_" in sender
            and "@icloud.com" in sender
        )

    for _ in range(retries):
        try:
            with MailBox(host).login(imap_user, imap_pass) as mailbox:
                print("Searching for OTP email...")

                try:
                    msgs = mailbox.fetch(AND(seen=False, to=target_email), limit=10, reverse=True)
                    for msg in msgs:
                        if _is_expected_sender(msg.from_):
                            otp = _extract_otp(msg)
                            if otp:
                                print("OTP found.")
                                return otp
                except Exception:
                    pass

                try:
                    msgs = mailbox.fetch(AND(to=target_email), limit=25, reverse=True)
                    for msg in msgs:
                        otp = _extract_otp(msg)
                        if otp and (_is_expected_sender(msg.from_) or True):
                            print("OTP found.")
                            return otp
                except Exception:
                    pass

                try:
                    msgs = mailbox.fetch(AND(seen=False), limit=50, reverse=True)
                    for msg in msgs:
                        sender = (msg.from_ or "").lower()
                        subject = (msg.subject or "").lower()
                        if not (
                            _is_expected_sender(sender)
                            or "noreply" in sender
                            or "verify" in subject
                            or "verification" in subject
                            or "code" in subject
                            or "la28" in subject
                            or "olympic" in subject
                        ):
                            continue
                        otp = _extract_otp(msg)
                        if otp:
                            print("OTP found.")
                            return otp
                except Exception:
                    pass

            print(f"OTP not found yet. Retrying in {delay}s...")
            time.sleep(delay)

        except Exception:
            print("IMAP error.")
            time.sleep(delay)

    return None
