# main.py (Flask-SQLAlchemy ORM 統合版 - Render対応 - 改善版 + 自動欠席判定機能 + 欠席確認機能)

import os
from datetime import datetime, date, timedelta, time
from flask import Flask, render_template, request, url_for, jsonify, redirect, cli
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, Index, and_, case
from sqlalchemy.exc import IntegrityError, ProgrammingError
import click

# =========================================================================
# データベース設定
# =========================================================================

app = Flask(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///school.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace("postgres://", "postgresql://")
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
    曜日ID = db.Column(db.SmallInteger, primary_key=True)  # 0は日曜日
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
    開講期 = db.Column(db.String(10), nullable=False)  # 例: "1,2"
    備考 = db.Column(db.Text)
    
    学科 = db.relationship('学科', backref=db.backref('授業科目', lazy=True))

# 6. 学生マスタ (ユーザーの新しいスキーマに合わせて変更)
class 学生マスタ(db.Model):
    __tablename__ = '学生マスタ'
    学籍番号 = db.Column(db.Integer, primary_key=True, index=True)  # インデックス追加
    氏名 = db.Column(db.String(50))  # TEXT NULL
    学年 = db.Column(db.SmallInteger, index=True)  # インデックス追加
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), index=True)  # インデックス追加
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), nullable=False, index=True)  # インデックス追加
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
    学生番号 = db.Column(db.Integer, db.ForeignKey('学生マスタ.学籍番号'), nullable=False, index=True)  # インデックス追加
    入室日時 = db.Column(db.DateTime, nullable=False)
    退室日時 = db.Column(db.DateTime, nullable=True)
    記録日 = db.Column(db.Date, nullable=False, index=True)  # インデックス追加
    ステータス = db.Column(db.String(10), nullable=False)  # '出席', '遅刻', '欠席', '早退', '未定'
    授業科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'), nullable=True)
    週時間割ID = db.Column(db.String(50), nullable=True)  # 便宜的な参照キー (年度-学科ID-期-曜日-時限)
    備考 = db.Column(db.Text)

    学生 = db.relationship('学生マスタ', backref=db.backref('出席記録', lazy=True))
    科目 = db.relationship('授業科目', backref=db.backref('出席記録', lazy=True))

# =========================================================================
# 自動欠席判定処理機能 (新規追加 + 遅刻判定拡張)
# =========================================================================

