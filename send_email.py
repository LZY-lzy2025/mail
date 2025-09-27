"""
文件路径: send_email.py
描述: 合规友好的事务邮件发送工具：支持 CSV 群发（需同意）、模板、内嵌图片、速率限制、日志、dry-run 与 EML 保存。
"""

import os
import sys
import csv
import re
import time
import smtplib
import logging
import mimetypes
import argparse
from email.utils import formataddr, make_msgid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders


def configure_logging(verbose):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def load_text(path):
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error("读取模板失败 %s: %s", path, e)
        sys.exit(1)


def render_template(template_str, variables):
    try:
        return template_str.format(**variables)
    except KeyError as e:
        missing = str(e).strip("'")
        logging.error("模板变量缺失: %s", missing)
        sys.exit(1)


def is_valid_email(address):
    if not address:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", address) is not None


def guess_mime_type(path):
    ctype, _ = mimetypes.guess_type(path)
    if ctype is None:
        return ("application", "octet-stream")
    maintype, subtype = ctype.split("/", 1)
    return (maintype, subtype)


def attach_regular(msg, path):
    try:
        maintype, subtype = guess_mime_type(path)
        filename = os.path.basename(path)
        with open(path, "rb") as f:
            if maintype == "image":
                part = MIMEImage(f.read(), _subtype=subtype, name=filename)
            else:
                part = MIMEBase(maintype, subtype)
                part.set_payload(f.read())
                encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)
        logging.debug("已附加附件: %s", filename)
    except FileNotFoundError:
        logging.warning("附件未找到，已跳过: %s", path)
    except Exception as e:
        logging.error("附加附件出错 %s: %s", path, e)


def attach_inline(msg, path):
    cid = None
    try:
        maintype, subtype = guess_mime_type(path)
        filename = os.path.basename(path)
        with open(path, "rb") as f:
            if maintype == "image":
                part = MIMEImage(f.read(), _subtype=subtype, name=filename)
            else:
                part = MIMEBase(maintype, subtype)
                part.set_payload(f.read())
                encoders.encode_base64(part)
        cid = filename  # 使用文件名作为 cid
        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", f'inline; filename="{filename}"')
        msg.attach(part)
        logging.debug("已内嵌资源: %s (cid=%s)", filename, cid)
        return cid
    except FileNotFoundError:
        logging.warning("内嵌资源未找到，已跳过: %s", path)
        return ""
    except Exception as e:
        logging.error("内嵌资源附加出错 %s: %s", path, e)
        return ""


def build_message(from_name, from_addr, to_addr, subject, text_body, html_body,
                  inline_paths, attach_paths, reply_to, unsubscribe, save_eml_dir):
    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    if reply_to:
        msg["Reply-To"] = reply_to
    if unsubscribe:
        msg["List-Unsubscribe"] = unsubscribe

    alt = MIMEMultipart("alternative")
    if text_body:
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    cid_map = {}
    for p in inline_paths or []:
        cid = attach_inline(msg, p)
        if cid:
            cid_map[os.path.basename(p)] = cid

    for p in attach_paths or []:
        attach_regular(msg, p)

    if save_eml_dir:
        try:
            os.makedirs(save_eml_dir, exist_ok=True)
            filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{to_addr}_{int(time.time())}.eml")
            dest = os.path.join(save_eml_dir, filename)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(msg.as_string())
            logging.debug("已保存 EML: %s", dest)
        except Exception as e:
            logging.error("保存 EML 失败: %s", e)

    return msg, cid_map


