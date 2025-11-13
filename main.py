# main.py (Flask-SQLAlchemy ORM 統合版 - PostgreSQL/Render安定版)

import os
from datetime import datetime, date, timedelta, time
from flask import Flask, render_template, request, url_for, jsonify, redirect
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.engine import Engine
from sqlalchemy import event

# =========================================================================
# データベース設定
# =========================================================================

app = Flask(__name__)

# PostgreSQLの接続設定を優先し、環境変数がない場合はSQLiteにフォールバック
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///school.db')
# Render互換性のために、PostgreSQL URLスキームを修正
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 外部キー制約の有効化 (SQLite環境でのみ必要、PostgreSQLでは自動)
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# =========================================================================
# 出席判定に関する定数
# =========================================================================
ABSENT_THRESHOLD_MINUTES = 20
LATE_THRESHOLD_MINUTES = 10

# =========================================================================
# データベーススキーマ定義 (ORMクラス)
# 🚨 修正点: __tablename__を全て小文字に統一 (PostgreSQL互換性のため)
# =========================================================================

# 1. 曜日マスタ
class 曜日マスタ(db.Model):
    __tablename__ = '曜日マスタ' # ⬅️ 小文字に統一
    曜日ID = db.Column(db.SmallInteger, primary_key=True)
    曜日名 = db.Column(db.String(10), nullable=False)

# 2. 期マスタ
class 期マスタ(db.Model):
    __tablename__ = '期マスタ' # ⬅️ 小文字に統一
    期ID = db.Column(db.SmallInteger, primary_key=True)
    期名 = db.Column(db.String(20), nullable=False)

# 3. 学科
class 学科(db.Model):
    __tablename__ = '学科' # ⬅️ 小文字に統一
    学科ID = db.Column(db.SmallInteger, primary_key=True)
    学科名 = db.Column(db.String(50))

# 4. 教室
class 教室(db.Model):
    __tablename__ = '教室' # ⬅️ 小文字に統一
    教室ID = db.Column(db.SmallInteger, primary_key=True)
    教室名 = db.Column(db.String(50), nullable=False)
    収容人数 = db.Column(db.SmallInteger, nullable=False)

# 5. 授業科目
class 授業科目(db.Model):
    __tablename__ = '授業科目' # ⬅️ 小文字に統一
    授業科目ID = db.Column(db.SmallInteger, primary_key=True)
    授業科目名 = db.Column(db.String(100), nullable=False)
    # 外部キー名はクラス名と一致させる
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), nullable=False)
    単位 = db.Column(db.SmallInteger)
    学科 = db.relationship('学科', backref=db.backref('授業科目_list', lazy=True))

# 6. 学生マスタ
class 学生マスタ(db.Model):
    __tablename__ = '学生マスタ' # ⬅️ 小文字に統一
    学籍番号 = db.Column(db.Integer, primary_key=True)
    氏名 = db.Column(db.String(50), nullable=False)
    # 外部キー名はクラス名と一致させる
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), nullable=False)
    期ID = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), nullable=False)
    学科 = db.relationship('学科', backref=db.backref('学生_list', lazy=True))
    期 = db.relationship('期マスタ', backref=db.backref('学生_list', lazy=True))

# 7. TimeTable（時限マスタ）
class TimeTable(db.Model):
    __tablename__ = 'timetable' # ⬅️ 小文字に統一
    id = db.Column(db.Integer, primary_key=True)
    時限 = db.Column(db.SmallInteger, nullable=False, unique=True)
    開始時刻 = db.Column(db.Time, nullable=False)
    終了時刻 = db.Column(db.Time, nullable=False)

# 8. 週時間割
class 週時間割(db.Model):
    __tablename__ = '週時間割' # ⬅️ 小文字に統一
    id = db.Column(db.Integer, primary_key=True)
    年度 = db.Column(db.SmallInteger, nullable=False)
    学科ID = db.Column(db.SmallInteger, db.ForeignKey('学科.学科ID'), nullable=False)
    期 = db.Column(db.SmallInteger, db.ForeignKey('期マスタ.期ID'), nullable=False)
    曜日 = db.Column(db.SmallInteger, db.ForeignKey('曜日マスタ.曜日ID'), nullable=False)
    時限 = db.Column(db.SmallInteger, db.ForeignKey('timetable.時限'), nullable=False) # ⬅️ TimeTableも小文字参照
    科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'), nullable=False)
    教室ID = db.Column(db.SmallInteger, db.ForeignKey('教室.教室ID'))
    備考 = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('年度', '学科ID', '期', '曜日', '時限', name='_unique_time_slot'),
    )
    曜日マスタ = db.relationship('曜日マスタ', backref=db.backref('時間割_list', lazy=True))
    授業科目 = db.relationship('授業科目', backref=db.backref('時間割_list', lazy=True))
    教室 = db.relationship('教室', backref=db.backref('時間割_list', lazy=True))

