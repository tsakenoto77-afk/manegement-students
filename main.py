# main.py (Flask-SQLAlchemy ORM 統合版 - 学生マスタ構造変更済み)

import os
from datetime import datetime, date, timedelta, time
from flask import Flask, render_template, request, url_for, jsonify, redirect, cli
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
import click

# =========================================================================
# データベース設定
# =========================================================================

app = Flask(__name__)

# PostgreSQLの接続設定を優先し、環境変数がない場合はSQLiteにフォールバック
# Flask-SQLAlchemyは、PostgreSQL接続に 'postgresql://' スキームを使用
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///school.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# =========================================================================
# 出席判定に関する定数
# =========================================================================
# 授業開始時刻からこの時間(分)を超えると「欠席」とする (DB改.pyより)
ABSENT_THRESHOLD_MINUTES = 20 
# 遅刻判定の閾値（例: 授業開始から10分）
LATE_THRESHOLD_MINUTES = 10 

# =========================================================================
# データベーススキーマ定義 (db_setup.pyのSQLをクラスに変換)
# =========================================================================

# 1. 曜日マスタ
class 曜日マスタ(db.Model):
    __tablename__ = '曜日マスタ'
    曜日ID = db.Column(db.SmallInteger, primary_key=True) # 0は日曜日
    曜日名 = db.Column(db.String(10), nullable=False)
    備考 = db.Column(db.Text)

# 2. 期マスタ
class 期マスタ(db.Model):
    __tablename__ = '期マスタ'
    期ID = db.Column(db.SmallInteger, primary_key=True)
    期名 = db.Column(db.String(20), nullable=False)
    備考 = db.Column(db.Text)

# 3. 学科
class 学科(db.Model):
    __tablename__ = '学科'
    学科ID = db.Column(db.SmallInteger, primary_key=True)
    学科名 = db.Column(db.String(50))
    備考 = db.Column(db.Text)

# 4. 教室
class 教室(db.Model):
    __tablename__ = '教室'
    教室ID = db.Column(db.SmallInteger, primary_key=True)
    教室名 = db.Column(db.String(50), nullable=False)
    収容人数 = db.Column(db.SmallInteger, nullable=False)
    備考 = db.Column(db.Text)

# 5. 授業科目
class 授業科目(db.Model):
    __tablename__ = '授業科目'
    授業科目ID = db.Column(db.SmallInteger, primary_key=True)
    授業科目名 = db.Column(db.String(100), nullable=False)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), nullable=False)
    単位 = db.Column(db.SmallInteger, nullable=False)
    開講期 = db.Column(db.String(10), nullable=False) # 例: "1,2"
    備考 = db.Column(db.Text)
    
    学科 = db.relationship('学科', backref=db.backref('授業科目', lazy=True))

# 6. 学生マスタ (ユーザーの新しいスキーマに合わせて変更)
class 学生マスタ(db.Model):
    __tablename__ = '学生マスタ'
    学籍番号 = db.Column(db.Integer, primary_key=True) # 変更: 学生番号 -> 学籍番号
    氏名 = db.Column(db.String(50)) # TEXT NULL
    学年 = db.Column(db.SmallInteger) # 追加: 学年 INTEGER NULL
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID')) # NULL許容に変更
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), nullable=False) # 追加: 期 TINYINT NOT NULL
    # 入学年度, 備考 は削除
    
    学科 = db.relationship('学科', backref=db.backref('学生', lazy=True))
    期情報 = db.relationship('期マスタ', backref=db.backref('学生', lazy=True))

# 7. 時限設定
class TimeTable(db.Model):
    __tablename__ = 'TimeTable'
    時限 = db.Column(db.SmallInteger, primary_key=True)
    開始時刻 = db.Column(db.Time, nullable=False)
    終了時刻 = db.Column(db.Time, nullable=False)
    備考 = db.Column(db.Text)

