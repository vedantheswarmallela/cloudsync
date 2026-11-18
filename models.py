import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Boolean,
    DateTime, ForeignKey, Text, Float
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default='user')  # 'user', 'admin'
    storage_quota_bytes = Column(BigInteger, default=5 * 1024 * 1024 * 1024)  # 5GB
    storage_used_bytes = Column(BigInteger, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    files = relationship('File', back_populates='user', cascade='all, delete-orphan')
    folders = relationship('Folder', back_populates='user', cascade='all, delete-orphan')
    devices = relationship('Device', back_populates='user', cascade='all, delete-orphan')
    activity_logs = relationship('ActivityLog', back_populates='user', cascade='all, delete-orphan')
    notifications = relationship('Notification', back_populates='user', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'storage_quota_bytes': self.storage_quota_bytes,
            'storage_used_bytes': self.storage_used_bytes,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Folder(Base):
    __tablename__ = 'folders'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    parent_id = Column(Integer, ForeignKey('folders.id', ondelete='CASCADE'), nullable=True)
    color = Column(String(30), default='#6366f1')
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='folders')
    files = relationship('File', back_populates='folder', cascade='all, delete-orphan')
    children = relationship('Folder', backref='parent', remote_side=[id])

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'parent_id': self.parent_id,
            'color': self.color,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class File(Base):
    __tablename__ = 'files'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey('folders.id', ondelete='SET NULL'), nullable=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    original_name = Column(String(255), nullable=False)
    category = Column(String(50), default='others')  # documents, images, videos, audio, archives, others
    mime_type = Column(String(100), default='application/octet-stream')
    file_size = Column(BigInteger, default=0)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    storage_path = Column(String(500), nullable=False)
    current_version = Column(Integer, default=1)
    is_starred = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)  # Soft delete for trash
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User', back_populates='files')
    folder = relationship('Folder', back_populates='files')
    versions = relationship('FileVersion', back_populates='file', cascade='all, delete-orphan', order_by='FileVersion.version_num.desc()')
    sync_states = relationship('DeviceSyncState', back_populates='file', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'folder_id': self.folder_id,
            'folder_name': self.folder.name if self.folder else None,
            'name': self.name,
            'original_name': self.original_name,
            'category': self.category,
            'mime_type': self.mime_type,
            'file_size': self.file_size,
            'file_hash': self.file_hash,
            'current_version': self.current_version,
            'is_starred': self.is_starred,
            'is_deleted': self.is_deleted,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class FileVersion(Base):
    __tablename__ = 'file_versions'

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False, index=True)
    version_num = Column(Integer, nullable=False)
    file_size = Column(BigInteger, default=0)
    file_hash = Column(String(64), nullable=False)
    storage_path = Column(String(500), nullable=False)
    modified_by_device_id = Column(Integer, ForeignKey('devices.id', ondelete='SET NULL'), nullable=True)
    change_summary = Column(String(255), default='File updated')
    created_at = Column(DateTime, default=datetime.utcnow)

    file = relationship('File', back_populates='versions')
    device = relationship('Device')

    def to_dict(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'file_name': self.file.name if self.file else '',
            'version_num': self.version_num,
            'file_size': self.file_size,
            'file_hash': self.file_hash,
            'modified_by_device_id': self.modified_by_device_id,
            'device_name': self.device.name if self.device else 'Web Interface',
            'change_summary': self.change_summary,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Device(Base):
    __tablename__ = 'devices'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    device_type = Column(String(50), default='Desktop')  # Desktop, Laptop, Mobile, Tablet, Server
    os_info = Column(String(100), default='Unknown OS')
    ip_address = Column(String(50), default='127.0.0.1')
    is_online = Column(Boolean, default=True)
    last_synced_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='devices')
    sync_states = relationship('DeviceSyncState', back_populates='device', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'device_type': self.device_type,
            'os_info': self.os_info,
            'ip_address': self.ip_address,
            'is_online': self.is_online,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class DeviceSyncState(Base):
    __tablename__ = 'device_sync_states'

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False, index=True)
    synced_version = Column(Integer, default=0)
    status = Column(String(30), default='synced')  # synced, pending, conflict, failed
    last_synced_at = Column(DateTime, default=datetime.utcnow)

    device = relationship('Device', back_populates='sync_states')
    file = relationship('File', back_populates='sync_states')

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device.name if self.device else '',
            'file_id': self.file_id,
            'file_name': self.file.name if self.file else '',
            'synced_version': self.synced_version,
            'current_file_version': self.file.current_version if self.file else 0,
            'status': self.status,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None
        }


class ConflictReport(Base):
    __tablename__ = 'conflict_reports'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='SET NULL'), nullable=True)
    original_version = Column(Integer, nullable=False)
    conflicted_version = Column(Integer, nullable=False)
    conflicted_file_id = Column(Integer, ForeignKey('files.id', ondelete='SET NULL'), nullable=True)
    status = Column(String(30), default='unresolved')  # unresolved, resolved_server, resolved_device, resolved_both
    details = Column(Text, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship('User')
    original_file = relationship('File', foreign_keys=[file_id])
    conflicted_file = relationship('File', foreign_keys=[conflicted_file_id])
    device = relationship('Device')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'file_id': self.file_id,
            'file_name': self.original_file.name if self.original_file else 'Unknown File',
            'device_id': self.device_id,
            'device_name': self.device.name if self.device else 'Unknown Device',
            'original_version': self.original_version,
            'conflicted_version': self.conflicted_version,
            'conflicted_file_id': self.conflicted_file_id,
            'conflicted_file_name': self.conflicted_file.name if self.conflicted_file else None,
            'status': self.status,
            'details': self.details,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None
        }


class ActivityLog(Base):
    __tablename__ = 'activity_logs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)  # upload, download, delete, sync, conflict, restore, device, auth
    description = Column(String(255), nullable=False)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='SET NULL'), nullable=True)
    ip_address = Column(String(50), default='127.0.0.1')
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='activity_logs')
    device = relationship('Device')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'action_type': self.action_type,
            'description': self.description,
            'device_id': self.device_id,
            'device_name': self.device.name if self.device else None,
            'ip_address': self.ip_address,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Notification(Base):
    __tablename__ = 'notifications'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    notification_type = Column(String(50), default='info')  # upload, sync, conflict, storage, device, info
    title = Column(String(100), nullable=False)
    message = Column(String(255), nullable=False)
    is_read = Column(Boolean, default=False)
    link = Column(String(255), default='')
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='notifications')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'type': self.notification_type,
            'title': self.title,
            'message': self.message,
            'is_read': self.is_read,
            'link': self.link,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# Database session helpers
def init_database(db_url):
    """Initialize database and return scoped session maker."""
    # Ensure directory exists for sqlite files
    if db_url.startswith('sqlite:///') and not db_url.startswith('sqlite:///:memory:'):
        db_path = db_url.replace('sqlite:///', '')
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        
    engine = create_engine(db_url, connect_args={'check_same_thread': False} if db_url.startswith('sqlite') else {})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db_session = scoped_session(session_factory)
    return engine, db_session