def auto_absent_check():
    """
    授業開始時刻までに「入室」記録がない学生を「欠席」と記録する。
    さらに、授業開始後一定時間（10分）を超えて入室した場合を「遅刻」、20分を超えて入室した場合を「欠席」と記録。
    現在の授業スケジュールをチェックし、該当学生に対して入退室_出席記録にレコードを挿入。
    """
    now = datetime.now()
    today = date.today()
    today_weekday = now.weekday() + 1  # Pythonのweekday()は0=月曜日なので+1

    app.logger.info(f"自動欠席/遅刻判定を開始: {now}")

    try:
        # 1. 今日の授業スケジュールを取得 (週時間割から)
        # 曜日IDは1=月曜日, ..., 7=日曜日
        todays_schedules = db.session.query(週時間割).filter(
            and_(
                週時間割.曜日 == today_weekday,
                週時間割.年度 == 2025  # 年度は固定（必要に応じて動的に）
            )
        ).all()

        for schedule in todays_schedules:
            # 授業開始時刻を取得
            timetable = db.session.query(TimeTable).filter(TimeTable.時限 == schedule.時限).first()
            if not timetable:
                continue
            class_start_time = datetime.combine(today, timetable.開始時刻)
            # 遅刻判定タイミング: 授業開始 + LATE_THRESHOLD_MINUTES
            late_check_time = class_start_time + timedelta(minutes=LATE_THRESHOLD_MINUTES)
            # 欠席判定タイミング: 授業開始 + ABSENT_THRESHOLD_MINUTES
            absent_check_time = class_start_time + timedelta(minutes=ABSENT_THRESHOLD_MINUTES)

            # 2. 該当授業の学生リストを取得 (学科・期に基づく)
            students = db.session.query(学生マスタ).filter(
                and_(
                    学生マスタ.学科ID == schedule.学科ID,
                    学生マスタ.期 == schedule.期
                )
            ).all()

            # 3. 各学生について、入室記録があるかをチェック
            for student in students:
                # 入退室_出席記録で、今日のこの授業の入室記録があるか？
                existing_record = db.session.query(入退室_出席記録).filter(
                    and_(
                        入退室_出席記録.学生番号 == student.学籍番号,
                        入退室_出席記録.記録日 == today,
                        入退室_出席記録.授業科目ID == schedule.科目ID,
                        入退室_出席記録.入室日時.isnot(None)  # 入室記録あり
                    )
                ).first()

                if existing_record:
                    # 入室記録がある場合、遅刻判定
                    if existing_record.入室日時 > late_check_time and existing_record.入室日時 <= absent_check_time:
                        # 遅刻判定: ステータスを'遅刻'に更新（既存が'未定'の場合）
                        if existing_record.ステータス == '未定':
                            existing_record.ステータス = '遅刻'
                            existing_record.備考 = '自動遅刻判定'
                            app.logger.info(f"遅刻記録更新: 学生 {student.学籍番号} - 科目 {schedule.科目ID}")
                    # 出席の場合はそのまま（既存記録を尊重）
                    continue

                # 入室記録がない場合、欠席判定
                if now > absent_check_time:
                    # 欠席レコードが既に存在するかチェック
                    absent_record = db.session.query(入退室_出席記録).filter(
                        and_(
                            入退室_出席記録.学生番号 == student.学籍番号,
                            入退室_出席記録.記録日 == today,
                            入退室_出席記録.授業科目ID == schedule.科目ID,
                            入退室_出席記録.ステータス == '欠席'
                        )
                    ).first()

                    if absent_record:
                        # 既に欠席記録があるのでスキップ
                        continue

                    # 欠席レコードを挿入
                    week_schedule_id = f"{schedule.年度}-{schedule.学科ID}-{schedule.期}-{schedule.曜日}-{schedule.時限}"
                    new_absent_record = 入退室_出席記録(
                        学生番号=student.学籍番号,
                        入室日時=None,  # 入室なし
                        退室日時=None,
                        記録日=today,
                        ステータス='欠席',
                        授業科目ID=schedule.科目ID,
                        週時間割ID=week_schedule_id,
                        備考='自動欠席判定'
                    )
                    db.session.add(new_absent_record)
                    app.logger.info(f"欠席記録挿入: 学生 {student.学籍番号} - 科目 {schedule.科目ID}")

        db.session.commit()
        app.logger.info("自動欠席/遅刻判定完了")

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"自動欠席/遅刻判定中にエラー: {e}")



# =========================================================================
# 初期データ挿入関数 (マスタデータ) - 期をパラメータ化
# =========================================================================

