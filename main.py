# main.py (Flask-SQLAlchemy ORM 統合版)

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
    曜日ID = db.Column(db.SmallInteger, primary_key=True) # TINYINT -> SmallInteger
    曜日名 = db.Column(db.String(50), nullable=False)
    備考 = db.Column(db.Text)

# 2. 期マスタ
class 期マスタ(db.Model):
    __tablename__ = '期マスタ'
    期ID = db.Column(db.SmallInteger, primary_key=True) # TINYINT -> SmallInteger
    期名 = db.Column(db.String(50), nullable=False)
    備考 = db.Column(db.Text)

# 3. 学科
class 学科(db.Model):
    __tablename__ = '学科'
    学科ID = db.Column(db.SmallInteger, primary_key=True)
    学科名 = db.Column(db.String(100))
    備考 = db.Column(db.Text)

# 4. 教室
class 教室(db.Model):
    __tablename__ = '教室'
    教室ID = db.Column(db.SmallInteger, primary_key=True)
    教室名 = db.Column(db.String(100), nullable=False)
    収容人数 = db.Column(db.SmallInteger, nullable=False)
    備考 = db.Column(db.Text)

# 5. 授業科目
class 授業科目(db.Model):
    __tablename__ = '授業科目'
    授業科目ID = db.Column(db.SmallInteger, primary_key=True)
    授業科目名 = db.Column(db.String(255), nullable=False)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), nullable=False)
    単位 = db.Column(db.SmallInteger, nullable=False) # TINYINT -> SmallInteger
    学科フラグ = db.Column(db.SmallInteger, nullable=False) # TINYINT -> SmallInteger
    備考 = db.Column(db.Text)

# 6. TimeTable (時刻管理)
class TimeTable(db.Model):
    __tablename__ = 'TimeTable'
    時限 = db.Column(db.SmallInteger, primary_key=True) # TINYINT -> SmallInteger
    開始時刻 = db.Column(db.Time, nullable=False)
    終了時刻 = db.Column(db.Time, nullable=False)
    備考 = db.Column(db.Text)

# 7. 学生マスタ
class 学生マスタ(db.Model):
    __tablename__ = '学生マスタ'
    # SQLiteではINTEGERがPRIMARY KEYだが、PostgreSQL互換を意識してSmallInteger/Stringを選択
    学籍番号 = db.Column(db.String(20), primary_key=True) 
    氏名 = db.Column(db.String(100))
    学年 = db.Column(db.SmallInteger)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'))
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), nullable=False)

# 8. 週時間割 (複合主キー)
class 週時間割(db.Model):
    __tablename__ = '週時間割'
    年度 = db.Column(db.Integer, primary_key=True)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), primary_key=True)
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), primary_key=True)
    曜日 = db.Column(db.SmallInteger, db.ForeignKey('曜日マスタ.曜日ID'), primary_key=True)
    時限 = db.Column(db.SmallInteger, db.ForeignKey('TimeTable.時限'), primary_key=True)
    科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'))
    教室ID = db.Column(db.SmallInteger, db.ForeignKey('教室.教室ID'))
    備考 = db.Column(db.Text)

# 9. 授業計画
class 授業計画(db.Model):
    __tablename__ = '授業計画'
    日付 = db.Column(db.Date, primary_key=True)
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'))
    授業曜日 = db.Column(db.SmallInteger, db.ForeignKey('曜日マスタ.曜日ID'))
    備考 = db.Column(db.Text)

# 10. 入退室_出席記録
class 入退室_出席記録(db.Model):
    __tablename__ = '入退室_出席記録'
    # PostgreSQLでは自動でSERIALとして扱われる
    記録ID = db.Column(db.Integer, primary_key=True, autoincrement=True) 
    日時 = db.Column(db.DateTime, nullable=False)
    学籍番号 = db.Column(db.String(20), db.ForeignKey('学生マスタ.学籍番号'), nullable=False)
    教室ID = db.Column(db.SmallInteger, db.ForeignKey('教室.教室ID'), nullable=False)
    入退室状況 = db.Column(db.String(10), nullable=False)
    出席状況 = db.Column(db.String(10))

# =========================================================================
# ヘルパー関数 (ORM対応)
# =========================================================================

def get_weekday_id(date_str):
    """日付文字列から曜日ID (0:日, 1:月, ..., 6:土) を取得"""
    try:
        # datetime.weekday() は 0=月曜, 6=日曜 を返すため、+1して%7で調整
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return (dt.weekday() + 1) % 7 
    except ValueError:
        return None