def send_messages(args):
    username = args.username or os.environ.get("MAIL_USERNAME")
    password = args.password or os.environ.get("MAIL_PASSWORD")
    server_address = args.server or os.environ.get("MAIL_SERVER") or "smtp.example.com"
    server_port = int(args.port or os.environ.get("MAIL_PORT") or 587)

    if args.dry_run:
        logging.info("Dry-run 模式：不会实际发送邮件。")

    smtp = None
    if not args.dry_run:
        if not username or not password:
            logging.error("缺少 SMTP 凭据。请设置 --username/--password 或环境变量。")
            sys.exit(1)
        try:
            smtp = smtplib.SMTP(server_address, server_port, timeout=30)
            smtp.starttls()
            smtp.login(username, password)
        except Exception as e:
            logging.error("连接/登录 SMTP 失败: %s", e)
            sys.exit(1)

    default_text = "Hi {name},\n\nThis is a friendly message.\n"
    default_html = "<p>Hi <strong>{name}</strong>,</p><p>This is a friendly message.</p>"

    template_text = load_text(args.template_text) if args.template_text else default_text
    template_html = load_text(args.template_html) if args.template_html else default_html

    rate_delay = 0.0
    if args.rate_per_minute and args.rate_per_minute > 0:
        rate_delay = 60.0 / float(args.rate_per_minute)

    total = 0
    sent = 0
    skipped = 0
    seen = set()

    def iter_recipients():
        if args.csv:
            with open(args.csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    yield row.get(args.email_field or "email", "").strip(), row.get(args.name_field or "name", "").strip(), row
        else:
            yield args.to, args.name or "", {}

    for email_addr, name, row in iter_recipients():
        total += 1
        if not is_valid_email(email_addr):
            logging.warning("邮箱无效，跳过: %s", email_addr)
            skipped += 1
            continue
        if email_addr in seen:
            logging.info("检测到重复邮箱，跳过: %s", email_addr)
            skipped += 1
            continue
        seen.add(email_addr)
        if args.require_consent:
            consent_value = (row.get(args.consent_field, "") if row else "").strip().lower()
            if args.csv and consent_value not in ("1", "true", "yes", "y"):
                logging.info("无明确同意(consent)，跳过: %s", email_addr)
                skipped += 1
                continue

        # 变量包含基础字段与 CSV 行的所有列，便于模板中直接使用 {列名}
        variables = {"name": name or email_addr, "email": email_addr}
        if row:
            for k, v in row.items():
                if k not in variables:
                    variables[k] = v
        text_body = render_template(template_text, variables) if template_text else ""
        html_body = render_template(template_html, variables) if template_html else ""

        msg, _ = build_message(
            from_name=args.from_name,
            from_addr=args.from_addr or username or "",
            to_addr=email_addr,
            subject=args.subject,
            text_body=text_body,
            html_body=html_body,
            inline_paths=args.inline or [],
            attach_paths=args.attach or [],
            reply_to=args.reply_to,
            unsubscribe=args.unsubscribe,
            save_eml_dir=args.save_eml_dir
        )

        if args.dry_run:
            logging.info("Dry-run: 构建完成 -> %s, 主题: %s", email_addr, args.subject)
            sent += 1
        else:
            try:
                smtp.send_message(msg)
                logging.info("已发送 -> %s", email_addr)
                sent += 1
                if rate_delay > 0:
                    time.sleep(rate_delay)
            except Exception as e:
                logging.error("发送失败 %s: %s", email_addr, e)
                skipped += 1

    if smtp:
        try:
            smtp.quit()
        except Exception:
            pass

    logging.info("完成：总计=%d 成功=%d 跳过=%d", total, sent, skipped)


def build_parser():
    parser = argparse.ArgumentParser(description="合规友好的事务邮件发送工具")
    recipients = parser.add_mutually_exclusive_group(required=True)
    recipients.add_argument("--to", help="单个收件人邮箱")
    recipients.add_argument("--csv", help="CSV 文件路径，需包含 email,name,consent 等列")

    parser.add_argument("--name", help="单个收件人姓名")
    parser.add_argument("--email-field", default="email", help="CSV 邮箱列名，默认 email")
    parser.add_argument("--name-field", default="name", help="CSV 姓名列名，默认 name")
    parser.add_argument("--consent-field", default="consent", help="CSV 同意列名，默认 consent")
    parser.add_argument("--require-consent", action="store_true", default=True, help="仅向已同意的收件人发送")
    parser.add_argument("--no-require-consent", dest="require_consent", action="store_false", help="允许忽略 consent")

    parser.add_argument("--subject", default="Hello from Example", help="邮件主题")
    parser.add_argument("--from-addr", help="发件邮箱，不填则使用 MAIL_USERNAME")
    parser.add_argument("--from-name", default="Example Team", help="发件人名称")
    parser.add_argument("--reply-to", help="Reply-To 邮箱")
    parser.add_argument("--unsubscribe", help='List-Unsubscribe 头，如 "<mailto:unsubscribe@example.com>, <https://example.com/unsub?id=123>"')

    parser.add_argument("--template-text", help="纯文本模板文件路径，支持 {name} 等变量")
    parser.add_argument("--template-html", help="HTML 模板文件路径，支持 {name} 等变量")

    parser.add_argument("--attach", action="append", help="添加附件，可重复传入")
    parser.add_argument("--inline", action="append", help="添加内嵌资源(图片等)，HTML 使用 cid:文件名 引用")

    parser.add_argument("--save-eml-dir", help="保存生成的 .eml 文件目录")

    parser.add_argument("--server", help="SMTP 服务器地址，默认取 MAIL_SERVER")
    parser.add_argument("--port", type=int, help="SMTP 端口，默认取 MAIL_PORT 或 587")
    parser.add_argument("--username", help="SMTP 用户名，默认取 MAIL_USERNAME")
    parser.add_argument("--password", help="SMTP 密码，默认取 MAIL_PASSWORD")

    parser.add_argument("--rate-per-minute", type=float, help="每分钟发送上限，用于限速")
    parser.add_argument("--dry-run", action="store_true", help="仅构建，不发送")
    parser.add_argument("--verbose", action="store_true", help="显示调试日志")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    send_messages(args)


if __name__ == "__main__":
    main()