def insert_initial_data(term=3):
    """マスタデータと週時間割の全データを挿入する。期を動的に設定可能。"""
    
    # --- 1. 曜日マスタ ---
    db.session.add_all([
        曜日マスタ(曜日ID=0, 曜日名='授業日'),
        曜日マスタ(曜日ID=1, 曜日名='月曜日'),
        曜日マスタ(曜日ID=2, 曜日名='火曜日'),
        曜日マスタ(曜日ID=3, 曜日名='水曜日'),
        曜日マスタ(曜日ID=4, 曜日名='木曜日'),
        曜日マスタ(曜日ID=5, 曜日名='金曜日'),
        曜日マスタ(曜日ID=6, 曜日名='土曜日'),
        曜日マスタ(曜日ID=7, 曜日名='日曜日'),
        曜日マスタ(曜日ID=8, 曜日名='祝祭日'),
        曜日マスタ(曜日ID=9, 曜日名='休日')
    ])

    # --- 2. 期マスタ ---
    db.session.add_all([
        期マスタ(期ID=1, 期名='Ⅰ'),
        期マスタ(期ID=2, 期名='Ⅱ'),
        期マスタ(期ID=3, 期名='Ⅲ'),
        期マスタ(期ID=4, 期名='Ⅳ'),
        期マスタ(期ID=5, 期名='Ⅴ'),
        期マスタ(期ID=6, 期名='Ⅵ'),
        期マスタ(期ID=7, 期名='Ⅶ'),
        期マスタ(期ID=8, 期名='Ⅷ'),
        期マスタ(期ID=9, 期名='前期(Ⅱ期)集中'),
        期マスタ(期ID=10, 期名='後期(Ⅲ期)集中')
    ])

    # --- 3. 学科 ---
    db.session.add_all([
        学科(学科ID=1, 学科名='生産機械システム技術科', 備考=''),
        学科(学科ID=2, 学科名='生産電気システム技術科', 備考=''),
        学科(学科ID=3, 学科名='生産電子情報システム技術科', 備考='')
    ])

    # --- 4. 教室 ---
    db.session.add_all([
        教室(教室ID=1205, 教室名='A205', 収容人数=20, 備考=''),
        教室(教室ID=2102, 教室名='B102/103', 収容人数=20, 備考=''),
        教室(教室ID=2201, 教室名='B201', 収容人数=20, 備考=''),
        教室(教室ID=2202, 教室名='B202', 収容人数=20, 備考=''),
        教室(教室ID=2204, 教室名='B204', 収容人数=20, 備考=''),
        教室(教室ID=2205, 教室名='B205', 収容人数=20, 備考=''),
        教室(教室ID=2301, 教室名='B301', 収容人数=20, 備考=''),
        教室(教室ID=2302, 教室名='B302', 収容人数=20, 備考=''),
        教室(教室ID=2303, 教室名='B303', 収容人数=20, 備考=''),
        教室(教室ID=2304, 教室名='B304', 収容人数=20, 備考=''),
        教室(教室ID=2305, 教室名='B305', 収容人数=20, 備考=''),
        教室(教室ID=2306, 教室名='B306(視聴覚室)', 収容人数=20, 備考=''),
        教室(教室ID=3101, 教室名='C101(生産ロボット室)', 収容人数=20, 備考=''),
        教室(教室ID=3103, 教室名='C103(開発課題実習室)', 収容人数=20, 備考=''),
        教室(教室ID=3201, 教室名='C201', 収容人数=20, 備考=''),
        教室(教室ID=3202, 教室名='C202(応用課程計測制御応用実習室)', 収容人数=20, 備考=''),
        教室(教室ID=3203, 教室名='C203', 収容人数=20, 備考=''),
        教室(教室ID=3204, 教室名='C204', 収容人数=20, 備考=''),
        教室(教室ID=3231, 教室名='C231(資料室)', 収容人数=20, 備考=''),
        教室(教室ID=3301, 教室名='C301(マルチメディア実習室)', 収容人数=20, 備考=''),
        教室(教室ID=3302, 教室名='C302(システム開発実習室)', 収容人数=20, 備考=''),
        教室(教室ID=3303, 教室名='C303(システム開発実習室Ⅱ)', 収容人数=20, 備考=''),
        教室(教室ID=3304, 教室名='C304/305(応用課程生産管理ネットワーク応用実習室)', 収容人数=20, 備考=''),
        教室(教室ID=3306, 教室名='C306(共通実習室)', 収容人数=20, 備考=''),
        教室(教室ID=4102, 教室名='D102(回路基板加工室)', 収容人数=20, 備考=''),
        教室(教室ID=4201, 教室名='D201(開発課題実習室)', 収容人数=20, 備考=''),
        教室(教室ID=4202, 教室名='D202(電子情報技術科教官室)', 収容人数=20, 備考=''),
        教室(教室ID=4231, 教室名='D231(準備室)', 収容人数=20, 備考=''),
        教室(教室ID=4301, 教室名='D301', 収容人数=20, 備考=''),
        教室(教室ID=4302, 教室名='D302(PC実習室)', 収容人数=20, 備考='')
    ])

    # --- 5. 授業科目 ---
    db.session.add_all([
        授業科目(授業科目ID=301, 授業科目名='工業技術英語', 学科ID=3, 単位=2, 開講期='3,4'),
        授業科目(授業科目ID=302, 授業科目名='生産管理', 学科ID=3, 単位=2, 開講期='3'),
        授業科目(授業科目ID=303, 授業科目名='品質管理', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=304, 授業科目名='経営管理', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=305, 授業科目名='創造的開発技法', 学科ID=3, 単位=2, 開講期='3'),
        授業科目(授業科目ID=306, 授業科目名='工業法規', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=307, 授業科目名='職業能力開発体系論', 学科ID=3, 単位=2, 開講期='3'),

        授業科目(授業科目ID=308, 授業科目名='機械工学概論', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=309, 授業科目名='アナログ回路応用設計技術', 学科ID=3, 単位=2, 開講期='3'),
        授業科目(授業科目ID=310, 授業科目名='ディジタル回路応用設計技術', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=311, 授業科目名='複合電子回路応用設計技術', 学科ID=3, 単位=2, 開講期='3,4'),

        授業科目(授業科目ID=312, 授業科目名='ロボット工学', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=313, 授業科目名='通信プロトコル実装設計', 学科ID=3, 単位=2, 開講期='3'),
        授業科目(授業科目ID=314, 授業科目名='セキュアシステム設計', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=315, 授業科目名='組込みシステム設計', 学科ID=3, 単位=4, 開講期='3,4'),

        授業科目(授業科目ID=316, 授業科目名='安全衛生管理', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=317, 授業科目名='機械工作・組立実習', 学科ID=3, 単位=4, 開講期='3'),
        授業科目(授業科目ID=318, 授業科目名='実装設計製作実習', 学科ID=3, 単位=4, 開講期='4'),
        授業科目(授業科目ID=319, 授業科目名='EMC応用実習', 学科ID=3, 単位=4, 開講期='3,4'),

        授業科目(授業科目ID=320, 授業科目名='電子回路設計製作応用実習', 学科ID=3, 単位=4, 開講期='4'),
        授業科目(授業科目ID=321, 授業科目名='制御回路設計製作実習', 学科ID=3, 単位=4, 開講期='3'),
        授業科目(授業科目ID=322, 授業科目名='センシングシステム構築実習', 学科ID=3, 単位=4, 開講期='4'),
        授業科目(授業科目ID=323, 授業科目名='ロボット工学実習', 学科ID=3, 単位=2, 開講期='3,4'),

        授業科目(授業科目ID=324, 授業科目名='通信プロトコル実装実習', 学科ID=3, 単位=4, 開講期='4'),
        授業科目(授業科目ID=325, 授業科目名='セキュアシステム構築実習', 学科ID=3, 単位=4, 開講期='3'),
        授業科目(授業科目ID=326, 授業科目名='生産管理システム構築実習Ⅰ', 学科ID=3, 単位=2, 開講期='4'),
        授業科目(授業科目ID=327, 授業科目名='生産管理システム構築実習Ⅱ', 学科ID=3, 単位=2, 開講期='3,4'),

        授業科目(授業科目ID=328, 授業科目名='組込システム構築実習', 学科ID=3, 単位=4, 開講期='4'),
        授業科目(授業科目ID=329, 授業科目名='組込デバイス設計実習', 学科ID=3, 単位=4, 開講期='3'),
        授業科目(授業科目ID=330, 授業科目名='組込システム構築課題実習', 学科ID=3, 単位=10, 開講期='4'),
        授業科目(授業科目ID=331, 授業科目名='電子通信機器設計製作課題実習', 学科ID=3, 単位=10, 開講期='3,4'),

        授業科目(授業科目ID=332, 授業科目名='ロボット機器製作課題実習(電子情報)', 学科ID=3, 単位=10, 開講期='4'),
        授業科目(授業科目ID=333, 授業科目名='ロボット機器運用課題実習(電子情報)', 学科ID=3, 単位=10, 開講期='3'),
        授業科目(授業科目ID=380, 授業科目名='標準課題Ⅰ', 学科ID=3, 単位=10, 開講期='4'),
        授業科目(授業科目ID=381, 授業科目名='標準課題Ⅱ', 学科ID=3, 単位=10, 開講期='3,4'),

        授業科目(授業科目ID=334, 授業科目名='電子装置設計製作応用課題実習', 学科ID=3, 単位=54, 開講期='4'),
        授業科目(授業科目ID=335, 授業科目名='組込システム応用課題実習', 学科ID=3, 単位=54, 開講期='3'),
        授業科目(授業科目ID=336, 授業科目名='通信システム応用課題実習', 学科ID=3, 単位=54, 開講期='4'),
        授業科目(授業科目ID=337, 授業科目名='ロボットシステム応用課題実習', 学科ID=3, 単位=54, 開講期='3,4'),
        授業科目(授業科目ID=390, 授業科目名='開発課題', 学科ID=3, 単位=54, 開講期='3,4'),

    ])

    # --- 6. 学生マスタ (電子情報系 30名) - 新しいスキーマに対応 ---
    STUDENT_GRADE = 3 # 学年
    STUDENT_TERM = term # 期をパラメータ化
    
    # 新しいデータ構造: (学籍番号, 氏名, 学科ID, 学年, 期)
    students_data = [
        (222521301,'青井 渓一郎',3,1,STUDENT_TERM),(222521302,'赤坂 龍成',3,1,STUDENT_TERM),(222521303,'秋好 拓海',3,1,STUDENT_TERM),(222521304,'伊川 翔',3,1,STUDENT_TERM),
        (222521305,'岩切 亮太',3,1,STUDENT_TERM),(222521306,'上田 和輝',3,1,STUDENT_TERM),(222521307,'江本 龍之介',3,1,STUDENT_TERM),(222521308,'大久保 碧瀧',3,1,STUDENT_TERM),
        (222521309,'加來 涼雅',3,1,STUDENT_TERM),(222521310,'梶原 悠平',3,1,STUDENT_TERM),(222521311,'管野 友富紀',3,1,STUDENT_TERM),(222521312,'髙口 翔真',3,1,STUDENT_TERM),
        (222521313,'古城 静雅',3,1,STUDENT_TERM),(222521314,'小柳 知也',3,1,STUDENT_TERM),(222521315,'酒元 翼',3,1,STUDENT_TERM),(222521316,'座光寺 孝彦',3,1,STUDENT_TERM),
        (222521317,'佐野 勇太',3,1,STUDENT_TERM),(222521318,'清水 健心',3,1,STUDENT_TERM),(222521319,'新谷 雄飛',3,1,STUDENT_TERM),(222521320,'関原 響樹',3,1,STUDENT_TERM),
        (222521321,'髙橋 優人',3,1,STUDENT_TERM),(222521322,'武富 義樹',3,1,STUDENT_TERM),(222521323,'内藤 俊介',3,1,STUDENT_TERM),(222521324,'野田 千尋',3,1,STUDENT_TERM),
        (222521325,'野中 雄学',3,1,STUDENT_TERM),(222521326,'東 奈月',3,1,STUDENT_TERM),(222521327,'古田 雅也',3,1,STUDENT_TERM),(222521328,'牧野 倭大',3,1,STUDENT_TERM),
        (222521330,'宮岡 嘉熙',3,1,STUDENT_TERM),(222521329,'松隈 駿介',3,1,STUDENT_TERM)
    ]
    
    db.session.add_all([
        学生マスタ(学籍番号=s[0], 氏名=s[1], 学科ID=s[2], 学年=s[3], 期=s[4]) for s in students_data
    ])

    # --- 7. 時限設定 ---
    db.session.add_all([
        TimeTable(時限=1, 開始時刻=time(8, 50), 終了時刻=time(10, 30), 備考='1限目'),
        TimeTable(時限=2, 開始時刻=time(10, 35), 終了時刻=time(12, 15), 備考='2限目'),
        TimeTable(時限=3, 開始時刻=time(13, 0), 終了時刻=time(14, 40), 備考='3限目'),
        TimeTable(時限=4, 開始時刻=time(14, 45), 終了時刻=time(16, 25), 備考='4限目'),
        TimeTable(時限=5, 開始時刻=time(16, 40), 終了時刻=time(18, 20), 備考='5限目')
    ])
    
    # --- 8. 週時間割 (2025年度 電子情報系 302) ---
    # 月曜から金曜の1〜5時限を網羅
    timetable_data = [
        # 2025年度 3期 (電子情報系: 302)
        # 月曜日 (曜日ID=1)
        (2025, 3, 3, 1, 1, 327, 3301, '/中山'),
        (2025, 3, 3, 1, 2, 327, 3301, '/中山'),
        (2025, 3, 3, 1, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 1, 4, 380, 3301, 'C302/電子情報系'),
        # 火曜日 (曜日ID=2)
        (2025, 3, 3, 2, 1, 317, 3302, 'K302/機械系'),
        (2025, 3, 3, 2, 2, 317, 3302, 'K302/機械系'),
        (2025, 3, 3, 2, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 2, 4, 380, 3301, 'C302/電子情報系'),
        # 水曜日 (曜日ID=3)
        (2025, 3, 3, 3, 1, 329, 3301, '/岡田'),
        (2025, 3, 3, 3, 2, 329, 3301, '/岡田'),
        (2025, 3, 3, 3, 3, 308, 2301, '/上野'),
        # 木曜日 (曜日ID=4)
        (2025, 3, 3, 4, 1, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 2, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 4, 4, 380, 3301, 'C302/電子情報系'),
        # 金曜日 (曜日ID=5)
        (2025, 3, 3, 5, 1, 321, 3302, '/玉井'),
        (2025, 3, 3, 5, 2, 321, 3302, '/玉井'),
        (2025, 3, 3, 5, 3, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 3, 5, 4, 380, 3301, 'C302/電子情報系'),

        # 2025年度 4期 (電子情報系: 302)
        # 月曜日 (曜日ID=1)
        (2025, 3, 4, 1, 1, 381, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 1, 2, 381, 3302, 'C101/電子情報系'),
        # 火曜日 (曜日ID=2)
        (2025, 3, 4, 2, 1, 317, 3302, 'K302/機械系'),
        (2025, 3, 4, 2, 2, 317, 3302, 'K302/機械系'),
        (2025, 3, 4, 2, 3, 381, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 2, 4, 381, 3302, 'C101/電子情報系'),
        # 水曜日 (曜日ID=3)
        (2025, 3, 4, 3, 1, 329, 3301, '/岡田'),
        (2025, 3, 4, 3, 2, 329, 3301, '/岡田'),
        (2025, 3, 4, 3, 3, 308, 2301, '/上野'),
        # 木曜日 (曜日ID=4)
        (2025, 3, 4, 4, 1, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 2, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 3, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 4, 4, 331, 3302, 'C101/電子情報系'),
        # 金曜日 (曜日ID=5)
        (2025, 3, 4, 5, 1, 331, 3302, 'C101/電子情報系'),
        (2025, 3, 4, 5, 2, 331, 3302, 'C101/電子情報系')
    ]

    db.session.add_all([
        週時間割(年度=t[0], 学科ID=t[1], 期=t[2], 曜日=t[3], 時限=t[4], 科目ID=t[5], 教室ID=t[6], 備考=t[7]) for t in timetable_data
    ])

    db.session.commit()

