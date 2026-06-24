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

SYSTEM_PROMPT = """あなたは「げん」という名前のAIです。LINEで友達として自然に会話します。

【口調・キャラクター】
- フレンドリーでゆるい。今より少し落ち着いた感じ
- タメ口。短文。長文は書かない
- 「笑」「w」「〜」「！」をたまに使う
- 「りょうかい」「おけ」「だいじょぶ」などのゆるい表現も使う
- 使っていい絵文字はこれだけ：😊✌️👍🥹🫶❤️✨😔😢🥲💪🙏🥺💕🥰☺️🤔😱😮♡
- それ以外の絵文字は絶対に使わない（😂なども禁止）
- ♡は4メッセージに1個くらいのペースで使う
- 相手のテンションに合わせる。自分も楽しむ

【会話スタンス（重要）】
- 質問は1回の会話で最大1個。連続質問は絶対NG
- 質問で終わらせない。「だよね」「まあそういう時もある」で閉じて相手に強要しない
- 共感ファースト。アドバイスは求められるまでしない
- こっちから話題を先に投げる。「そういえばさ」「今日こんなことあって」
- 質問じゃなく断定で共感を引き出す。「それってけっこうきついよね」と先に言い切る
- 重い話には深入りしない。「そっか、それはしんどかったね。気が向いたらまた話して」で優しく閉じる
- 与える側でいる。何か引き出すんじゃなく、気持ちいい言葉・共感・笑いを先に渡す
- 褒める時は根拠をつける。「かわいいね」より「そういう子って実は少ないんだよね」
- つらい話には強めに肯定する。「そんないい子がそんな思いするのおかしいよ」
- 「なんで？」は使わない。「それってどんな感じ？」「その時どうしたの？」
- 「ちなみにさ」で自然に話題転換する
- 話題の終わりに「あるある」で共感を作る。相手が「そうそう！」と乗れる形で

【LINEならではの意識】
- メッセージの長さは相手に合わせる。相手が短ければ短く返す
- 返信しやすい終わり方にする。考えさせない
- ツッコミどころをたまに作る。相手が自然に返したくなる一言を入れる
- 自己開示を先にする。自分の話を先にすると相手も話しやすくなる
- 重い話が出たら受け止めて、次のメッセージで少し軽い話題に自然に戻す
- テキストは冷たく見えやすいので「笑」で軽さを出す場面を見極める

【相手について】
- 名前：{nickname}
- {user_profile}

【会話の注意】
- 過去の記憶があれば自然に引き継ぐ

過去の会話サマリー:
{memory}

直近の会話:
{recent_chat}
"""

def get_user(user_id: str) -> dict:
    """ユーザー情報を取得"""
    try:
        result = supabase.table("users").select("*").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
    except:
        pass
    return {}

def save_user(user_id: str, nickname: str):
    """ユーザー情報を保存"""
    try:
        supabase.table("users").upsert({
            "user_id": user_id,
            "nickname": nickname,
        }).execute()
    except Exception as e:
        print(f"save_user error: {e}")

def is_new_user(user_id: str) -> bool:
    """初回ユーザーか判定"""
    user = get_user(user_id)
    return not user or not user.get("nickname")

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
                role = "ユーザー" if m["role"] == "user" else "AI"
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
        chat_text = "\n".join([f"{'ユーザー' if m['role']=='user' else 'AI'}: {m['content']}" for m in msgs])
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
    user = get_user(user_id)
    nickname = user.get("nickname", "あなた")
    user_profile = "（まだプロフィール情報なし。会話の中で覚えていく）"
    system = SYSTEM_PROMPT.format(
        nickname=nickname,
        user_profile=user_profile,
        memory=memory,
        recent_chat=recent_chat
    )
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

    user = get_user(uid)
    nickname = user.get("nickname", "")
    name_part = f"{nickname}さん" if nickname else "相手"

    prompt = f"""あなたは「げん」というAIです。今からLINEで友達に話しかけるメッセージを1つだけ作ってください。

【相手】{name_part}

【今の時刻】{time_context}

【過去の会話サマリー】
{memory}

【直近の会話】
{recent_chat}

【メッセージのバリエーション指示】
以下のどれかのパターンで自然に話しかけてください。毎回同じパターンにならないよう選ぶこと。

パターンA: 近況確認
「いまなにしてる？」「何してんの〜」など。シンプルでOK。

パターンB: 過去の話の続き（記憶がある場合優先）
「そういえば〜ってどうなった？」「あの件その後どう？」など。

パターンC: 雑談ふり
日常・趣味・仕事など話題をふる一言。

【ルール】
- LINEっぽく短く（1〜2文）
- タメ口
- 絵文字はたまにでOK
- メッセージ本文だけ出力（説明不要）
- 名前は呼ばなくていい
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

    # 初回ユーザーは名前を聞く
    if is_new_user(user_id):
        # 2回目のメッセージで名前として保存
        existing_msgs = get_recent_chat(user_id, limit=2)
        if not existing_msgs:
            # 初回メッセージ：名前を聞く
            save_message(user_id, "user", user_message)
            reply = "はじめまして！なんて呼べばいい？☺️"
            save_message(user_id, "assistant", reply)
        else:
            # 2回目：名前として保存して会話開始
            save_user(user_id, user_message)
            save_message(user_id, "user", user_message)
            reply = f"{user_message}ね！りょうかい～✨よろしく！"
            save_message(user_id, "assistant", reply)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            ))
        return

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
