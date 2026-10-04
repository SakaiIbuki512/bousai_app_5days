from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁の警報・注意報APIにおける青森市の市区町村コード
# 1420500 は青森市の市町村コードではなく、別地域のコードのため修正する
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_shelters():
    """避難所データをファイルに保存する"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = []
    for item in instructions:
        target = str(item.get('target', '')).strip()
        publish = bool(item.get('publish_to_residents', True))
        if target in ('住民', '住民向け') and publish:
            resident_notices.append(item)
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()

        if not name:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所名を入力してください。'
            )

        if any(existing.get('name') == name for existing in shelters):
            return render_template(
                'shelter_register.html',
                error=True,
                message='その避難所名はすでに登録されています。'
            )

        shelter_id = max((s.get('id', 0) for s in shelters), default=0) + 1
        shelters.append({'id': shelter_id, 'name': name})
        save_shelters()

        return render_template(
            'shelter_register.html',
            success=True,
            message=f'避難所「{name}」を登録しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
EMERGENCY_ORDER = {
    '高': 3,
    '中': 2,
    '低': 1,
}

@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    if request.method == 'POST':
        if request.form.get('update_status') == 'on':
            instruction_id = request.form.get('instruction_id', '')
            response_status = request.form.get('response_status', '').strip()
            allowed_statuses = ('未対応', '対応中', '対応済み')
            instruction = next(
                (item for item in instructions if str(item.get('id')) == instruction_id),
                None
            )

            if instruction and response_status in allowed_statuses:
                instruction['status'] = response_status
                instruction['updated_at'] = get_japan_time()
                save_instructions()
                status_message = '対応状況を更新しました。'
                status_error = False
            else:
                status_message = '対応状況を更新できませんでした。'
                status_error = True

            sorted_instructions = sorted(
                instructions,
                key=lambda item: EMERGENCY_ORDER.get(str(item.get('emergency_level', '中')), 0),
                reverse=True
            )
            return render_template(
                'board.html',
                instructions=sorted_instructions,
                shelters=shelters,
                success=not status_error,
                error=status_error,
                message=status_message
            )

        if request.form.get('sort_priority') == 'on':
            sorted_instructions = sorted(
                instructions,
                key=lambda item: EMERGENCY_ORDER.get(str(item.get('emergency_level', '中')), 0),
                reverse=True
            )
            return render_template('board.html', instructions=sorted_instructions, shelters=shelters, sort_applied=True)

        content = request.form.get('content', '').strip()
        notice_type = request.form.get('notice_type', '避難指示').strip() or '避難指示'
        target = request.form.get('target', '住民向け').strip() or '住民向け'
        shelter = request.form.get('shelter', '').strip()
        region = request.form.get('region', '全域').strip() or '全域'
        emergency_level = request.form.get('emergency_level', '中').strip() or '中'
        response_status = request.form.get('response_status', '未対応').strip() or '未対応'
        publish_to_residents = (
            target in ('住民', '住民向け')
            and request.form.get('publish_to_residents') == 'on'
        )

        shelter_names = {str(item.get('name', '')).strip() for item in shelters}
        if notice_type not in ('避難指示', '避難所開設'):
            return render_template(
                'board.html',
                instructions=instructions,
                shelters=shelters,
                error=True,
                message='発信種別を選択してください。'
            )

        if shelter and shelter not in shelter_names:
            return render_template(
                'board.html',
                instructions=instructions,
                shelters=shelters,
                error=True,
                message='登録済みの避難所を選択してください。'
            )

        if notice_type == '避難所開設' and not shelter:
            return render_template(
                'board.html',
                instructions=instructions,
                shelters=shelters,
                error=True,
                message='避難所開設の発信では、開設する避難所を選択してください。'
            )

        if shelter:
            destination_message = (
                f'{region}の{shelter}を開設しました。'
                if notice_type == '避難所開設'
                else f'{region}は{shelter}へ避難してください。'
            )
        else:
            destination_message = f'{region}の人は安全な場所へ避難してください。'

        next_id = max((int(i.get('id', 0)) for i in instructions), default=0) + 1
        now = get_japan_time()
        new_instruction = {
            'id': next_id,
            'target': target,
            'content': content,
            'destination_message': destination_message,
            'shelter': shelter,
            'region': region,
            'status': response_status,
            'emergency_level': emergency_level,
            'publish_to_residents': publish_to_residents,
            'notice_type': notice_type,
            'created_at': now,
            'updated_at': now,
        }
        instructions.insert(0, new_instruction)
        save_instructions()

        ordered_instructions = sorted(
            instructions,
            key=lambda item: EMERGENCY_ORDER.get(str(item.get('emergency_level', '中')), 0),
            reverse=True
        )
        return render_template(
            'board.html',
            instructions=ordered_instructions,
            shelters=shelters,
            success=True,
            message='発信を登録しました。'
        )

    ordered_instructions = sorted(
        instructions,
        key=lambda item: EMERGENCY_ORDER.get(str(item.get('emergency_level', '中')), 0),
        reverse=True
    )
    return render_template('board.html', instructions=ordered_instructions, shelters=shelters)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