def get_current_lesson_info(student_no, current_date_str, current_time_str):
    """
    現在の日付と時刻から、その学生が受講しているはずの授業情報を取得する。(ORM対応)
    """
    try:
        current_time_obj = datetime.strptime(current_time_str[:5], '%H:%M').time()
    except ValueError:
        return None

    weekday_id = get_weekday_id(current_date_str)
    if weekday_id is None:
        return None

    # 1. 時刻マスタから、現在の時刻がどの時限に該当するかを検索
    time_result = TimeTable.query.filter(
        TimeTable.開始時刻 <= current_time_obj, 
        TimeTable.終了時刻 >= current_time_obj
    ).first()

    if not time_result:
        return None # 現在、授業時間外

    current_period = time_result.時限
    
    # 2. 学生情報を取得
    student = 学生マスタ.query.filter_by(学籍番号=student_no).first()
    if not student:
        return None

    # 3. 週時間割から、その学生が現在受講している授業を取得
    lesson_info = db.session.query(週時間割, 授業科目.授業科目名, TimeTable.開始時刻, TimeTable.終了時刻) \
        .join(授業科目, 週時間割.科目ID == 授業科目.授業科目ID) \
        .join(TimeTable, 週時間割.時限 == TimeTable.時限) \
        .filter(
            週時間割.年度 == 2025, # 固定
            週時間割.学科ID == student.学科ID,
            週時間割.期 == student.期,
            週時間割.曜日 == weekday_id,
            週時間割.時限 == current_period
        ).first()
    
    if lesson_info:
        lesson_record = lesson_info[0]
        return {
            '科目ID': lesson_record.科目ID,
            '教室ID': lesson_record.教室ID,
            '授業科目名': lesson_info[1],
            # TimeTableからの時刻を使用
            '開始時刻': time_result.開始時刻.strftime('%H:%M'), 
            '終了時刻': time_result.終了時刻.strftime('%H:%M'), 
            '時限': current_period
        }
    
    return None

