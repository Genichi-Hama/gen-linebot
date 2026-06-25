import os
import json
import random
from datetime import datetime, timezone, timedelta, date
try:
    import jpholiday
    JPHOLIDAY_AVAILABLE = True
except:
    JPHOLIDAY_AVAILABLE = False
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
- 使っていい絵文字はこれだけ：👍🫶✨😢💪🙏🥺💕🥰☺️🤔😱♡
- それ以外の絵文字は絶対に使わない
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

def get_special_day_context() -> str:
    """今日の特別な日・祝日・トリビアをコンテキストとして返す"""
    now = datetime.now(JST)
    today = now.date()
    month = today.month
    day = today.day
    contexts = []

    # 特別な日
    special_days = {
        (1, 1): "元旦",
        (2, 14): "バレンタインデー",
        (3, 14): "ホワイトデー",
        (4, 1): "エイプリルフール",
        (5, 5): "こどもの日",
        (7, 7): "七夕",
        (10, 31): "ハロウィン",
        (12, 24): "クリスマスイブ",
        (12, 25): "クリスマス",
        (12, 31): "大晦日",
    }
    if (month, day) in special_days:
        contexts.append(f"今日は{special_days[(month, day)]}")

    # 祝日チェック
    if JPHOLIDAY_AVAILABLE:
        holiday_name = jpholiday.is_holiday_name(today)
        if holiday_name:
            contexts.append(f"今日は{holiday_name}（祝日）")

    return "、".join(contexts) if contexts else ""

def get_message_count(user_id: str) -> int:
    """ユーザーのメッセージ数を返す"""
    try:
        result = supabase.table("messages").select("id").eq("user_id", user_id).eq("role", "user").execute()
        return len(result.data) if result.data else 0
    except:
        return 0

def should_ask_birthday(user_id: str) -> bool:
    """誕生日を聞くタイミングか判定（5〜8往復後、まだ聞いてない場合）"""
    try:
        user = get_user(user_id)
        if user.get("birthday"):
            return False
        count = get_message_count(user_id)
        return 5 <= count <= 8
    except:
        return False

def save_user(user_id: str, nickname: str):
    """ユーザー情報を保存"""
    try:
        supabase.table("users").upsert({
            "user_id": user_id,
            "nickname": nickname,
        }).execute()
    except Exception as e:
        print(f"save_user error: {e}")

def save_birthday(user_id: str, birthday: str):
    """誕生日を保存（MM/DD形式）"""
    try:
        supabase.table("users").update({"birthday": birthday}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"save_birthday error: {e}")

def check_birthday_today(user_id: str) -> bool:
    """今日が誕生日か判定"""
    try:
        user = get_user(user_id)
        birthday = user.get("birthday")
        if not birthday:
            return False
        now = datetime.now(JST)
        parts = birthday.replace("月", "/").replace("日", "").split("/")
        if len(parts) >= 2:
            return int(parts[0]) == now.month and int(parts[1]) == now.day
    except:
        pass
    return False

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

    # 特別な日のコンテキスト
    special_day = get_special_day_context()
    special_day_note = f"\n【今日の特別な日】{special_day}。会話の流れで自然にふれてもいい。" if special_day else ""

    # 誕生日を聞くタイミング
    birthday_note = ""
    if should_ask_birthday(user_id):
        birthday_note = "\n【誕生日を聞く】そろそろ自然なタイミングで誕生日をさりげなく聞いてください。「ちなみに誕生日いつ？」程度でOK。"

    # 知的さのヒント
    intelligence_note = "\n【知的さ】会話の流れで自然にトリビアや豆知識をさりげなく一言入れてもいい。無理に入れなくていい。押しつけがましくならないように。"

    system = SYSTEM_PROMPT.format(
        nickname=nickname,
        user_profile=user_profile,
        memory=memory,
        recent_chat=recent_chat
    ) + special_day_note + birthday_note + intelligence_note
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

