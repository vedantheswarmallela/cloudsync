from datetime import datetime
from models import Device, DeviceSyncState, File, ActivityLog, Notification

class SyncEngine:
    def __init__(self, db_session):
        self.db = db_session

    def get_sync_summary(self, user_id):
        """Calculates global synchronization statistics for the user."""
        devices = self.db.query(Device).filter_by(user_id=user_id).all()
        device_ids = [d.id for d in devices]

        if not device_ids:
            return {
                'total_devices': 0,
                'online_devices': 0,
                'offline_devices': 0,
                'pending_sync_count': 0,
                'synced_count': 0,
                'last_synced_at': None,
                'status': 'optimal'
            }

        online_count = sum(1 for d in devices if d.is_online)
        offline_count = len(devices) - online_count

        # Pending records across all devices for active files
        pending_records = self.db.query(DeviceSyncState).join(File).filter(
            DeviceSyncState.device_id.in_(device_ids),
            File.is_deleted == False,
            DeviceSyncState.synced_version < File.current_version
        ).all()

        synced_records = self.db.query(DeviceSyncState).join(File).filter(
            DeviceSyncState.device_id.in_(device_ids),
            File.is_deleted == False,
            DeviceSyncState.synced_version >= File.current_version
        ).count()

        latest_sync = self.db.query(Device.last_synced_at).filter(
            Device.user_id == user_id,
            Device.last_synced_at != None
        ).order_by(Device.last_synced_at.desc()).first()

        last_sync_time = latest_sync[0].isoformat() if latest_sync and latest_sync[0] else None

        status = 'optimal'
        if len(pending_records) > 0:
            status = 'pending_sync'

        return {
            'total_devices': len(devices),
            'online_devices': online_count,
            'offline_devices': offline_count,
            'pending_sync_count': len(pending_records),
            'synced_count': synced_records,
            'last_synced_at': last_sync_time,
            'status': status
        }

    def get_pending_files(self, user_id):
        """Returns details on every file currently pending sync on any device."""
        devices = {d.id: d for d in self.db.query(Device).filter_by(user_id=user_id).all()}
        if not devices:
            return []

        pending_states = self.db.query(DeviceSyncState).join(File).filter(
            DeviceSyncState.device_id.in_(devices.keys()),
            File.is_deleted == False,
            DeviceSyncState.synced_version < File.current_version
        ).all()

        results = []
        for state in pending_states:
            dev = devices.get(state.device_id)
            results.append({
                'id': state.id,
                'file_id': state.file_id,
                'file_name': state.file.name,
                'file_size': state.file.file_size,
                'device_id': state.device_id,
                'device_name': dev.name if dev else 'Unknown',
                'device_type': dev.device_type if dev else 'Desktop',
                'is_online': dev.is_online if dev else False,
                'synced_version': state.synced_version,
                'target_version': state.file.current_version,
                'status': 'offline_queued' if (dev and not dev.is_online) else 'ready_to_sync'
            })
        return results

    def sync_device(self, device_id, user_id):
        """Synchronizes a specific device with the latest server file versions."""
        device = self.db.query(Device).filter_by(id=device_id, user_id=user_id).first()
        if not device:
            raise ValueError("Device not found.")

        if not device.is_online:
            raise ValueError(f"Device '{device.name}' is currently offline. Turn it online to synchronize.")

        # Find all active files for this user
        files = self.db.query(File).filter_by(user_id=user_id, is_deleted=False).all()
        synced_count = 0

        for file in files:
            sync_state = self.db.query(DeviceSyncState).filter_by(device_id=device.id, file_id=file.id).first()
            if not sync_state:
                sync_state = DeviceSyncState(
                    device_id=device.id,
                    file_id=file.id,
                    synced_version=file.current_version,
                    status='synced',
                    last_synced_at=datetime.utcnow()
                )
                self.db.add(sync_state)
                synced_count += 1
            elif sync_state.synced_version < file.current_version:
                sync_state.synced_version = file.current_version
                sync_state.status = 'synced'
                sync_state.last_synced_at = datetime.utcnow()
                synced_count += 1

        device.last_synced_at = datetime.utcnow()

        # Log
        log = ActivityLog(
            user_id=user_id,
            action_type='sync',
            description=f"Synchronized {synced_count} file(s) to '{device.name}'",
            device_id=device.id
        )
        self.db.add(log)

        notif = Notification(
            user_id=user_id,
            notification_type='sync',
            title='Device Synchronized',
            message=f"'{device.name}' is now fully synchronized with {synced_count} file updates."
        )
        self.db.add(notif)

        self.db.commit()
        return {
            'device_id': device.id,
            'device_name': device.name,
            'files_synced': synced_count,
            'synced_at': device.last_synced_at.isoformat()
        }

    def sync_all(self, user_id):
        """Synchronizes all online devices for this user."""
        devices = self.db.query(Device).filter_by(user_id=user_id, is_online=True).all()
        total_synced_files = 0
        synced_devices = []

        files = self.db.query(File).filter_by(user_id=user_id, is_deleted=False).all()

        now = datetime.utcnow()
        for dev in devices:
            dev_synced = 0
            for file in files:
                sync_state = self.db.query(DeviceSyncState).filter_by(device_id=dev.id, file_id=file.id).first()
                if not sync_state:
                    sync_state = DeviceSyncState(
                        device_id=dev.id,
                        file_id=file.id,
                        synced_version=file.current_version,
                        status='synced',
                        last_synced_at=now
                    )
                    self.db.add(sync_state)
                    dev_synced += 1
                elif sync_state.synced_version < file.current_version:
                    sync_state.synced_version = file.current_version
                    sync_state.status = 'synced'
                    sync_state.last_synced_at = now
                    dev_synced += 1

            dev.last_synced_at = now
            total_synced_files += dev_synced
            synced_devices.append(dev.name)

        if devices:
            log = ActivityLog(
                user_id=user_id,
                action_type='sync',
                description=f"Global Sync All completed across {len(devices)} online devices ({total_synced_files} file sync actions)."
            )
            self.db.add(log)

            notif = Notification(
                user_id=user_id,
                notification_type='sync',
                title='Sync All Completed',
                message=f"Successfully synchronized {total_synced_files} updates across {len(devices)} online devices."
            )
            self.db.add(notif)

        self.db.commit()
        return {
            'synced_devices_count': len(devices),
            'synced_devices': synced_devices,
            'total_file_syncs': total_synced_files,
            'synced_at': now.isoformat()
        }
