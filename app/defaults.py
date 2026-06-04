from __future__ import annotations


DEFAULT_WELCOME_TEXT = bytes.fromhex(
    "e597a8e597a8efbd9ee68891e698afe5909be99b85475920f09fabb60a"
    "e5a682e69e9ce69c89e4bbbbe4bd95e5958fe9a18ce38081e683b3e6b395e68896e5bbbae8adb00ae983bde6ada1e8bf8ee79599e8a880e7b5a6e68891e594b7f09fa48d0a0ae68891e983bde69c83e4b880e58987e4b880e58987e79a840ae4bb94e7b4b0e996b1e8ae80e58f8ae59b9ee8a6860ae4b99fe5be88e78f8de6839ce5a4a7e5aeb6e79a84e59b9ee9a58be88887e58886e4baabf09fa5b90ae8ac9de8ac9de4bda0e58091e79a84e694afe68c81e88887e999aae4bcb4f09f98adf09f9295"
).decode("utf-8")

DEFAULT_HANDOFF_BUTTON_TEXT = "\u4eba\u5de5\u5ba2\u670d"
DEFAULT_END_HANDOFF_BUTTON_TEXT = "\u7d50\u675f\u4eba\u5de5\u670d\u52d9"
DEFAULT_HANDOFF_OPEN_TEXT = "\u5df2\u70ba\u60a8\u8f49\u63a5\u4eba\u5de5\u5ba2\u670d\uff0c\u8acb\u76f4\u63a5\u767c\u9001\u60a8\u7684\u554f\u984c\u3002"
DEFAULT_HANDOFF_CLOSE_TEXT = "\u4eba\u5de5\u670d\u52d9\u5df2\u7d50\u675f\uff0c\u60a8\u53ef\u4ee5\u7e7c\u7e8c\u4f7f\u7528\u81ea\u52a9\u9078\u55ae\u3002"
DEFAULT_UNAUTHORIZED_TEXT = "\u7576\u524d Telegram ID \u672a\u6388\u6b0a\uff0c\u8acb\u806f\u7e6b\u7ba1\u7406\u54e1\u6dfb\u52a0\u3002"
DEFAULT_HANDOFF_TIMEOUT_MINUTES = 30
DEFAULT_CONVERSATION_RETENTION_DAYS = 30
TOPIC_HANDOFF_NOTICE_TEXT = "\u5df2\u9032\u5165\u4eba\u5de5\u670d\u52d9\uff0c\u8acb\u76f4\u63a5\u767c\u9001\u60a8\u7684\u8a0a\u606f\u3002"
AUTO_HANDOFF_TIMEOUT_TEXT = "\u4eba\u5de5\u670d\u52d9\u56e0\u9577\u6642\u9593\u672a\u6536\u5230\u65b0\u8a0a\u606f\uff0c\u5df2\u81ea\u52d5\u7d50\u675f\u3002\u60a8\u53ef\u4ee5\u91cd\u65b0\u9078\u64c7\u9078\u55ae\u3002"

PAYMENT_BUTTON_TEXT = "\u4ed8\u6b3e\u76f8\u95dc\u554f\u984c"
FEEDBACK_BUTTON_TEXT = "\u7d66GY\u7684\u5efa\u8b70\uff06\u5fc3\u5f97"
OTHER_BUTTON_TEXT = "\u5176\u4ed6\u554f\u984c\u9ede\u9019\u88e1"
FUZZY_MATCH_REPLY_TEXT = "✅✅"

PAYMENT_HANDOFF_TEXT = """嗚嗚不好意思🥺

如果你已經完成付款
但還沒有到群組連結的話～

再麻煩把你的「<a href="tg://settings">用戶名稱</a>」傳給我🤍
GY 馬上幫你確認看看🔍💕"""
PAYMENT_USERNAME_MISSING_TEXT = """你的 Telegram 帳號目前沒有設定 Username。

請先點這裡開啟 <a href="tg://settings">Telegram 個人資料設定</a>，
設定完成後再回來輸入你的 Username，
GY 才能幫你確認付款連結唷🔍💕"""
PAYMENT_AFTER_INPUT_TEXT = """讓你久等了！

你的專屬付費群連結在這裡👇

🔗 https://t.me/+lTbzPDzsOOU3YjU9"""
PAYMENT_LINK_URL = "https://t.me/+lTbzPDzsOOU3YjU9"
FEEDBACK_PROMPT_TEXT = """有什麼心得感想或建議
都歡迎跟我說唷！

不用不好意思啦～🥺

不管是喜歡的內容
想看的影片類型，
還是想看的照片風格📸✨

通通都可以告訴我！

你們的回饋對我來說很重要🥰

也能讓我知道大家喜歡什麼
努力帶給你們更多喜歡的內容呀～💕"""
FEEDBACK_THANKS_TEXT = """謝謝你的意見回饋🥺🤍

每一則留言我都很珍惜
也會認真參考大家的想法！

因為有你們一路支持著我
我才有動力繼續努力下去🫶

真的真的超愛你們啦😭💕"""
OTHER_HANDOFF_TEXT = """除了付款問題和意見回饋之外💌

有任何想問的事情，
都可以留言在這裡！

我看到後會盡快回覆你唷～🥰✨"""
OTHER_ACK_TEXT = """🫡 收到啦！

我這邊會盡快回覆大家的訊息💌✨

不過因為大家實在太熱情了🥹🩷
每天都會收到很多很多訊息
所以有時候沒辦法立即回覆～

如果讓你久等了
還請再耐心等我一下下唷🥰

愛你😘"""