# 9. 入退室_出席記録
class 入退室_出席記録(db.Model):
    __tablename__ = '入退室_出席記録' # ⬅️ 小文字に統一
    記録ID = db.Column(db.Integer, primary_key=True)
    学籍番号 = db.Column(db.Integer, db.ForeignKey('学生マスタ.学籍番号'), nullable=False)
    入退室区分 = db.Column(db.String(10), nullable=False)
    タイムスタンプ = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    出席状況 = db.Column(db.String(10), default='未判定', nullable=False)
    授業科目ID = db.Column(db.SmallInteger, db.ForeignKey('授業科目.授業科目ID'), nullable=True)
    教室ID = db.Column(db.SmallInteger, db.ForeignKey('教室.教室ID'), nullable=True)
    学生 = db.relationship('学生マスタ', backref=db.backref('入退室_list', lazy=True))
    授業科目 = db.relationship('授業科目', backref=db.backref('記録_list', lazy=True))
    教室 = db.relationship('教室', backref=db.backref('記録_list', lazy=True))


# =========================================================================
# 初期データ挿入関数 (マスタデータ)
# =========================================================================

def _insert_initial_data():
    """データベースにマスタデータと初期データを挿入します。"""
    try:
        # 曜日マスタ
        db.session.add_all([
            曜日マスタ(曜日ID=1, 曜日名='月曜日'), 曜日マスタ(曜日ID=2, 曜日名='火曜日'),
            曜日マスタ(曜日ID=3, 曜日名='水曜日'), 曜日マスタ(曜日ID=4, 曜日名='木曜日'),
            曜日マスタ(曜日ID=5, 曜日名='金曜日'),
        ])
        # 期マスタ
        db.session.add_all([
            期マスタ(期ID=1, 期名='一期'), 期マスタ(期ID=2, 期名='二期'),
            期マスタ(期ID=3, 期名='三期'), 期マスタ(期ID=4, 期名='四期'),
        ])
        # TimeTable（時限マスタ）
        db.session.add_all([
            TimeTable(時限=1, 開始時刻=time(9, 0), 終了時刻=time(10, 30)),
            TimeTable(時限=2, 開始時刻=time(10, 40), 終了時刻=time(12, 10)),
            TimeTable(時限=3, 開始時刻=time(13, 0), 終了時刻=time(14, 30)),
            TimeTable(時限=4, 開始時刻=time(14, 40), 終了時刻=time(16, 10)),
        ])
        # 学科 (仮データ)
        db.session.add_all([
            学科(学科ID=3, 学科名='電子情報系'), 学科(学科ID=4, 学科名='機械系'),
        ])
        # 教室 (仮データ)
        db.session.add_all([
            教室(教室ID=3301, 教室名='C301', 収容人数=40),
            教室(教室ID=3302, 教室名='C302', 収容人数=40),
            教室(教室ID=3101, 教室名='C101', 収容人数=40),
            教室(教室ID=3202, 教室名='K302', 収容人数=40),
        ])
        # 授業科目 (仮データ)
        db.session.add_all([
            授業科目(授業科目ID=317, 授業科目名='機械実習Ⅰ', 学科ID=4, 単位=2),
            授業科目(授業科目ID=321, 授業科目名='制御回路設計製作実習', 学科ID=3, 単位=2),
            授業科目(授業科目ID=380, 授業科目名='標準課題Ⅰ', 学科ID=3, 単位=2),
            授業科目(授業科目ID=381, 授業科目名='標準課題Ⅱ', 学科ID=3, 単位=2),
            授業科目(授業科目ID=400, 授業科目名='電子情報系総合実習', 学科ID=3, 単位=2),
            授業科目(授業科目ID=401, 授業科目名='機械系総合実習', 学科ID=4, 単位=2),
        ])
        # 学生マスタ (仮データ)
        db.session.add_all([
            学生マスタ(学籍番号=2025001, 氏名='佐藤 太郎', 学科ID=3, 期ID=3),
            学生マスタ(学籍番号=2025002, 氏名='鈴木 花子', 学科ID=3, 期ID=3),
            学生マスタ(学籍番号=2025003, 氏名='田中 次郎', 学科ID=4, 期ID=4),
        ])
        # 週時間割（省略 - 必要に応じてここに挿入）

        db.session.commit()
        print('✅ マスタデータの挿入が完了しました。')
    except IntegrityError:
        db.session.rollback()
        print('ℹ️ マスタデータは既に挿入されています。スキップしました。')
    except Exception as e:
        db.session.rollback()
        print(f"❌ 初期データ挿入中にエラーが発生しました: {e}")


# =========================================================================
# データベース初期化ロジック (Render安定化用)
# =========================================================================

def init_db_on_startup():
    """
    アプリケーション起動時にデータベースの初期化を試行します。
    テーブルが存在しない場合（初回デプロイ時）のみ作成します。
    """
    with app.app_context():
        try:
            # 🚨 修正点: テーブル名を小文字でチェックするように変更
            if db.engine.dialect.has_table(db.engine.connect(), '学生マスタ'):
                print("ℹ️ データベースのテーブルは既に存在します。初期化をスキップします。")
            else:
                print("⚠️ データベースのテーブルが存在しません。テーブル作成と初期データ挿入を開始します。")
                db.create_all() # すべてのテーブルを作成
                print("✅ テーブル作成が完了しました。")
                _insert_initial_data() # マスタデータを挿入

        except ProgrammingError as e:
            # データベース接続は成功したが、テーブルチェックでエラーが出た場合
            print(f"⚠️ 警告: テーブルチェック中にエラーが発生。強制的にテーブル作成を試みます: {e}")
            db.create_all()
            _insert_initial_data()
        except Exception as e:
            print(f"❌ 致命的なデータベース初期化エラー: {e}")