# 8. 週時間割
class 週時間割(db.Model):
    __tablename__ = '週時間割'
    年度 = db.Column(db.SmallInteger, primary_key=True)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), primary_key=True)
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), primary_key=True)
    曜日 = db.Column(db.SmallInteger, db.ForeignKey('曜日マスタ.曜日ID'), primary_key=True)
    時限 = db.Column(db.SmallInteger, db.ForeignKey('TimeTable.時限'), primary_key=True)
    科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'), nullable=False)
    教室ID = db.Column(db.SmallInteger, db.ForeignKey('教室.教室ID'), nullable=False)
    備考 = db.Column(db.Text)

    科目 = db.relationship('授業科目', backref=db.backref('時間割', lazy=True))
    教室 = db.relationship('教室', backref=db.backref('時間割', lazy=True))
    時間帯 = db.relationship('TimeTable', backref=db.backref('時間割', lazy=True))
    曜日情報 = db.relationship('曜日マスタ', backref=db.backref('時間割', lazy=True))

# 9. 入退室_出席記録 (ログテーブル) - 学生マスタのPK名変更に対応
class 入退室_出席記録(db.Model):
    __tablename__ = '入退室_出席記録'
    記録ID = db.Column(db.Integer, primary_key=True)
    学生番号 = db.Column(db.Integer, db.ForeignKey('学生マスタ.学籍番号'), nullable=False) # 外部キー参照を学籍番号に変更
    入室日時 = db.Column(db.DateTime, nullable=False)
    退室日時 = db.Column(db.DateTime, nullable=True)
    記録日 = db.Column(db.Date, nullable=False) 
    ステータス = db.Column(db.String(10), nullable=False) # '出席', '遅刻', '欠席', '早退', '未定'
    授業科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'), nullable=True)
    週時間割ID = db.Column(db.String(50), nullable=True) # 便宜的な参照キー (年度-学科ID-期-曜日-時限)
    備考 = db.Column(db.Text)

    学生 = db.relationship('学生マスタ', backref=db.backref('出席記録', lazy=True))
    科目 = db.relationship('授業科目', backref=db.backref('出席記録', lazy=True))


# =========================================================================
# データベース初期化とデータ挿入 (完全版)
# =========================================================================

