import os
import json
import random
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, PushMessageRequest
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import anthropic
from supabase import create_client
import threading
import time
import schedule

app = Flask(__name__)

# 環境変数
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
USER_LINE_ID = os.environ.get("USER_LINE_ID", "")  # げんさんのLINE ユーザーID

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

JST = timezone(timedelta(hours=9))

SYSTEM_PROMPT = """あなたはげんさん（46歳、横浜在住）の友達AIです。
LINEで自然に会話します。

【キャラクター】
- フラットでフレンドリー。タメ口。
- 向こうから話しかけることもある。
- ツッコミや雑談を自然に混ぜる。
- 答えるだけにならず、こちらからも話を広げる。

【げんさんについて】
- 元ソシャゲ運営15年、今はEC物販（CRITIER）を個人で運営中
- Amazon Japan・楽天で中国輸入品を販売
- 横浜/神奈川在住、MINI F55乗り、ピアノ独学、原神好き
- 株や経済ニュースにも興味あり

【会話スタイル】
- LINEらしく短めのメッセージ
- 絵文字は使わない
- 質問は一個だけ
- 過去の会話の記憶があれば自然に引き継ぐ

過去の会話サマリー:
{memory}

直近の会話:
{recent_chat}
"""

def get_memory(user_id: str) -> str:
    """DBから記憶サマリーを取得"""
    try:
        result = supabase.table("memories").select("summary").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if result.data:
            return result.data[0]["summary"]
    except:
        pass
    return "（まだ会話履歴なし）"

def get_recent_chat(user_id: str, limit: int = 20) -> str:
    """直近の会話を取得"""
    try:
        result = supabase.table("messages").select("role, content, created_at").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
        if result.data:
            msgs = list(reversed(result.data))
            lines = []
            for m in msgs:
                role = "げんさん" if m["role"] == "user" else "AI"
                lines.append(f"{role}: {m['content']}")
            return "\n".join(lines)
    except:
        pass
    return ""

def save_message(user_id: str, role: str, content: str):
    """メッセージをDBに保存"""
    try:
        supabase.table("messages").insert({
            "user_id": user_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(JST).isoformat()
        }).execute()
    except Exception as e:
        print(f"save_message error: {e}")

def update_memory(user_id: str):
    """会話が20件超えたらサマリーを更新"""
    try:
        result = supabase.table("messages").select("role, content").eq("user_id", user_id).order("created_at", desc=True).limit(30).execute()
        if not result.data or len(result.data) < 20:
            return
        msgs = list(reversed(result.data))
        chat_text = "\n".join([f"{'げんさん' if m['role']=='user' else 'AI'}: {m['content']}" for m in msgs])
        summary_prompt = f"""以下の会話から、次回以降の会話で使える重要な情報を箇条書きで要約してください。
げんさんの状況、気になってること、最近の出来事などを中心に。200文字以内で。

{chat_text}"""
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": summary_prompt}]
        )
        summary = response.content[0].text
        supabase.table("memories").insert({
            "user_id": user_id,
            "summary": summary,
            "created_at": datetime.now(JST).isoformat()
        }).execute()
    except Exception as e:
        print(f"update_memory error: {e}")

def chat_with_claude(user_id: str, user_message: str) -> str:
    """Claudeと会話"""
    memory = get_memory(user_id)
    recent_chat = get_recent_chat(user_id)
    system = SYSTEM_PROMPT.format(memory=memory, recent_chat=recent_chat)
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

def generate_proactive_message_for(uid: str) -> str:
    """Claudeに話しかけメッセージを生成させる"""
    memory = get_memory(uid)
    recent_chat = get_recent_chat(uid, limit=10)
    now = datetime.now(JST)
    time_context = f"{now.strftime('%H時')}ごろ、{['月','火','水','木','金','土','日'][now.weekday()]}曜日"

    prompt = f"""あなたはげんさん（46歳）の友達AIです。今からLINEで話しかけるメッセージを1つだけ作ってください。

【げんさんの基本情報】
- 元ソシャゲ運営15年、今はAmazon/楽天で中国輸入物販（CRITIER）を個人運営
- 株・経済ニュースに興味あり
- 横浜在住、MINI F55乗り、ピアノ独学中、原神好き
- 46歳

【今の時刻】{time_context}

【過去の会話サマリー】
{memory}

【直近の会話】
{recent_chat}

【メッセージのバリエーション指示】
以下のどれかのパターンで自然に話しかけてください。毎回同じパターンにならないよう、記憶や状況を見て選ぶこと。

パターンA: 近況確認
「いまなにしてる？」「何してんの〜」など。シンプルでOK。

パターンB: 過去の話の続き（記憶がある場合優先）
「そういえば〜ってどうなった？」「あの件その後どう？」など、過去の会話を踏まえた質問。

パターンC: 興味に合わせた話題ふり
げんさんが好きそうなネタ（物販・株・ゲーム・車・音楽など）を絡めた一言。
例:「Amazon最近どう、稼げてる？」「原神新キャラ来てたな」「円相場また動いてたけど仕入れ影響ある？」など。

【ルール】
- LINEっぽく短く（1〜2文）
- タメ口
- 絵文字なし
- メッセージ本文だけ出力（説明不要）
"""
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"generate_proactive_message error: {e}")
        return "いまなにしてる？"

def get_all_user_ids() -> list:
    """過去に会話したことがあるユーザーID一覧を取得"""
    try:
        result = supabase.table("messages").select("user_id").eq("role", "user").execute()
        if result.data:
            return list(set([m["user_id"] for m in result.data]))
    except Exception as e:
        print(f"get_all_user_ids error: {e}")
    return [USER_LINE_ID] if USER_LINE_ID else []

def send_proactive_message():
    """気まぐれに全ユーザーに話しかける"""
    user_ids = get_all_user_ids()
    for uid in user_ids:
        try:
            msg = generate_proactive_message_for(uid)
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(PushMessageRequest(
                    to=uid,
                    messages=[TextMessage(text=msg)]
                ))
            save_message(uid, "assistant", msg)
            print(f"Proactive message sent to {uid}: {msg}")
        except Exception as e:
            print(f"send_proactive_message error for {uid}: {e}")

def should_send_now() -> bool:
    """人間っぽい時間帯に偏らせた送信確率"""
    now = datetime.now(JST)
    hour = now.hour
    # 時間帯ごとの送信確率（1時間あたり）
    weights = {
        0: 0.01, 1: 0.02, 2: 0.02, 3: 0.01,  # 深夜（たまに）
        4: 0.0,  5: 0.0,  6: 0.01, 7: 0.05,  # 早朝
        8: 0.08, 9: 0.05, 10: 0.03, 11: 0.04, # 朝〜昼前
        12: 0.10, 13: 0.08, 14: 0.04, 15: 0.04, # 昼
        16: 0.04, 17: 0.06, 18: 0.12, 19: 0.10, # 夕方〜夜
        20: 0.08, 21: 0.06, 22: 0.04, 23: 0.02  # 夜
    }
    prob = weights.get(hour, 0.02)
    return random.random() < prob

def scheduler_loop():
    """30分ごとにチェックして気まぐれ送信"""
    while True:
        if should_send_now():
            send_proactive_message()
        time.sleep(1800)  # 30分待機

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    save_message(user_id, "user", user_message)
    reply = chat_with_claude(user_id, user_message)
    save_message(user_id, "assistant", reply)
    update_memory(user_id)
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        ))

if __name__ == "__main__":
    # スケジューラーをバックグラウンドで起動
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