# =========================================================================
# データベース初期化ロジック (変更: 自動欠席判定を追加)
# =========================================================================
with app.app_context():
    try:
        db.session.query(学生マスタ).first()
        app.logger.info("データベースのテーブルは既に存在します。初期化をスキップします。")
    except Exception:
        app.logger.info("データベースのテーブルが存在しません。テーブル作成と初期データ挿入を開始します。")
        db.create_all()
        term = int(os.environ.get('STUDENT_TERM', 3))
        insert_initial_data(term)
        app.logger.info("? データベースの初期化とマスタデータの上書きが完了しました。")

    # アプリ起動時に自動欠席判定を実行 (テスト用。本番ではスケジューラー推奨)
    auto_absent_check()


# =========================================================================
# エラーハンドリング
# =========================================================================

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal Server Error: {error}")
    return "内部サーバーエラー。管理者に連絡してください。", 500

# =========================================================================
# ルーティング (更新: 欠席確認機能を追加 + 新しいページルート追加)
# =========================================================================

@app.route('/')
def index_page():
    current_class_id = 3
    current_class_name = "電子情報系3年"
    today = date.today()

    try:
        # 学生リストに欠席カウントを追加 (今日の欠席数をカウント)
        students_with_info = db.session.query(
            学生マスタ.学籍番号,
            学生マスタ.氏名,
            学科.学科名,
            期マスタ.期名,
            func.count(入退室_出席記録.記録ID).label('absent_count')  # 欠席数をカウント
        ).join(学科, 学生マスタ.学科ID == 学科.学科ID) \
         .join(期マスタ, 学生マスタ.期 == 期マスタ.期ID) \
         .outerjoin(入退室_出席記録, and_(
             入退室_出席記録.学生番号 == 学生マスタ.学籍番号,
             入退室_出席記録.記録日 == today,
             入退室_出席記録.ステータス == '欠席'
         )) \
         .filter(学生マスタ.学科ID == current_class_id) \
         .group_by(学生マスタ.学籍番号, 学生マスタ.氏名, 学科.学科名, 期マスタ.期名) \
         .order_by(学生マスタ.学籍番号).all()

        return render_template('index.html', 
                               students=students_with_info, 
                               current_class=current_class_name, 
                               current_class_id=current_class_id)
        
    except Exception as e:
        app.logger.error(f"データベースクエリ実行中にエラーが発生しました: {e}")
        return "データベースクエリ実行中にエラーが発生しました。", 500