def insert_initial_data():
    """マスタデータと週時間割の全データを挿入する"""
    
    # --- 1. 曜日マスタ ---
    db.session.add_all([
        曜日マスタ(曜日ID=1, 曜日名='月'),
        曜日マスタ(曜日ID=2, 曜日名='火'),
        曜日マスタ(曜日ID=3, 曜日名='水'),
        曜日マスタ(曜日ID=4, 曜日名='木'),
        曜日マスタ(曜日ID=5, 曜日名='金'),
        曜日マスタ(曜日ID=6, 曜日名='土'),
        曜日マスタ(曜日ID=0, 曜日名='日')
    ])

    # --- 2. 期マスタ ---
    db.session.add_all([
        期マスタ(期ID=1, 期名='一期', 備考='前期前半'),
        期マスタ(期ID=2, 期名='二期', 備考='前期後半'),
        期マスタ(期ID=3, 期名='三期', 備考='後期前半'),
        期マスタ(期ID=4, 期名='四期', 備考='後期後半')
    ])

    # --- 3. 学科 ---
    db.session.add_all([
        学科(学科ID=300, 学科名='共通', 備考='全学科共通'),
        学科(学科ID=301, 学科名='機械系', 備考='3年機械系学科'),
        学科(学科ID=302, 学科名='電子情報系', 備考='3年電子情報系学科'),
        学科(学科ID=303, 学科名='建設系', 備考='3年建設系学科')
    ])

    # --- 4. 教室 ---
    db.session.add_all([
        (1205,'A205',20),
        (2102,'B102/103',20),
        (2201,'B201',20),
        (2202,'B202',20),
        (2204,'B204',20),
        (2205,'B205',20),
        (2301,'B301',20),
        (2302,'B302',20),
        (2303,'B303',20),
        (2304,'B304',20),
        (2305,'B305',20),
        (2306,'B306(視聴覚室)',20),
        (3101,'C101(生産ロボット室)',20),
        (3103,'C103(開発課題実習室)',20),
        (3201,'C201',20),
        (3202,'C202(応用課程計測制御応用実習室)',20),
        (3203,'C203',20),
        (3204,'C204',20),
        (3231,'C231(資料室)',20),
        (3301,'C301(マルチメディア実習室)',20),
        (3302,'C302(システム開発実習室)',20),
        (3303,'C303(システム開発実習室Ⅱ)',20),
        (3304,'C304/305(応用課程生産管理ネットワーク応用実習室)',20),
        (3306,'C306(共通実習室)',20),
        (4102,'D102(回路基板加工室)',20),
        (4201,'D201(開発課題実習室)',20),
        (4202,'D202(電子情報技術科教官室)',20),
        (4231,'D231(準備室)',20),
        (4301,'D301',20),
        (4302,'D302(PC実習室)',20);
        教室(教室ID=3305, 教室名='B102製図室', 収容人数=40, 備考='建設系製図用')
    ])

    # --- 5. 授業科目 ---
    db.session.add_all([
        授業科目(授業科目ID=311, 授業科目名='機械設計製図', 学科ID=301, 単位=2, 開講期='3,4'),
        授業科目(授業科目ID=312, 授業科目名='機械実習A', 学科ID=301, 単位=2, 開講期='3'),
        授業科目(授業科目ID=313, 授業科目名='機械実習B', 学科ID=301, 単位=2, 開講期='4'),
        授業科目(授業科目ID=317, 授業科目名='応用数学', 学科ID=300, 単位=2, 開講期='4'),
        授業科目(授業科目ID=321, 授業科目名='制御回路設計製作実習', 学科ID=302, 単位=4, 開講期='3'),
        授業科目(授業科目ID=322, 授業科目名='プログラミング応用', 学科ID=302, 単位=4, 開講期='4'),
        授業科目(授業科目ID=331, 授業科目名='測量実習', 学科ID=303, 単位=3, 開講期='3'),
        授業科目(授業科目ID=332, 授業科目名='建設構造物設計', 学科ID=303, 単位=3, 開講期='4'),
        授業科目(授業科目ID=380, 授業科目名='標準課題Ⅰ', 学科ID=300, 単位=2, 開講期='3', 備考='卒業研究の前段階 (共通)'),
        授業科目(授業科目ID=381, 授業科目名='標準課題Ⅱ', 学科ID=300, 単位=2, 開講期='4', 備考='卒業研究の前段階 (共通)'),
        授業科目(授業科目ID=390, 授業科目名='専門英語', 学科ID=300, 単位=2, 開講期='3,4', 備考='共通科目')
    ])

    # --- 6. 学生マスタ (電子情報系 30名) - 新しいスキーマに対応 ---
    STUDENT_GRADE = 3 # 学年
    STUDENT_TERM = 4 # 期 (4期を仮定)
    
    # 新しいデータ構造: (学籍番号, 氏名, 学科ID, 学年, 期)
    students_data = [
        (222521301,'青井 渓一郎',3,1,3),(222521302,'赤坂 龍成',3,1,3),(222521303,'秋好 拓海',3,1,3),(222521304,'伊川 翔',3,1,3),
    (222521305,'岩切 亮太',3,1,3),(222521306,'上田 和輝',3,1,3),(222521307,'江本 龍之介',3,1,3),(222521308,'大久保 碧瀧',3,1,3),
    (222521309,'加來 涼雅',3,1,3),(222521310,'梶原 悠平',3,1,3),(222521311,'管野 友富紀',3,1,3),(222521312,'髙口 翔真',3,1,3),
    (222521313,'古城 静雅',3,1,3),(222521314,'小柳 知也',3,1,3),(222521315,'酒元 翼',3,1,3),(222521316,'座光寺 孝彦',3,1,3),
    (222521317,'佐野 勇太',3,1,3),(222521318,'清水 健心',3,1,3),(222521319,'新谷 雄飛',3,1,3),(222521320,'関原 響樹',3,1,3),
    (222521321,'髙橋 優人',3,1,3),(222521322,'武富 義樹',3,1,3),(222521323,'内藤 俊介',3,1,3),(222521324,'野田 千尋',3,1,3),
    (222521325,'野中 雄学',3,1,3),(222521326,'東 奈月',3,1,3),(222521327,'古田 雅也',3,1,3),(222521328,'牧野 倭大',3,1,3),
    (222521330,'宮岡 嘉熙',3,1,3),(222521329,'松隈 駿介',3,1,3);
    ]
    
    db.session.add_all([
        学生マスタ(学籍番号=s[0], 氏名=s[1], 学科ID=s[2], 学年=s[3], 期=s[4]) for s in students_data
    ])

    # --- 7. 時限設定 ---
    db.session.add_all([
        TimeTable(時限=1, 開始時刻=time(9, 0), 終了時刻=time(10, 30), 備考='1コマ目'),
        TimeTable(時限=2, 開始時刻=time(10, 40), 終了時刻=time(12, 10), 備考='2コマ目'),
        TimeTable(時限=3, 開始時刻=time(13, 0), 終了時刻=time(14, 30), 備考='3コマ目'),
        TimeTable(時限=4, 開始時刻=time(14, 40), 終了時刻=time(16, 10), 備考='4コマ目'),
        TimeTable(時限=5, 開始時刻=time(16, 20), 終了時刻=time(17, 50), 備考='5コマ目')
    ])
    
    # --- 8. 週時間割 (2025年度 電子情報系 302) ---
    # 月曜から金曜の1〜5時限を網羅
    timetable_data = [
        # 2025年度 3期 (電子情報系: 302)
        # 月曜日 (曜日ID=1)
        (2025, 3, 3, 1, 1, 327, 3301, '/中山'),
        (2025, 3, 3, 1, 2, 327, 3301, '/中山'),
        (2025, 3, 3, 1, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 1, 4, 380, 3301, 'C302/電子情報系');
        # 火曜日 (曜日ID=2)
        (2025, 3, 3, 2, 1, 317, 3302, 'K302/機械系'),
        (2025, 3, 3, 2, 2, 317, 3302, 'K302/機械系'),
        (2025, 3, 3, 2, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 2, 4, 380, 3301, 'C302/電子情報系');
        # 水曜日 (曜日ID=3)
        (2025, 3, 3, 3, 1, 329, 3301, '/岡田'),
        (2025, 3, 3, 3, 2, 329, 3301, '/岡田'),
        (2025, 3, 3, 3, 3, 308, 2301, '/上野');
        # 木曜日 (曜日ID=4)
        (2025, 3, 3, 4, 1, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 2, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 4, 380, 3301, 'C302/電子情報系');
        # 金曜日 (曜日ID=5)
        (2025, 3, 3, 5, 1, 321, 3302, '/玉井'),
        (2025, 3, 3, 5, 2, 321, 3302, '/玉井'),
        (2025, 3, 3, 5, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 5, 4, 380, 3301, 'C302/電子情報系');

        # 2025年度 4期 (電子情報系: 302)
        # 月曜日 (曜日ID=1)
        (2025, 3, 4, 1, 1, 381, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 1, 2, 381, 3302, 'C101/電子情報系');
        # 火曜日 (曜日ID=2)
        (2025, 3, 4, 2, 1, 317, 3302, 'K302/機械系'),
        (2025, 3, 4, 2, 2, 317, 3302, 'K302/機械系'),
        (2025, 3, 4, 2, 3, 381, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 2, 4, 381, 3302, 'C101/電子情報系');
        # 水曜日 (曜日ID=3)
        (2025, 3, 4, 3, 1, 329, 3301, '/岡田'),
        (2025, 3, 4, 3, 2, 329, 3301, '/岡田'),
        (2025, 3, 4, 3, 3, 308, 2301, '/上野');
        # 木曜日 (曜日ID=4)
        (2025, 3, 4, 4, 1, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 2, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 3, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 4, 331, 3302, 'C101/電子情報系');
        # 金曜日 (曜日ID=5)
        (2025, 3, 4, 5, 1, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 5, 2, 331, 3302, 'C101/電子情報系');
    ]

    db.session.add_all([
        週時間割(年度=t[0], 学科ID=t[1], 期=t[2], 曜日=t[3], 時限=t[4], 科目ID=t[5], 教室ID=t[6], 備考=t[7]) for t in timetable_data
    ])
    
    db.session.commit()
    
