from datetime import datetime, timedelta
from sqlalchemy import func
from models import File, User, Device, ActivityLog

class AnalyticsService:
    def __init__(self, db_session):
        self.db = db_session

    def get_user_analytics(self, user_id):
        user = self.db.query(User).filter_by(id=user_id).first()
        if not user:
            return None

        # Storage totals
        allocated = user.storage_quota_bytes
        used = user.storage_used_bytes
        remaining = max(0, allocated - used)
        used_percent = min(100.0, round((used / allocated * 100), 1)) if allocated > 0 else 0

        # Category Breakdown
        categories = ['documents', 'images', 'videos', 'audio', 'archives', 'others']
        cat_stats = {cat: {'count': 0, 'bytes': 0} for cat in categories}

        active_files = self.db.query(File).filter_by(user_id=user_id, is_deleted=False).all()
        for f in active_files:
            cat = f.category if f.category in cat_stats else 'others'
            cat_stats[cat]['count'] += 1
            cat_stats[cat]['bytes'] += f.file_size

        # 14-day Activity Trend
        today = datetime.utcnow().date()
        date_counts = {}
        for i in range(13, -1, -1):
            d = today - timedelta(days=i)
            date_counts[d.strftime('%b %d')] = {'uploads': 0, 'syncs': 0}

        logs = self.db.query(ActivityLog).filter(
            ActivityLog.user_id == user_id,
            ActivityLog.created_at >= datetime.utcnow() - timedelta(days=14)
        ).all()

        for log in logs:
            log_date_str = log.created_at.date().strftime('%b %d')
            if log_date_str in date_counts:
                if log.action_type == 'upload':
                    date_counts[log_date_str]['uploads'] += 1
                elif log.action_type == 'sync':
                    date_counts[log_date_str]['syncs'] += 1

        # Largest Files
        largest_files = self.db.query(File).filter_by(user_id=user_id, is_deleted=False)\
            .order_by(File.file_size.desc()).limit(5).all()

        # Devices list with file counts
        devices = self.db.query(Device).filter_by(user_id=user_id).all()
        device_data = []
        for dev in devices:
            device_data.append({
                'id': dev.id,
                'name': dev.name,
                'type': dev.device_type,
                'is_online': dev.is_online,
                'last_synced_at': dev.last_synced_at.isoformat() if dev.last_synced_at else 'Never'
            })

        return {
            'storage': {
                'allocated_bytes': allocated,
                'used_bytes': used,
                'remaining_bytes': remaining,
                'used_percent': used_percent,
                'allocated_formatted': f"{allocated / (1024**3):.1f} GB",
                'used_formatted': f"{used / (1024**2):.1f} MB" if used < (1024**3) else f"{used / (1024**3):.2f} GB",
                'remaining_formatted': f"{remaining / (1024**3):.1f} GB"
            },
            'categories': cat_stats,
            'activity_trend': {
                'labels': list(date_counts.keys()),
                'uploads': [v['uploads'] for v in date_counts.values()],
                'syncs': [v['syncs'] for v in date_counts.values()]
            },
            'largest_files': [f.to_dict() for f in largest_files],
            'devices': device_data,
            'total_files_count': len(active_files)
        }
