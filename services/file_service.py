import os
import hashlib
import mimetypes
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from models import File, FileVersion, Folder, User, Device, DeviceSyncState, ActivityLog, Notification

CATEGORY_MAP = {
    'documents': {'.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt', '.csv', '.xls', '.xlsx', '.ppt', '.pptx', '.md', '.json', '.xml', '.py', '.js', '.html', '.css', '.ts', '.c', '.cpp', '.java', '.sql', '.log'},
    'images': {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff'},
    'videos': {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.flv', '.m4v'},
    'audio': {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma'},
    'archives': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.iso'}
}

def detect_category(filename):
    _, ext = os.path.splitext(filename.lower())
    for category, extensions in CATEGORY_MAP.items():
        if ext in extensions:
            return category
    return 'others'

def compute_sha256(file_path):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def compute_stream_sha256(stream):
    hasher = hashlib.sha256()
    pos = stream.tell() if hasattr(stream, 'tell') else 0
    while chunk := stream.read(65536):
        hasher.update(chunk)
    if hasattr(stream, 'seek'):
        stream.seek(pos)
    return hasher.hexdigest()

class FileService:
    def __init__(self, db_session, upload_base_folder):
        self.db = db_session
        self.upload_base_folder = upload_base_folder

    def _get_user_upload_dir(self, user_id):
        user_dir = os.path.join(self.upload_base_folder, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        return user_dir

    def check_duplicate(self, user_id, file_hash, filename=None):
        """Check if file with same hash already exists for user (excluding trash)."""
        existing = self.db.query(File).filter(
            File.user_id == user_id,
            File.file_hash == file_hash,
            File.is_deleted == False
        ).first()
        return existing

    def upload_file(self, user_id, file_obj, folder_id=None, device_id=None, custom_name=None):
        """Upload a file, check quota, check duplicates, store to disk, and register in DB."""
        user = self.db.query(User).filter_by(id=user_id).first()
        if not user:
            raise ValueError("User not found.")

        raw_filename = custom_name or file_obj.filename
        orig_filename = secure_filename(raw_filename)
        if not orig_filename:
            orig_filename = f"file_{uuid.uuid4().hex[:8]}.bin"

        # Read into temp storage to determine size & hash
        user_dir = self._get_user_upload_dir(user_id)
        temp_name = f"temp_{uuid.uuid4().hex}_{orig_filename}"
        temp_path = os.path.join(user_dir, temp_name)
        if hasattr(file_obj, 'save'):
            file_obj.save(temp_path)
        elif hasattr(file_obj, 'read'):
            with open(temp_path, 'wb') as f:
                f.write(file_obj.read())
        else:
            raise ValueError("Unsupported file stream object")

        file_size = os.path.getsize(temp_path)
        file_hash = compute_sha256(temp_path)

        # Quota Validation
        if (user.storage_used_bytes + file_size) > user.storage_quota_bytes:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Storage quota exceeded. Free space: {(user.storage_quota_bytes - user.storage_used_bytes)/(1024*1024):.1f} MB, File size: {file_size/(1024*1024):.1f} MB.")

        # Check duplicate
        duplicate = self.check_duplicate(user_id, file_hash)

        # Move to permanent storage path
        storage_filename = f"{uuid.uuid4().hex}_{orig_filename}"
        permanent_path = os.path.join(user_dir, storage_filename)
        os.rename(temp_path, permanent_path)

        # Metadata
        category = detect_category(orig_filename)
        mime_type, _ = mimetypes.guess_type(orig_filename)
        if not mime_type:
            mime_type = 'application/octet-stream'

        # Create File record
        new_file = File(
            user_id=user_id,
            folder_id=folder_id,
            name=orig_filename,
            original_name=orig_filename,
            category=category,
            mime_type=mime_type,
            file_size=file_size,
            file_hash=file_hash,
            storage_path=permanent_path,
            current_version=1,
            is_starred=False,
            is_deleted=False
        )
        self.db.add(new_file)
        self.db.flush()

        # Create FileVersion 1 record
        version_1 = FileVersion(
            file_id=new_file.id,
            version_num=1,
            file_size=file_size,
            file_hash=file_hash,
            storage_path=permanent_path,
            modified_by_device_id=device_id,
            change_summary='Initial upload'
        )
        self.db.add(version_1)

        # Update user's storage usage
        user.storage_used_bytes += file_size

        # Create DeviceSyncStates for all user devices
        devices = self.db.query(Device).filter_by(user_id=user_id).all()
        for dev in devices:
            # If uploaded from this device, it's already synced to v1. Otherwise, it's pending.
            is_source = (device_id and dev.id == device_id)
            sync_state = DeviceSyncState(
                device_id=dev.id,
                file_id=new_file.id,
                synced_version=1 if is_source else 0,
                status='synced' if is_source else 'pending',
                last_synced_at=datetime.utcnow() if is_source else None
            )
            self.db.add(sync_state)

        # Activity Log
        log = ActivityLog(
            user_id=user_id,
            action_type='upload',
            description=f"Uploaded '{orig_filename}' ({file_size // 1024} KB)" + (" [Duplicate content detected]" if duplicate else ""),
            device_id=device_id
        )
        self.db.add(log)

        # Notification
        notif = Notification(
            user_id=user_id,
            notification_type='upload',
            title='File Uploaded',
            message=f"'{orig_filename}' has been securely uploaded and versioned (v1)."
        )
        self.db.add(notif)

        self.db.commit()
        return new_file, bool(duplicate)

    def get_file(self, file_id, user_id=None, include_deleted=False):
        query = self.db.query(File).filter_by(id=file_id)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        if not include_deleted:
            query = query.filter_by(is_deleted=False)
        return query.first()

    def list_files(self, user_id, folder_id=None, category=None, search=None, is_starred=None, is_deleted=False, sort_by='date', sort_order='desc'):
        query = self.db.query(File).filter(File.user_id == user_id, File.is_deleted == is_deleted)

        if folder_id is not None:
            query = query.filter(File.folder_id == folder_id)
        elif not search and is_starred is None and category is None and not is_deleted:
            # Root directory files when not in a folder and not searching
            query = query.filter(File.folder_id == None)

        if category and category != 'all':
            query = query.filter(File.category == category)

        if is_starred is not None:
            query = query.filter(File.is_starred == is_starred)

        if search:
            search_term = f"%{search.strip().lower()}%"
            query = query.filter(File.name.ilike(search_term))

        # Sorting
        if sort_by == 'name':
            query = query.order_by(File.name.asc() if sort_order == 'asc' else File.name.desc())
        elif sort_by == 'size':
            query = query.order_by(File.file_size.asc() if sort_order == 'asc' else File.file_size.desc())
        else:  # date
            query = query.order_by(File.updated_at.asc() if sort_order == 'asc' else File.updated_at.desc())

        return query.all()

    def toggle_star(self, file_id, user_id):
        file = self.get_file(file_id, user_id)
        if not file:
            return None
        file.is_starred = not file.is_starred
        self.db.commit()
        return file

    def rename_file(self, file_id, new_name, user_id):
        file = self.get_file(file_id, user_id)
        if not file:
            return None
        old_name = file.name
        safe_name = secure_filename(new_name)
        if not safe_name:
            raise ValueError("Invalid file name")
        file.name = safe_name
        file.category = detect_category(safe_name)

        log = ActivityLog(
            user_id=user_id,
            action_type='file',
            description=f"Renamed '{old_name}' to '{safe_name}'"
        )
        self.db.add(log)
        self.db.commit()
        return file

    def move_file(self, file_id, target_folder_id, user_id):
        file = self.get_file(file_id, user_id)
        if not file:
            return None
        if target_folder_id:
            folder = self.db.query(Folder).filter_by(id=target_folder_id, user_id=user_id).first()
            if not folder:
                raise ValueError("Target folder not found")
        file.folder_id = target_folder_id

        log = ActivityLog(
            user_id=user_id,
            action_type='file',
            description=f"Moved '{file.name}' to {'root' if not target_folder_id else folder.name}"
        )
        self.db.add(log)
        self.db.commit()
        return file

    def delete_file(self, file_id, user_id, permanent=False):
        """Soft delete (trash) or permanently remove file and its version assets."""
        file = self.get_file(file_id, user_id, include_deleted=True)
        if not file:
            return False

        user = self.db.query(User).filter_by(id=user_id).first()

        if permanent:
            # Delete physical files of all versions
            for ver in file.versions:
                if os.path.exists(ver.storage_path):
                    try:
                        os.remove(ver.storage_path)
                    except OSError:
                        pass
            if os.path.exists(file.storage_path):
                try:
                    os.remove(file.storage_path)
                except OSError:
                    pass

            if user and user.storage_used_bytes >= file.file_size:
                user.storage_used_bytes -= file.file_size

            log = ActivityLog(
                user_id=user_id,
                action_type='delete',
                description=f"Permanently deleted '{file.name}'"
            )
            self.db.add(log)
            self.db.delete(file)
        else:
            file.is_deleted = True
            log = ActivityLog(
                user_id=user_id,
                action_type='delete',
                description=f"Moved '{file.name}' to Trash"
            )
            self.db.add(log)

        self.db.commit()
        return True

    def restore_from_trash(self, file_id, user_id):
        file = self.get_file(file_id, user_id, include_deleted=True)
        if not file:
            return None
        file.is_deleted = False
        for sync_state in file.sync_states:
            sync_state.synced_version = 0
            sync_state.status = 'pending'
            sync_state.last_synced_at = None

        log = ActivityLog(
            user_id=user_id,
            action_type='restore',
            description=f"Restored '{file.name}' from Trash"
        )
        self.db.add(log)

        notification = Notification(
            user_id=user_id,
            notification_type='sync',
            title='File restored',
            message=f"'{file.name}' was restored and is ready to sync to your devices."
        )
        self.db.add(notification)

        self.db.commit()
        return file

    def get_preview_data(self, file_id, user_id):
        """Retrieve preview data for modal viewing."""
        file = self.get_file(file_id, user_id)
        if not file or not os.path.exists(file.storage_path):
            return None, "File content not found on server."

        # Preview determination
        is_text = file.category == 'documents' and file.mime_type in [
            'text/plain', 'text/markdown', 'text/html', 'text/css', 'text/javascript',
            'application/json', 'application/xml', 'text/x-python', 'text/csv'
        ] or file.name.endswith(('.txt', '.md', '.json', '.py', '.js', '.html', '.css', '.csv', '.sql', '.log'))

        if is_text:
            try:
                with open(file.storage_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(150000)  # Max 150KB for preview
                return {
                    'type': 'text',
                    'file': file.to_dict(),
                    'content': content
                }, None
            except Exception as e:
                return None, f"Error reading text content: {str(e)}"

        if file.category == 'images':
            return {
                'type': 'image',
                'file': file.to_dict(),
                'url': f"/api/files/{file.id}/download"
            }, None

        if file.mime_type == 'application/pdf' or file.name.endswith('.pdf'):
            return {
                'type': 'pdf',
                'file': file.to_dict(),
                'url': f"/api/files/{file.id}/download"
            }, None

        if file.category == 'audio':
            return {
                'type': 'audio',
                'file': file.to_dict(),
                'url': f"/api/files/{file.id}/download"
            }, None

        if file.category == 'videos':
            return {
                'type': 'video',
                'file': file.to_dict(),
                'url': f"/api/files/{file.id}/download"
            }, None

        return {
            'type': 'generic',
            'file': file.to_dict(),
            'message': 'Direct preview not supported for this file type. Please download to view.'
        }, None