# Flask CLIコマンドとしてデータベース初期化を登録
@click.command('init-db')
def init_db_command():
    """データベーステーブルを作成し、初期データを挿入します。"""
    with app.app_context():
        # 既存のテーブルを全て削除（テスト用）
        db.drop_all() 
        # 新しくテーブルを作成
        db.create_all() 
        # マスタデータを挿入
        insert_initial_data() 
        click.echo('Initialized the database with complete master data and timetable.')

app.cli.add_command(init_db_command)

# =========================================================================
# ヘルパー関数: 現在の授業を取得
# =========================================================================

def get_current_lesson(target_dt: datetime):
    """
    与えられた日時に対応する週時間割の授業情報を取得します。
    対象: 2025年度、電子情報系 (302) の3期・4期のみ
    
    戻り値:
        Tuple[週時間割 | None, TimeTable | None]
    """
    try:
        current_date = target_dt.date()
        current_time = target_dt.time()
        
        # 曜日ID (月=1, 火=2, ... 金=5, 土=6, 日=0)
        # Pythonのweekday()は月=0, ... 日=6 なので調整
        weekday_int = target_dt.weekday() + 1
        if weekday_int == 7: # 日曜日
            weekday_int = 0
            
        # 授業があるのは月曜から金曜のみ (1-5)
        if weekday_int < 1 or weekday_int > 5:
            return None, None
            
        # 2025年度、電子情報系 (302) の時間割のみを対象とする
        TARGET_YEAR = 2025
        TARGET_DEPT_ID = 302
        
        # 現在の時刻に対応する時限を検索
        current_period = TimeTable.query.filter(
            TimeTable.開始時刻 <= current_time,
            TimeTable.終了時刻 >= current_time
        ).first()
        
        if not current_period:
            return None, None # 授業時間外
            
        # 季節によって期を仮定 (簡易的に、3期を後期前半、4期を後期後半とする)
        # 10月〜12月を3期、1月〜3月を4期と仮定
        current_month = target_dt.month
        current_term_id = None
        if current_month >= 10 or current_month <= 3: # 10月〜3月
            if current_month >= 10 and current_month <= 12:
                current_term_id = 3 # 3期
            elif current_month >= 1 and current_month <= 3:
                current_term_id = 4 # 4期
        else:
             # それ以外の月はここでは対象外とする (1期, 2期)
             return None, None
             
        if not current_term_id:
            return None, None
            
        # 週時間割を検索
        lesson = 週時間割.query.filter_by(
            年度=TARGET_YEAR,
            学科ID=TARGET_DEPT_ID,
            期=current_term_id,
            曜日=weekday_int,
            時限=current_period.時限
        ).first()

        return lesson, current_period

    except Exception as e:
        print(f"Error in get_current_lesson: {e}")
        return None, None

