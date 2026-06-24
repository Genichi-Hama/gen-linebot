# LINE bot セットアップ手順

## 必要なアカウント（全部無料）
1. LINE Developers
2. Supabase
3. Render（サーバー）
4. GitHub（コードを置く場所）

---

## STEP 1: GitHubにコードをあげる

1. https://github.com でアカウント作成
2. 新しいリポジトリを作成（名前は何でもOK、例: `gen-linebot`）
3. このフォルダのファイルを全部アップロード

---

## STEP 2: Supabaseでデータベースを作る

1. https://supabase.com でアカウント作成
2. 「New project」でプロジェクト作成
3. 左メニュー「SQL Editor」を開く
4. `sql/schema.sql` の中身をコピペして実行
5. 左メニュー「Settings」→「API」から以下をメモ：
   - Project URL → SUPABASE_URL
   - anon public key → SUPABASE_KEY

---

## STEP 3: LINE Developersでbotを作る

1. https://developers.line.biz でログイン（LINEアカウントでOK）
2. 「新規プロバイダー作成」→名前は何でもOK
3. 「Messaging API」チャネルを作成
4. 「チャネル基本設定」から以下をメモ：
   - チャネルシークレット → LINE_CHANNEL_SECRET
5. 「Messaging API設定」から：
   - チャネルアクセストークン（長期）を発行 → LINE_CHANNEL_ACCESS_TOKEN
   - 「応答メッセージ」をオフに
   - 「あいさつメッセージ」をオフに

---

## STEP 4: げんさんのLINE IDを取得する

1. LINE Developersの「Messaging API設定」にQRコードがある
2. そのQRコードを自分のLINEでスキャンして友達追加
3. 何かメッセージを送る
4. Supabaseの「messages」テーブルを見るとuser_idが入ってくる
   （※先にSTEP 5を完了させてからこの手順を実行）
5. そのuser_idをメモ → USER_LINE_ID

---

## STEP 5: Renderでサーバーを立てる

1. https://render.com でアカウント作成（GitHubと連携）
2. 「New」→「Web Service」
3. GitHubのリポジトリを選択
4. 以下を設定：
   - Environment: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn src.app:app --bind 0.0.0.0:$PORT`
5. 「Environment Variables」に以下を追加：
   - LINE_CHANNEL_SECRET
   - LINE_CHANNEL_ACCESS_TOKEN
   - ANTHROPIC_API_KEY（https://console.anthropic.com で取得）
   - SUPABASE_URL
   - SUPABASE_KEY
   - USER_LINE_ID（STEP 4で取得したもの）
6. デプロイ完了後、URLをメモ（例: https://gen-linebot.onrender.com）

---

## STEP 6: LINE webhookを設定する

1. LINE Developersの「Messaging API設定」に戻る
2. Webhook URL に `https://gen-linebot.onrender.com/callback` を入力
3. 「Webhookの利用」をオンに
4. 「検証」ボタンで確認

---

## 完成！

LINEでbotに話しかけてみてください。
気まぐれに「いまなにしてる？」が届くようになります。