@app.route('/absent-check')
def absent_check_page():
    """欠席確認ページ: 今日の欠席学生リストを表示"""
    today = date.today()

    try:
        # 欠席学生の詳細を取得
        absent_students = db.session.query(
            学生マスタ.学籍番号,
            学生マスタ.氏名,
            学科.学科名,
            入退室_出席記録.授業科目ID,
            入退室_出席記録.備考
        ).join(入退室_出席記録, 入退室_出席記録.学生番号 == 学生マスタ.学籍番号) \
         .join(学科, 学生マスタ.学科ID == 学科.学科ID) \
         .filter(and_(
             入退室_出席記録.記録日 == today,
             入退室_出席記録.ステータス == '欠席'
         )) \
         .order_by(学生マスタ.学籍番号).all()

        return render_template('absent_check.html', absent_students=absent_students)
        
    except Exception as e:
        app.logger.error(f"欠席確認クエリ実行中にエラーが発生しました: {e}")
        return "欠席確認中にエラーが発生しました。", 500

@app.route('/trigger-absent-check', methods=['POST'])
def trigger_absent_check():
    """手動で自動欠席判定を実行"""
    try:
        auto_absent_check()
        return jsonify({"message": "自動欠席判定を実行しました。"}), 200
    except Exception as e:
        app.logger.error(f"手動欠席判定実行中にエラー: {e}")
        return jsonify({"error": "実行中にエラーが発生しました。"}), 500

