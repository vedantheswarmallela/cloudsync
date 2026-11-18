import os
import random
import hashlib
import tempfile
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for

from config import Config
from models import Device, File, Notification, User, init_database
from services.file_service import FileService, compute_sha256
from services.pdf_service import validate_pdf_file
from services.sync_engine import SyncEngine
from services.version_service import VersionService

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']


@app.context_processor
def inject_firebase_config():
    return {'firebase_config': app.config['FIREBASE_CONFIG']}


@app.errorhandler(413)
def handle_request_entity_too_large(_error):
    flash('File too large. Maximum upload size is 100 MB.', 'error')
    return redirect(url_for('dashboard'))


os.makedirs(Config.DATA_DIR, exist_ok=True)
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

_, db_session = init_database(app.config['SQLALCHEMY_DATABASE_URI'])
db = db_session

file_service = FileService(db, Config.UPLOAD_FOLDER)
sync_engine = SyncEngine(db)
version_service = VersionService(db, Config.UPLOAD_FOLDER)

DEMO_EMAIL = '24691a37f1@gmail.com'
DEMO_DATA_EMAIL = 'demo@cloudsync.pro'
DEMO_PASSWORD = 'demo123'
EDITABLE_TEXT_EXTENSIONS = {
    '.txt', '.md', '.json', '.xml', '.py', '.js', '.html', '.css',
    '.ts', '.c', '.cpp', '.java', '.sql', '.log', '.csv'
}


def format_bytes(byte_count):
    value = float(byte_count or 0)
    if value < 1024:
        return f'{int(value)} B'

    for unit in ('KB', 'MB', 'GB', 'TB'):
        value /= 1024
        if value < 1024 or unit == 'TB':
            precision = 2 if unit == 'KB' else 1
            return f'{value:.{precision}f} {unit}'


def generate_captcha():
    a = random.randint(2, 9)
    b = random.randint(1, 9)
    answer = a + b
    session['captcha_answer'] = str(answer)
    session['captcha_question'] = f'{a} + {b}'
    return session['captcha_question']


def ensure_demo_user():
    user = db.query(User).filter_by(email=DEMO_DATA_EMAIL).first()
    if user is None:
        user = db.query(User).filter_by(email=DEMO_EMAIL).first()
    if user is None:
        user = db.query(User).filter_by(email='demo@cloudsync.local').first()
        if user is not None:
            user.email = DEMO_DATA_EMAIL
            user.set_password(DEMO_PASSWORD)
            db.commit()
    if user is None:
        user = User(
            username=DEMO_EMAIL,
            email=DEMO_EMAIL,
            role='user',
            storage_quota_bytes=Config.DEFAULT_USER_QUOTA
        )
        user.set_password(DEMO_PASSWORD)
        db.add(user)
        db.commit()

    if user.email == DEMO_DATA_EMAIL:
        user.set_password(DEMO_PASSWORD)
        db.commit()

    device = db.query(Device).filter_by(user_id=user.id, name='Web Browser').first()
    if device is None:
        device = Device(
            user_id=user.id,
            name='Web Browser',
            device_type='Desktop',
            os_info='Browser Session',
            ip_address='127.0.0.1',
            is_online=True
        )
        db.add(device)
        db.commit()

    return user


def ensure_user_device(user):
    device = db.query(Device).filter_by(user_id=user.id, name='Web Browser').first()
    if device is None:
        device = Device(
            user_id=user.id,
            name='Web Browser',
            device_type='Desktop',
            os_info='Browser Session',
            ip_address='127.0.0.1',
            is_online=True,
        )
        db.add(device)
        db.commit()
    return device


def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = db.query(User).filter_by(id=user_id).first()
    if user and user.email == DEMO_EMAIL:
        demo_user = db.query(User).filter_by(email=DEMO_DATA_EMAIL).first()
        if demo_user:
            session['user_id'] = demo_user.id
            return demo_user
    return user


