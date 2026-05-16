from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from modules.database import db, Settings, Product, ABTest, ABVariant
from modules import ab_test as ab_module
import os
import subprocess
import requests

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = 'wb-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'wb_tool.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)

with app.app_context():
    os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads'), exist_ok=True)
    db.create_all()
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for sql in [
            'ALTER TABLE ab_test ADD COLUMN campaign_type VARCHAR(32) DEFAULT "manual"',
            'ALTER TABLE ab_test ADD COLUMN campaign_id INTEGER',
            'ALTER TABLE ab_test ADD COLUMN campaign_name VARCHAR(256) DEFAULT ""',
            'ALTER TABLE settings ADD COLUMN wb_content_client_secret VARCHAR(256) DEFAULT ""',
            'ALTER TABLE settings ADD COLUMN wb_analytics_api_key VARCHAR(256) DEFAULT ""',
        ]:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()


# ── Главная ──────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    products_count = Product.query.count()
    active_tests = ABTest.query.filter_by(status='active').count()
    completed_tests = ABTest.query.filter_by(status='completed').count()
    recent_tests = ABTest.query.order_by(ABTest.created_at.desc()).limit(5).all()
    return render_template('dashboard.html',
                           products_count=products_count,
                           active_tests=active_tests,
                           completed_tests=completed_tests,
                           recent_tests=recent_tests)


# ── Товары ───────────────────────────────────────────────────────────────────

@app.route('/products')
def products():
    s = Settings.query.first()
    sync_error = None
    sync_added = 0
    if s and s.wb_content_api_key:
        sync_added, sync_error = _sync_wb_products(s.wb_content_api_key)
    all_products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('products.html', products=all_products,
                           sync_error=sync_error, sync_added=sync_added)


def _sync_wb_products(api_key):
    """Pull all seller cards from WB Content API. Returns (added_count, error_str)."""
    token = api_key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'
    cursor = {}
    added = 0
    try:
        while True:
            body = {'settings': {'cursor': {'limit': 100, **cursor}, 'filter': {'withPhoto': -1}}}
            r = requests.post(
                'https://content-api.wildberries.ru/content/v3/cards/filter',
                json=body,
                headers={'Authorization': token, 'Content-Type': 'application/json'},
                timeout=15
            )
            if r.status_code == 401:
                return 0, 'Ошибка авторизации (401) — проверьте API-ключ в Настройках'
            if r.status_code == 403:
                return 0, 'Нет доступа (403) — токен должен иметь права «Контент» (чтение)'
            if r.status_code != 200:
                return 0, f'WB API вернул ошибку {r.status_code}: {r.text[:150]}'

            data = r.json()
            cards = data.get('cards', [])
            if not cards:
                break
            for card in cards:
                nm_id = str(card.get('nmID', ''))
                if not nm_id:
                    continue
                if not Product.query.filter_by(wb_article=nm_id).first():
                    name = card.get('title', '') or card.get('subjectName', '') or f'Товар {nm_id}'
                    category = card.get('subjectName', '')
                    db.session.add(Product(wb_article=nm_id, name=name, category=category))
                    added += 1
            db.session.commit()
            next_cursor = data.get('cursor', {})
            if not next_cursor.get('nmID') or len(cards) < 100:
                break
            cursor = {'nmID': next_cursor['nmID'], 'updatedAt': next_cursor.get('updatedAt', '')}
        return added, None
    except Exception as e:
        return 0, str(e)