# =========================================================================
# 自動出席判定処理
# =========================================================================

def check_and_set_absent_status(target_date: date):
    """
    指定された日付の授業について、入室記録がない学生を欠席として記録します。
    または、遅刻・早退・出席のステータスを最終決定します。
    """
    
    # 処理対象の日付の曜日を取得 (月=1, ... 金=5)
    weekday_int = target_date.weekday() + 1
    if weekday_int == 7: weekday_int = 0
    if weekday_int < 1 or weekday_int > 5: return # 土日祝は処理しない (簡易化)

    # 処理対象の期を決定 (このアプリは電子情報系(302)の3期, 4期のみを対象とする)
    current_month = target_date.month
    current_term_id = None
    if current_month >= 10 and current_month <= 12:
        current_term_id = 3 # 3期
    elif current_month >= 1 and current_month <= 3:
        current_term_id = 4 # 4期
    else:
        return # 処理対象外の期

    TARGET_YEAR = 2025
    TARGET_DEPT_ID = 302
    
    # 該当日の全ての授業時間割を取得
    lessons_for_day = 週時間割.query.filter_by(
        年度=TARGET_YEAR,
        学科ID=TARGET_DEPT_ID,
        期=current_term_id,
        曜日=weekday_int
    ).order_by(週時間割.時限).all()
    
    # 電子情報系3年生全員を取得
    all_students = 学生マスタ.query.filter_by(学科ID=TARGET_DEPT_ID).all()
    
    # 授業ごとに処理
    for lesson in lessons_for_day:
        time_period = TimeTable.query.get(lesson.時限)
        if not time_period: continue
        
        lesson_start_time = datetime.combine(target_date, time_period.開始時刻)
        lesson_absent_threshold = lesson_start_time + timedelta(minutes=ABSENT_THRESHOLD_MINUTES)
        
        lesson_id_key = f"{lesson.年度}-{lesson.学科ID}-{lesson.期}-{lesson.曜日}-{lesson.時限}"

        for student in all_students:
            # 既に記録が存在するか確認 (入室記録、または手動で「欠席」が設定されている可能性)
            existing_record = 入退室_出席記録.query.filter(
                入退室_出席記録.学生番号 == student.学籍番号, # 学生番号(FK)の比較対象を学籍番号(PK)に変更
                入退室_出席記録.記録日 == target_date,
                入退室_出席記録.授業科目ID == lesson.科目ID,
                入退室_出席記録.週時間割ID == lesson_id_key
            ).first()
            
            if existing_record:
                # 記録が未定 (入退室時の初期ステータス) の場合、最終判定を行う
                if existing_record.ステータス == '未定':
                    # 入室時刻
                    enter_dt = existing_record.入室日時
                    
                    # 遅刻判定時間
                    late_threshold = lesson_start_time + timedelta(minutes=LATE_THRESHOLD_MINUTES)
                    
                    new_status = '出席'
                    if enter_dt > late_threshold and enter_dt <= lesson_absent_threshold:
                        new_status = '遅刻'
                    elif enter_dt > lesson_absent_threshold:
                        # 授業開始から欠席閾値以降に入室した場合は「遅刻（遅れすぎ）」と見なすが、
                        # システム的には記録があるため欠席とはしない
                        new_status = '遅刻' 
                    
                    # 退室時刻があれば早退判定も可能だが、簡易化のため割愛
                    
                    existing_record.ステータス = new_status
                    db.session.add(existing_record)
            else:
                # 記録がない場合、欠席として新規作成
                if datetime.now() > lesson_absent_threshold:
                    new_record = 入退室_出席記録(
                        学生番号=student.学籍番号, # 挿入する値も学籍番号を使用
                        入室日時=lesson_absent_threshold, # 便宜的に欠席閾値を設定
                        退室日時=None,
                        記録日=target_date,
                        ステータス='欠席',
                        授業科目ID=lesson.科目ID,
                        週時間割ID=lesson_id_key,
                        備考='自動判定（入室記録なし）'
                    )
                    db.session.add(new_record)
                    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"自動判定処理中にエラーが発生しました: {e}")
        