# =========================================================================
# ルーティング (省略されていた部分を可能な限り復元)
# =========================================================================

@app.route('/')
def index_page():
    """トップページ: 学生一覧と基本情報表示"""
    try:
        # 結合条件の省略はそのまま維持（PostgreSQLとの互換性を最大化するため）
        students_with_info = db.session.query(
            学生マスタ.学籍番号, 学生マスタ.氏名, 学科.学科名, 期マスタ.期名
        ).join(学科) \
         .join(期マスタ) \
         .order_by(学生マスタ.学籍番号).all()
        
        # 💡 ここで 'index.html' のレンダリングが必要ですが、今回はコードのみを提供
        # テンプレートファイル 'index.html' が存在しない場合は、以下のデバッグメッセージが表示されます
        # return render_template('index.html', students=students_with_info)
        
        # デバッグ用: 取得データを表示
        student_list_html = "<h1>学生一覧</h1><ul>"
        for s in students_with_info:
            student_list_html += f"<li>{s[0]} - {s[1]} ({s[2]}, {s[3]})</li>"
        student_list_html += "</ul>"
        return student_list_html

    except Exception as e:
        # エラーメッセージをログに出力し、デバッグを容易にします
        print(f"❌ トップページのデータ取得エラーが発生しました: {e}")
        # ユーザーにエラーメッセージを表示
        return f"トップページのデータ取得エラー: テーブルが正しく初期化されているか確認してください。エラー詳細: {e}", 500

@app.route('/logs')
def logs_page():
    """入退室・出席記録の一覧ページ"""
    try:
        # 🚨 修正: 外部キー結合が必要なクエリを簡略化 (PostgreSQL向け)
        records = db.session.query(
            入退室_出席記録, 学生マスタ.氏名, 授業科目.授業科目名, 教室.教室名
        ).join(学生マスタ, 入退室_出席記録.学籍番号 == 学生マスタ.学籍番号) \
         .outerjoin(授業科目) \
         .outerjoin(教室) \
         .order_by(入退室_出席記録.タイムスタンプ.desc()).limit(100).all()

        # デバッグ用: 取得データを表示
        log_list_html = "<h1>入退室ログ</h1><ul>"
        for record, name, subject, room in records:
            log_list_html += f"<li>{record.タイムスタンプ} | {name} ({record.入退室区分}) | 科目: {subject or 'N/A'} | 教室: {room or 'N/A'}</li>"
        log_list_html += "</ul>"
        return log_list_html

    except Exception as e:
        print(f"❌ ログページのデータ取得エラーが発生しました: {e}")
        return f"ログページのデータ取得エラー: {e}", 500

@app.route('/api/attendance', methods=['POST'])
def attendance_api_post():
    """入退室のAPIエンドポイント (学生がカードをかざす処理)"""
    data = request.json
    try:
        # 仮の入退室記録挿入
        record = 入退室_出席記録(
            学籍番号=data['student_id'],
            入退室区分=data['direction'],
            授業科目ID=data.get('subject_id'),
            教室ID=data.get('room_id')
        )
        db.session.add(record)
        db.session.commit()
        return jsonify({"message": "記録成功"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"記録エラー: {e}"}), 400

# =========================================================================
# CRUD/削除ルート (ORM対応)
# =========================================================================

@app.route('/delete/<int:record_id>', methods=['POST'])
def delete_record(record_id):
    """個別の入退室記録をIDで削除する"""
    try:
        record = 入退室_出席記録.query.get_or_404(record_id)
        db.session.delete(record)
        db.session.commit()
        return redirect(request.referrer or url_for('logs_page')) 
    except Exception as e:
        db.session.rollback()
        return f"削除エラー: {e}", 500

@app.route('/delete_all', methods=['POST'])
def delete_all_records():
    """全ての入退室_出席記録を削除する（テーブルは残る）"""
    try:
        # SQLAlchemy 2.0 style mass deletion
        db.session.query(入退室_出席記録).delete()
        db.session.commit()
        return redirect(url_for('logs_page')) 
    except Exception as e:
        db.session.rollback()
        return f"全削除エラー: {e}", 500


# =========================================================================
# データベースの初期化とWebアプリの実行
# =========================================================================

# 💡 Gunicornがアプリをロードする際に、この関数が実行され初期化が完了します。
init_db_on_startup()


if __name__ == "__main__":
    print("\n-------------------------------------------")
    print("ORMベースのFlask Webアプリを起動します。")
    print("Render環境ではGunicornを使用してください。")
    app.run(debug=True, host='0.0.0.0', port=5000)

