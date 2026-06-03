from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from modules.database import db, Settings, Product, ABTest, ABVariant, CoverGeneration, N8nEvent
from modules import ab_test as ab_module
from modules import cover_generator as cover_module
from modules import n8n_integration as n8n_module
import os
import threading

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
    all_products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('products.html', products=all_products)


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

    test = ab_module.create_test(product_id, name, target_ctr, min_impressions, rotation_interval)
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
    db.session.delete(test)
    db.session.commit()
    flash('Тест удалён', 'success')
    return redirect(url_for('ab_tests'))


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
    s.wb_stats_api_key = request.form.get('wb_stats_api_key', '').strip()
    s.wb_content_api_key = request.form.get('wb_content_api_key', '').strip()
    s.image_ai_api_key = request.form.get('image_ai_api_key', '').strip()
    s.ai_provider = request.form.get('ai_provider', 'openai').strip()
    s.ai_model = request.form.get('ai_model', 'dall-e-3').strip()
    s.n8n_webhook_url = request.form.get('n8n_webhook_url', '').strip()
    s.n8n_secret = request.form.get('n8n_secret', '').strip()
    s.n8n_notify_on_complete = bool(request.form.get('n8n_notify_on_complete'))
    s.n8n_notify_on_winner = bool(request.form.get('n8n_notify_on_winner'))
    db.session.commit()
    flash('Настройки сохранены', 'success')
    return redirect(url_for('settings'))


# ── Генерация обложек ─────────────────────────────────────────────────────────

@app.route('/covers')
def covers():
    generations = cover_module.get_all_generations()
    products = Product.query.all()
    return render_template('covers.html', generations=generations, products=products)


@app.route('/covers/generate', methods=['POST'])
def generate_cover():
    product_id = request.form.get('product_id')
    style = request.form.get('style', 'product_photo')
    custom_prompt = request.form.get('custom_prompt', '').strip()

    if not product_id:
        flash('Выберите товар', 'error')
        return redirect(url_for('covers'))

    product = Product.query.get_or_404(product_id)
    settings = Settings.query.first()
    prompt = cover_module.build_prompt(product.name, product.category, style, custom_prompt)

    gen = CoverGeneration(
        product_id=product_id,
        prompt=prompt,
        style=style,
        ai_provider=settings.ai_provider if settings else 'openai',
        ai_model=settings.ai_model if settings else 'dall-e-3',
        status='pending',
    )
    db.session.add(gen)
    db.session.commit()

    # Запускаем генерацию в фоновом потоке
    t = threading.Thread(target=cover_module.generate_covers, args=(gen.id,))
    t.daemon = True
    t.start()

    flash(f'Генерация запущена для товара «{product.name}»', 'success')
    return redirect(url_for('cover_detail', gen_id=gen.id))


@app.route('/covers/<int:gen_id>')
def cover_detail(gen_id):
    gen = cover_module.get_generation(gen_id)
    products = Product.query.all()
    return render_template('cover_detail.html', gen=gen, products=products)


@app.route('/covers/<int:gen_id>/status')
def cover_status(gen_id):
    gen = CoverGeneration.query.get_or_404(gen_id)
    return jsonify({'status': gen.status, 'images': gen.images, 'error': gen.error_message})


@app.route('/covers/<int:gen_id>/create-test', methods=['POST'])
def cover_create_test(gen_id):
    test_name = request.form.get('test_name', '').strip()
    if not test_name:
        flash('Укажите название теста', 'error')
        return redirect(url_for('cover_detail', gen_id=gen_id))

    test = cover_module.create_ab_test_from_generation(gen_id, test_name)
    flash(f'A/B тест «{test_name}» создан из сгенерированных обложек', 'success')
    return redirect(url_for('ab_test_detail', test_id=test.id))


@app.route('/covers/<int:gen_id>/delete', methods=['POST'])
def delete_generation(gen_id):
    gen = CoverGeneration.query.get_or_404(gen_id)
    db.session.delete(gen)
    db.session.commit()
    flash('Генерация удалена', 'success')
    return redirect(url_for('covers'))


# ── n8n Интеграция ────────────────────────────────────────────────────────────

@app.route('/n8n')
def n8n_dashboard():
    events = n8n_module.get_event_log(limit=50)
    settings = Settings.query.first()
    return render_template('n8n.html', events=events, settings=settings)