@click.command('run-attendance-check')
def run_attendance_check_command():
    """本日の授業に対して自動欠席判定処理を実行するCLIコマンド"""
    with app.app_context():
        # 通常は昨日の日付を対象とするが、ここでは実行日の前日を対象とする
        yesterday = date.today() - timedelta(days=1)
        check_and_set_absent_status(yesterday)
        click.echo(f'Finished running daily attendance check for {yesterday}.')

app.cli.add_command(run_attendance_check_command)

# =========================================================================
# ルート定義
# =========================================================================

@app.route('/')
def index():
    """トップページ（入退室記録インターフェース）"""
    # ORMのselectで学生番号の代わりに学籍番号を使用
    students = 学生マスタ.query.order_by(学生マスタ.学籍番号).all() 
    lesson, time_period = get_current_lesson(datetime.now())
    
    current_class_name = "授業時間外または時間割未登録"
    current_class_id = None
    
    if lesson:
        current_class_name = f"{lesson.科目.授業科目名} ({lesson.教室.教室名}) - {time_period.時限}限"
        current_class_id = lesson.科目ID
        
    return render_template('index.html', students=students, current_class=current_class_name, current_class_id=current_class_id)

@app.route('/record_io', methods=['POST'])
def record_io():
    """入退室を記録するAPIエンドポイント"""
    student_id = request.form.get('student_id')
    
    if not student_id:
        return jsonify({"success": False, "message": "学生番号が指定されていません。"}), 400

    try:
        # get_or_404はPK（学籍番号）で検索
        student = 学生マスタ.query.get(int(student_id)) 
        if not student:
            return jsonify({"success": False, "message": f"学籍番号 {student_id} は存在しません。"}), 404

        now = datetime.now()
        current_date = now.date()
        
        # 現在の授業情報を取得
        lesson, time_period = get_current_lesson(now)
        class_id = lesson.科目ID if lesson else None
        
        # 週時間割のキーを作成 (レコードを特定しやすくするため)
        lesson_id_key = None
        if lesson:
            lesson_id_key = f"{lesson.年度}-{lesson.学科ID}-{lesson.期}-{lesson.曜日}-{lesson.時限}"

        # 既にその日のその授業で入室記録があるかを確認
        # 未定ステータス（入室済み）のレコードを探す
        existing_record = 入退室_出席記録.query.filter(
            入退室_出席記録.学生番号 == student.学籍番号, # 比較対象を学籍番号に変更
            入退室_出席記録.記録日 == current_date,
            入退室_出席記録.授業科目ID == class_id,
            入退室_出席記録.ステータス == '未定'
        ).first()

        if existing_record:
            # --- 退室処理 ---
            existing_record.退室日時 = now
            
            # ステータスを「早退」に更新するか、最終的な「出席」「遅刻」に確定させる
            # 自動判定処理で最終化される前提だが、ここでは入室時のステータスに基づいてメッセージを返す
            # 簡易化のため、ここでは入室時ステータスのままとする
            
            db.session.commit()
            return jsonify({
                "success": True, 
                "message": f"学籍番号 {student_id} ({student.氏名}) の退室を記録しました。",
                "status": "exit",
                "class_name": lesson.科目.授業科目名 if lesson else "授業時間外"
            })
        else:
            # --- 入室処理 ---
            status = '未定' # 初期ステータス（後で自動判定）
            
            message = f"学籍番号 {student_id} ({student.氏名}) の入室を記録しました。"
            
            # 授業時間内の場合、入室時刻からステータスを暫定決定
            if lesson:
                lesson_start_dt = datetime.combine(current_date, time_period.開始時刻)
                late_threshold = lesson_start_dt + timedelta(minutes=LATE_THRESHOLD_MINUTES)
                absent_threshold = lesson_start_dt + timedelta(minutes=ABSENT_THRESHOLD_MINUTES)
                
                if now > absent_threshold:
                    # 欠席扱いとなる時刻だが、記録があったため「遅刻(遅れすぎ)」として記録
                    status = '遅刻'
                    message = f"学籍番号 {student_id} ({student.氏名}) を授業開始{ABSENT_THRESHOLD_MINUTES}分超過後に記録しました。（遅刻）"
                elif now > late_threshold:
                    status = '遅刻'
                    message = f"学籍番号 {student_id} ({student.氏名}) を授業開始{LATE_THRESHOLD_MINUTES}分超過後に記録しました。（遅刻）"
                else:
                    status = '出席'
                    message = f"学籍番号 {student_id} ({student.氏名}) の出席を記録しました。"
            
            new_record = 入退室_出席記録(
                学生番号=student.学籍番号, # 学籍番号をFKの値として使用
                入室日時=now,
                記録日=current_date,
                ステータス=status,
                授業科目ID=class_id,
                週時間割ID=lesson_id_key
            )
            db.session.add(new_record)
            db.session.commit()
            
            return jsonify({
                "success": True, 
                "message": message,
                "status": "enter",
                "class_name": lesson.科目.授業科目名 if lesson else "授業時間外"
            })

    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "データベースエラーが発生しました（IntegrityError）。"}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"予期せぬエラー: {str(e)}"}), 500


