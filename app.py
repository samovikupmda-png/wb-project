from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, Response
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

# WB CDN URL helper (mirrors Python formula in cover_gen.py — no imports needed)
def _wb_cover_url(article, photo_number=1):
    nm = int(article)
    vol = nm // 100000
    part = nm // 1000
    fixed = [(143,1),(287,2),(431,3),(719,4),(1007,5),(1061,6),(1115,7),(1169,8),
             (1313,9),(1601,10),(1655,11),(1919,12),(2045,13),(2189,14),(2405,15),(2621,16),(2837,17)]
    basket = next((str(b).zfill(2) for thr, b in fixed if vol <= thr), None)
    if basket is None:
        basket = str(18 + (vol - 2838) // 216).zfill(2)
    return f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{photo_number}.webp'

app.jinja_env.globals['wb_cover_url'] = _wb_cover_url


def _make_openai_client(api_key, timeout=60.0, max_retries=2, proxy_url=None):
    """Build OpenAI client, optionally routing through an HTTP/SOCKS proxy."""
    from openai import OpenAI
    if proxy_url and proxy_url.strip():
        import httpx
        http_client = httpx.Client(proxy=proxy_url.strip(), timeout=timeout)
        return OpenAI(api_key=api_key, http_client=http_client, max_retries=max_retries)
    return OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

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
            'ALTER TABLE settings ADD COLUMN wb_content_api_key VARCHAR(256) DEFAULT ""',
            'ALTER TABLE product ADD COLUMN photo_url VARCHAR(512) DEFAULT ""',
            'ALTER TABLE settings ADD COLUMN openai_proxy_url VARCHAR(512) DEFAULT ""',
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

@app.route('/img/<article>')
@app.route('/img/<article>/<int:num>')
def product_image_redirect(article, num=1):
    """Redirect to WB CDN via server — browser follows redirect without sending our Referer to WB."""
    p = Product.query.filter_by(wb_article=str(article)).first()
    if num == 1 and p and p.photo_url:
        url = p.photo_url
    else:
        url = _wb_cover_url(article, num)
    resp = redirect(url, 302)
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@app.route('/products')
def products():
    all_products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('products.html', products=all_products)


@app.route('/products/sync', methods=['POST'])
def sync_products():
    s = Settings.query.first()
    api_key = (s.wb_content_api_key or s.wb_stats_api_key or '') if s else ''
    if not api_key:
        flash('API-ключ WB не настроен. Добавьте его в Настройках.', 'error')
        return redirect(url_for('products'))
    added, error = _sync_wb_products(api_key)
    if error:
        flash(f'Ошибка синхронизации: {error}', 'error')
    elif added > 0:
        flash(f'Добавлено новых товаров с WB: {added}', 'success')
    else:
        flash('Товары актуальны — ничего нового', 'success')
    return redirect(url_for('products'))


def _sync_wb_products(api_key):
    """Pull all seller cards from WB Content API v2. Returns (added_count, error_str)."""
    token = api_key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'
    cursor = {}
    added = 0
    try:
        while True:
            body = {'settings': {'cursor': {'limit': 100, **cursor}, 'filter': {'withPhoto': -1}}}
            r = requests.post(
                'https://content-api.wildberries.ru/content/v2/get/cards/list',
                json=body,
                headers={'Authorization': token, 'Content-Type': 'application/json'},
                timeout=20
            )
            if r.status_code == 401:
                return 0, 'Ошибка авторизации (401) — проверьте API-ключ в Настройках'
            if r.status_code == 403:
                return 0, 'Нет доступа (403) — токен должен иметь права «Контент»'
            if r.status_code != 200:
                return 0, f'WB API {r.status_code}: {r.text[:200]}'

            data = r.json()
            cards = data.get('cards', [])
            if not cards:
                break
            for card in cards:
                nm_id = str(card.get('nmID', ''))
                if not nm_id:
                    continue
                # Extract first photo URL from API response
                media = card.get('mediaFiles') or []
                photo_url = media[0] if media else ''
                if not photo_url:
                    photos = card.get('photos') or []
                    if photos:
                        p0 = photos[0]
                        photo_url = p0.get('big') or p0.get('c516x688') or (p0 if isinstance(p0, str) else '')
                existing = Product.query.filter_by(wb_article=nm_id).first()
                if not existing:
                    name = card.get('title', '') or card.get('subjectName', '') or f'Товар {nm_id}'
                    category = card.get('subjectName', '')
                    db.session.add(Product(wb_article=nm_id, name=name, category=category, photo_url=photo_url))
                    added += 1
                elif photo_url and not existing.photo_url:
                    existing.photo_url = photo_url
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
                'https://content-api.wildberries.ru/content/v2/get/cards/list',
                json=body,
                headers={'Authorization': token, 'Content-Type': 'application/json'},
                timeout=20
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


@app.route('/api/wb/diagnose')
def api_wb_diagnose():
    """Show exactly what WB API returns — for debugging."""
    s = Settings.query.first()
    result = {
        'wb_stats_key_len': len(s.wb_stats_api_key or ''),
        'wb_content_key_len': len(s.wb_content_api_key or ''),
        'wb_analytics_key_len': len(s.wb_analytics_api_key or ''),
    }
    key = s.wb_content_api_key or s.wb_stats_api_key or ''
    if not key:
        result['error'] = 'Ключ не найден в БД — зайдите в Настройки и сохраните заново'
        return jsonify(result)
    token = key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'
    result['token_prefix'] = token[:30] + '...'
    try:
        r = requests.post(
            'https://content-api.wildberries.ru/content/v2/get/cards/list',
            json={'settings': {'cursor': {'limit': 3}, 'filter': {'withPhoto': -1}}},
            headers={'Authorization': token, 'Content-Type': 'application/json'},
            timeout=15
        )
        result['status_code'] = r.status_code
        result['response'] = r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text[:300]
    except Exception as e:
        result['exception'] = str(e)
    return jsonify(result)


def _get_wb_card_info(api_key, article):
    """Get product characteristics from WB Content API as text."""
    if not api_key:
        return ''
    token = api_key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'
    try:
        body = {'settings': {'cursor': {'limit': 1}, 'filter': {'textSearch': str(article)}}}
        r = requests.post(
            'https://content-api.wildberries.ru/content/v2/get/cards/list',
            json=body, headers={'Authorization': token, 'Content-Type': 'application/json'},
            timeout=10
        )
        if r.status_code != 200:
            return ''
        cards = r.json().get('cards', [])
        if not cards:
            return ''
        card = cards[0]
        parts = []
        if card.get('title'):
            parts.append(f"Название: {card['title']}")
        if card.get('description'):
            parts.append(f"Описание: {card['description'][:400]}")
        for ch in (card.get('characteristics') or [])[:12]:
            if isinstance(ch, dict):
                name = ch.get('name', '')
                vals = ch.get('value', ch.get('values', ''))
                if name and vals:
                    parts.append(f"- {name}: {vals}")
        return '\n'.join(parts)
    except Exception:
        return ''


@app.route('/api/covers/test-openai')
def api_test_openai():
    """Quick check that OpenAI key works."""
    s = Settings.query.first()
    if not s or not s.image_ai_api_key:
        return jsonify({'ok': False, 'error': 'Ключ OpenAI не настроен в Настройках'})
    try:
        client = _make_openai_client(s.image_ai_api_key, timeout=15.0, max_retries=0,
                                     proxy_url=getattr(s, 'openai_proxy_url', None))
        models = client.models.list()
        return jsonify({'ok': True, 'models_count': len(list(models))})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/covers/analyze', methods=['POST'])
def api_covers_analyze():
    """
    Step 1-3: product info (WB API) + competitor search + GPT-4o analysis.
    Tries visual analysis of competitor images first (20s timeout, 0 retries).
    Falls back instantly to enhanced text-only if images are blocked.
    """
    s = Settings.query.first()
    if not s or not s.image_ai_api_key:
        return jsonify({'error': 'API-ключ OpenAI не настроен'}), 400

    product_id = request.form.get('product_id')
    keyword = (request.form.get('keyword') or '').strip()
    extra_hint = (request.form.get('extra_hint') or '').strip()
    if not keyword or not product_id:
        return jsonify({'error': 'Укажите товар и ключевой запрос'}), 400

    product = Product.query.get_or_404(product_id)
    _proxy = getattr(s, 'openai_proxy_url', None)

    # ── Step 1: Product info from WB Content API (text, always works) ──
    wb_key = (s.wb_content_api_key or s.wb_stats_api_key or '').strip()
    card_info = _get_wb_card_info(wb_key, product.wb_article)
    cover_info = card_info or f'Товар: {product.name} (арт. {product.wb_article})'

    # ── Step 2: Competitor covers from WB search ──
    covers = []
    try:
        from modules import cover_gen
        covers = cover_gen.search_wb_covers(keyword, limit=8)
    except Exception:
        covers = []

    # ── Step 3a: Try VISUAL analysis of competitor images (15s, no retry) ──
    visual_insight = ''
    if covers:
        try:
            client_fast = _make_openai_client(s.image_ai_api_key, timeout=15.0, max_retries=0, proxy_url=_proxy)
            img_content = [
                {'type': 'image_url', 'image_url': {'url': c['url'], 'detail': 'low'}}
                for c in covers[:4]
            ]
            vis_resp = client_fast.chat.completions.create(
                model='gpt-4o',
                messages=[{'role': 'user', 'content': [
                    {'type': 'text', 'text': (
                        f'Ты маркетолог-аналитик Wildberries. Проанализируй эти обложки товаров из ниши "{keyword}".\n'
                        'Опиши КОНКРЕТНО:\n'
                        '1. ЦВЕТА — какие цвета доминируют на фоне, на продукте, в тексте\n'
                        '2. ФОНЫ — однотонный/градиент/текстура/сцена, цвета\n'
                        '3. ТЕКСТ — что написано на обложках, какие УТП, цифры, claims\n'
                        '4. КОМПОЗИЦИЯ — где стоит товар, как расположены элементы\n'
                        '5. ЧТО ВЫДЕЛЯЕТ лучшие обложки среди остальных\n'
                        'Ответ: 5-7 конкретных пунктов с деталями.'
                    )},
                ] + img_content}],
                max_tokens=400,
            )
            visual_insight = vis_resp.choices[0].message.content
        except Exception:
            visual_insight = ''

    # ── Step 3b: Build final prompt with GPT-4o (text + optional visual insight) ──
    hint_line = f'\nДополнительные пожелания от продавца: {extra_hint}' if extra_hint else ''
    competitor_names = '\n'.join(
        f"• {c['brand']} — {c['name']}" for c in covers[:6] if c.get('name')
    ) or 'не получены'

    visual_block = (
        f'\n\nВИЗУАЛЬНЫЙ АНАЛИЗ ОБЛОЖЕК КОНКУРЕНТОВ (детальный):\n{visual_insight}\n'
    ) if visual_insight else (
        f'\n\nКонкуренты в нише (названия):\n{competitor_names}\n'
        'Визуальный анализ: используй свои знания об эффективных обложках в этой нише.\n'
    )

    final_prompt = f"""Ты эксперт по визуальному маркетингу на Wildberries.

Ниша: "{keyword}"
Мой товар: {product.name}

ИНФОРМАЦИЯ О ТОВАРЕ (из кабинета WB — используй эти данные для текста на обложке):
{cover_info}
{hint_line}
{visual_block}
Создай детальный промпт для DALL-E 3 для новой конкурентоспособной обложки.

Промпт ДОЛЖЕН включать:
- Конкретные цвета фона и акцентов (лучшие из анализа ниши)
- Расположение и подачу товара в кадре
- Точные тексты на обложке: название, количество, состав, УТП из данных товара выше
- Стиль шрифтов и бейджей
- Почему эта обложка будет кликабельнее конкурентов

Формат:
АНАЛИЗ НИШИ:
[3-5 конкретных инсайтов — цвета, паттерны, что работает]

ПРОМПТ ДЛЯ DALLE:
[Детальный промпт на английском, 200-300 слов]"""

    try:
        client = _make_openai_client(s.image_ai_api_key, timeout=40.0, max_retries=0, proxy_url=_proxy)
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': final_prompt}],
            max_tokens=700,
        )
        analysis_text = resp.choices[0].message.content
    except Exception as e:
        return jsonify({'error': f'OpenAI ошибка анализа: {str(e)}'}), 500

    dalle_prompt = ''
    for marker in ['ПРОМПТ ДЛЯ DALLE:', 'ПРОМПТ:']:
        if marker in analysis_text:
            dalle_prompt = analysis_text.split(marker)[-1].strip()
            break
    if not dalle_prompt:
        dalle_prompt = (
            f'Professional Wildberries marketplace product cover for {product.name}. '
            f'Category: {keyword}. High CTR design, bold text badges, studio lighting, photorealistic.'
        )

    return jsonify({
        'cover_info': cover_info,
        'covers': covers,
        'analysis': analysis_text,
        'dalle_prompt': dalle_prompt,
        'used_current_cover': bool(card_info),
        'visual_analysis_done': bool(visual_insight),
    })


    dalle_prompt = ''
    if 'ПРОМПТ:' in analysis_text:
        dalle_prompt = analysis_text.split('ПРОМПТ:')[-1].strip()
    if not dalle_prompt:
        dalle_prompt = (
            f'Professional Wildberries product cover for {product.name}. '
            f'Category: {keyword}. Eye-catching, bold text badges, studio lighting, photorealistic.'
        )

    return jsonify({
        'cover_info': cover_info,
        'covers': covers,
        'analysis': analysis_text,
        'dalle_prompt': dalle_prompt,
        'used_current_cover': bool(cover_info and '[Не удалось' not in cover_info),
    })


