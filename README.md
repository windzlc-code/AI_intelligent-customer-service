# Telegram 双端口智能客服 Bot

一个单 Bot、双端口的 Telegram 智能客服项目：

- 用户端：只有后台添加过 Telegram 用户 ID 的用户可使用。
- 管理员端：只有后台添加过 Telegram 管理员 ID 的人员可用 `/admin` 进入。
- 用户点击“人工客服”后，人工模式下的文本、图片、语音、文件会实时推送给所有管理员，并带用户名称和 Telegram ID。
- 管理员在 Telegram 管理端接管会话后，直接发送文本即可回复对应用户。
- Web 后台用于配置 Bot、授权用户/管理员、维护预制话术、查看会话记录。

## 运行

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8098
```

打开 `http://127.0.0.1:8098/admin`。

默认后台账号：

- 用户名：`admin`
- 密码：`admin123`

生产环境请通过环境变量设置初始账号：

```powershell
$env:ADMIN_USERNAME="your-admin"
$env:ADMIN_PASSWORD="change-this-password"
```

## Telegram Webhook

后台保存 Bot Token 后，使用后台显示的 `webhook_secret` 拼出 webhook 地址：

```text
https://你的域名/telegram/webhook/{webhook_secret}
```

然后调用 Telegram `setWebhook`：

```text
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://你的域名/telegram/webhook/{webhook_secret}
```

## 本地 Telegram 测试

本地 `127.0.0.1` 不能接收 Telegram Webhook。开发测试时可以单独启动 polling：

```bash
py -3 run_bot_polling.py
```

保持 Web 后台服务和 polling 脚本同时运行：后台负责配置，polling 负责接收 Telegram 消息。

## 本地数据

默认 SQLite 数据库位置：

```text
data/app.db
```

可通过 `APP_DB_PATH` 覆盖。
