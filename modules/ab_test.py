from .database import db, ABTest, ABVariant, Product
from datetime import datetime
import os
from werkzeug.utils import secure_filename


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_all_tests():
    return ABTest.query.order_by(ABTest.created_at.desc()).all()


def get_test(test_id):
    return ABTest.query.get_or_404(test_id)


def create_test(product_id, name, target_ctr, min_impressions, rotation_interval,
                campaign_type='manual', campaign_id=None, campaign_name=''):
    test = ABTest(
        product_id=product_id,
        name=name,
        target_ctr=float(target_ctr),
        min_impressions=int(min_impressions),
        rotation_interval=int(rotation_interval),
        campaign_type=campaign_type,
        campaign_id=int(campaign_id) if campaign_id else None,
        campaign_name=campaign_name,
        status='paused'
    )
    db.session.add(test)
    db.session.commit()
    return test


def add_variant(test_id, variant_name, image_file):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    filename = ''
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"test{test_id}_{variant_name}_{image_file.filename}")
        image_file.save(os.path.join(UPLOAD_FOLDER, filename))

    variant = ABVariant(
        test_id=test_id,
        name=variant_name,
        image_filename=filename
    )
    db.session.add(variant)
    db.session.commit()
    return variant


def update_variant_stats(variant_id, impressions, clicks):
    variant = ABVariant.query.get(variant_id)
    if variant:
        variant.impressions = impressions
        variant.clicks = clicks
        db.session.commit()
        check_test_completion(variant.test_id)


def check_test_completion(test_id):
    test = ABTest.query.get(test_id)
    if not test or test.status != 'active':
        return

    variants = test.variants
    all_tested = all(v.impressions >= test.min_impressions for v in variants)

    if not all_tested:
        return

    best = max(variants, key=lambda v: v.ctr)
    if best.ctr >= test.target_ctr:
        best.is_winner = True
        test.status = 'completed'
        test.completed_at = datetime.utcnow()
        db.session.commit()


def get_test_stats(test_id):
    test = ABTest.query.get(test_id)
    if not test:
        return None

    variants_data = []
    for v in test.variants:
        variants_data.append({
            'id': v.id,
            'name': v.name,
            'image_filename': v.image_filename,
            'impressions': v.impressions,
            'clicks': v.clicks,
            'ctr': v.ctr,
            'is_tested': v.is_tested,
            'is_winner': v.is_winner,
        })

    return {
        'id': test.id,
        'name': test.name,
        'status': test.status,
        'target_ctr': test.target_ctr,
        'min_impressions': test.min_impressions,
        'rotation_interval': test.rotation_interval,
        'variants': variants_data,
    }