@app.route('/')
def index():
    if get_current_user():
        return redirect(url_for('dashboard'))
    return render_template('index.html', captcha_question=generate_captcha())


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        captcha_value = (request.form.get('captcha') or '').strip()

        if not username or not email or not password:
            flash('Please complete all fields.', 'error')
            return render_template('login.html', captcha_question=generate_captcha())

        if db.query(User).filter((User.email == email) | (User.username == username)).first():
            flash('A user with that email or username already exists.', 'error')
            return render_template('login.html', captcha_question=generate_captcha())

        if captcha_value != session.get('captcha_answer'):
            flash('Captcha verification failed. Please try again.', 'error')
            return render_template('login.html', captcha_question=generate_captcha())

        user = User(
            username=username,
            email=email,
            role='user',
            storage_quota_bytes=Config.DEFAULT_USER_QUOTA,
        )
        user.set_password(password)
        db.add(user)
        db.commit()

        session['user_id'] = user.id
        flash('Account created successfully.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html', captcha_question=generate_captcha())


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        if email == DEMO_EMAIL and password == DEMO_PASSWORD:
            session['user_id'] = ensure_demo_user().id
            flash('Demo login successful.', 'success')
            return redirect(url_for('dashboard'))

        user = db.query(User).filter_by(email=email).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            flash('Login successful.', 'success')
            return redirect(url_for('dashboard'))

        flash('Invalid email or password.', 'error')
        return render_template('login.html', captcha_question=generate_captcha())

    if get_current_user():
        return redirect(url_for('dashboard'))
    return render_template('login.html', captcha_question=generate_captcha())


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    user = get_current_user()
    if user is None:
        user = ensure_demo_user()
        session['user_id'] = user.id

    device = ensure_user_device(user)

    if request.method == 'POST' and 'file' in request.files:
        uploaded_file = request.files['file']
        if uploaded_file and uploaded_file.filename:
            try:
                uploaded, duplicate = file_service.upload_file(
                    user.id,
                    uploaded_file,
                    device_id=device.id,
                )
                if duplicate:
                    flash(f"Uploaded '{uploaded.name}' as a duplicate of an existing file.", 'warning')
                else:
                    flash(f"Uploaded '{uploaded.name}' successfully.", 'success')
            except ValueError as exc:
                flash(str(exc), 'error')
            except Exception as exc:
                flash(f'Upload failed: {str(exc)}', 'error')
        else:
            flash('Please choose a file to upload.', 'error')
        return redirect(url_for('dashboard'))

    summary = sync_engine.get_sync_summary(user.id)
    files = db.query(File).filter_by(user_id=user.id, is_deleted=False).order_by(File.updated_at.desc()).all()
    stored_files = db.query(File.file_size).filter_by(user_id=user.id).all()
    storage_used_bytes = sum(file_size for (file_size,) in stored_files)
    storage_quota_bytes = user.storage_quota_bytes or 0
    storage_percent = min(100, storage_used_bytes / storage_quota_bytes * 100) if storage_quota_bytes else 0
    storage_bar_percent = min(100, max(1.5, storage_percent)) if storage_used_bytes else 0
    storage_percent_display = (
        f'{storage_percent:.1f}%'
        if storage_percent >= 0.1
        else '<0.1%' if storage_used_bytes else '0%'
    )
    devices = db.query(Device).filter_by(user_id=user.id).all()
    notifications = db.query(Notification).filter_by(user_id=user.id).order_by(Notification.created_at.desc()).limit(5).all()
    pending = sync_engine.get_pending_files(user.id)

    return render_template(
        'dashboard.html',
        user=user,
        summary=summary,
        files=files,
        devices=devices,
        notifications=notifications,
        pending=pending,
        current_device=device,
        storage_used_bytes=storage_used_bytes,
        storage_used_display=format_bytes(storage_used_bytes),
        storage_quota_display=format_bytes(storage_quota_bytes),
        storage_percent=storage_percent,
        storage_bar_percent=storage_bar_percent,
        storage_percent_display=storage_percent_display,
        editable_file_ids={
            file.id for file in files
            if (
                os.path.splitext(file.name)[1].lower() in EDITABLE_TEXT_EXTENSIONS
                or os.path.splitext(file.name)[1].lower() == '.pdf'
            )
        },
    )