def check_existing_record(student_no, record_datetime, lesson_start_time_str, lesson_end_time_str, check_status):
    """
    指定された日時における既存の入退室記録をチェックする。(ORM対応)
    """
    if lesson_start_time_str is None or lesson_end_time_str is None:
        return False
    
    record_date_str = record_datetime.split(' ')[0] 
    
    try:
        # 授業開始と終了の datetime オブジェクトを生成 (秒を:00として統一)
        lesson_start_dt = datetime.strptime(f"{record_date_str} {lesson_start_time_str}:00", '%Y-%m-%d %H:%M:%S')
        lesson_end_dt = datetime.strptime(f"{record_date_str} {lesson_end_time_str}:00", '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        print(f"時刻解析エラー (check_existing_record): {e}")
        return False

    # データベース検索の期間を指定
    result = 入退室_出席記録.query.filter(
        入退室_出席記録.学籍番号 == student_no, 
        入退室_出席記録.入退室状況 == check_status, 
        入退室_出席記録.日時.between(lesson_start_dt, lesson_end_dt)
    ).first()
    
    return result is not None

def run_daily_attendance_check_by_lesson():
    """
    前日以前の授業を対象に、入退室記録がない学生を「欠席」として記録する。(ORM対応)
    """
    print("?? 日次欠席判定処理を実行します...")
    
    # 1. 当日または未来の日付の「欠席」記録を削除
    today_str = date.today().strftime('%Y-%m-%d')
    delete_count = 入退室_出席記録.query.filter(
        入退室_出席記録.入退室状況 == '欠席', 
        func.strftime('%Y-%m-%d', 入退室_出席記録.日時) >= today_str
    ).delete(synchronize_session=False) # ORMのDELETE
    db.session.commit()
    print(f"   将来の日付の欠席記録を {delete_count} 件削除しました。")


    # 2. 過去の全ての授業時間割を取得 (簡単化のため、固定の年度/学科/期を使用)
    timetables = db.session.query(週時間割, TimeTable.開始時刻, TimeTable.終了時刻) \
        .join(TimeTable, 週時間割.時限 == TimeTable.時限) \
        .filter(週時間割.年度 == 2025, 週時間割.学科ID == 3, 週時間割.期 == 3) \
        .all()

    new_absent_count = 0
    
    # 3. 過去の日付（例：30日前まで）をチェック
    start_date = date.today() - timedelta(days=30) 
    end_date = date.today() - timedelta(days=1)
    
    current_date = start_date
    while current_date <= end_date:
        lesson_date_str = current_date.strftime('%Y-%m-%d')
        current_date_weekday = get_weekday_id(lesson_date_str)
        
        for lesson_tuple in timetables:
            lesson = lesson_tuple[0] # 週時間割レコード
            lesson_start_time = lesson_tuple[1].strftime('%H:%M') # TimeTable.開始時刻
            lesson_end_time = lesson_tuple[2].strftime('%H:%M') # TimeTable.終了時刻
            
            if current_date_weekday == lesson.曜日:
                
                # その日付・授業に対する学生リストを取得
                students = 学生マスタ.query.filter_by(学科ID=lesson.学科ID, 期=lesson.期).all()

                for student in students:
                    student_no = student.学籍番号
                    
                    # 授業開始日時/終了日時 (秒を:00として統一)
                    lesson_start_datetime = datetime.strptime(f"{lesson_date_str} {lesson_start_time}:00", '%Y-%m-%d %H:%M:%S')
                    lesson_end_datetime = datetime.strptime(f"{lesson_date_str} {lesson_end_time}:00", '%Y-%m-%d %H:%M:%S')
                    
                    # 4. 期間内の入室/退室記録をチェック
                    record_exists = 入退室_出席記録.query.filter(
                        入退室_出席記録.学籍番号 == student_no, 
                        入退室_出席記録.日時.between(lesson_start_datetime, lesson_end_datetime),
                        入退室_出席記録.入退室状況.in_(['入室', '退室'])
                    ).first()

                    # 5. 記録がない場合は「欠席」として挿入
                    if not record_exists:
                        absent_record_datetime = lesson_start_datetime 
                        
                        # 既に「欠席」記録がないかチェック（重複挿入防止）
                        absent_record_check = 入退室_出席記録.query.filter(
                            入退室_出席記録.学籍番号 == student_no, 
                            入退室_出席記録.入退室状況 == '欠席', 
                            入退室_出席記録.日時 == absent_record_datetime
                        ).first()
                        
                        if not absent_record_check:
                            new_record = 入退室_出席記録(
                                学籍番号=student_no, 
                                入退室状況='欠席', 
                                出席状況='欠席', 
                                日時=absent_record_datetime, 
                                教室ID=lesson.教室ID
                            )
                            db.session.add(new_record)
                            new_absent_count += 1
                            
        current_date += timedelta(days=1)
    
    db.session.commit()
    if new_absent_count > 0:
        print(f"   新規欠席記録を {new_absent_count} 件挿入しました。")
    print("? 日次欠席判定処理を完了しました。")

# =========================================================================
# データベース初期化 CLI コマンド
# =========================================================================

# db_setup.py のデータをOR Mオブジェクトとして定義
def get_initial_data():
    # db_setup.pyのデータ
    
    # 1. 曜日マスタ
    曜日マスタ_data = [
        (0,'授業日'), (1,'月曜日'), (2,'火曜日'), (3,'水曜日'), (4,'木曜日'), 
        (5,'金曜日'), (6,'土曜日'), (7,'日曜日'), (8,'祝祭日'), (9,'休日')
    ]
    # 2. 期マスタ
    期マスタ_data = [
        (1,'Ⅰ'), (2,'Ⅱ'), (3,'Ⅲ'), (4,'Ⅳ'), (5,'Ⅴ'), (6,'Ⅵ'), (7,'Ⅶ'), (8,'Ⅷ'), 
        (9,'前期(Ⅱ期)集中'), (10,'後期(Ⅲ期)集中')
    ]
    # 3. TimeTable
    TimeTable_data = [
        (1,'8:50','10:30','1限目'), (2,'10:35','12:15','2限目'), (3,'13:00','14:40','3限目'), 
        (4,'14:45','16:25','4限目'), (5,'16:40','18:20','5限目')
    ]
    # 4. 学科
    学科_data = [
        (1, '生産機械システム技術科'), (2, '生産電気システム技術科'), (3, '生産電子情報システム技術科')
    ]
    # 5. 教室
    教室_data = [
        (1205,'A205',20), (2102,'B102/103',20), (2201,'B201',20), (2202,'B202',20), 
        (2204,'B204',20), (2205,'B205',20), (2301,'B301',20), (2302,'B302',20), 
        (2303,'B303',20), (2304,'B304',20), (2305,'B305',20), (2306,'B306(視聴覚室)',20), 
        (3101,'C101(生産ロボット室)',20), (3103,'C103(開発課題実習室)',20), (3201,'C201',20), 
        (3202,'C202(応用課程計測制御応用実習室)',20), (3203,'C203',20), (3204,'C204',20), 
        (3231,'C231(資料室)',20), (3301,'C301(マルチメディア実習室)',20), (3302,'C302(システム開発実習室)',20), 
        (3303,'C303(システム開発実習室Ⅱ)',20), (3304,'C304/305(応用課程生産管理ネットワーク応用実習室)',20), 
        (3306,'C306(共通実習室)',20), (4102,'D102(回路基板加工室)',20), (4201,'D201(開発課題実習室)',20), 
        (4202,'D202(電子情報技術科教官室)',20), (4231,'D231(準備室)',20), (4301,'D301',20), 
        (4302,'D302(PC実習室)',20)
    ]
    # 6. 授業科目
    授業科目_data = [
        (301,'工業技術英語',3,2,0), (302,'生産管理',3,2,0), (303,'品質管理',3,2,0), (304,'経営管理',3,2,0), 
        (305,'創造的開発技法',3,2,0), (306,'工業法規',3,2,0), (307,'職業能力開発体系論',3,2,0), (308,'機械工学概論',3,2,0), 
        (309,'アナログ回路応用設計技術',3,2,0), (310,'ディジタル回路応用設計技術',3,2,0), (311,'複合電子回路応用設計技術',3,2,0), 
        (312,'ロボット工学',3,2,0), (313,'通信プロトコル実装設計',3,2,0), (314,'セキュアシステム設計',3,2,0), (315,'組込システム設計',3,4,0), 
        (316,'安全衛生管理',3,2,0), (317,'機械工作・組立実習',3,4,0), (318,'実装設計製作実習',3,4,0), (319,'EMC応用実習',3,4,0), 
        (320,'電子回路設計製作応用実習',3,4,0), (321,'制御回路設計製作実習',3,4,0), (322,'センシングシステム構築実習',3,4,0), 
        (323,'ロボット工学実習',3,2,0), (324,'通信プロトコル実装実習',3,4,0), (325,'セキュアシステム構築実習',3,4,0), 
        (326,'生産管理システム構築実習Ⅰ',3,2,0), (327,'生産管理システム構築実習Ⅱ',3,2,0), (328,'組込システム構築実習',3,4,0), 
        (329,'組込デバイス設計実習',3,4,0), (330,'組込システム構築課題実習',3,10,0), (331,'電子通信機器設計制作課題実習',3,10,0), 
        (332,'ロボット機器制作課題実習(電子情報)',3,10,0), (333,'ロボット機器運用課題実習(電子情報)',3,10,0), (380,'標準課題Ⅰ',3,10,0), 
        (381,'標準課題Ⅱ',3,10,0), (334,'電子装置設計製作応用課題実習',3,54,0), (335,'組込システム応用課題実習',3,54,0), 
        (336,'通信システム応用課題実習',3,54,0), (337,'ロボットシステム応用課題実習',3,54,0), (390,'開発課題',3,54,0)
    ]
    # 7. 学生マスタ
    学生マスタ_data = [
        ('222521301','青井 渓一郎',1,3,3), ('222521302','赤坂 龍成',1,3,3), ('222521303','秋好 拓海',1,3,3), 
        ('222521304','伊川 翔',1,3,3), ('222521305','岩切 亮太',1,3,3), ('222521306','上田 和輝',1,3,3), 
        ('222521307','江本 龍之介',1,3,3), ('222521308','大久保 碧瀧',1,3,3), ('222521309','加來 涼雅',1,3,3), 
        ('222521310','梶原 悠平',1,3,3), ('222521311','管野 友富紀',1,3,3), ('222521312','髙口 翔真',1,3,3), 
        ('222521313','古城 静雅',1,3,3), ('222521314','小柳 知也',1,3,3), ('222521315','酒元 翼',1,3,3), 
        ('222521316','座光寺 孝彦',1,3,3), ('222521317','佐野 勇太',1,3,3), ('222521318','清水 健心',1,3,3), 
        ('222521319','新谷 雄飛',1,3,3), ('222521320','関原 響樹',1,3,3), ('222521321','髙橋 優人',1,3,3), 
        ('222521322','武富 義樹',1,3,3), ('222521323','内藤 俊介',1,3,3), ('222521324','野田 千尋',1,3,3), 
        ('222521325','野中 雄学',1,3,3), ('222521326','東 奈月',1,3,3), ('222521327','古田 雅也',1,3,3), 
        ('222521328','牧野 倭大',1,3,3), ('222521330','宮岡 嘉熙',1,3,3), ('222521329','松隈 駿介',1,3,3)
    ]
    # 8. 週時間割 (膨大なので一部抜粋)
    週時間割_data = [
        (2025, 3, 1, 1, 1, 325, 3301, 'C304/寺内'), (2025, 3, 1, 1, 2, 325, 3301, 'C304/寺内'), 
        (2025, 3, 1, 1, 3, 301, 2201, '/ワット'), (2025, 3, 1, 1, 4, 313, 3301, 'C302/中山'),
        (2025, 3, 1, 2, 1, 314, 3301, 'C304/寺内/'), (2025, 3, 1, 2, 2, 309, 3301, 'C304/諏訪原'), 
        # ... (db_setup.pyの全週時間割データをここに展開)
        # 抜粋されたデータのみを今回は使用
        (2025, 3, 3, 5, 4, 380, 3301, 'C302/電子情報系'),
        (2025, 3, 4, 1, 1, 381, 3302, 'C101/電子情報系')
    ]
    # 9. 授業計画 (webpagegamen14.pyで修正済みのデータ形式)
    授業計画_data = [
        ('2025-04-08', 1, 2),('2025-04-09', 1, 3),('2025-04-10', 1, 4),('2025-04-11', 1, 5),
        ('2025-04-14', 1, 1),('2025-04-15', 1, 2),('2025-04-16', 1, 3),('2025-04-17', 1, 4),
        ('2025-04-18', 1, 5),('2025-04-21', 1, 1),('2025-04-22', 1, 2),('2025-04-23', 1, 3),
        ('2025-04-24', 1, 4),('2025-04-25', 1, 5),('2025-04-28', 1, 1),('2025-05-07', 1, 3),
        ('2025-05-08', 1, 4),('2025-05-09', 1, 5),('2025-05-12', 1, 1),('2025-05-13', 1, 2),
        ('2025-05-15', 1, 4),('2025-05-16', 1, 5),('2025-05-19', 1, 1),('2025-05-20', 1, 2),
        ('2025-05-21', 1, 3),('2025-05-22', 1, 4),('2025-05-23', 1, 5),('2025-05-26', 1, 1),
        ('2025-05-27', 1, 2),('2025-05-28', 1, 3),('2025-05-29', 1, 4),('2025-05-30', 1, 5),
        ('2025-06-02', 1, 1),('2025-06-03', 1, 2),('2025-06-04', 1, 3),('2025-06-05', 1, 4),
        ('2025-06-06', 1, 5),('2025-06-09', 1, 1),('2025-06-10', 1, 2),('2025-06-11', 1, 3),
        ('2025-06-12', 1, 4),('2025-06-13', 1, 5),('2025-06-16', 1, 1),('2025-06-17', 1, 2),
        ('2025-06-18', 1, 3),('2025-06-19', 2, 4),('2025-06-20', 2, 5),('2025-06-23', 2, 1),
        ('2025-06-24', 2, 2),('2025-06-25', 2, 3),('2025-06-26', 2, 4),('2025-06-27', 2, 5),
        ('2025-06-30', 2, 1),('2025-07-01', 2, 2),('2025-07-02', 2, 3),('2025-07-03', 2, 4),
        ('2025-07-04', 2, 5),('2025-07-07', 2, 1),('2025-07-08', 2, 2),('2025-07-09', 2, 3),
        ('2025-07-10', 2, 4),('2025-07-11', 2, 5),('2025-07-14', 2, 1),('2025-07-15', 9, 0),
        ('2025-07-16', 9, 0),('2025-07-17', 9, 0),('2025-07-18', 9, 0),('2025-07-21', 9, 0),
        ('2025-07-22', 9, 0),('2025-07-23', 9, 0),('2025-07-24', 9, 0),('2025-07-25', 9, 0),
        ('2025-08-20', 2, 3),('2025-08-21', 2, 4),('2025-08-22', 2, 5),('2025-08-23', 2, 2),
        ('2025-08-25', 2, 1),('2025-08-26', 2, 2),('2025-08-27', 2, 3),('2025-08-28', 2, 4),
        ('2025-08-29', 2, 5),('2025-09-01', 2, 1),('2025-09-02', 2, 2),('2025-09-03', 2, 3),
        ('2025-09-04', 2, 4),('2025-09-05', 2, 5),('2025-09-08', 2, 1),('2025-09-09', 2, 2),
        ('2025-09-10', 2, 3),('2025-09-11', 2, 4),('2025-09-12', 2, 5),('2025-09-16', 2, 2),
        ('2025-09-17', 2, 3),('2025-09-18', 2, 1),('2025-09-19', 2, 5),('2025-09-22', 2, 1),
        ('2025-09-24', 2, 3),('2025-09-25', 2, 4),('2025-09-26', 2, 2),('2025-09-29', 2, 0),
        ('2025-09-30', 10, 0),('2025-10-01', 10, 0),('2025-10-02', 10, 0),('2025-10-03', 10, 0),
        ('2025-10-06', 10, 0),('2025-10-07', 10, 0),('2025-10-08', 10, 0),('2025-10-09', 10, 0),
        ('2025-10-10', 10, 0),('2025-10-14', 3, 2),('2025-10-15', 3, 3),('2025-10-16', 3, 4),
        ('2025-10-17', 3, 5),('2025-10-20', 3, 1),('2025-10-21', 3, 2),('2025-10-22', 3, 3),
        ('2025-10-23', 3, 4),('2025-10-24', 3, 5),('2025-10-27', 3, 1),('2025-10-28', 3, 2),
        ('2025-10-29', 3, 3),('2025-10-30', 3, 4),('2025-10-31', 3, 5),('2025-11-04', 3, 2),
        ('2025-11-05', 3, 3),('2025-11-06', 3, 1),('2025-11-07', 3, 5),('2025-11-10', 3, 1),
        ('2025-11-11', 3, 2),('2025-11-12', 3, 3),('2025-11-13', 3, 4),('2025-11-14', 3, 5),
        ('2025-11-17', 3, 1),('2025-11-18', 3, 2),('2025-11-19', 3, 3),('2025-11-20', 3, 4),
        ('2025-11-21', 3, 5),('2025-11-25', 3, 1),('2025-11-26', 3, 3),('2025-11-27', 3, 4),
        ('2025-11-28', 3, 5),('2025-12-01', 3, 1),('2025-12-02', 3, 2),('2025-12-03', 3, 3),
        ('2025-12-04', 3, 4),('2025-12-08', 3, 1),('2025-12-09', 3, 2),('2025-12-10', 3, 3),
        ('2025-12-11', 3, 4),('2025-12-12', 3, 5),('2025-12-15', 3, 1),('2025-12-16', 3, 2),
        ('2025-12-17', 4, 3),('2025-12-18', 3, 4),('2025-12-19', 3, 5),('2025-12-22', 4, 1),
        ('2025-12-23', 4, 2),('2025-12-24', 4, 3),('2025-12-25', 4, 4),('2025-12-26', 4, 5),
        ('2026-01-13', 4, 1),('2026-01-14', 4, 3),('2026-01-15', 4, 4),('2026-01-16', 4, 5),
        ('2026-01-19', 4, 1),('2026-01-20', 4, 2),('2026-01-21', 4, 3),('2026-01-22', 4, 4),
        ('2026-01-23', 4, 5),('2026-01-26', 4, 1),('2026-01-27', 4, 2),('2026-01-28', 4, 3),
        ('2026-01-29', 4, 4),('2026-01-30', 4, 5),('2026-02-02', 4, 1),('2026-02-03', 4, 2),
        ('2026-02-04', 4, 3),('2026-02-06', 4, 5),('2026-02-09', 4, 1),('2026-02-10', 4, 2),
        ('2026-02-12', 4, 4),('2026-02-13', 4, 5),('2026-02-16', 4, 1),('2026-02-17', 4, 2),
        ('2026-02-18', 4, 3),('2026-02-19', 4, 4),('2026-02-20', 4, 5),('2026-02-21', 4, 4),
        ('2026-02-24', 4, 2),('2026-02-25', 4, 3),('2026-02-26', 4, 4),('2026-02-27', 4, 5),
        ('2026-03-02', 4, 1),('2026-03-03', 4, 2),('2026-03-04', 4, 3),('2026-03-05', 4, 4),
        ('2026-03-06', 4, 5),('2026-03-09', 4, 1),('2026-03-10', 4, 2),('2026-03-11', 4, 0)
    ]
    
    return {
        '曜日マスタ': 曜日マスタ_data,
        '期マスタ': 期マスタ_data,
        'TimeTable': TimeTable_data,
        '学科': 学科_data,
        '教室': 教室_data,
        '授業科目': 授業科目_data,
        '学生マスタ': 学生マスタ_data,
        '週時間割': 週時間割_data,
        '授業計画': 授業計画_data
    }

@cli.with_appcontext
@app.cli.command('init-db')
@click.argument('seed', default=False, type=click.BOOL)
def init_db_command(seed):
    """データベーステーブルを再作成し、マスタデータを挿入する (ORM対応)"""
    try:
        # 1. 既存のデータをすべて削除し、テーブルを作成
        # PostgreSQLの場合、DROP/CREATEをしないと型変更などに柔軟に対応できないため、
        # development環境でのみ使用
        db.drop_all()
        db.create_all()
        print("? データベーススキーマを作成しました。")
        
        # 2. マスタデータの挿入
        data = get_initial_data()
        
        # 曜日マスタ
        for id, name in data['曜日マスタ']:
            db.session.add(曜日マスタ(曜日ID=id, 曜日名=name))
        
        # 期マスタ
        for id, name in data['期マスタ']:
            db.session.add(期マスタ(期ID=id, 期名=name))
            
        # TimeTable
        for period, start, end, note in data['TimeTable']:
            start_time = datetime.strptime(start, '%H:%M').time()
            end_time = datetime.strptime(end, '%H:%M').time()
            db.session.add(TimeTable(時限=period, 開始時刻=start_time, 終了時刻=end_time, 備考=note))

        # 学科
        for id, name in data['学科']:
            db.session.add(学科(学科ID=id, 学科名=name))

        # 教室
        for id, name, capacity in data['教室']:
            db.session.add(教室(教室ID=id, 教室名=name, 収容人数=capacity))

        # 授業科目
        for id, name, dept_id, units, flag in data['授業科目']:
            db.session.add(授業科目(授業科目ID=id, 授業科目名=name, 学科ID=dept_id, 単位=units, 学科フラグ=flag))
        
        # 学生マスタ
        for student_id, name, grade, dept_id, term in data['学生マスタ']:
            db.session.add(学生マスタ(学籍番号=student_id, 氏名=name, 学年=grade, 学科ID=dept_id, 期=term))
            
        # 週時間割
        for year, dept_id, term, weekday, period, subject_id, room_id, note in data['週時間割']:
            db.session.add(週時間割(年度=year, 学科ID=dept_id, 期=term, 曜日=weekday, 時限=period, 科目ID=subject_id, 教室ID=room_id, 備考=note))

        # 授業計画
        for date_str, term, weekday in data['授業計画']:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            db.session.add(授業計画(日付=date_obj, 期=term, 授業曜日=weekday))
        
        db.session.commit()
        # print("? マスタデータと初期データを挿入しました。")
        click.echo('データベースの初期化とマスタデータの挿入が完了しました。')
    except Exception as e:
        print(f"? データベース初期化エラー: {e}")
        db.session.rollback()

app.cli.add_command(init_db_command)

# =========================================================================
# APIからの入退室記録 ルート (ORM対応)
# =========================================================================
@app.route('/api/record', methods=['POST'])
def api_record():
    """機器からの入退室記録APIエンドポイント"""
    data = request.get_json()
    student_no = data.get('student_no')
    status = data.get('status') # '入室' or '退室'
    
    now = datetime.now()
    record_date = now.strftime('%Y-%m-%d')
    record_time = now.strftime('%H:%M:%S')
    record_datetime = now # datetimeオブジェクト

    if not student_no or not status:
        return jsonify({'status': 'error', 'message': '学籍番号またはステータスが不足しています。'}), 400

    lesson_info = get_current_lesson_info(student_no, record_date, record_time)
    
    attendance_status = '適用外' 
    教室ID = 3301 # 教室IDを3301に固定

    lesson_start_time_str = lesson_info['開始時刻'] if lesson_info else None
    lesson_end_time_str = lesson_info['終了時刻'] if lesson_info else None

    if lesson_info and lesson_start_time_str and lesson_end_time_str:
        
        # 授業開始日時を datetime オブジェクトで作成 (秒は:00を付加)
        lesson_start_datetime = datetime.strptime(f"{record_date} {lesson_start_time_str}:00", '%Y-%m-%d %H:%M:%S')
        
        if status == '入室':
            
            # 1. 出席/遅刻/欠席判定
            if record_datetime > lesson_start_datetime:
                time_difference = record_datetime - lesson_start_datetime
                time_difference_minutes = time_difference.total_seconds() / 60
                
                # 【DB改.pyロジック】: 20分以上遅れたら「欠席」
                if time_difference_minutes >= ABSENT_THRESHOLD_MINUTES: 
                    attendance_status = '欠席'
                # 10分以上遅れたら「遅刻」
                elif time_difference_minutes > LATE_THRESHOLD_MINUTES: 
                    attendance_status = '遅刻'
                else:
                    attendance_status = '出席' # 10分未満の遅れは許容
            else:
                attendance_status = '出席'

            # 2. 途中入室判定（既に退室記録があるか）
            has_exit = check_existing_record(student_no, record_date, lesson_start_time_str, lesson_end_time_str, check_status='退室')
            if has_exit:
                attendance_status = '途中入室'
                
        elif status == '退室':
            # 途中退室判定（既に入室記録があり、授業終了時刻前か）
            has_entry = check_existing_record(student_no, record_date, lesson_start_time_str, lesson_end_time_str, check_status='入室')

            # 入室記録があり、かつ退室時刻が授業終了時刻前であれば途中退室
            lesson_end_datetime = datetime.strptime(f"{record_date} {lesson_end_time_str}:00", '%Y-%m-%d %H:%M:%S')

            if has_entry and record_datetime < lesson_end_datetime:
                attendance_status = '途中退室'
            else:
                attendance_status = '適用外' 

    try:
        new_record = 入退室_出席記録(
            学籍番号=student_no, 
            入退室状況=status, 
            出席状況=attendance_status, 
            日時=record_datetime, 
            教室ID=教室ID
        )
        db.session.add(new_record)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'記録エラー: {e}'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'予期せぬエラー: {e}'}), 500
        
    return jsonify({'status': 'ok', 'message': '記録されました', 'attendance': attendance_status})


