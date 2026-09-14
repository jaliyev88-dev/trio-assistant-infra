import email
import imaplib
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path


def _decode(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="replace")
        else:
            out += text
    return out


def register_email_tools(mcp, tenant: str, data_root: str) -> bool:
    """Registers <tenant>_email_inbox/read/draft_reply/send tools if IMAP_HOST is configured in .env.
    Returns True if registered, False if this tenant has no mailbox yet."""

    imap_host = os.environ.get("IMAP_HOST")
    if not imap_host:
        return False

    imap_port = int(os.environ.get("IMAP_PORT", "993"))
    imap_user = os.environ["IMAP_USER"]
    imap_pass = os.environ["IMAP_PASS"]
    smtp_host = os.environ.get("SMTP_HOST", imap_host.replace("imap", "smtp"))
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", imap_user)
    smtp_pass = os.environ.get("SMTP_PASS", imap_pass)

    sent_log_path = Path(data_root) / "sent_log.db"
    sent_log_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_log():
        conn = sqlite3.connect(sent_log_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT NOT NULL,
                to_addr TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                attachments TEXT
            )"""
        )
        conn.commit()
        conn.close()

    _init_log()

    def _imap_connect():
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        conn.login(imap_user, imap_pass)
        return conn

    def email_inbox(days: int = 3, unread_only: bool = True) -> list[dict]:
        """Son <days> gündəki inbox məktublarının siyahısı (ən yenidən köhnəyə)."""
        conn = _imap_connect()
        try:
            conn.select("INBOX")
            since = (datetime.utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
            criteria = f"(UNSEEN SINCE {since})" if unread_only else f"(SINCE {since})"
            status, data = conn.search(None, criteria)
            if status != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()
            results = []
            for msg_id in reversed(ids):
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                results.append({
                    "msg_id": msg_id.decode(),
                    "from": _decode(msg.get("From", "")),
                    "subject": _decode(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                })
            return results
        finally:
            conn.logout()

    def email_read(msg_id: str) -> dict:
        """Bir məktubun tam mətni və qoşma siyahısı (msg_id — email_inbox-dan)."""
        conn = _imap_connect()
        try:
            conn.select("INBOX")
            status, msg_data = conn.fetch(msg_id.encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                return {"error": "Mesaj tapılmadı"}
            msg = email.message_from_bytes(msg_data[0][1])
            body_text = ""
            attachments = []
            if msg.is_multipart():
                for part in msg.walk():
                    disp = str(part.get("Content-Disposition") or "")
                    ctype = part.get_content_type()
                    if "attachment" in disp:
                        fname = part.get_filename()
                        if fname:
                            attachments.append(_decode(fname))
                    elif ctype == "text/plain" and not body_text:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body_text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            conn.store(msg_id.encode(), "+FLAGS", "\\Seen")
            return {
                "msg_id": msg_id,
                "from": _decode(msg.get("From", "")),
                "to": _decode(msg.get("To", "")),
                "subject": _decode(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": body_text.strip(),
                "attachments": attachments,
            }
        finally:
            conn.logout()

    def email_draft_reply(msg_id: str, points: str) -> dict:
        """Cavab qaralaması hazırlayır (GÖNDƏRMİR). points — cavabın əsas nöqtələri."""
        original = email_read(msg_id)
        if "error" in original:
            return original
        subject = original["subject"]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        draft_body = (
            f"{points}\n\n---\n"
            f"Orijinal məktub ({original['date']}, {original['from']}):\n"
            f"{original['body'][:500]}"
        )
        return {
            "to": original["from"],
            "subject": subject,
            "body": draft_body,
            "note": "Bu qaralamadır, göndərilməyib. Göndərmək üçün istifadəçi təsdiqindən sonra email_send çağırılmalıdır.",
        }

    def email_send(to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict:
        """Məktub göndərir (yalnız istifadəçinin açıq təsdiqindən sonra çağırılmalıdır)."""
        msg = EmailMessage()
        msg["From"] = smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        attached_names = []
        for path in attachments or []:
            p = Path(path)
            if p.is_file():
                msg.add_attachment(
                    p.read_bytes(), maintype="application", subtype="octet-stream", filename=p.name
                )
                attached_names.append(p.name)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        conn = sqlite3.connect(sent_log_path)
        conn.execute(
            "INSERT INTO sent_log (sent_at, to_addr, subject, body, attachments) VALUES (?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), to, subject, body, ",".join(attached_names)),
        )
        conn.commit()
        conn.close()

        return {"status": "göndərildi", "to": to, "subject": subject, "attachments": attached_names}

    mcp.tool(name=f"{tenant}_email_inbox")(email_inbox)
    mcp.tool(name=f"{tenant}_email_read")(email_read)
    mcp.tool(name=f"{tenant}_email_draft_reply")(email_draft_reply)
    mcp.tool(name=f"{tenant}_email_send")(email_send)

    return True
