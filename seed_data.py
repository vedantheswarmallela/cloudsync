import os
import shutil
import zipfile
import hashlib
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from models import (
    init_database, User, Folder, File, FileVersion, Device,
    DeviceSyncState, ConflictReport, ActivityLog, Notification
)
from services.file_service import detect_category, compute_sha256

def generate_svg_banner():
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" width="100%" height="100%">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#4f46e5" />
      <stop offset="50%" stop-color="#7c3aed" />
      <stop offset="100%" stop-color="#2563eb" />
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="8" result="coloredBlur"/>
      <feMerge>
        <feMergeNode in="coloredBlur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <rect width="800" height="450" rx="24" fill="url(#bgGrad)"/>
  <circle cx="400" cy="225" r="140" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="2" stroke-dasharray="6 6"/>
  <circle cx="400" cy="225" r="100" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="3"/>
  <g transform="translate(340, 165) scale(2.5)" fill="white" filter="url(#glow)">
    <path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5h-3z"/>
  </g>
  <text x="400" y="340" font-family="system-ui, -apple-system, sans-serif" font-size="28" font-weight="bold" fill="white" text-anchor="middle">CloudSync Pro 2026</text>
  <text x="400" y="375" font-family="system-ui, -apple-system, sans-serif" font-size="16" fill="rgba(255,255,255,0.8)" text-anchor="middle">Next-Gen Intelligent File Synchronization Engine</text>
</svg>"""

def create_sample_zip(dest_path):
    with zipfile.ZipFile(dest_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "CloudSync Pro Archive Sample Data\nSecurely compressed and verified.")
        zf.writestr("manifest.json", '{"project": "CloudSync Pro", "version": "2.4.0", "status": "active"}')

def create_sample_wav(dest_path):
    # Minimal 44-byte standard RIFF WAV header for valid audio playback
    riff = b'RIFF'
    size = (36).to_bytes(4, 'little')
    wave = b'WAVEfmt '
    subchunk1size = (16).to_bytes(4, 'little')
    audio_format = (1).to_bytes(2, 'little')
    num_channels = (1).to_bytes(2, 'little')
    sample_rate = (8000).to_bytes(4, 'little')
    byte_rate = (8000).to_bytes(4, 'little')
    block_align = (1).to_bytes(2, 'little')
    bits_per_sample = (8).to_bytes(2, 'little')
    data_hdr = b'data'
    data_size = (0).to_bytes(4, 'little')
    wav_content = riff + size + wave + subchunk1size + audio_format + num_channels + sample_rate + byte_rate + block_align + bits_per_sample + data_hdr + data_size
    with open(dest_path, 'wb') as f:
        f.write(wav_content)

def seed_database(db_url, upload_base_dir):
    engine, db = init_database(db_url)
    os.makedirs(upload_base_dir, exist_ok=True)

    # Clean existing data for a fresh seed
    for tbl in reversed(models_order := [
        Notification, ActivityLog, ConflictReport, DeviceSyncState,
        FileVersion, File, Folder, Device, User
    ]):
        db.query(tbl).delete()
    db.commit()

    # 1. Create Admin User
    admin = User(
        username="admin",
        email="admin@cloudsync.pro",
        role="admin",
        storage_quota_bytes=50 * 1024 * 1024 * 1024,  # 50 GB
        storage_used_bytes=0,
        is_active=True,
        created_at=datetime.utcnow() - timedelta(days=60)
    )
    admin.set_password("admin123")
    db.add(admin)

    # 2. Create Demo User
    demo_user = User(
        username="alex_rivers",
        email="demo@cloudsync.pro",
        role="user",
        storage_quota_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
        storage_used_bytes=0,
        is_active=True,
        created_at=datetime.utcnow() - timedelta(days=45)
    )
    demo_user.set_password("demo123")
    db.add(demo_user)
    db.flush()

    # User uploads directory
    demo_upload_dir = os.path.join(upload_base_dir, str(demo_user.id))
    os.makedirs(demo_upload_dir, exist_ok=True)

    # 3. Create Folders for Demo User
    f_work = Folder(user_id=demo_user.id, name="Work Documents", color="#4f46e5")
    f_design = Folder(user_id=demo_user.id, name="Design Assets", color="#9333ea")
    f_code = Folder(user_id=demo_user.id, name="Source Code", color="#059669")
    f_personal = Folder(user_id=demo_user.id, name="Personal Media", color="#d97706")
    db.add_all([f_work, f_design, f_code, f_personal])
    db.flush()

    # 4. Create Devices
    d_macbook = Device(
        user_id=demo_user.id,
        name="MacBook Pro 16",
        device_type="Laptop",
        os_info="macOS Sonoma 14.5",
        ip_address="192.168.1.101",
        is_online=True,
        last_synced_at=datetime.utcnow() - timedelta(minutes=15)
    )
    d_iphone = Device(
        user_id=demo_user.id,
        name="iPhone 15 Pro",
        device_type="Mobile",
        os_info="iOS 17.5.1",
        ip_address="192.168.1.120",
        is_online=True,
        last_synced_at=datetime.utcnow() - timedelta(hours=2)
    )
    d_pc = Device(
        user_id=demo_user.id,
        name="Dev Workstation PC",
        device_type="Desktop",
        os_info="Windows 11 Pro",
        ip_address="192.168.1.150",
        is_online=True,
        last_synced_at=datetime.utcnow() - timedelta(minutes=40)
    )
    d_ipad = Device(
        user_id=demo_user.id,
        name="iPad Pro M4",
        device_type="Tablet",
        os_info="iPadOS 17.5",
        ip_address="192.168.1.135",
        is_online=False,
        last_synced_at=datetime.utcnow() - timedelta(days=2)
    )
    db.add_all([d_macbook, d_iphone, d_pc, d_ipad])
    db.flush()

    # 5. Create Realistic Physical Sample Files
    files_to_create = [
        {
            'name': 'CloudSync_Architecture_Overview.md',
            'folder': f_work,
            'content': """# CloudSync Pro Architecture Specification

## Overview
CloudSync Pro delivers zero-latency multi-device synchronization with distributed conflict detection and non-destructive version lineage.

### Key Tenets
1. **Intelligent Version Lineage**: Incremental integer versions protect file history.
2. **Deterministic Conflict Resolution**: Outdated pushes create isolated branch copies rather than overwriting master files.
3. **SHA-256 Checksum Validation**: Integrity guaranteed on every upload and download step.
4. **Multi-Platform Support**: Mac, Windows, iOS, Android, and Cloud Daemons.
""",
            'category': 'documents',
            'mime': 'text/markdown',
            'device': d_macbook
        },
        {
            'name': 'sync_engine_daemon.py',
            'folder': f_code,
            'content': """import time
import hashlib
import requests

class SyncDaemon:
    def __init__(self, endpoint, api_token):
        self.endpoint = endpoint
        self.token = api_token

    def poll_updates(self):
        print(f"[SyncDaemon] Polling server {self.endpoint} for state changes...")
        return {"status": "ok", "changes_detected": False}

if __name__ == '__main__':
    daemon = SyncDaemon("https://api.cloudsync.pro/v1", "cs_token_88291a")
    daemon.poll_updates()
""",
            'category': 'documents',
            'mime': 'text/x-python',
            'device': d_pc
        },
        {
            'name': 'production_environment.json',
            'folder': f_work,
            'content': """{
  "application": "CloudSync Pro Enterprise",
  "version": "2.4.1",
  "cluster": "us-east-prod-01",
  "replication": {
    "nodes": 3,
    "strategy": "multi-zone-active-active"
  },
  "storage_engine": "hybrid-block-cache",
  "encryption": "AES-256-GCM"
}""",
            'category': 'documents',
            'mime': 'application/json',
            'device': d_macbook
        },
        {
            'name': 'cloudsync_brand_banner.svg',
            'folder': f_design,
            'content': generate_svg_banner(),
            'category': 'images',
            'mime': 'image/svg+xml',
            'device': d_macbook
        }
    ]

    total_storage = 0
    created_files = []

    for item in files_to_create:
        disk_filename = f"seed_{item['name']}"
        disk_path = os.path.join(demo_upload_dir, disk_filename)
        with open(disk_path, 'w', encoding='utf-8') as f:
            f.write(item['content'])

        size = os.path.getsize(disk_path)
        fhash = compute_sha256(disk_path)
        total_storage += size

        f_model = File(
            user_id=demo_user.id,
            folder_id=item['folder'].id,
            name=item['name'],
            original_name=item['name'],
            category=item['category'],
            mime_type=item['mime'],
            file_size=size,
            file_hash=fhash,
            storage_path=disk_path,
            current_version=1,
            is_starred=True if item['name'].endswith('.md') else False,
            is_deleted=False
        )
        db.add(f_model)
        db.flush()

        v1 = FileVersion(
            file_id=f_model.id,
            version_num=1,
            file_size=size,
            file_hash=fhash,
            storage_path=disk_path,
            modified_by_device_id=item['device'].id,
            change_summary='Initial document release'
        )
        db.add(v1)
        created_files.append(f_model)

    # 6. Add Binary Zip & Audio files
    zip_path = os.path.join(demo_upload_dir, "seed_Project_Assets_Bundle.zip")
    create_sample_zip(zip_path)
    zip_size = os.path.getsize(zip_path)
    zip_hash = compute_sha256(zip_path)
    total_storage += zip_size

    zip_file = File(
        user_id=demo_user.id,
        folder_id=f_work.id,
        name="Project_Assets_Bundle.zip",
        original_name="Project_Assets_Bundle.zip",
        category="archives",
        mime_type="application/zip",
        file_size=zip_size,
        file_hash=zip_hash,
        storage_path=zip_path,
        current_version=1,
        is_starred=False,
        is_deleted=False
    )
    db.add(zip_file)
    db.flush()
    db.add(FileVersion(
        file_id=zip_file.id, version_num=1, file_size=zip_size,
        file_hash=zip_hash, storage_path=zip_path,
        modified_by_device_id=d_pc.id, change_summary="Weekly archive snapshot"
    ))
    created_files.append(zip_file)

    wav_path = os.path.join(demo_upload_dir, "seed_voice_memo_meeting.wav")
    create_sample_wav(wav_path)
    wav_size = os.path.getsize(wav_path)
    wav_hash = compute_sha256(wav_path)
    total_storage += wav_size

    wav_file = File(
        user_id=demo_user.id,
        folder_id=f_personal.id,
        name="Voice_Memo_Architecture_Review.wav",
        original_name="Voice_Memo_Architecture_Review.wav",
        category="audio",
        mime_type="audio/wav",
        file_size=wav_size,
        file_hash=wav_hash,
        storage_path=wav_path,
        current_version=1,
        is_starred=False,
        is_deleted=False
    )
    db.add(wav_file)
    db.flush()
    db.add(FileVersion(
        file_id=wav_file.id, version_num=1, file_size=wav_size,
        file_hash=wav_hash, storage_path=wav_path,
        modified_by_device_id=d_iphone.id, change_summary="Recorded from iPhone"
    ))
    created_files.append(wav_file)

    # 7. Multi-Version File Demo: Product_Roadmap_2026.md (Versions 1, 2, and 3)
    roadmap_v1_path = os.path.join(demo_upload_dir, "seed_roadmap_v1.md")
    with open(roadmap_v1_path, 'w', encoding='utf-8') as f:
        f.write("# Product Roadmap 2026 - Q1 Initial Planning\n- Setup Core Engine\n- Basic Uploads")

    roadmap_v2_path = os.path.join(demo_upload_dir, "seed_roadmap_v2.md")
    with open(roadmap_v2_path, 'w', encoding='utf-8') as f:
        f.write("# Product Roadmap 2026 - Q2 Revision\n- Setup Core Engine\n- Intelligent Sync Engine\n- Conflict Management Engine")

    roadmap_v3_path = os.path.join(demo_upload_dir, "seed_roadmap_v3.md")
    with open(roadmap_v3_path, 'w', encoding='utf-8') as f:
        f.write("# Product Roadmap 2026 - Q3 Final Enterprise Spec\n- Setup Core Engine\n- Intelligent Sync Engine\n- Conflict Management Engine\n- Real-Time Admin Telemetry\n- Chart.js Visual Storage Analytics")

    size_v3 = os.path.getsize(roadmap_v3_path)
    hash_v3 = compute_sha256(roadmap_v3_path)
    total_storage += size_v3

    roadmap_file = File(
        user_id=demo_user.id,
        folder_id=f_work.id,
        name="Product_Roadmap_2026.md",
        original_name="Product_Roadmap_2026.md",
        category="documents",
        mime_type="text/markdown",
        file_size=size_v3,
        file_hash=hash_v3,
        storage_path=roadmap_v3_path,
        current_version=3,
        is_starred=True,
        is_deleted=False
    )
    db.add(roadmap_file)
    db.flush()

    db.add(FileVersion(
        file_id=roadmap_file.id, version_num=1, file_size=os.path.getsize(roadmap_v1_path),
        file_hash=compute_sha256(roadmap_v1_path), storage_path=roadmap_v1_path,
        modified_by_device_id=d_macbook.id, change_summary="Initial roadmap draft (Q1)",
        created_at=datetime.utcnow() - timedelta(days=10)
    ))
    db.add(FileVersion(
        file_id=roadmap_file.id, version_num=2, file_size=os.path.getsize(roadmap_v2_path),
        file_hash=compute_sha256(roadmap_v2_path), storage_path=roadmap_v2_path,
        modified_by_device_id=d_pc.id, change_summary="Added sync & conflict modules (Q2)",
        created_at=datetime.utcnow() - timedelta(days=4)
    ))
    db.add(FileVersion(
        file_id=roadmap_file.id, version_num=3, file_size=size_v3,
        file_hash=hash_v3, storage_path=roadmap_v3_path,
        modified_by_device_id=d_macbook.id, change_summary="Final enterprise analytics spec (Q3)",
        created_at=datetime.utcnow() - timedelta(hours=3)
    ))
    created_files.append(roadmap_file)

    # 8. Seed Device Sync States
    # MacBook Pro is fully synced
    for cf in created_files:
        db.add(DeviceSyncState(
            device_id=d_macbook.id, file_id=cf.id,
            synced_version=cf.current_version, status='synced',
            last_synced_at=datetime.utcnow() - timedelta(minutes=15)
        ))

    # Dev Workstation is fully synced
    for cf in created_files:
        db.add(DeviceSyncState(
            device_id=d_pc.id, file_id=cf.id,
            synced_version=cf.current_version, status='synced',
            last_synced_at=datetime.utcnow() - timedelta(minutes=40)
        ))

    # iPhone has 1 pending sync (roadmap_file was updated to v3)
    for cf in created_files:
        v_synced = 2 if cf.id == roadmap_file.id else cf.current_version
        status = 'pending' if v_synced < cf.current_version else 'synced'
        db.add(DeviceSyncState(
            device_id=d_iphone.id, file_id=cf.id,
            synced_version=v_synced, status=status,
            last_synced_at=datetime.utcnow() - timedelta(hours=2)
        ))

    # iPad Pro is offline with pending syncs
    for cf in created_files:
        db.add(DeviceSyncState(
            device_id=d_ipad.id, file_id=cf.id,
            synced_version=1, status='pending',
            last_synced_at=datetime.utcnow() - timedelta(days=2)
        ))

    # 9. Seed Active Conflict Demo
    # Conflicted copy created from iPad Pro modifying roadmap while still on v1
    conflict_copy_path = os.path.join(demo_upload_dir, "seed_roadmap_conflicted_iPadPro.md")
    with open(conflict_copy_path, 'w', encoding='utf-8') as f:
        f.write("# Product Roadmap 2026 (iPad Pro Offline Changes)\n- Edited while traveling\n- Added Mobile SDK specs")

    conflicted_size = os.path.getsize(conflict_copy_path)
    conflicted_hash = compute_sha256(conflict_copy_path)
    total_storage += conflicted_size

    conflicted_file_entry = File(
        user_id=demo_user.id,
        folder_id=f_work.id,
        name="Product_Roadmap_2026_conflicted_iPadPro_20261117.md",
        original_name="Product_Roadmap_2026.md",
        category="documents",
        mime_type="text/markdown",
        file_size=conflicted_size,
        file_hash=conflicted_hash,
        storage_path=conflict_copy_path,
        current_version=1,
        is_starred=False,
        is_deleted=False
    )
    db.add(conflicted_file_entry)
    db.flush()

    report = ConflictReport(
        user_id=demo_user.id,
        file_id=roadmap_file.id,
        device_id=d_ipad.id,
        original_version=3,
        conflicted_version=2,
        conflicted_file_id=conflicted_file_entry.id,
        status='unresolved',
        details="Device 'iPad Pro M4' attempted to push modifications based on outdated version 1 while server is already at version 3.",
        created_at=datetime.utcnow() - timedelta(hours=1)
    )
    db.add(report)

    # 10. Update user storage used
    demo_user.storage_used_bytes = total_storage

    # 11. Seed Activity Logs
    db.add_all([
        ActivityLog(user_id=demo_user.id, action_type='auth', description="User logged in from 'MacBook Pro 16' (192.168.1.101)", device_id=d_macbook.id, created_at=datetime.utcnow() - timedelta(days=5)),
        ActivityLog(user_id=demo_user.id, action_type='upload', description="Uploaded 'CloudSync_Architecture_Overview.md' (v1)", device_id=d_macbook.id, created_at=datetime.utcnow() - timedelta(days=4)),
        ActivityLog(user_id=demo_user.id, action_type='upload', description="Uploaded 'sync_engine_daemon.py' (v1)", device_id=d_pc.id, created_at=datetime.utcnow() - timedelta(days=3)),
        ActivityLog(user_id=demo_user.id, action_type='version', description="Updated 'Product_Roadmap_2026.md' to v3", device_id=d_macbook.id, created_at=datetime.utcnow() - timedelta(hours=3)),
        ActivityLog(user_id=demo_user.id, action_type='sync', description="Device 'Dev Workstation PC' completed full synchronization (6 files)", device_id=d_pc.id, created_at=datetime.utcnow() - timedelta(minutes=40)),
        ActivityLog(user_id=demo_user.id, action_type='conflict', description="Conflict detected on 'Product_Roadmap_2026.md' from 'iPad Pro M4'!", device_id=d_ipad.id, created_at=datetime.utcnow() - timedelta(hours=1)),
    ])

    # 12. Seed Notifications
    db.add_all([
        Notification(user_id=demo_user.id, notification_type='conflict', title='Sync Conflict Detected', message="Outdated push on 'Product_Roadmap_2026.md' from iPad Pro M4.", is_read=False, link='/dashboard#conflicts'),
        Notification(user_id=demo_user.id, notification_type='sync', title='Sync Required', message="'iPhone 15 Pro' has 1 pending file update available.", is_read=False, link='/dashboard#sync'),
        Notification(user_id=demo_user.id, notification_type='upload', title='File Uploaded', message="'cloudsync_brand_banner.svg' uploaded successfully.", is_read=True, link='/dashboard#files'),
    ])

    db.commit()
    print("Database successfully seeded with pristine demo data!")

if __name__ == '__main__':
    from config import Config
    seed_database(Config.SQLALCHEMY_DATABASE_URI, Config.UPLOAD_FOLDER)