# =========================================================================
# Webルート (画面表示) (ORM対応)
# =========================================================================

@app.route('/', methods=['GET'])
def index_page():
    """ホーム画面 - 学生一覧と手動記録フォーム"""
    students = db.session.query(学生マスタ.学籍番号, 学生マスタ.氏名, 学科.学科名, 期マスタ.期名) \
        .join(学科, 学生マスタ.学科ID == 学科.学科ID) \
        .join(期マスタ, 学生マスタ.期 == 期マスタ.期ID) \
        .order_by(学生マスタ.学籍番号).all()
    
    today_str = date.today().strftime('%Y-%m-%d')
    current_time_str = datetime.now().strftime('%H:%M') 
    
    return render_template('index.html', 
                           title='出席管理システム - ホーム',
                           students=students,
                           today=today_str,           
                           current_time=current_time_str)


@app.route('/record_manual', methods=['POST'])
def record_manual():
    """手動入退室記録処理"""
    student_no = request.form.get('student_id')
    status = request.form.get('status') # '入室' or '退室'
    record_date_str = request.form.get('date')
    record_time_str = request.form.get('time')
    
    if not student_no or not status or not record_date_str or not record_time_str:
        return redirect(url_for('index_page', error='すべてのフィールドを入力してください'))
    
    try:
        record_datetime = datetime.strptime(f"{record_date_str} {record_time_str}:00", '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return redirect(url_for('index_page', error='日時の形式が不正です'))

    lesson_info = get_current_lesson_info(student_no, record_date_str, record_time_str)
    
    attendance_status = '適用外' 
    教室ID = 3301 # 教室IDを3301に固定

    lesson_start_time_str = lesson_info['開始時刻'] if lesson_info else None
    lesson_end_time_str = lesson_info['終了時刻'] if lesson_info else None
    
    if lesson_info and lesson_start_time_str and lesson_end_time_str:
        
        # 授業開始日時を datetime オブジェクトで作成
        lesson_start_datetime = datetime.strptime(f"{record_date_str} {lesson_start_time_str}:00", '%Y-%m-%d %H:%M:%S')

        if status == '入室':
            
            # 1. 出席/遅刻/欠席判定
            if record_datetime > lesson_start_datetime:
                time_difference = record_datetime - lesson_start_datetime
                time_difference_minutes = time_difference.total_seconds() / 60
                
                if time_difference_minutes >= ABSENT_THRESHOLD_MINUTES: 
                    attendance_status = '欠席'
                elif time_difference_minutes > LATE_THRESHOLD_MINUTES: 
                    attendance_status = '遅刻'
                else:
                    attendance_status = '出席'
            else:
                attendance_status = '出席'

            # 2. 途中入室判定
            has_exit = check_existing_record(student_no, record_date_str, lesson_start_time_str, lesson_end_time_str, check_status='退室')
            if has_exit:
                attendance_status = '途中入室'
                
        elif status == '退室':
            # 途中退室判定
            has_entry = check_existing_record(student_no, record_date_str, lesson_start_time_str, lesson_end_time_str, check_status='入室')

            lesson_end_datetime = datetime.strptime(f"{record_date_str} {lesson_end_time_str}:00", '%Y-%m-%d %H:%M:%S')

            if has_entry and record_datetime < lesson_end_datetime:
                attendance_status = '途中退室'
            else:
                attendance_status = '適用外'
    
    try:
        new_record = 入退室_出席記録(
            学籍番号=student_no, 
            入退室状況=status, 
            出席状況=attendance_status, 
            日時=record_datetime, 
            教室ID=教室ID
        )
        db.session.add(new_record)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return redirect(url_for('index_page', error=f'記録エラー: {e}'))
    except Exception as e:
        db.session.rollback()
        return redirect(url_for('index_page', error=f'予期せぬエラー: {e}'))
        
    msg = f"手動記録が完了しました ({attendance_status})。"
    return redirect(url_for('index_page', success_msg=msg))


@app.route('/logs')
def logs_page():
    """入退室・出席記録の全ログを表示する"""
    try:
        # DB改.pyのロジック: 日時を降順(DESC)に修正
        records = db.session.query(
                入退室_出席記録.記録ID, 
                入退室_出席記録.学籍番号, 
                学生マスタ.氏名.label('名前'), 
                入退室_出席記録.日時,
                入退室_出席記録.入退室状況, 
                入退室_出席記録.出席状況
            ) \
            .join(学生マスタ, 入退室_出席記録.学籍番号 == 学生マスタ.学籍番号) \
            .order_by(入退室_出席記録.日時.desc()) \
            .all()
        
        # テンプレート用に日時の整形 (Python側で実施)
        formatted_records = []
        for r in records:
            formatted_records.append({
                '記録ID': r.記録ID,
                '学籍番号': r.学籍番号,
                '名前': r.名前,
                '日付': r.日時.strftime('%Y-%m-%d'),
                '時刻': r.日時.strftime('%H:%M:%S'),
                '入退室状況': r.入退室状況,
                '出席状況': r.出席状況,
            })
        
        return render_template('logs.html', title='全入退室・出席ログ', records=formatted_records)

    except Exception as e:
        print(f"logs_pageでエラーが発生しました: {e}")
        return redirect(url_for('index_page', error=f'ログ表示エラー: {e}'))

# =========================================================================
# その他のWebルート (ORM対応)
# =========================================================================

@app.route('/student_management', methods=['GET'])
def student_management_page():
    """学生別出席状況選択ページと時間割表示"""
    students = 学生マスタ.query.order_by(学生マスタ.学籍番号).all()
    terms = 期マスタ.query.order_by(期マスタ.期ID).all()
    
    return render_template('student_management.html', 
                           title='学生別出席状況',
                           students=students,
                           terms=terms)


@app.route('/student_log/<string:student_no>/<int:term_id>', methods=['GET'])
def student_log_page(student_no, term_id):
    """学生個別の出席統計とログを表示"""
    # 簡略化されたロジックは DB改.py を踏襲
    student_info = 学生マスタ.query.filter_by(学籍番号=student_no).first()
    term_name = 期マスタ.query.filter_by(期ID=term_id).first().期名
    
    # DB改.pyに従い、ログの取得とサマリーはダミーまたは簡略化
    
    # 出席状況を集計 (ここではダミーデータ)
    summary = {
        'present_count': 0, 'late_count': 0, 'absent_count': 0, 
        'early_exit_count': 0, 'total_lessons': 0
    }
    
    # ログの取得 (ここでは省略)
    student_records = []
    
    data = {
        'student_id': student_no,
        'student_info': {'氏名': student_info.氏名, '学科ID': student_info.学科ID, '期': student_info.期} if student_info else {},
        'term_id': term_id,
        'term_name': term_name,
        'summary': summary,
        'records': student_records
    }
    return render_template('student_log.html', title=f"{student_info.氏名}さんの出席ログ" if student_info else '学生ログ', data=data)


@app.route('/timetable', methods=['GET'])
def timetable_page():
    """週時間割を表示する"""
    time_slots = TimeTable.query.order_by(TimeTable.時限).all()
    weekdays = 曜日マスタ.query.order_by(曜日マスタ.曜日ID).all()

    # 週時間割データを取得 (ここでは固定の年度/学科/期)
    timetable_data = db.session.query(
            週時間割.曜日, 週時間割.時限, 授業科目.授業科目名, 教室.教室名, 週時間割.備考
        ) \
        .join(授業科目, 週時間割.科目ID == 授業科目.授業科目ID) \
        .join(教室, 週時間割.教室ID == 教室.教室ID) \
        .filter(週時間割.年度 == 2025, 週時間割.学科ID == 3, 週時間割.期 == 3) \
        .all()

    # テンプレート表示用にデータを整形
    timetable_matrix = {}
    for lesson in timetable_data:
        timetable_matrix[(lesson[0], lesson[1])] = {
            '授業科目名': lesson[2], 
            '教室名': lesson[3], 
            '備考': lesson[4]
        }
    
    return render_template('timetable.html', 
                           title='週時間割',
                           time_slots=time_slots,
                           weekdays=weekdays,
                           timetable_matrix=timetable_matrix)


@app.route('/time_master', methods=['GET'])
def time_master_page():
    """時刻マスタ一覧を表示する"""
    time_masters = TimeTable.query.order_by(TimeTable.時限).all()
    return render_template('time_master.html', title='TimeTable', time_masters=time_masters)


@app.route('/subject_master', methods=['GET'])
def subject_master_page():
    """授業科目マスタ一覧を表示する"""
    subjects = db.session.query(授業科目.授業科目ID, 授業科目.授業科目名, 授業科目.単位, 学科.学科名) \
        .join(学科, 授業科目.学科ID == 学科.学科ID) \
        .order_by(授業科目.授業科目ID).all()
    return render_template('subject_master.html', title='授業科目マスタ', subjects=subjects)

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
    # 例: flask init-db
    print("\n-------------------------------------------")
    print("ORMベースのFlask Webアプリを起動します。")
    print("データベース初期化とマスタデータ挿入には 'flask init-db' を実行してください。")
    print("-------------------------------------------")
    
    # 起動時に自動欠席判定を実行 (ORMではコンテキストが必要)
    with app.app_context():
        run_daily_attendance_check_by_lesson()
        
    # 【重要】テンプレートファイル（*.html）は、このPythonファイルと同じ階層にある 'templates' フォルダに配置してください
    app.run(debug=True, host='0.0.0.0', port=5000)