@app.route('/logs')
def logs_page():
    """入退室記録と出席状況の一覧を表示するページ"""
    # 最新の記録が上にくるようにソート
    logs = 入退室_出席記録.query.order_by(入退室_出席記録.入室日時.desc()).all()
    
    # 関連データをプリロード (Eager loading)
    logs_data = []
    for log in logs:
        student_name = log.学生.氏名 if log.学生 else "不明"
        subject_name = log.科目.授業科目名 if log.科目 else "授業時間外"
        logs_data.append({
            '記録ID': log.記録ID,
            '学生番号': log.学生番号, # 表示は学生番号(FK)のまま
            '学生氏名': student_name,
            '授業科目': subject_name,
            '入室日時': log.入室日時.strftime('%Y-%m-%d %H:%M:%S'),
            '退室日時': log.退室日時.strftime('%Y-%m-%d %H:%M:%S') if log.退室日時 else '-',
            'ステータス': log.ステータス,
            '備考': log.備考 or '-'
        })

    return render_template('logs.html', logs=logs_data)


@app.route('/attendance_status')
def attendance_status():
    """出席状況を集計して表示するページ"""
    
    # 全授業科目を取得（集計表示用）
    subjects = 授業科目.query.order_by(授業科目.授業科目ID).all()
    
    # 週時間割に登録されている科目IDのリストを取得
    scheduled_subject_ids = db.session.query(週時間割.科目ID).distinct().all()
    scheduled_subject_ids = [s[0] for s in scheduled_subject_ids]
    
    # 集計対象の授業科目のみをフィルタリング
    display_subjects = [s for s in subjects if s.授業科目ID in scheduled_subject_ids]

    # 集計結果を格納する辞書 {科目ID: {ステータス: カウント}}
    attendance_summary = {}

    for subject in display_subjects:
        summary = db.session.query(
            入退室_出席記録.ステータス,
            func.count(入退室_出席記録.ステータス)
        ).filter(
            入退室_出席記録.授業科目ID == subject.授業科目ID
        ).group_by(入退室_出席記録.ステータス).all()
        
        attendance_summary[subject.授業科目ID] = {row[0]: row[1] for row in summary}
        
    return render_template('attendance_status.html', subjects=display_subjects, summary=attendance_summary)


