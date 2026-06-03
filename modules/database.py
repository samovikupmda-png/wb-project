from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()


class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wb_stats_api_key = db.Column(db.String(256), default='')
    wb_content_api_key = db.Column(db.String(256), default='')
    image_ai_api_key = db.Column(db.String(256), default='')
    # AI provider: openai | stability
    ai_provider = db.Column(db.String(32), default='openai')
    ai_model = db.Column(db.String(64), default='dall-e-3')
    # n8n
    n8n_webhook_url = db.Column(db.String(512), default='')
    n8n_secret = db.Column(db.String(256), default='')
    n8n_notify_on_complete = db.Column(db.Boolean, default=True)
    n8n_notify_on_winner = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wb_article = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    category = db.Column(db.String(128), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ab_tests = db.relationship('ABTest', backref='product', lazy=True)
    cover_generations = db.relationship('CoverGeneration', backref='product', lazy=True)


class ABTest(db.Model):
    __tablename__ = 'ab_test'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), default='active')  # active, paused, completed
    target_ctr = db.Column(db.Float, default=3.0)
    min_impressions = db.Column(db.Integer, default=2000)
    rotation_interval = db.Column(db.Integer, default=30)  # minutes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    variants = db.relationship('ABVariant', backref='test', lazy=True)


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


class CoverGeneration(db.Model):
    """Запись о генерации обложки через AI."""
    __tablename__ = 'cover_generation'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    style = db.Column(db.String(64), default='product_photo')
    ai_provider = db.Column(db.String(32), default='openai')
    ai_model = db.Column(db.String(64), default='dall-e-3')
    status = db.Column(db.String(32), default='pending')  # pending, done, error
    error_message = db.Column(db.Text, default='')
    images_json = db.Column(db.Text, default='[]')
    ab_test_id = db.Column(db.Integer, db.ForeignKey('ab_test.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def images(self):
        try:
            return json.loads(self.images_json or '[]')
        except Exception:
            return []

    @images.setter
    def images(self, value):
        self.images_json = json.dumps(value)


class N8nEvent(db.Model):
    """Лог событий, отправленных в n8n."""
    __tablename__ = 'n8n_event'
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(64), nullable=False)
    payload_json = db.Column(db.Text, default='{}')
    status = db.Column(db.String(32), default='pending')  # pending, sent, error
    response_code = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    @property
    def payload(self):
        try:
            return json.loads(self.payload_json or '{}')
        except Exception:
            return {}
