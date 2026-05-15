from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wb_stats_api_key = db.Column(db.String(256), default='')
    wb_content_api_key = db.Column(db.String(256), default='')
    image_ai_api_key = db.Column(db.String(256), default='')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wb_article = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    category = db.Column(db.String(128), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ab_tests = db.relationship('ABTest', backref='product', lazy=True)


class ABTest(db.Model):
    __tablename__ = 'ab_test'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), default='paused')  # active, paused, completed
    campaign_type = db.Column(db.String(32), default='manual')  # manual, unified
    campaign_id = db.Column(db.Integer, nullable=True)
    campaign_name = db.Column(db.String(256), default='')
    target_ctr = db.Column(db.Float, default=3.0)
    min_impressions = db.Column(db.Integer, default=2000)
    rotation_interval = db.Column(db.Integer, default=30)  # minutes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    variants = db.relationship('ABVariant', backref='test', lazy=True, cascade='all, delete-orphan')


class ABVariant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('ab_test.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    image_url = db.Column(db.String(512), default='')
    image_filename = db.Column(db.String(256), default='')
    impressions = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)
    is_winner = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def ctr(self):
        if self.impressions == 0:
            return 0.0
        return round((self.clicks / self.impressions) * 100, 2)

    @property
    def is_tested(self):
        return self.impressions >= (self.test.min_impressions if self.test else 2000)