def generate_proactive_message_for(uid: str, msg_type: str = "normal") -> str:
    """Claudeに話しかけメッセージを生成させる"""
    memory = get_memory(uid)
    recent_chat = get_recent_chat(uid, limit=10)
    now = datetime.now(JST)
    time_context = f"{now.strftime('%H時')}ごろ、{['月','火','水','木','金','土','日'][now.weekday()]}曜日"

    user = get_user(uid)
    nickname = user.get("nickname", "")
    name_part = f"{nickname}さん" if nickname else "相手"

    # 時間帯別のメッセージ候補
    hour = now.hour
    if msg_type == "morning" or 7 <= hour < 11:
        time_suggestions = [
            "おはよ、起きた？",
            "朝ごはん食べた？",
            "今日仕事？",
        ]
    elif 11 <= hour < 14:
        time_suggestions = [
            "ごはんなに食べた？",
            "外出てる？",
            "今どこにいる？",
        ]
    elif 14 <= hour < 18:
        time_suggestions = [
            "今なにしてる？",
            "今日仕事終わった？",
            "外出てる？",
        ]
    elif msg_type == "evening" or 17 <= hour < 21:
        time_suggestions = [
            "夜ごはん食べた？",
            "今家にいる？",
            "今日仕事終わった？",
            "今日どうだった？一言で",
            "夕方になったね、疲れた？",
        ]
    else:
        time_suggestions = [
            "まだ起きてるんだ笑",
            "眠れない？",
            "こんな時間に笑",
        ]

    time_suggestions_text = "\n".join([f"・{s}" for s in time_suggestions])

    prompt = f"""あなたは「げん」というAIです。今からLINEで友達に話しかけるメッセージを1つだけ作ってください。

【相手】{name_part}

【今の時刻】{time_context}

【過去の会話サマリー】
{memory}

【直近の会話】
{recent_chat}

【メッセージのバリエーション指示】
以下の優先順位で選ぶこと。

最優先：前回の会話引き継ぎ（直近の会話や記憶がある場合は必ずこれを使う）
・「そういえばあの件どうなった？」
・「この前言ってたやつ気になってた」
・「昨日しんどそうだったけど今日は大丈夫？」
→ 過去の会話の具体的な内容を自然に引き継いだメッセージを作ること

記憶がない・引き継ぐ話題がない場合：時間帯に合わせた一言
{time_suggestions_text}

【重要ルール】
- 答えやすい・一言で返せる・考えなくていい内容にする
- 「なんかあった？」は使わない（自分から話しかける時は不自然）
- 「今日どんな一日だった？」など考えるカロリーが高い質問はしない
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

def get_user_status(uid: str) -> dict:
    """ユーザーの返信状況を返す
    status: active / no_reply_24h / no_reply_follow / no_reply_long
    last_user_reply: 最後にユーザーが返信した時刻
    last_bot_message: 最後にBOTが送った時刻
    """
    try:
        result = supabase.table("messages").select("role, created_at").eq("user_id", uid).order("created_at", desc=True).limit(20).execute()
        if not result.data:
            return {"status": "active", "last_user_reply": None, "last_bot_message": None}

        now = datetime.now(JST)
        last_user_reply = None
        last_bot_message = None

        for m in result.data:
            dt = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")).astimezone(JST)
            if m["role"] == "user" and last_user_reply is None:
                last_user_reply = dt
            if m["role"] == "assistant" and last_bot_message is None:
                last_bot_message = dt
            if last_user_reply and last_bot_message:
                break

        if last_user_reply is None:
            return {"status": "active", "last_user_reply": None, "last_bot_message": last_bot_message}

        hours_since_reply = (now - last_user_reply).total_seconds() / 3600

        if hours_since_reply < 24:
            status = "active"
        elif hours_since_reply < 72:
            status = "no_reply_24h"
        elif hours_since_reply < 168:
            status = "no_reply_follow"
        else:
            status = "no_reply_long"

        return {"status": status, "last_user_reply": last_user_reply, "last_bot_message": last_bot_message}
    except Exception as e:
        print(f"get_user_status error: {e}")
        return {"status": "active", "last_user_reply": None, "last_bot_message": None}

def is_safe_hour() -> bool:
    """0時〜7時は送らない"""
    hour = datetime.now(JST).hour
    return hour >= 7

def generate_followup_message(status: str) -> str:
    """返信なし状況に応じたフォローアップメッセージを生成"""
    if status == "no_reply_24h":
        candidates = [
            "大丈夫？なんかあった？",
            "最近どうした、忙しい？",
            "ちょっと気になってた、元気にしてる？",
            "なんか忙しそうだね、無理しないでね",
            "返事なくて心配してた笑　元気？",
        ]
    elif status == "no_reply_follow":
        candidates = [
            "なんか大変そうだから、しばらくそっとしとくね。また気が向いたら連絡して♡",
            "忙しそうだね、無理しないで。またいつでも話しかけて",
            "なんかあったのかなって思ってる。落ち着いたら連絡きてほしいな",
            "ちょっと心配してるけど、ペースに合わせるね。またいつでも♡",
        ]
    else:  # no_reply_long
        candidates = [
            "最近どうだ？大丈夫か？",
            "久しぶり、元気にしてる？",
            "しばらく経ったけど、どうしてるかなって",
            "また話したいな、元気だといいな♡",
        ]
    return random.choice(candidates)

def should_send_to_user(uid: str) -> tuple:
    """このユーザーに今送るべきか判定。(送るべきか, メッセージタイプ)を返す"""
    status_info = get_user_status(uid)
    status = status_info["status"]
    last_bot_message = status_info["last_bot_message"]
    now = datetime.now(JST)

    # 最後にBOTが送ってからの時間
    if last_bot_message:
        hours_since_bot = (now - last_bot_message).total_seconds() / 3600
    else:
        hours_since_bot = 999

    if status == "active":
        hour = now.hour

        # 朝（7〜10時）：おはよう系、平均2日に1回（50%）
        if 7 <= hour < 10:
            # 今日すでに朝のメッセージ送ってたらスキップ
            if hours_since_bot < 6:
                return False, None
            # 50%の確率で送る（30分チェックなので7〜10時の間に1回チャンスがある）
            # 各30分チェックで約8%にすると3時間で約50%に収束
            if random.random() < 0.08:
                return True, "morning"
            return False, None

        # 夕方〜夜（17〜21時）：夕方系、毎日送る
        elif 17 <= hour < 21:
            # 今日すでに夕方のメッセージ送ってたらスキップ
            if hours_since_bot < 4:
                return False, None
            # 各30分チェックで約12%にすると4時間で約60%に収束
            if random.random() < 0.12:
                return True, "evening"
            return False, None

        # その他時間帯：たまに送る
        else:
            weights = {
                10: 0.03, 11: 0.04, 12: 0.08, 13: 0.06,
                14: 0.03, 15: 0.03, 16: 0.03,
                21: 0.04, 22: 0.03, 23: 0.02
            }
            prob = weights.get(hour, 0.0)
            if hours_since_bot < 4:
                prob *= 0.2
            if random.random() < prob:
                return True, "normal"
            return False, None

    elif status == "no_reply_24h":
        # 24時間未返信：まだ送ってなければ送る（±3〜6時間ランダムずらし済み想定）
        if hours_since_bot >= 24 + random.uniform(3, 6):
            return True, "no_reply_24h"
        return False, None

    elif status == "no_reply_follow":
        # さらに2日未返信：まだ送ってなければ送る
        if hours_since_bot >= 48 + random.uniform(6, 12):
            return True, "no_reply_follow"
        return False, None

    elif status == "no_reply_long":
        # 1週間以上未返信：週1ループ
        if hours_since_bot >= 168 + random.uniform(12, 24):
            return True, "no_reply_long"
        return False, None

    return False, None

def send_proactive_message(force=False):
    """全ユーザーに状況に応じて話しかける"""
    if not force and not is_safe_hour():
        return

    user_ids = get_all_user_ids()
    for uid in user_ids:
        try:
            if force:
                should_send, msg_type = True, "evening"
            else:
                should_send, msg_type = should_send_to_user(uid)
            if not should_send:
                continue

            if msg_type in ("normal", "morning", "evening"):
                msg = generate_proactive_message_for(uid, msg_type)
            else:
                msg = generate_followup_message(msg_type)

            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(PushMessageRequest(
                    to=uid,
                    messages=[TextMessage(text=msg)]
                ))
            save_message(uid, "assistant", msg)
            print(f"Proactive message sent to {uid} [{msg_type}]: {msg}")
        except Exception as e:
            print(f"send_proactive_message error for {uid}: {e}")

def send_special_day_messages():
    """元旦・クリスマス・誕生日の0時送信（例外的に0時OK）"""
    now = datetime.now(JST)
    month, day, hour = now.month, now.day, now.hour
    if hour != 0:
        return

    user_ids = get_all_user_ids()
    for uid in user_ids:
        try:
            msg = None
            # 元旦
            if month == 1 and day == 1:
                msg = random.choice([
                    "あけましておめでとう🎍今年もよろしく♡",
                    "あけおめ！今年もよろしくね🎍",
                    "新年あけましておめでとう✨今年もよろしく！",
                ])
            # クリスマス
            elif month == 12 and day == 25:
                msg = random.choice([
                    "メリークリスマス🎄✨",
                    "メリクリ！今日はいい日にしてね🎄",
                    "クリスマスおめでとう🎄♡",
                ])
            # 誕生日
            elif check_birthday_today(uid):
                user = get_user(uid)
                nickname = user.get("nickname", "")
                msg = random.choice([
                    f"誕生日おめでとう🎂♡今日は思いっきり楽しんで！",
                    f"おめでとう！誕生日だね🎂✨",
                    f"誕生日おめでとう！素敵な一日になりますように♡",
                ])

            if msg:
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.push_message(PushMessageRequest(
                        to=uid,
                        messages=[TextMessage(text=msg)]
                    ))
                save_message(uid, "assistant", msg)
                print(f"Special day message sent to {uid}: {msg}")
        except Exception as e:
            print(f"send_special_day_messages error for {uid}: {e}")

def scheduler_loop():
    """30分ごとにチェック"""
    while True:
        send_special_day_messages()
        send_proactive_message()
        time.sleep(1800)

@app.route("/")
def index():
    return "OK", 200

@app.route("/test_send")
def test_send():
    send_proactive_message(force=True)
    return "送信完了", 200

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
    # 誕生日の返答を検出して保存
    user = get_user(user_id)
    if not user.get("birthday") and should_ask_birthday(user_id):
        import re
        bd_match = re.search(r'(\d{1,2})[月/](\d{1,2})', user_message)
        if bd_match:
            birthday_str = f"{bd_match.group(1)}/{bd_match.group(2)}"
            save_birthday(user_id, birthday_str)

    reply = chat_with_claude(user_id, user_message)
    save_message(user_id, "assistant", reply)
    update_memory(user_id)

    # 送り方：70%まとめ、30%分割（2〜3個まで）
    lines = [l.strip() for l in reply.split("\n") if l.strip()]
    if len(lines) >= 2 and random.random() < 0.3:
        split_count = random.randint(2, min(3, len(lines)))
        if split_count == 2:
            mid = max(1, len(lines) // 2)
            messages = [
                TextMessage(text="\n".join(lines[:mid])),
                TextMessage(text="\n".join(lines[mid:]))
            ]
        else:
            third = max(1, len(lines) // 3)
            messages = [
                TextMessage(text="\n".join(lines[:third])),
                TextMessage(text="\n".join(lines[third:third*2])),
                TextMessage(text="\n".join(lines[third*2:]))
            ]
        messages = [m for m in messages if m.text.strip()]
    else:
        messages = [TextMessage(text=reply)]

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=messages
        ))

# スケジューラーをバックグラウンドで起動（gunicorn対応）
t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