# --- ここに新しいルートを追加 ---
@app.route('/student_management')
def student_management_page():
    """学生別出席状況ページ: 学生ごとの出席記録を表示（時間割ベース）"""
    try:
        # GETパラメータを取得
        selected_student_no = request.args.get('student_no', type=int)
        selected_term_id = request.args.get('term_id', type=int)

        # 全学生と期を取得
        students = db.session.query(学生マスタ).all()
        terms = db.session.query(期マスタ).filter(期マスタ.期ID.between(1, 4)).all()

        # 曜日と時限の順序
        曜日順序 = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日']
        時限順序 = [1, 2, 3, 4, 5]

        # 時限詳細を取得
        timetable_details = db.session.query(TimeTable).order_by(TimeTable.時限).all()

        lesson_matrix = {}
        if selected_student_no and selected_term_id:
            # 選択された学生の学科を取得
            student = db.session.query(学生マスタ).filter(学生マスタ.学籍番号 == selected_student_no).first()
            if not student:
                return render_template('student_management.html', students=students, terms=terms, 曜日順序=曜日順序, 時限順序=時限順序, selected_student_no=selected_student_no, selected_term_id=selected_term_id, lesson_matrix={}, data={'timetable_details': timetable_details})

            # 該当する時間割を取得（年度固定: 2025）
            schedules = db.session.query(週時間割).filter(
                and_(
                    週時間割.年度 == 2025,
                    週時間割.学科ID == student.学科ID,
                    週時間割.期 == selected_term_id
                )
            ).all()

            # 曜日IDを曜日名にマッピング
            weekday_map = {1: '月曜日', 2: '火曜日', 3: '水曜日', 4: '木曜日', 5: '金曜日'}

            # lesson_matrixを構築
            for schedule in schedules:
                weekday_name = weekday_map.get(schedule.曜日)
                if weekday_name not in lesson_matrix:
                    lesson_matrix[weekday_name] = {}
                
                # 出席記録を取得（該当授業の記録）
                records = db.session.query(入退室_出席記録).filter(
                    and_(
                        入退室_出席記録.学生番号 == selected_student_no,
                        入退室_出席記録.授業科目ID == schedule.科目ID
                    )
                ).order_by(入退室_出席記録.記録日).all()

                # 記録を日付形式に変換
                dates_recorded = [{'記録日': record.記録日, 'ステータス': record.ステータス} for record in records]

                lesson_matrix[weekday_name][schedule.時限] = {
                    'lesson_info': {
                        '授業科目名': schedule.科目.授業科目名 if schedule.科目 else '科目不明',
                        '教室名': schedule.教室.教室名 if schedule.教室 else '教室不明'
                    },
                    'dates_recorded': dates_recorded
                }

        return render_template('student_management.html', 
                               students=students, 
                               terms=terms, 
                               曜日順序=曜日順序, 
                               時限順序=時限順序, 
                               selected_student_no=selected_student_no, 
                               selected_term_id=selected_term_id, 
                               lesson_matrix=lesson_matrix, 
                               data={'timetable_details': timetable_details})
    except Exception as e:
        app.logger.error(f"学生別出席状況クエリ実行中にエラーが発生しました: {e}")
        return "学生別出席状況の取得中にエラーが発生しました。", 500

