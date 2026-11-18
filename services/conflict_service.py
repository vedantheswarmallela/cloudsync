import os
import shutil
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from models import ConflictReport, File, FileVersion, Device, ActivityLog, Notification, DeviceSyncState

class ConflictService:
    def __init__(self, db_session, upload_base_folder):
        self.db = db_session
        self.upload_base_folder = upload_base_folder

    def detect_and_handle_conflict(self, user_id, file_id, device_id, base_version, incoming_file_path, incoming_filename, incoming_hash, incoming_size):
        """
        Detects if an incoming modification from a device conflicts with the server version.
        If base_version < file.current_version:
          - Preserves latest server file.
          - Creates a separate conflicted copy file.
          - Creates ConflictReport.
          - Sends Notification.
          - Logs Activity.
        """
        file = self.db.query(File).filter_by(id=file_id, user_id=user_id).first()
        if not file:
            raise ValueError("File not found.")

        device = self.db.query(Device).filter_by(id=device_id, user_id=user_id).first()
        device_name = device.name if device else "Unknown Device"
        clean_dev_name = "".join(c for c in device_name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')

        if base_version < file.current_version:
            # Conflict detected!
            timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            name_parts = os.path.splitext(file.name)
            conflicted_filename = f"{name_parts[0]}_conflicted_{clean_dev_name}_{timestamp_str}{name_parts[1]}"

            user_dir = os.path.join(self.upload_base_folder, str(user_id))
            os.makedirs(user_dir, exist_ok=True)
            conflicted_storage_name = f"{uuid.uuid4().hex}_{conflicted_filename}"
            conflicted_permanent_path = os.path.join(user_dir, conflicted_storage_name)

            shutil.copy2(incoming_file_path, conflicted_permanent_path)

            # Register conflicted file as independent file
            conflicted_file = File(
                user_id=user_id,
                folder_id=file.folder_id,
                name=conflicted_filename,
                original_name=incoming_filename,
                category=file.category,
                mime_type=file.mime_type,
                file_size=incoming_size,
                file_hash=incoming_hash,
                storage_path=conflicted_permanent_path,
                current_version=1,
                is_starred=False,
                is_deleted=False
            )
            self.db.add(conflicted_file)
            self.db.flush()

            # Version record for conflicted file
            v1 = FileVersion(
                file_id=conflicted_file.id,
                version_num=1,
                file_size=incoming_size,
                file_hash=incoming_hash,
                storage_path=conflicted_permanent_path,
                modified_by_device_id=device_id,
                change_summary=f"Conflicted revision from {device_name}"
            )
            self.db.add(v1)

            # Conflict Report
            report = ConflictReport(
                user_id=user_id,
                file_id=file.id,
                device_id=device_id,
                original_version=file.current_version,
                conflicted_version=base_version + 1,
                conflicted_file_id=conflicted_file.id,
                status='unresolved',
                details=f"Device '{device_name}' attempted to update '{file.name}' from outdated base version {base_version} while server is already at version {file.current_version}."
            )
            self.db.add(report)

            # Update sync state for this device to show conflict status
            sync_state = self.db.query(DeviceSyncState).filter_by(device_id=device_id, file_id=file.id).first()
            if sync_state:
                sync_state.status = 'conflict'

            # Activity Log
            log = ActivityLog(
                user_id=user_id,
                action_type='conflict',
                description=f"Conflict detected on '{file.name}' from '{device_name}'! Created conflicted copy '{conflicted_filename}'.",
                device_id=device_id
            )
            self.db.add(log)

            # Notification
            notif = Notification(
                user_id=user_id,
                notification_type='conflict',
                title='Sync Conflict Detected',
                message=f"Conflict on '{file.name}' from {device_name}. Created separate copy '{conflicted_filename}'.",
                link='/dashboard#conflicts'
            )
            self.db.add(notif)

            self.db.commit()
            return True, report, conflicted_file
        else:
            return False, None, None

    def list_conflicts(self, user_id, status=None):
        query = self.db.query(ConflictReport).filter_by(user_id=user_id)
        if status:
            query = query.filter_by(status=status)
        return query.order_by(ConflictReport.created_at.desc()).all()

    def resolve_conflict(self, conflict_id, resolution_choice, user_id):
        """
        Resolves a conflict report:
        - 'keep_server': Discard conflicted copy file, retain server version.
        - 'keep_device': Promote conflicted file's content to be the new server version increment.
        - 'keep_both': Keep both files as permanent standalone files.
        """
        report = self.db.query(ConflictReport).filter_by(id=conflict_id, user_id=user_id).first()
        if not report:
            raise ValueError("Conflict report not found.")

        if report.status != 'unresolved':
            raise ValueError("Conflict already resolved.")

        original_file = report.original_file
        conflicted_file = report.conflicted_file

        if resolution_choice == 'keep_server':
            report.status = 'resolved_server'
            report.resolved_at = datetime.utcnow()
            if conflicted_file:
                # Soft delete or remove conflicted copy
                conflicted_file.is_deleted = True

            desc = f"Resolved conflict on '{original_file.name}' by keeping the server version."

        elif resolution_choice == 'keep_device':
            report.status = 'resolved_device'
            report.resolved_at = datetime.utcnow()

            if conflicted_file and original_file:
                # Promote device's file content to a new version increment of original file
                new_version_num = original_file.current_version + 1
                new_ver = FileVersion(
                    file_id=original_file.id,
                    version_num=new_version_num,
                    file_size=conflicted_file.file_size,
                    file_hash=conflicted_file.file_hash,
                    storage_path=conflicted_file.storage_path,
                    modified_by_device_id=report.device_id,
                    change_summary=f"Resolved conflict: Adopted revision from device '{report.device.name if report.device else 'Device'}'"
                )
                self.db.add(new_ver)
                original_file.current_version = new_version_num
                original_file.file_size = conflicted_file.file_size
                original_file.file_hash = conflicted_file.file_hash
                original_file.storage_path = conflicted_file.storage_path
                original_file.updated_at = datetime.utcnow()

                # Conflicted file record can now be removed as it is incorporated
                conflicted_file.is_deleted = True

            desc = f"Resolved conflict on '{original_file.name}' by adopting the device's revision (advanced to v{original_file.current_version})."

        elif resolution_choice == 'keep_both':
            report.status = 'resolved_both'
            report.resolved_at = datetime.utcnow()
            # Both files stay active and separate
            if conflicted_file:
                conflicted_file.is_deleted = False

            desc = f"Resolved conflict on '{original_file.name}' by keeping both versions as separate files."
        else:
            raise ValueError("Invalid resolution choice. Must be 'keep_server', 'keep_device', or 'keep_both'.")

        # Clear conflict state on devices
        if report.device_id and original_file:
            sync_state = self.db.query(DeviceSyncState).filter_by(device_id=report.device_id, file_id=original_file.id).first()
            if sync_state:
                sync_state.status = 'synced'
                sync_state.synced_version = original_file.current_version

        log = ActivityLog(
            user_id=user_id,
            action_type='conflict',
            description=desc,
            device_id=report.device_id
        )
        self.db.add(log)

        notif = Notification(
            user_id=user_id,
            notification_type='conflict',
            title='Conflict Resolved',
            message=desc
        )
        self.db.add(notif)

        self.db.commit()
        return report
