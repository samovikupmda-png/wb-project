"""Интеграция с n8n: исходящие вебхуки и входящий API."""
import hashlib
import hmac
import json
import requests
from datetime import datetime
from modules.database import db, N8nEvent, Settings, ABTest, ABVariant, CoverGeneration, Product


# ── Исходящие события ─────────────────────────────────────────────────────────

EVENT_TEST_COMPLETED = 'test_completed'
EVENT_WINNER_FOUND = 'winner_found'
EVENT_COVER_GENERATED = 'cover_generated'
EVENT_STATS_UPDATED = 'stats_updated'


def _send_event(event_type: str, payload: dict) -> N8nEvent:
    """Отправляет событие на вебхук n8n и логирует результат."""
    settings = Settings.query.first()
    event = N8nEvent(
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        status='pending',
    )
    db.session.add(event)
    db.session.flush()

    webhook_url = settings.n8n_webhook_url if settings else ''
    if not webhook_url:
        event.status = 'error'
        event.error_message = 'n8n webhook URL не настроен'
        db.session.commit()
        return event

    headers = {'Content-Type': 'application/json'}
    if settings.n8n_secret:
        sig = hmac.new(
            settings.n8n_secret.encode(),
            json.dumps(payload, sort_keys=True, default=str).encode(),
            hashlib.sha256,
        ).hexdigest()
        headers['X-Webhook-Signature'] = sig

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        event.response_code = resp.status_code
        event.status = 'sent' if resp.ok else 'error'
        if not resp.ok:
            event.error_message = resp.text[:512]
    except Exception as e:
        event.status = 'error'
        event.error_message = str(e)

    event.sent_at = datetime.utcnow()
    db.session.commit()
    return event


def notify_test_completed(test: ABTest):
    settings = Settings.query.first()
    if not (settings and settings.n8n_notify_on_complete):
        return
    payload = {
        'event': EVENT_TEST_COMPLETED,
        'test_id': test.id,
        'test_name': test.name,
        'product_id': test.product_id,
        'product_name': test.product.name if test.product else '',
        'wb_article': test.product.wb_article if test.product else '',
        'completed_at': datetime.utcnow().isoformat(),
        'variants': [
            {
                'id': v.id,
                'name': v.name,
                'impressions': v.impressions,
                'clicks': v.clicks,
                'ctr': v.ctr,
                'is_winner': v.is_winner,
            }
            for v in test.variants
        ],
    }
    return _send_event(EVENT_TEST_COMPLETED, payload)


def notify_winner_found(test: ABTest, winner: ABVariant):
    settings = Settings.query.first()
    if not (settings and settings.n8n_notify_on_winner):
        return
    payload = {
        'event': EVENT_WINNER_FOUND,
        'test_id': test.id,
        'test_name': test.name,
        'product_id': test.product_id,
        'product_name': test.product.name if test.product else '',
        'wb_article': test.product.wb_article if test.product else '',
        'winner': {
            'id': winner.id,
            'name': winner.name,
            'impressions': winner.impressions,
            'clicks': winner.clicks,
            'ctr': winner.ctr,
            'image_filename': winner.image_filename,
        },
        'found_at': datetime.utcnow().isoformat(),
    }
    return _send_event(EVENT_WINNER_FOUND, payload)


def notify_cover_generated(gen: CoverGeneration):
    payload = {
        'event': EVENT_COVER_GENERATED,
        'generation_id': gen.id,
        'product_id': gen.product_id,
        'product_name': gen.product.name if gen.product else '',
        'wb_article': gen.product.wb_article if gen.product else '',
        'style': gen.style,
        'images_count': len(gen.images),
        'images': gen.images,
        'generated_at': datetime.utcnow().isoformat(),
    }
    return _send_event(EVENT_COVER_GENERATED, payload)


def get_event_log(limit: int = 50):
    return N8nEvent.query.order_by(N8nEvent.created_at.desc()).limit(limit).all()


# ── Входящие команды от n8n ────────────────────────────────────────────────────

def verify_incoming_signature(secret: str, body: bytes, signature: str) -> bool:
    if not secret:
        return True
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def handle_incoming(data: dict) -> dict:
    """
    Обрабатывает входящую команду из n8n.
    Возвращает dict с результатом.
    """
    action = data.get('action', '')

    if action == 'get_tests':
        tests = ABTest.query.all()
        return {
            'ok': True,
            'tests': [
                {
                    'id': t.id,
                    'name': t.name,
                    'status': t.status,
                    'product': t.product.name if t.product else '',
                    'variants_count': len(t.variants),
                }
                for t in tests
            ],
        }

    if action == 'get_test_stats':
        test_id = data.get('test_id')
        test = ABTest.query.get(test_id)
        if not test:
            return {'ok': False, 'error': 'Test not found'}
        return {
            'ok': True,
            'test_id': test.id,
            'name': test.name,
            'status': test.status,
            'variants': [
                {
                    'id': v.id,
                    'name': v.name,
                    'impressions': v.impressions,
                    'clicks': v.clicks,
                    'ctr': v.ctr,
                    'is_winner': v.is_winner,
                }
                for v in test.variants
            ],
        }

    if action == 'update_stats':
        variant_id = data.get('variant_id')
        impressions = int(data.get('impressions', 0))
        clicks = int(data.get('clicks', 0))
        variant = ABVariant.query.get(variant_id)
        if not variant:
            return {'ok': False, 'error': 'Variant not found'}
        variant.impressions = impressions
        variant.clicks = clicks
        db.session.commit()
        return {'ok': True, 'ctr': variant.ctr}

    if action == 'complete_test':
        test_id = data.get('test_id')
        winner_id = data.get('winner_variant_id')
        test = ABTest.query.get(test_id)
        if not test:
            return {'ok': False, 'error': 'Test not found'}
        test.status = 'completed'
        test.completed_at = datetime.utcnow()
        if winner_id:
            for v in test.variants:
                v.is_winner = (v.id == winner_id)
        db.session.commit()
        return {'ok': True}

    if action == 'generate_cover':
        from modules.cover_generator import generate_covers, build_prompt
        product_id = data.get('product_id')
        product = Product.query.get(product_id)
        if not product:
            return {'ok': False, 'error': 'Product not found'}
        settings = Settings.query.first()
        style = data.get('style', 'product_photo')
        custom_prompt = data.get('custom_prompt', '')
        prompt = build_prompt(product.name, product.category, style, custom_prompt)
        gen = CoverGeneration(
            product_id=product_id,
            prompt=prompt,
            style=style,
            ai_provider=settings.ai_provider if settings else 'openai',
            ai_model=settings.ai_model if settings else 'dall-e-3',
        )
        db.session.add(gen)
        db.session.commit()
        generate_covers(gen.id)
        gen = CoverGeneration.query.get(gen.id)
        return {
            'ok': True,
            'generation_id': gen.id,
            'status': gen.status,
            'images': gen.images,
            'error': gen.error_message,
        }

    return {'ok': False, 'error': f'Unknown action: {action}'}
