import mimetypes
import os
import shutil
import uuid

from werkzeug.utils import secure_filename


class StorageService:
    def __init__(self, upload_base_folder, use_vercel_blob=False):
        self.upload_base_folder = upload_base_folder
        self.use_vercel_blob = use_vercel_blob

    def store_file(self, source_path, user_id, filename):
        safe_name = secure_filename(filename) or 'file.bin'
        stored_name = f'{uuid.uuid4().hex}_{safe_name}'

        if self.use_vercel_blob:
            blob = self._blob_client()
            pathname = f'cloudsync/{user_id}/{stored_name}'
            content_type = mimetypes.guess_type(safe_name)[0] or 'application/octet-stream'
            file_size = os.path.getsize(source_path)
            with open(source_path, 'rb') as source_file:
                result = blob.put_blob(
                    pathname,
                    source_file,
                    access='private',
                    content_length=file_size,
                    content_type=content_type,
                )
            return result.url

        user_dir = os.path.join(self.upload_base_folder, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        destination = os.path.join(user_dir, stored_name)
        shutil.copy2(source_path, destination)
        return destination

    def copy_file(self, source_path, user_id, filename):
        if self.use_vercel_blob and source_path.startswith('https://'):
            safe_name = secure_filename(filename) or 'file.bin'
            pathname = f'cloudsync/{user_id}/{uuid.uuid4().hex}_{safe_name}'
            return self._blob_client().copy_blob(
                source_path,
                pathname,
                access='private',
            ).url

        return self.store_file(source_path, user_id, filename)

    def read_file(self, storage_path):
        if self.use_vercel_blob:
            return self._blob_client().get_blob(storage_path, access='private').body

        with open(storage_path, 'rb') as stored_file:
            return stored_file.read()

    def file_exists(self, storage_path):
        if self.use_vercel_blob:
            from vercel.blob.errors import BlobNotFoundError

            try:
                self._blob_client().head_blob(storage_path)
            except BlobNotFoundError:
                return False
            return True

        return os.path.exists(storage_path)

    def delete_file(self, storage_path):
        if self.use_vercel_blob:
            self._blob_client().delete_blob(storage_path)
        elif os.path.exists(storage_path):
            os.remove(storage_path)

    def _blob_client(self):
        try:
            from vercel.blob import sync
        except ImportError as exc:
            raise RuntimeError(
                'Vercel Blob storage is enabled, but vercel-blob is not installed.'
            ) from exc
        return sync