@app.route('/dashboard/files/<int:file_id>/edit', methods=['GET', 'POST'])
def edit_file(file_id):
    user = get_current_user()
    if user is None:
        return redirect(url_for('index'))

    file_record = file_service.get_file(file_id, user.id)
    if not file_record:
        flash('The selected file is unavailable.', 'error')
        return redirect(url_for('dashboard'))

    file_extension = os.path.splitext(file_record.name)[1].lower()
    if file_extension == '.pdf':
        if request.method == 'POST':
            try:
                submitted_version = int(request.form.get('current_version', ''))
            except ValueError:
                flash('The edit form was invalid. Please reopen the file and try again.', 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))

            if submitted_version != file_record.current_version:
                flash('This file changed after you opened it. Reopen it before replacing it.', 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))

            replacement_pdf = request.files.get('replacement_pdf')
            if not replacement_pdf or not replacement_pdf.filename:
                flash('Choose the edited PDF file to upload.', 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))

            if os.path.splitext(replacement_pdf.filename)[1].lower() != '.pdf':
                flash('The replacement file must have a .pdf extension.', 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))

            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode='wb',
                    prefix='replace_pdf_',
                    suffix='.pdf',
                    dir=os.path.dirname(file_record.storage_path),
                    delete=False,
                ) as temporary_file:
                    temporary_path = temporary_file.name
                    replacement_pdf.save(temporary_file)

                file_size = os.path.getsize(temporary_path)
                if file_size > Config.MAX_CONTENT_LENGTH:
                    flash('The replacement PDF is too large. Maximum size is 100 MB.', 'error')
                    return redirect(url_for('edit_file', file_id=file_record.id))

                validate_pdf_file(temporary_path)
                version_service.create_new_version(
                    file_record.id,
                    user.id,
                    temporary_path,
                    compute_sha256(temporary_path),
                    file_size,
                    device_id=ensure_user_device(user).id,
                    change_summary='Replaced PDF in dashboard',
                )
            except ValueError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))
            except OSError as exc:
                flash(f'Unable to save the replacement PDF: {exc}', 'error')
                return redirect(url_for('edit_file', file_id=file_record.id))
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    os.remove(temporary_path)

            flash(f"Saved '{file_record.name}' as version v{file_record.current_version}.", 'success')
            return redirect(url_for('dashboard'))

        return render_template('replace_pdf.html', file=file_record)

    if file_extension not in EDITABLE_TEXT_EXTENSIONS:
        flash('This file type cannot be edited in the dashboard.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            submitted_version = int(request.form.get('current_version', ''))
        except ValueError:
            flash('The edit form was invalid. Please reopen the file and try again.', 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))

        if submitted_version != file_record.current_version:
            flash('This file changed after you opened it. Reopen it before saving to avoid overwriting newer edits.', 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))

        content = request.form.get('content')
        if content is None:
            flash('No file content was submitted.', 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))

        content_bytes = content.encode('utf-8')
        if len(content_bytes) > Config.MAX_CONTENT_LENGTH:
            flash('Edited file is too large. Maximum size is 100 MB.', 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))

        if not os.path.exists(file_record.storage_path):
            flash('The current file content is unavailable on the server.', 'error')
            return redirect(url_for('dashboard'))

        with open(file_record.storage_path, 'rb') as current_file:
            if current_file.read() == content_bytes:
                flash('No changes were made to this file.', 'info')
                return redirect(url_for('dashboard'))

        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='wb',
                prefix='edit_',
                suffix='.tmp',
                dir=os.path.dirname(file_record.storage_path),
                delete=False,
            ) as temporary_file:
                temporary_file.write(content_bytes)
                temporary_path = temporary_file.name

            version_service.create_new_version(
                file_record.id,
                user.id,
                temporary_path,
                hashlib.sha256(content_bytes).hexdigest(),
                len(content_bytes),
                device_id=ensure_user_device(user).id,
                change_summary='Edited in dashboard',
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))
        except OSError as exc:
            flash(f'Unable to save edits: {exc}', 'error')
            return redirect(url_for('edit_file', file_id=file_record.id))
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

        flash(f"Saved '{file_record.name}' as version v{file_record.current_version}.", 'success')
        return redirect(url_for('dashboard'))

    if not os.path.exists(file_record.storage_path):
        flash('The current file content is unavailable on the server.', 'error')
        return redirect(url_for('dashboard'))

    try:
        with open(file_record.storage_path, 'r', encoding='utf-8') as current_file:
            content = current_file.read()
    except UnicodeDecodeError:
        flash('This file is not valid UTF-8 text and cannot be edited in the dashboard.', 'error')
        return redirect(url_for('dashboard'))
    except OSError as exc:
        flash(f'Unable to read this file: {exc}', 'error')
        return redirect(url_for('dashboard'))

    return render_template('edit_file.html', file=file_record, content=content)


@app.route('/dashboard/sync', methods=['POST'])
def sync_dashboard():
    user = get_current_user()
    if user is None:
        return redirect(url_for('index'))

    result = sync_engine.sync_all(user.id)
    flash(f"Synchronization completed for {result['synced_devices_count']} device(s).", 'success')
    return redirect(url_for('dashboard'))


@app.route('/dashboard/files/<int:file_id>/delete', methods=['POST'])
def delete_file(file_id):
    user = get_current_user()
    if user is None:
        return redirect(url_for('index'))

    deleted = file_service.delete_file(file_id, user.id, permanent=False)
    if deleted:
        flash('File moved to trash.', 'success')
    else:
        flash('Could not delete the selected file.', 'error')
    return redirect(url_for('dashboard'))


@app.route('/dashboard/files/<int:file_id>/download')
def download_file(file_id):
    user = get_current_user()
    if user is None:
        return redirect(url_for('index'))

    file_record = file_service.get_file(file_id, user.id)
    if not file_record or not os.path.exists(file_record.storage_path):
        flash('The requested file is unavailable.', 'error')
        return redirect(url_for('dashboard'))

    return send_file(file_record.storage_path, as_attachment=True, download_name=file_record.original_name or file_record.name)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