@app.route('/n8n/test-webhook', methods=['POST'])
def n8n_test_webhook():
    """Отправляет тестовое событие на n8n для проверки соединения."""
    from modules.n8n_integration import _send_event
    event = _send_event('test_ping', {'message': 'WB Tool ping', 'source': 'manual_test'})
    if event.status == 'sent':
        flash(f'Тестовое событие отправлено успешно (HTTP {event.response_code})', 'success')
    else:
        flash(f'Ошибка отправки: {event.error_message}', 'error')
    return redirect(url_for('n8n_dashboard'))


@app.route('/n8n/webhook', methods=['POST'])
def n8n_incoming_webhook():
    """Входящий вебхук от n8n для выполнения команд."""
    settings = Settings.query.first()
    secret = settings.n8n_secret if settings else ''

    # Проверка подписи
    if secret:
        sig = request.headers.get('X-Webhook-Signature', '')
        if not n8n_module.verify_incoming_signature(secret, request.get_data(), sig):
            return jsonify({'ok': False, 'error': 'Invalid signature'}), 401

    data = request.get_json(force=True) or {}
    result = n8n_module.handle_incoming(data)
    return jsonify(result)


# ── REST API (для n8n HTTP-узлов) ─────────────────────────────────────────────

@app.route('/api/v1/tests', methods=['GET'])
def api_get_tests():
    tests = ABTest.query.all()
    return jsonify([
        {
            'id': t.id,
            'name': t.name,
            'status': t.status,
            'product_id': t.product_id,
            'product_name': t.product.name if t.product else '',
            'wb_article': t.product.wb_article if t.product else '',
            'created_at': t.created_at.isoformat(),
        }
        for t in tests
    ])


@app.route('/api/v1/tests/<int:test_id>', methods=['GET'])
def api_get_test(test_id):
    test = ABTest.query.get_or_404(test_id)
    return jsonify({
        'id': test.id,
        'name': test.name,
        'status': test.status,
        'target_ctr': test.target_ctr,
        'min_impressions': test.min_impressions,
        'product_id': test.product_id,
        'product_name': test.product.name if test.product else '',
        'wb_article': test.product.wb_article if test.product else '',
        'variants': [
            {
                'id': v.id,
                'name': v.name,
                'impressions': v.impressions,
                'clicks': v.clicks,
                'ctr': v.ctr,
                'is_winner': v.is_winner,
                'image_filename': v.image_filename,
            }
            for v in test.variants
        ],
    })


@app.route('/api/v1/variants/<int:variant_id>/stats', methods=['POST'])
def api_update_variant_stats(variant_id):
    data = request.get_json(force=True) or {}
    variant = ABVariant.query.get_or_404(variant_id)
    variant.impressions = int(data.get('impressions', variant.impressions))
    variant.clicks = int(data.get('clicks', variant.clicks))
    db.session.commit()
    return jsonify({'ok': True, 'ctr': variant.ctr})


@app.route('/api/v1/covers', methods=['GET'])
def api_get_covers():
    gens = CoverGeneration.query.order_by(CoverGeneration.created_at.desc()).limit(20).all()
    return jsonify([
        {
            'id': g.id,
            'product_id': g.product_id,
            'style': g.style,
            'status': g.status,
            'images': g.images,
            'created_at': g.created_at.isoformat(),
        }
        for g in gens
    ])


@app.route('/api/v1/covers/generate', methods=['POST'])
def api_generate_cover():
    data = request.get_json(force=True) or {}
    product_id = data.get('product_id')
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'ok': False, 'error': 'Product not found'}), 404

    settings = Settings.query.first()
    style = data.get('style', 'product_photo')
    custom_prompt = data.get('custom_prompt', '')
    prompt = cover_module.build_prompt(product.name, product.category, style, custom_prompt)

    gen = CoverGeneration(
        product_id=product_id,
        prompt=prompt,
        style=style,
        ai_provider=settings.ai_provider if settings else 'openai',
        ai_model=settings.ai_model if settings else 'dall-e-3',
    )
    db.session.add(gen)
    db.session.commit()

    t = threading.Thread(target=cover_module.generate_covers, args=(gen.id,))
    t.daemon = True
    t.start()

    return jsonify({'ok': True, 'generation_id': gen.id, 'status': 'pending'})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
