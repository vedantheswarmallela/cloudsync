import os
import uuid
from datetime import datetime
from models import File, FileVersion, Device, DeviceSyncState, ActivityLog, Notification, User
from services.storage_service import StorageService

class VersionService:
    def __init__(self, db_session, upload_base_folder, storage=None):
        self.db = db_session
        self.upload_base_folder = upload_base_folder
        self.storage = storage or StorageService(upload_base_folder)

    def get_file_versions(self, file_id, user_id):
        file = self.db.query(File).filter_by(id=file_id, user_id=user_id).first()
        if not file:
            return None, []
        versions = self.db.query(FileVersion).filter_by(file_id=file.id).order_by(FileVersion.version_num.desc()).all()
        return file, versions

    def create_new_version(self, file_id, user_id, new_file_path, new_hash, new_size, device_id=None, change_summary='Updated file'):
        """Creates an incremental version of an existing file."""
        file = self.db.query(File).filter_by(id=file_id, user_id=user_id).first()
        if not file:
            raise ValueError("File not found.")

        user = self.db.query(User).filter_by(id=user_id).first()
        if not user:
            raise ValueError("User not found.")

        size_difference = new_size - file.file_size
        if size_difference > 0 and user.storage_used_bytes + size_difference > user.storage_quota_bytes:
            free_space = max(0, user.storage_quota_bytes - user.storage_used_bytes)
            raise ValueError(
                f"Storage quota exceeded. Free space: {free_space / (1024 * 1024):.1f} MB, "
                f"File size increase: {size_difference / (1024 * 1024):.1f} MB."
            )

        storage_filename = f"v{file.current_version + 1}_{file.name}"
        permanent_path = self.storage.store_file(new_file_path, user_id, storage_filename)

        new_version_num = file.current_version + 1

        version_record = FileVersion(
            file_id=file.id,
            version_num=new_version_num,
            file_size=new_size,
            file_hash=new_hash,
            storage_path=permanent_path,
            modified_by_device_id=device_id,
            change_summary=change_summary
        )
        self.db.add(version_record)

        # Update file master record
        file.current_version = new_version_num
        file.file_size = new_size
        file.file_hash = new_hash
        file.storage_path = permanent_path
        file.updated_at = datetime.utcnow()
        user.storage_used_bytes += size_difference

        # Update sync states for devices: modifying device is synced, others become pending
        sync_states = self.db.query(DeviceSyncState).filter_by(file_id=file.id).all()
        for state in sync_states:
            if device_id and state.device_id == device_id:
                state.synced_version = new_version_num
                state.status = 'synced'
                state.last_synced_at = datetime.utcnow()
            else:
                state.status = 'pending'

        log = ActivityLog(
            user_id=user_id,
            action_type='version',
            description=f"Created version v{new_version_num} of '{file.name}' ({change_summary})",
            device_id=device_id
        )
        self.db.add(log)

        notification = Notification(
            user_id=user_id,
            notification_type='sync',
            title='File updated',
            message=f"'{file.name}' was updated to v{new_version_num}; other devices may need to sync."
        )
        self.db.add(notification)

        self.db.commit()
        return file, version_record

    def restore_version(self, file_id, target_version_num, user_id, device_id=None):
        """
        Restores a previous version by creating a BRAND NEW incremented version,
        NEVER overwriting the historical records.
        """
        file = self.db.query(File).filter_by(id=file_id, user_id=user_id).first()
        if not file:
            raise ValueError("File not found.")

        target_ver = self.db.query(FileVersion).filter_by(file_id=file.id, version_num=target_version_num).first()
        if not target_ver:
            raise ValueError(f"Version {target_version_num} does not exist for this file.")

        if not self.storage.file_exists(target_ver.storage_path):
            raise ValueError("Historical file data is not available on disk.")

        # Create new version increment
        new_version_num = file.current_version + 1
        storage_filename = f"v{new_version_num}_restored_{file.name}"
        permanent_path = self.storage.copy_file(
            target_ver.storage_path,
            user_id,
            storage_filename,
        )

        new_version_record = FileVersion(
            file_id=file.id,
            version_num=new_version_num,
            file_size=target_ver.file_size,
            file_hash=target_ver.file_hash,
            storage_path=permanent_path,
            modified_by_device_id=device_id,
            change_summary=f"Restored from version {target_version_num}"
        )
        self.db.add(new_version_record)

        # Update master file
        file.current_version = new_version_num
        file.file_size = target_ver.file_size
        file.file_hash = target_ver.file_hash
        file.storage_path = permanent_path
        file.updated_at = datetime.utcnow()

        # Flag all devices as pending sync to receive restored version
        sync_states = self.db.query(DeviceSyncState).filter_by(file_id=file.id).all()
        for state in sync_states:
            state.status = 'pending'

        log = ActivityLog(
            user_id=user_id,
            action_type='restore',
            description=f"Restored '{file.name}' from v{target_version_num} -> New version v{new_version_num} generated.",
            device_id=device_id
        )
        self.db.add(log)

        notif = Notification(
            user_id=user_id,
            notification_type='sync',
            title='Version Restored',
            message=f"'{file.name}' restored to v{target_version_num} content as new version v{new_version_num}."
        )
        self.db.add(notif)

        self.db.commit()
        return file, new_version_record