@app.route('/logs')
def logs_page():
    """全ログページ: 入退室記録の全ログを表示"""
    try:
        # 例: 全入退室記録を取得
        logs = db.session.query(入退室_出席記録).order_by(入退室_出席記録.記録ID).all()
        return render_template('logs.html', logs=logs)
    except Exception as e:
        app.logger.error(f"全ログクエリ実行中にエラーが発生しました: {e}")
        return "全ログの取得中にエラーが発生しました。", 500

@app.route('/timetable')
def timetable_page():
    """時間割ページ: 週時間割を表示"""
    try:
        # 例: 2025年度の時間割を取得
        timetables = db.session.query(週時間割).filter(週時間割.年度 == 2025).all()
        return render_template('timetable.html', timetables=timetables)
    except Exception as e:
        app.logger.error(f"時間割クエリ実行中にエラーが発生しました: {e}")
        return "時間割の取得中にエラーが発生しました。", 500

@app.route('/time_master')
def time_master_page():
    """時刻マスタページ: 時限設定を表示"""
    try:
        # 例: 全時限を取得
        times = db.session.query(TimeTable).all()
        return render_template('time_master.html', times=times)
    except Exception as e:
        app.logger.error(f"時刻マスタクエリ実行中にエラーが発生しました: {e}")
        return "時刻マスタの取得中にエラーが発生しました。", 500


# =========================================================================
# データベースの初期化とWebアプリの実行
# =========================================================================

if __name__ == "__main__":
    # ローカル実行用: デバッグモードを環境変数で制御
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
else:
    # Gunicorn/Renderで起動した場合: 初期化は既に完了しているので、何もしない
    app.logger.info("Render/Gunicorn環境で起動しました。")