# =========================================================================
# CRUD/削除ルート (ORM対応)
# =========================================================================

@app.route('/delete/<int:record_id>', methods=['POST'])
def delete_record(record_id):
    """個別の入退室記録をIDで削除する"""
    record = 入退室_出席記録.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    # 削除後、元のページ（ログ一覧など）に戻る
    return redirect(request.referrer or url_for('logs_page')) 

@app.route('/delete_all', methods=['POST'])
def delete_all_records():
    """全ての入退室_出席記録を削除する（テーブルは残る）"""
    db.session.query(入退室_出席記録).delete()
    db.session.commit()
    return redirect(url_for('logs_page')) 


# =========================================================================
# データベースの初期化とWebアプリの実行
# =========================================================================

if __name__ == "__main__":
    
    # ORMの場合、init-dbコマンドを手動で実行する必要があることが多い
    # 実行方法: python main.py init-db （データベース初期化）
    #           python main.py run-attendance-check （欠席判定実行）
    #           python main.py run （アプリ起動）
    print("\n-------------------------------------------")
    print("Flask-SQLAlchemy ORMベースのWebアプリを起動します。")
    print("データベース初期化とマスタデータ挿入には 'flask init-db' を実行してください。")
    print("アプリケーション実行には 'flask run' を使用してください。")
    print("-------------------------------------------\n")