@app.route('/api/wb/import-products', methods=['POST'])
def api_import_products():
    s = Settings.query.first()
    if not s or not s.wb_content_api_key:
        return jsonify({'error': 'API-ключ WB не настроен. Добавьте его в Настройки.'}), 400

    token = s.wb_content_api_key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'

    added = 0
    skipped = 0
    cursor = {}

    try:
        while True:
            body = {'settings': {'cursor': {'limit': 100, **cursor}, 'filter': {'withPhoto': -1}}}
            r = requests.post(
                'https://content-api.wildberries.ru/content/v3/cards/filter',
                json=body,
                headers={'Authorization': token, 'Content-Type': 'application/json'},
                timeout=15
            )
            if r.status_code != 200:
                return jsonify({'error': f'WB API ошибка {r.status_code}: {r.text[:200]}'}), 400

            data = r.json()
            cards = data.get('cards', [])
            if not cards:
                break

            for card in cards:
                nm_id = str(card.get('nmID', ''))
                name = card.get('title', '') or card.get('subjectName', '') or 'Товар WB'
                category = card.get('subjectName', '')
                vendor_code = card.get('vendorCode', '')

                if not nm_id:
                    continue

                if Product.query.filter_by(wb_article=nm_id).first():
                    skipped += 1
                    continue

                product = Product(wb_article=nm_id, name=name, category=category)
                db.session.add(product)
                added += 1

            db.session.commit()

            # Pagination
            next_cursor = data.get('cursor', {})
            if not next_cursor.get('nmID') or len(cards) < 100:
                break
            cursor = {'nmID': next_cursor['nmID'], 'updatedAt': next_cursor.get('updatedAt', '')}

        return jsonify({'added': added, 'skipped': skipped})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/products/add', methods=['POST'])
def add_product():
    wb_article = request.form.get('wb_article', '').strip()
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()

    if not wb_article or not name:
        flash('Заполните артикул и название товара', 'error')
        return redirect(url_for('products'))

    if Product.query.filter_by(wb_article=wb_article).first():
        flash('Товар с таким артикулом уже добавлен', 'error')
        return redirect(url_for('products'))

    product = Product(wb_article=wb_article, name=name, category=category)
    db.session.add(product)
    db.session.commit()
    flash(f'Товар «{name}» добавлен', 'success')
    return redirect(url_for('products'))


@app.route('/products/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash('Товар удалён', 'success')
    return redirect(url_for('products'))


# ── AB-тестирование ───────────────────────────────────────────────────────────

@app.route('/ab-tests')
def ab_tests():
    tests = ab_module.get_all_tests()
    products = Product.query.all()
    return render_template('ab_tests.html', tests=tests, products=products)


@app.route('/ab-tests/create', methods=['POST'])
def create_ab_test():
    product_id = request.form.get('product_id')
    name = request.form.get('name', '').strip()
    target_ctr = request.form.get('target_ctr', 3.0)
    min_impressions = request.form.get('min_impressions', 2000)
    rotation_interval = request.form.get('rotation_interval', 30)

    if not product_id or not name:
        flash('Выберите товар и укажите название теста', 'error')
        return redirect(url_for('ab_tests'))

    campaign_type = request.form.get('campaign_type', 'manual')
    campaign_id = request.form.get('campaign_id') or None
    campaign_name = request.form.get('campaign_name', '')
    test = ab_module.create_test(product_id, name, target_ctr, min_impressions,
                                  rotation_interval, campaign_type, campaign_id, campaign_name)
    flash(f'Тест «{name}» создан', 'success')
    return redirect(url_for('ab_test_detail', test_id=test.id))


@app.route('/ab-tests/<int:test_id>')
def ab_test_detail(test_id):
    stats = ab_module.get_test_stats(test_id)
    test = ab_module.get_test(test_id)
    return render_template('ab_test_detail.html', test=test, stats=stats)


@app.route('/ab-tests/<int:test_id>/add-variant', methods=['POST'])
def add_variant(test_id):
    variant_name = request.form.get('variant_name', '').strip()
    image_file = request.files.get('image_file')

    if not variant_name:
        flash('Укажите название варианта', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))

    ab_module.add_variant(test_id, variant_name, image_file)
    flash(f'Вариант «{variant_name}» добавлен', 'success')
    return redirect(url_for('ab_test_detail', test_id=test_id))


@app.route('/ab-tests/<int:test_id>/update-stats', methods=['POST'])
def update_stats(test_id):
    data = request.get_json()
    variant_id = data.get('variant_id')
    impressions = data.get('impressions', 0)
    clicks = data.get('clicks', 0)
    ab_module.update_variant_stats(variant_id, impressions, clicks)
    stats = ab_module.get_test_stats(test_id)
    return jsonify(stats)


