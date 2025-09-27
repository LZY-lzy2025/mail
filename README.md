# 邮件发送工具

一个合规友好的事务邮件发送 CLI，支持：
- CSV 群发（默认仅向 consent=1/true/yes/y 发送）
- 模板：文本/HTML，支持 {name}/{email} 以及 CSV 列名变量
- 多附件与内嵌资源（HTML 中以 `cid:文件名` 引用）
- 速率限制、dry-run、日志输出、保存 .eml

## 安装要求
- Python 3.8+

## 环境变量（可选）
- MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD

## 快速开始
```bash
# 仅构建，不发送
python3 send_email.py --to someone@example.com \
  --subject "Hello" \
  --from-name "Example Team" \
  --template-text templates/example.txt \
  --template-html templates/example.html \
  --inline screenshot.png \
  --attach report.pdf \
  --dry-run --verbose

# 使用 CSV 群发（需 consent 列为 1/true/yes/y 才会发送）
python3 send_email.py --csv recipients.csv \
  --subject "Hello" \
  --template-text templates/example.txt \
  --template-html templates/example.html \
  --rate-per-minute 30
```

`templates/example.*` 模板中的变量：
- {name} / {email}
- 以及 CSV 中的任意列名（例如 {status}）。

HTML 内嵌资源：
- 通过 `--inline path/to/image.png` 添加
- 在 HTML 使用 `cid:image.png` 引用

## 重要合规提示
- 仅向明确同意（opt-in）的收件人发送（默认开启，可用 `--no-require-consent` 覆盖）。
- 提供退订方式（`--unsubscribe`）以便收件人取消。
- 控制发送频率，避免造成骚扰或被服务商限流。
- 尊重隐私与本地法律法规。