@app.route('/api/covers/generate-image', methods=['POST'])
def api_covers_generate_image():
    """Step 2: generate image from prompt. ~30-90s."""
    s = Settings.query.first()
    if not s or not s.image_ai_api_key:
        return jsonify({'error': 'API-ключ OpenAI не настроен'}), 400

    dalle_prompt = (request.form.get('dalle_prompt') or '').strip()
    if not dalle_prompt:
        return jsonify({'error': 'Промпт пустой'}), 400

    _proxy2 = getattr(s, 'openai_proxy_url', None)
    client = _make_openai_client(s.image_ai_api_key, timeout=80.0, max_retries=0, proxy_url=_proxy2)
    try:
        img_resp = client.images.generate(
            model='dall-e-3',
            prompt=dalle_prompt[:4000],
            size='1024x1024',
            quality='standard',
            n=1,
        )
        return jsonify({'generated_url': img_resp.data[0].url})
    except Exception as e:
        return jsonify({'error': f'Ошибка генерации DALL-E: {e}'}), 500


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
            _px = getattr(s, 'openai_proxy_url', None)
            client = _make_openai_client(s.image_ai_api_key, timeout=120.0, max_retries=1, proxy_url=_px)
            img_resp = client.images.generate(
                model='dall-e-3', prompt=custom_prompt[:4000],
                size='1024x1024', quality='standard', n=1
            )
            return jsonify({
                'covers': [], 'analysis': '', 'cover_info': '',
                'dalle_prompt': custom_prompt, 'generated_url': img_resp.data[0].url,
                'used_product_photo': False, 'used_current_cover': False,
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
    s.openai_proxy_url = request.form.get('openai_proxy_url', '').strip()
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

@app.route('/pip-install')
def pip_install():
    """Install/upgrade Python packages from requirements.txt."""
    if request.args.get('token') != DEPLOY_TOKEN:
        return 'Unauthorized', 401
    try:
        result = subprocess.run(
            ['/var/www/wb-project/venv/bin/pip', 'install', '-r',
             '/var/www/wb-project/requirements.txt', '--upgrade'],
            capture_output=True, text=True, timeout=300
        )
        return f'<pre>STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\nReturn code: {result.returncode}</pre>', 200
    except Exception as e:
        return f'<pre>Error: {e}</pre>', 500


@app.route('/deploy')
def deploy():
    if request.args.get('token') != DEPLOY_TOKEN:
        return 'Unauthorized', 401
    try:
        env = os.environ.copy()
        env['HOME'] = '/var/www'
        out = subprocess.run(
            ['/usr/bin/git', '-C', '/var/www/wb-project', 'pull', 'origin',
             'claude/project-management-analysis-jKH5e'],
            capture_output=True, text=True, timeout=60, env=env
        )
        pip = subprocess.run(
            ['/var/www/wb-project/venv/bin/pip', 'install', '-r',
             '/var/www/wb-project/requirements.txt', '-q'],
            capture_output=True, text=True, timeout=180
        )
        fix = subprocess.run(
            ['chown', '-R', 'www-data:www-data', '/var/www/wb-project'],
            capture_output=True, text=True
        )
        return f'<pre>OK\n{out.stdout}{out.stderr}\npip:\n{pip.stdout}{pip.stderr}</pre>', 200
    except Exception as e:
        return f'<pre>Error: {e}</pre>', 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