@app.route('/ab-tests/<int:test_id>/pause', methods=['POST'])
def pause_test(test_id):
    test = ABTest.query.get_or_404(test_id)
    test.status = 'paused' if test.status == 'active' else 'active'
    db.session.commit()
    action = 'приостановлен' if test.status == 'paused' else 'возобновлён'
    flash(f'Тест {action}', 'success')
    return redirect(url_for('ab_test_detail', test_id=test_id))


@app.route('/ab-tests/<int:test_id>/delete', methods=['POST'])
def delete_test(test_id):
    test = ABTest.query.get_or_404(test_id)
    ABVariant.query.filter_by(test_id=test_id).delete()
    db.session.delete(test)
    db.session.commit()
    flash('Тест удалён', 'success')
    return redirect(url_for('ab_tests'))


# ── Генерация обложек ─────────────────────────────────────────────────────────

@app.route('/cover-generator')
def cover_generator():
    products = Product.query.order_by(Product.name).all()
    return render_template('cover_generator.html', products=products)


@app.route('/api/wb/product-photos/<article>')
def api_product_photos(article):
    from modules import cover_gen
    photos = cover_gen.get_product_photo_urls(article, max_photos=6)
    return jsonify({'photos': photos})


@app.route('/api/covers/generate', methods=['POST'])
def api_covers_generate():
    s = Settings.query.first()
    if not s or not s.image_ai_api_key:
        return jsonify({'error': 'API-ключ OpenAI не настроен. Добавьте его в Настройки → Генерация изображений.'}), 400

    product_id = request.form.get('product_id')
    keyword = (request.form.get('keyword') or '').strip()
    extra_hint = (request.form.get('extra_hint') or '').strip()
    custom_prompt = (request.form.get('custom_prompt') or '').strip()
    photo_number = int(request.form.get('photo_number') or 1)

    if not keyword or not product_id:
        return jsonify({'error': 'Укажите товар и ключевой запрос'}), 400

    product = Product.query.get_or_404(product_id)

    product_image_bytes = None
    photo_file = request.files.get('product_photo')
    if photo_file and photo_file.filename:
        product_image_bytes = photo_file.read()

    from modules import cover_gen
    try:
        if custom_prompt:
            from openai import OpenAI
            client = OpenAI(api_key=s.image_ai_api_key)
            if product_image_bytes:
                png = cover_gen._to_png_bytes(product_image_bytes)
                try:
                    img_resp = client.images.edit(
                        model='gpt-image-1', image=('product.png', png, 'image/png'),
                        prompt=custom_prompt[:4000], size='1024x1024', timeout=180,
                    )
                    generated_url = f'data:image/png;base64,{img_resp.data[0].b64_json}'
                except Exception:
                    img_resp = client.images.generate(
                        model='dall-e-3', prompt=custom_prompt[:4000],
                        size='1024x1024', quality='standard', n=1
                    )
                    generated_url = img_resp.data[0].url
            else:
                img_resp = client.images.generate(
                    model='dall-e-3', prompt=custom_prompt[:4000],
                    size='1024x1024', quality='standard', n=1
                )
                generated_url = img_resp.data[0].url
            return jsonify({
                'covers': [], 'analysis': '', 'cover_info': '',
                'dalle_prompt': custom_prompt, 'generated_url': generated_url,
                'used_product_photo': bool(product_image_bytes), 'used_current_cover': False,
            })

        result = cover_gen.analyze_and_generate(
            keyword, product.name, s.image_ai_api_key,
            article=product.wb_article,
            product_photo_number=photo_number,
            product_image_bytes=product_image_bytes,
            extra_hint=extra_hint,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Аналитика ─────────────────────────────────────────────────────────────────

@app.route('/analytics')
def analytics():
    return render_template('analytics.html')


# ── Реклама ───────────────────────────────────────────────────────────────────

@app.route('/advertising')
def advertising():
    return render_template('advertising.html')


# ── Настройки ─────────────────────────────────────────────────────────────────

@app.route('/settings')
def settings():
    s = Settings.query.first()
    return render_template('settings.html', settings=s)


@app.route('/settings/save', methods=['POST'])
def save_settings():
    s = Settings.query.first()
    wb_key = request.form.get('wb_stats_api_key', '').strip()
    s.wb_stats_api_key = wb_key
    s.wb_content_api_key = wb_key
    s.wb_analytics_api_key = wb_key
    s.image_ai_api_key = request.form.get('image_ai_api_key', '').strip()
    db.session.commit()
    flash('Настройки сохранены', 'success')
    return redirect(url_for('settings'))


# ── WB API ───────────────────────────────────────────────────────────────────

@app.route('/api/wb/campaigns')
def api_wb_campaigns():
    s = Settings.query.first()
    if not s or not s.wb_stats_api_key:
        return jsonify({'error': 'API ключ не настроен'}), 400
    from modules import wb_api
    campaigns = wb_api.get_active_campaigns(s.wb_stats_api_key)
    return jsonify(campaigns)


@app.route('/ab-tests/<int:test_id>/sync', methods=['POST'])
def sync_test_stats(test_id):
    test = ABTest.query.get_or_404(test_id)
    s = Settings.query.first()
    if not s or not s.wb_stats_api_key:
        flash('Настройте API ключ WB Stats в настройках', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))
    if not test.campaign_id:
        flash('У теста не указана рекламная кампания', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))
    from modules import wb_api
    stats = wb_api.get_campaign_stats(s.wb_stats_api_key, test.campaign_id)
    if stats:
        flash(f"WB: {stats['views']} показов · {stats['clicks']} кликов · CTR {stats['ctr']}%", 'success')
    else:
        flash('Не удалось получить данные из WB. Проверьте API ключ.', 'error')
    return redirect(url_for('ab_test_detail', test_id=test_id))


