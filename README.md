# Worth The Drive?（仮称）

「少し安いから」と遠くのガソリンスタンドへ行った結果、往復で消費するガソリン代のせいで**結局損をする**ことがあります。  
**Worth The Drive?** は、ユーザーの条件（燃費/距離/給油量/価格差）から、往復コストを差し引いた **真の節約額（Net Savings）** を算出し、「行く価値があるか？」を直感的に判断できるようにするWebアプリです。

## ターゲット

- アメリカ（US）のドライバー

## コアバリュー（Net Savings）

ユーザーが入力する要素:

- **車の燃費**: MPG（Miles Per Gallon）
- **給油予定量**: Gallons
- **スタンドまでの距離**: Miles（片道）
- **価格差**: \($/gallon\)

計算ロジック（`core_calculator.py`）:

1. 単純な節約額 = \((current\_price - target\_price) \times gallons\_needed\)
2. **往復寄り道**: 往復のガソリン消費量 = \((distance\_miles \times 2) / car\_mpg\) → そのコストを差し引く
3. **片道寄り道**（その先へ進む想定）: 追加の片道分だけ燃費を計上してコストを差し引く
4. Net Savings = 単純な節約額 − 寄り道のガソリン代

## 画面での使い方（ストーリー）

1. ブラウザで `http://localhost:8000/` を開く。
2. **給油予定量（L）**、**許容する片道の時間**、**車のタイプ**、必要なら **基準価格（$/gal）の手入力**、**往復 / 片道寄り道** を選ぶ。
3. 「現在地からおすすめを見る」でGPSを許可する。
4. 上部のサマリーで「この条件なら、だいたい何km・何分先まで行けるとお得か」の目安と、いちばんお得そうな候補の一例を読む。
5. **許容時間内の候補（最大3件）** を見て、距離・時間・**実質の節約額（Net savings）** で比較する。
6. 許容時間を超えてもお得なスタンドがある場合は、**参考として1件**だけ別枠で表示される（時間オーバーであることは明示）。

## このリポジトリの現状（初期セットアップ）

- `core_calculator.py`: Net Savings の計算コア（Python）
- `.env`: APIキー等の**空テンプレート**
- `requirements.txt`: 最小依存（`requests`, `python-dotenv`）

## 使い方（ローカル）

依存関係のインストール:

```bash
python -m pip install -r requirements.txt
```

コア計算のテスト実行:

```bash
python core_calculator.py
```

バックエンドAPIの起動（RapidAPIキーは `.env` に設定）:

```bash
python api_server.py
```

右側の地図（Google Maps）を使う場合は `.env` に `GOOGLE_MAPS_API_KEY=` を設定（[Maps JavaScript API](https://developers.google.com/maps/documentation/javascript) を有効化し、キーは HTTP リファラーで制限推奨）。

起動後に確認:

- `http://localhost:8000/health`
- `http://localhost:8000/api/state-codes`
- `http://localhost:8000/api/state-prices?state=CA`

## 公開時の注意（重要）

- **RapidAPIキーはフロント（ブラウザ）に出さない**（このAPIサーバーが代理で呼び出します）
- **パブリックネットワークを許可するなら最低限の防御を有効化**:
  - `.env` の `APP_API_KEY` を設定し、呼び出し時にヘッダー `x-app-api-key` を付ける
  - `.env` の `ALLOWED_ORIGINS` にフロントのURLだけを設定（CORS制限）
  - `.env` の `RATE_LIMIT_PER_MINUTE` を適切に設定（IPレート制限）
  - `.env` の `STATE_PRICES_URL` を設定して州別価格APIを有効化（`/api/state-prices`）

## Production checklist (Render / similar)

Use `.env.example` as a template. On the host, set at least:

| Variable | Example |
|----------|---------|
| `RAPIDAPI_KEY` | Your RapidAPI key |
| `RAPIDAPI_HOST` | `gas-price.p.rapidapi.com` |
| `ALL_USA_PRICE_URL` | `https://gas-price.p.rapidapi.com/allUsaPrice` |
| `ALLOWED_ORIGINS` | `https://your-service.onrender.com` (no trailing slash) |
| `GOOGLE_MAPS_API_KEY` | Maps JavaScript API key (restrict by HTTP referrer) |
| `FREE_DAILY_LIMIT` | `20` (optional) |
| `DONATION_PAYPAL_URL` / `DONATION_COFFEE_URL` | Optional |

**Security:** Never commit `.env`. Confirm with `git ls-files` that `.env` is not tracked.

**Live site:** [worth-the-drive.onrender.com](https://worth-the-drive.onrender.com/) (example)

## 今後のロードマップ（案）

- **Step 1: APIプロキシ（バックエンド）**
  - RapidAPIのキーをフロントに出さないため、サーバー側でGas Price APIを呼び出す
  - 例: `GET /api/state-prices` や `GET /api/state?code=CA`
  - キャッシュ（数分〜）とレート制限を導入
- **Step 2: 最小フロント（Web UI）**
  - 州別の平均価格などを一覧表示（A案）
  - 入力フォーム（MPG / gallons / 距離 / 価格差 or 価格）でNet Savingsを表示
- **Step 3: マップ/ルート連携**
  - 距離の自動計算（地図API）や候補スタンド比較
- **Step 4: 公開（デプロイ）**
  - バックエンド: Render / Railway / Fly.io など（環境変数でキー管理）
  - フロント: Vercel / Netlify など
- **Step 5: 体験改善**
  - おすすめスタンドの順位付け、保存、共有、計算履歴など

