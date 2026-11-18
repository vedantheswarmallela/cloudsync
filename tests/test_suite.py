import unittest
import os
import sys
import io
import shutil
import tempfile
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from werkzeug.security import check_password_hash
from pypdf import PdfReader, PdfWriter
from models import init_database, User, File, FileVersion, Device, DeviceSyncState, ConflictReport, ActivityLog
from services.file_service import FileService
from services.pdf_service import validate_pdf_file
from services.sync_engine import SyncEngine
from services.conflict_service import ConflictService
from services.storage_service import StorageService
from services.version_service import VersionService

class CloudSyncProTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="cloudsync_test_")
        cls.upload_dir = os.path.join(cls.test_dir, "uploads")
        os.makedirs(cls.upload_dir, exist_ok=True)
        db_path = os.path.join(cls.test_dir, "test.db")
        cls.engine, cls.db_session = init_database(f"sqlite:///{db_path}")

    @classmethod
    def tearDownClass(cls):
        cls.db_session.remove()
        cls.engine.dispose()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self.db = self.db_session
        self.file_service = FileService(self.db, self.upload_dir)
        self.sync_engine = SyncEngine(self.db)
        self.conflict_service = ConflictService(self.db, self.upload_dir)
        self.version_service = VersionService(self.db, self.upload_dir)

    # 1. User registration
    def test_01_user_registration(self):
        user = User(
            username="alex_rivers",
            email="alex@example.com",
            role="user",
            storage_quota_bytes=5 * 1024 * 1024 * 1024
        )
        user.set_password("SecurePassword2026!")
        self.db.add(user)
        self.db.commit()

        queried = self.db.query(User).filter_by(email="alex@example.com").first()
        self.assertIsNotNone(queried)
        self.assertEqual(queried.username, "alex_rivers")
        self.assertTrue(queried.password_hash.startswith("scrypt:") or queried.password_hash.startswith("pbkdf2:"))

    # 2. User authentication (valid login)
    def test_02_user_authentication(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        self.assertIsNotNone(user)
        self.assertTrue(user.check_password("SecurePassword2026!"))

    # 3. Invalid login handling
    def test_03_invalid_login(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        self.assertIsNotNone(user)
        self.assertFalse(user.check_password("WrongPassword123!"))

    # 4. File upload
    def test_04_file_upload(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        content = b"CloudSync Pro Core Architecture Documentation v1.0"
        file_obj = io.BytesIO(content)
        file_obj.filename = "architecture.txt"

        uploaded_file, is_duplicate = self.file_service.upload_file(user.id, file_obj)
        self.assertIsNotNone(uploaded_file)
        self.assertEqual(uploaded_file.name, "architecture.txt")
        self.assertEqual(uploaded_file.current_version, 1)
        self.assertEqual(uploaded_file.file_size, len(content))
        self.assertTrue(os.path.exists(uploaded_file.storage_path))
        self.assertFalse(is_duplicate)

    # 5. File download
    def test_05_file_download(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        file = self.db.query(File).filter_by(user_id=user.id, name="architecture.txt").first()
        self.assertIsNotNone(file)
        with open(file.storage_path, "rb") as f:
            downloaded_bytes = f.read()
        self.assertEqual(downloaded_bytes, b"CloudSync Pro Core Architecture Documentation v1.0")

    # 6. File deletion (soft delete & trash)
    def test_06_file_deletion(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        file_obj = io.BytesIO(b"Temporary file content to delete")
        file_obj.filename = "delete_me.txt"
        file, _ = self.file_service.upload_file(user.id, file_obj)

        result = self.file_service.delete_file(file.id, user.id, permanent=False)
        self.assertTrue(result)

        refreshed = self.file_service.get_file(file.id, user.id, include_deleted=False)
        self.assertIsNone(refreshed, "Soft-deleted file should not be returned in active list")

        in_trash = self.file_service.get_file(file.id, user.id, include_deleted=True)
        self.assertIsNotNone(in_trash)
        self.assertTrue(in_trash.is_deleted)

    # 7. Duplicate file detection
    def test_07_duplicate_file_detection(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        content = b"Duplicate file test payload - identical sha256"
        
        file_obj1 = io.BytesIO(content)
        file_obj1.filename = "original_doc.txt"
        f1, is_dup1 = self.file_service.upload_file(user.id, file_obj1)
        self.assertFalse(is_dup1)

        file_obj2 = io.BytesIO(content)
        file_obj2.filename = "copy_doc.txt"
        f2, is_dup2 = self.file_service.upload_file(user.id, file_obj2)
        self.assertTrue(is_dup2, "System must detect matching SHA-256 payload as duplicate")

    # 8. Storage quota validation
    def test_08_storage_quota_validation(self):
        # Create a user with tiny 1KB quota
        small_user = User(
            username="quota_user",
            email="quota@example.com",
            role="user",
            storage_quota_bytes=1024,  # 1 KB
            storage_used_bytes=0
        )
        small_user.set_password("pass")
        self.db.add(small_user)
        self.db.commit()

        # Try to upload 5KB file
        oversized = io.BytesIO(b"A" * 5120)
        oversized.filename = "oversized.bin"

        with self.assertRaises(ValueError) as ctx:
            self.file_service.upload_file(small_user.id, oversized)
        self.assertIn("quota exceeded", str(ctx.exception).lower())

    # 9. Device registration
    def test_09_device_registration(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        dev = Device(
            user_id=user.id,
            name="MacBook Pro 16",
            device_type="Laptop",
            os_info="macOS Sonoma",
            ip_address="192.168.1.105",
            is_online=True
        )
        self.db.add(dev)
        self.db.commit()

        self.assertIsNotNone(dev.id)
        # Test toggling online status
        dev.is_online = False
        self.db.commit()
        refreshed = self.db.query(Device).filter_by(id=dev.id).first()
        self.assertFalse(refreshed.is_online)
        # Set back online
        dev.is_online = True
        self.db.commit()

    # 10. Synchronization
    def test_10_synchronization(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        device = self.db.query(Device).filter_by(user_id=user.id, name="MacBook Pro 16").first()
        
        # Run sync for device
        sync_result = self.sync_engine.sync_device(device.id, user.id)
        self.assertIsNotNone(sync_result)
        self.assertEqual(sync_result['device_id'], device.id)

        # Check that DeviceSyncStates now match file current_versions
        states = self.db.query(DeviceSyncState).filter_by(device_id=device.id).all()
        for state in states:
            self.assertEqual(state.synced_version, state.file.current_version)
            self.assertEqual(state.status, 'synced')

    # 11. Offline synchronization
    def test_11_offline_synchronization(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        # Register a phone that is offline
        phone = Device(
            user_id=user.id,
            name="iPhone 15 Pro",
            device_type="Mobile",
            os_info="iOS 17.5",
            is_online=False
        )
        self.db.add(phone)
        self.db.commit()

        # Upload new file while phone is offline
        new_file_obj = io.BytesIO(b"Data uploaded while phone was disconnected")
        new_file_obj.filename = "offline_sync_test.txt"
        file, _ = self.file_service.upload_file(user.id, new_file_obj)

        # Sync while offline should fail or raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.sync_engine.sync_device(phone.id, user.id)
        self.assertIn("offline", str(ctx.exception).lower())

        # Check pending files list includes phone
        pending = self.sync_engine.get_pending_files(user.id)
        phone_pending = [p for p in pending if p['device_id'] == phone.id and p['file_id'] == file.id]
        self.assertTrue(len(phone_pending) > 0)
        self.assertEqual(phone_pending[0]['status'], 'offline_queued')

        # Bring phone online and sync
        phone.is_online = True
        self.db.commit()
        sync_res = self.sync_engine.sync_device(phone.id, user.id)
        self.assertTrue(sync_res['files_synced'] > 0)

    # 12. Conflict detection
    def test_12_conflict_detection(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        device = self.db.query(Device).filter_by(user_id=user.id, name="MacBook Pro 16").first()
        
        # Create a file at server version 2
        fobj = io.BytesIO(b"Version 1 Content")
        fobj.filename = "project_roadmap.md"
        file, _ = self.file_service.upload_file(user.id, fobj)

        # Increment to version 2 on server
        temp_v2 = os.path.join(self.test_dir, "v2_roadmap.md")
        with open(temp_v2, "wb") as f:
            f.write(b"Version 2 Server Content")
        self.version_service.create_new_version(
            file.id, user.id, temp_v2, "hash_v2_fake", len(b"Version 2 Server Content"), change_summary="Server edit"
        )
        self.assertEqual(file.current_version, 2)

        # Outdated device has base_version = 1 and sends modification
        temp_conflict = os.path.join(self.test_dir, "conflict_from_device.md")
        with open(temp_conflict, "wb") as f:
            f.write(b"Device edited outdated version 1!")

        has_conflict, report, conflicted_file = self.conflict_service.detect_and_handle_conflict(
            user_id=user.id,
            file_id=file.id,
            device_id=device.id,
            base_version=1,  # outdated! server is at 2
            incoming_file_path=temp_conflict,
            incoming_filename="project_roadmap.md",
            incoming_hash="hash_conflict_payload",
            incoming_size=len(b"Device edited outdated version 1!")
        )

        self.assertTrue(has_conflict)
        self.assertIsNotNone(report)
        self.assertIsNotNone(conflicted_file)
        self.assertEqual(report.status, 'unresolved')
        # Check original file preserved at version 2
        self.assertEqual(file.current_version, 2)
        # Conflicted copy created
        self.assertIn("conflicted", conflicted_file.name)

        # Test resolving conflict with 'keep_both'
        resolved = self.conflict_service.resolve_conflict(report.id, 'keep_both', user.id)
        self.assertEqual(resolved.status, 'resolved_both')

    # 13. Version restoration
    def test_13_version_restoration(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        fobj = io.BytesIO(b"First Draft")
        fobj.filename = "essay.txt"
        file, _ = self.file_service.upload_file(user.id, fobj)
        self.assertEqual(file.current_version, 1)

        # Create version 2
        v2_path = os.path.join(self.test_dir, "v2_essay.txt")
        with open(v2_path, "wb") as f:
            f.write(b"Second Draft with Edits")
        self.version_service.create_new_version(file.id, user.id, v2_path, "hash_v2", 23, change_summary="Draft 2")
        self.assertEqual(file.current_version, 2)

        # Restore version 1: MUST NOT OVERWRITE v2, MUST CREATE v3
        file_after_restore, new_ver = self.version_service.restore_version(file.id, 1, user.id)
        self.assertEqual(file_after_restore.current_version, 3, "Restoration must generate version 3")
        self.assertEqual(new_ver.version_num, 3)
        self.assertIn("Restored from version 1", new_ver.change_summary)

        # Verify historical v1 and v2 still exist intact
        all_versions = self.db.query(FileVersion).filter_by(file_id=file.id).all()
        version_nums = [v.version_num for v in all_versions]
        self.assertIn(1, version_nums)
        self.assertIn(2, version_nums)
        self.assertIn(3, version_nums)

    # 14. User data isolation
    def test_14_user_data_isolation(self):
        # Create User B
        user_b = User(username="user_b", email="user_b@example.com", role="user")
        user_b.set_password("passB")
        self.db.add(user_b)
        self.db.commit()

        # User A's file
        user_a = self.db.query(User).filter_by(email="alex@example.com").first()
        user_a_file = self.db.query(File).filter_by(user_id=user_a.id).first()
        self.assertIsNotNone(user_a_file)

        # User B attempts to access User A's file via FileService
        isolated_file = self.file_service.get_file(user_a_file.id, user_id=user_b.id)
        self.assertIsNone(isolated_file, "User B must NOT have access to User A's file")

        # User B attempts to delete User A's file
        delete_result = self.file_service.delete_file(user_a_file.id, user_id=user_b.id)
        self.assertFalse(delete_result, "User B must NOT be able to delete User A's file")

    def test_15_new_version_updates_usage_and_sync_states(self):
        user = self.db.query(User).filter_by(email="alex@example.com").first()
        source_device = Device(
            user_id=user.id,
            name="Edit Source Device",
            device_type="Desktop",
            is_online=True,
        )
        other_device = Device(
            user_id=user.id,
            name="Edit Other Device",
            device_type="Laptop",
            is_online=True,
        )
        self.db.add_all([source_device, other_device])
        self.db.commit()

        original_size = 3
        edited_content = b"edited text"
        file_obj = io.BytesIO(b"old")
        file_obj.filename = "editable.txt"
        file, _ = self.file_service.upload_file(
            user.id,
            file_obj,
            device_id=source_device.id,
        )
        original_usage = user.storage_used_bytes
        edited_path = os.path.join(self.test_dir, "edited_content.txt")
        with open(edited_path, "wb") as edited_file:
            edited_file.write(edited_content)

        self.version_service.create_new_version(
            file.id,
            user.id,
            edited_path,
            "edited-content-hash",
            len(edited_content),
            device_id=source_device.id,
            change_summary="Edited in dashboard",
        )

        source_state = self.db.query(DeviceSyncState).filter_by(
            device_id=source_device.id,
            file_id=file.id,
        ).first()
        other_state = self.db.query(DeviceSyncState).filter_by(
            device_id=other_device.id,
            file_id=file.id,
        ).first()
        self.assertEqual(file.current_version, 2)
        self.assertEqual(user.storage_used_bytes, original_usage - original_size + len(edited_content))
        self.assertEqual(source_state.status, "synced")
        self.assertEqual(other_state.status, "pending")
        self.file_service.delete_file(file.id, user.id, permanent=True)
        self.db.delete(source_device)
        self.db.delete(other_device)
        self.db.commit()

    def test_16_new_version_respects_storage_quota(self):
        small_user = User(
            username="edit_quota_user",
            email="edit_quota@example.com",
            role="user",
            storage_quota_bytes=5,
            storage_used_bytes=0,
        )
        small_user.set_password("pass")
        self.db.add(small_user)
        self.db.commit()

        file_obj = io.BytesIO(b"old")
        file_obj.filename = "quota_edit.txt"
        file, _ = self.file_service.upload_file(small_user.id, file_obj)
        edited_path = os.path.join(self.test_dir, "oversized_edit.txt")
        with open(edited_path, "wb") as edited_file:
            edited_file.write(b"larger")

        with self.assertRaises(ValueError) as ctx:
            self.version_service.create_new_version(
                file.id,
                small_user.id,
                edited_path,
                "oversized-edit-hash",
                6,
            )

        self.assertIn("quota exceeded", str(ctx.exception).lower())
        self.assertEqual(file.current_version, 1)
        self.assertEqual(small_user.storage_used_bytes, 3)
        self.file_service.delete_file(file.id, small_user.id, permanent=True)
        self.db.delete(small_user)
        self.db.commit()

    def test_17_restoring_file_marks_devices_pending(self):
        user = User(
            username="restore_sync_user",
            email="restore_sync@example.com",
            role="user",
            storage_quota_bytes=1024,
            storage_used_bytes=0,
        )
        user.set_password("pass")
        self.db.add(user)
        self.db.flush()
        device = Device(
            user_id=user.id,
            name="Restore Test Device",
            device_type="Desktop",
            is_online=True,
        )
        self.db.add(device)
        self.db.commit()

        file_obj = io.BytesIO(b"restore me")
        file_obj.filename = "restore_sync.txt"
        file, _ = self.file_service.upload_file(user.id, file_obj, device_id=device.id)
        self.file_service.delete_file(file.id, user.id, permanent=False)
        restored = self.file_service.restore_from_trash(file.id, user.id)
        state = self.db.query(DeviceSyncState).filter_by(device_id=device.id, file_id=file.id).first()

        self.assertFalse(restored.is_deleted)
        self.assertEqual(state.status, "pending")
        self.assertEqual(state.synced_version, 0)
        self.file_service.delete_file(file.id, user.id, permanent=True)
        self.db.delete(user)
        self.db.commit()

    def test_18_pdf_validation_rejects_invalid_and_encrypted_files(self):
        invalid_pdf_path = os.path.join(self.test_dir, "invalid.pdf")
        with open(invalid_pdf_path, "wb") as invalid_pdf:
            invalid_pdf.write(b"not a PDF")

        with self.assertRaisesRegex(ValueError, "valid, readable PDF"):
            validate_pdf_file(invalid_pdf_path)

        encrypted_pdf_path = os.path.join(self.test_dir, "encrypted.pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("test-password")
        with open(encrypted_pdf_path, "wb") as encrypted_pdf:
            writer.write(encrypted_pdf)

        with self.assertRaisesRegex(ValueError, "Password-protected"):
            validate_pdf_file(encrypted_pdf_path)

    def test_19_pdf_replacement_creates_a_new_version(self):
        import app as app_module

        user = self.db.query(User).filter_by(email="alex@example.com").first()
        original_pdf = PdfWriter()
        original_pdf.add_blank_page(width=200, height=200)
        original_bytes = io.BytesIO()
        original_pdf.write(original_bytes)
        original_upload = io.BytesIO(original_bytes.getvalue())
        original_upload.filename = "replace_me.pdf"
        file, _ = self.file_service.upload_file(user.id, original_upload)
        original_version_path = file.storage_path

        replacement_pdf = PdfWriter()
        replacement_pdf.add_blank_page(width=200, height=200)
        replacement_pdf.add_blank_page(width=200, height=200)
        replacement_bytes = io.BytesIO()
        replacement_pdf.write(replacement_bytes)

        original_db = app_module.db
        original_file_service = app_module.file_service
        original_version_service = app_module.version_service
        original_sync_engine = app_module.sync_engine
        app_module.db = self.db
        app_module.file_service = self.file_service
        app_module.version_service = self.version_service
        app_module.sync_engine = self.sync_engine
        try:
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session["user_id"] = user.id

                dashboard_response = client.get("/dashboard")
                self.assertEqual(dashboard_response.status_code, 200)
                self.assertIn(f"/dashboard/files/{file.id}/edit".encode(), dashboard_response.data)

                edit_url = f"/dashboard/files/{file.id}/edit"
                get_response = client.get(edit_url)
                self.assertEqual(get_response.status_code, 200)
                self.assertIn(b"Replace replace_me.pdf", get_response.data)

                post_response = client.post(
                    edit_url,
                    data={
                        "current_version": "1",
                        "replacement_pdf": (io.BytesIO(replacement_bytes.getvalue()), "edited.pdf"),
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(post_response.status_code, 302)

                invalid_response = client.post(
                    edit_url,
                    data={
                        "current_version": "2",
                        "replacement_pdf": (io.BytesIO(b"not a PDF"), "invalid.pdf"),
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(invalid_response.status_code, 302)
        finally:
            app_module.db = original_db
            app_module.file_service = original_file_service
            app_module.version_service = original_version_service
            app_module.sync_engine = original_sync_engine
            original_db.remove()
            app_module._.dispose()

        self.assertEqual(file.current_version, 2)
        self.assertTrue(os.path.exists(original_version_path))
        self.assertEqual(len(PdfReader(file.storage_path).pages), 2)
        self.assertEqual(len(PdfReader(original_version_path).pages), 1)

    def test_20_private_blob_storage_operations(self):
        class FakeBlobNotFoundError(Exception):
            pass

        class FakeBlobClient:
            def __init__(self):
                self.uploaded_bytes = None
                self.upload_options = None
                self.read_options = None
                self.copy_options = None
                self.deleted_url = None
                self.not_found = False

            def put_blob(self, pathname, stream, **options):
                self.uploaded_bytes = stream.read()
                self.upload_options = options
                return SimpleNamespace(url=f"https://blob.example/{pathname}")

            def get_blob(self, url, **options):
                self.read_options = (url, options)
                return SimpleNamespace(body=self.uploaded_bytes)

            def head_blob(self, url):
                if self.not_found:
                    raise FakeBlobNotFoundError()
                return SimpleNamespace(url=url)

            def copy_blob(self, source_url, pathname, **options):
                self.copy_options = (source_url, pathname, options)
                return SimpleNamespace(url=f"https://blob.example/{pathname}")

            def delete_blob(self, url):
                self.deleted_url = url

        source_path = os.path.join(self.test_dir, "blob_source.txt")
        with open(source_path, "wb") as source_file:
            source_file.write(b"private cloud content")

        storage = StorageService(self.upload_dir, use_vercel_blob=True)
        blob_client = FakeBlobClient()
        vercel_module = ModuleType("vercel")
        vercel_module.__path__ = []
        blob_module = ModuleType("vercel.blob")
        blob_module.__path__ = []
        errors_module = ModuleType("vercel.blob.errors")
        errors_module.BlobNotFoundError = FakeBlobNotFoundError
        fake_modules = {
            "vercel": vercel_module,
            "vercel.blob": blob_module,
            "vercel.blob.errors": errors_module,
        }
        with patch.dict(sys.modules, fake_modules):
            with patch.object(storage, "_blob_client", return_value=blob_client):
                stored_url = storage.store_file(source_path, 42, "private.txt")

                self.assertTrue(stored_url.startswith("https://blob.example/cloudsync/42/"))
                self.assertEqual(blob_client.uploaded_bytes, b"private cloud content")
                self.assertEqual(blob_client.upload_options["access"], "private")
                self.assertEqual(
                    blob_client.upload_options["content_length"],
                    len(b"private cloud content"),
                )
                self.assertTrue(storage.file_exists(stored_url))
                blob_client.not_found = True
                self.assertFalse(storage.file_exists(stored_url))
                blob_client.not_found = False
                self.assertEqual(storage.read_file(stored_url), b"private cloud content")
                self.assertEqual(blob_client.read_options, (stored_url, {"access": "private"}))

                copied_url = storage.copy_file(stored_url, 42, "copy.txt")
                self.assertTrue(copied_url.startswith("https://blob.example/cloudsync/42/"))
                self.assertEqual(blob_client.copy_options[0], stored_url)
                self.assertEqual(blob_client.copy_options[2], {"access": "private"})

                storage.delete_file(stored_url)
                self.assertEqual(blob_client.deleted_url, stored_url)


if __name__ == '__main__':
    unittest.main()