@app.route('/ab-tests/<int:test_id>/apply-photo/<int:variant_id>', methods=['POST'])
def apply_variant_photo(test_id, variant_id):
    test = ABTest.query.get_or_404(test_id)
    variant = ABVariant.query.get_or_404(variant_id)
    s = Settings.query.first()
    if not s or not s.wb_content_api_key:
        flash('Настройте API ключ WB Content в настройках', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))
    if not variant.image_filename:
        flash('У варианта нет загруженного изображения', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))
    from modules import wb_api
    photo_path = os.path.join(BASE_DIR, 'static', 'uploads', variant.image_filename)
    success, msg = wb_api.set_product_photo(
        s.wb_content_api_key, test.product.wb_article, photo_path,
        client_secret=s.wb_content_client_secret or None
    )
    if success:
        flash(f'Обложка «{variant.name}» применена на WB как главное фото', 'success')
    else:
        if 'forbidden' in msg.lower() or '403' in msg:
            flash('Ошибка доступа WB: проверьте API ключ контента — нужен ключ с правами на управление карточками товаров', 'error')
        else:
            flash(f'Ошибка WB API: {msg}', 'error')
    return redirect(url_for('ab_test_detail', test_id=test_id))


DEPLOY_TOKEN = 'wb-d3pl0y-k3y-2026'

@app.route('/deploy')
def deploy():
    if request.args.get('token') != DEPLOY_TOKEN:
        return 'Unauthorized', 401
    try:
        env = os.environ.copy()
        env['HOME'] = '/var/www'
        out = subprocess.run(
            ['git', '-C', '/var/www/wb-project', 'pull', 'origin',
             'claude/project-management-analysis-jKH5e'],
            capture_output=True, text=True, timeout=60, env=env
        )
        fix = subprocess.run(
            ['chown', '-R', 'www-data:www-data', '/var/www/wb-project'],
            capture_output=True, text=True
        )
        return f'<pre>OK\n{out.stdout}{out.stderr}</pre>', 200
    except Exception as e:
        return f'<pre>Error: {e}</pre>', 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
